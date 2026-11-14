import { describe, expect, test } from "bun:test";
import { clientMemoryActor, clientMemoryScope, procedureMemoryKey } from "./clientMemory";
import { fixtureJobIds, fixtureJobs, leaksIn, savedProcedureContract } from "./mcpFixtures";
import { jobSummary, jobSummarySchema, savedProcedureSummary, savedProcedureSummarySchema, waitingApproval, waitingApprovalSchema } from "./mcpViews";

const summaryOf = (jobId: string) => jobSummary(jobId, fixtureJobs[jobId]);

describe("a job's status for an AI client", () => {
  test("a job waiting for the OK gives the question and counts only, with who decided each step", () => {
    const summary = summaryOf(fixtureJobIds.waiting);

    expect(summary).toMatchObject({
      jobId: fixtureJobIds.waiting,
      clientId: "kumo",
      clientName: "Kumo Coffee Roasters",
      title: "Supplier stock sync",
      run: { kind: "learn" },
      status: "waiting",
      result: null,
      savedProcedure: null,
    });
    expect(summary.pendingApproval).toEqual({
      question: "Save 12 stock changes to the store admin?",
      changeCount: 12,
      soldOutCount: 3,
      undoable: true,
      changesSavedSteps: false,
      askedAt: expect.stringMatching(/^2026-11-14T09:\d\d:\d\d/),
    });
    expect(summary.steps.map((step) => [step.stepId, step.status, step.decidedBy ?? null])).toEqual([
      ["sign-in", "done", "claude"],
      ["read", "done", "claude"],
      ["compare", "done", "claude"],
      ["check-save", "done", "claude"],
      ["ask", "active", null],
      ["save", "pending", null],
    ]);
    expect(summary.steps[4]).toMatchObject({ title: "Ask you before saving", activity: "Waiting for your OK" });
    expect(summary.steps[2]).toMatchObject({ summary: "Compared with the store admin", detail: "12 products differ" });
    expect(summary.claudeDecisions).toBe(4);
    expect(leaksIn(summary)).toEqual([]);
  });

  test("a job still working has no approval and only the active step carries an activity", () => {
    const summary = summaryOf(fixtureJobIds.running);

    expect(summary.status).toBe("running");
    expect(summary.pendingApproval).toBeNull();
    expect(summary.progress).toBeGreaterThan(0);
    expect(summary.progress).toBeLessThan(100);
    expect(summary.steps.filter((step) => step.activity !== undefined).map((step) => step.status)).toEqual(["active"]);
  });

  test("a finished first run reports the checked rows and the saved steps' version, step count and expected replay time", () => {
    const summary = summaryOf(fixtureJobIds.learned);

    expect(summary.status).toBe("done");
    expect(summary.progress).toBe(100);
    expect(summary.result).toEqual({ kind: "done", written: 12, checked: 12, matched: 12 });
    expect(summary.savedProcedure).toEqual({ procedureId: "kumo-stock-sync", version: 1, stepCount: 6, expectedReplayMs: 5500 });
    expect(summary.steps.find((step) => step.stepId === "ask")?.decidedBy).toBe("human");
    expect(leaksIn(summary)).toEqual([]);
  });

  test("a step found again by the element tree says it was escalated from the script", () => {
    const summary = summaryOf(fixtureJobIds.refind);

    expect(summary.steps.find((step) => step.stepId === "check-save")).toMatchObject({ decidedBy: "tree", escalatedFrom: ["script"] });
    expect(summary.claudeDecisions).toBe(0);
    expect(summary.savedProcedure?.version).toBe(2);
  });

  test("a job blocked at the client boundary names the resource and audit line, never the other client", () => {
    const summary = summaryOf(fixtureJobIds.blocked);
    const text = JSON.stringify(summary);

    expect(summary.clientId).toBe("hinata");
    expect(summary.status).toBe("blocked");
    expect(summary.result).toEqual({ kind: "blocked", resource: "Maruyama Supply login", auditId: expect.stringMatching(/^audit-hinata-/) });
    expect(text).not.toContain("kumo");
    expect(text).not.toContain("Kumo");
  });

  test("a stopped job and a job whose rows did not match after saving end with their reason", () => {
    expect(summaryOf(fixtureJobIds.stopped)).toMatchObject({ status: "stopped", result: { kind: "stopped" }, pendingApproval: null });

    const mismatch = summaryOf(fixtureJobIds.mismatch);
    expect(mismatch.status).toBe("failed");
    expect(mismatch.result).toMatchObject({ kind: "failed", reason: "verify-mismatch", stepId: "save" });
    expect(mismatch.result?.kind === "failed" && mismatch.result.message).toContain("don't match");
    expect(leaksIn(mismatch)).toEqual([]);
  });

  test("a job with no events yet is starting, and one that ended without any is failed or stopped", () => {
    expect(summaryOf(fixtureJobIds.starting)).toEqual({
      jobId: fixtureJobIds.starting,
      clientId: null,
      clientName: null,
      title: null,
      run: null,
      status: "starting",
      progress: 0,
      steps: [],
      claudeDecisions: 0,
      pendingApproval: null,
      result: null,
      savedProcedure: null,
      startedAt: null,
      updatedAt: null,
    });
    expect(jobSummary("exec_gone", { status: "failed", events: [] }).status).toBe("failed");
    expect(jobSummary("exec_gone", { status: "cancelled", events: [] }).status).toBe("stopped");
  });

  test("every summary fits the strict output shape, which has no place for changes, pages, scripts or approval ids", () => {
    for (const jobId of Object.values(fixtureJobIds)) {
      const summary = summaryOf(jobId);
      expect(jobSummarySchema.safeParse(summary).success).toBe(true);
      expect(leaksIn(summary)).toEqual([]);
    }
    const withChanges = { ...summaryOf(fixtureJobIds.waiting) };
    const pending = { ...withChanges.pendingApproval, changes: [{ sku: "KC-ETH-200" }] };
    expect(jobSummarySchema.safeParse({ ...withChanges, pendingApproval: pending }).success).toBe(false);
    expect(jobSummarySchema.safeParse({ ...withChanges, page: { html: "<html></html>" } }).success).toBe(false);
  });
});

describe("a waiting approval for an AI client", () => {
  test("is the question and counts of a job the worker holds at the OK", () => {
    const approval = waitingApproval(fixtureJobIds.waiting, fixtureJobs[fixtureJobIds.waiting]);

    expect(approval).toEqual({
      jobId: fixtureJobIds.waiting,
      clientId: "kumo",
      title: "Supplier stock sync",
      question: "Save 12 stock changes to the store admin?",
      changeCount: 12,
      soldOutCount: 3,
      askedAt: expect.any(String),
    });
    expect(waitingApprovalSchema.safeParse(approval).success).toBe(true);
    expect(leaksIn(approval)).toEqual([]);
  });

  test("is nothing for a job that is still working, finished or stopped", () => {
    for (const jobId of [fixtureJobIds.running, fixtureJobIds.learned, fixtureJobIds.stopped, fixtureJobIds.starting]) {
      expect(waitingApproval(jobId, fixtureJobs[jobId])).toBeNull();
    }
    expect(waitingApproval(fixtureJobIds.waiting, { ...fixtureJobs[fixtureJobIds.waiting], status: "running" })).toBeNull();
  });
});

describe("a saved procedure for an AI client", () => {
  test("lives where the agent keeps it: the client's actor memory under the procedure key", () => {
    expect(clientMemoryScope).toBe(savedProcedureContract.memory.scope);
    expect(clientMemoryActor(savedProcedureContract.clientId)).toBe(savedProcedureContract.memory.scopeId);
    expect(procedureMemoryKey).toBe(savedProcedureContract.memory.key);
  });

  test("is its version, step count, expected replay time and save time, never its script, selectors or logins", () => {
    const summary = savedProcedureSummary(savedProcedureContract.record, "kumo");

    expect(summary).toEqual({ procedureId: "kumo-stock-sync", version: 1, stepCount: 6, expectedReplayMs: 4200, savedAt: "2026-11-14T08:41:13Z" });
    expect(savedProcedureSummarySchema.safeParse(summary).success).toBe(true);
    expect(leaksIn(summary)).toEqual([]);
  });

  test("is nothing when the record belongs to another client or cannot be read", () => {
    const record = savedProcedureContract.record;
    expect(savedProcedureSummary(record, "hinata")).toBeNull();
    expect(savedProcedureSummary({ ...record, clientId: "hinata" }, "hinata")).toBeNull();
    expect(savedProcedureSummary({ ...record, version: 2 }, "kumo")).toBeNull();
    expect(savedProcedureSummary({ ...record, procedureVersion: 0 }, "kumo")).toBeNull();
    expect(savedProcedureSummary({ ...record, steps: [] }, "kumo")).toBeNull();
    expect(savedProcedureSummary(null, "kumo")).toBeNull();
    expect(savedProcedureSummary("not a record", "kumo")).toBeNull();
  });
});
