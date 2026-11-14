import { describe, expect, test } from "bun:test";
import { eventWriter, type JobEvent } from "./events";
import { applyEvent, buildJobState } from "./jobState";

const startAt = "2026-11-14T09:00:00.000Z";

const plan = [
  { stepId: "sign-in", title: "Sign in to Maruyama Supply" },
  { stepId: "read", title: "Read today's stock" },
  { stepId: "compare", title: "Compare with the store admin" },
  { stepId: "ask", title: "Ask you before saving" },
  { stepId: "save", title: "Save and check every row" },
];

function firstSteps() {
  const write = eventWriter({ jobId: "job-1", clientId: "kumo", startAt });
  const events: JobEvent[] = [
    write("job.created", {
      clientName: "Kumo Coffee Roasters",
      title: "Supplier stock sync",
      request: "Sync today's stock from Maruyama Supply to our store admin.",
      run: { kind: "learn" },
      plan,
    }, 0),
    write("step.started", { stepId: "sign-in", activity: "Signing in to Maruyama Supply" }, 100),
    write("step.decided", { stepId: "sign-in", rung: "claude", tookMs: 2200 }, 2300),
    write("step.done", { stepId: "sign-in", summary: "Signed in to Maruyama Supply", tookMs: 2400 }, 2500),
    write("step.started", { stepId: "read", activity: "Reading today's stock" }, 2600),
    write("step.decided", { stepId: "read", rung: "claude", tookMs: 1100 }, 3700),
    write("step.done", { stepId: "read", summary: "Read today's stock", detail: "42 products", tookMs: 1300 }, 3900),
    write("step.started", { stepId: "compare", activity: "Comparing with the store admin", detail: "Matching by SKU" }, 4000),
  ];
  return { write, events };
}

const changes = [
  { sku: "KC-ETH-200", name: "Ethiopia Guji 200g", store: 18, supplier: 6 },
  { sku: "KC-KEN-200", name: "Kenya Nyeri 200g", store: 9, supplier: 0 },
];

function untilApproval() {
  const { write, events } = firstSteps();
  events.push(
    write("step.decided", { stepId: "compare", rung: "claude", tookMs: 3000 }, 7000),
    write("step.done", { stepId: "compare", summary: "Compared with the store admin", detail: "2 products differ", tookMs: 3200 }, 7200),
    write("step.started", { stepId: "ask", activity: "Waiting for your OK" }, 7300),
    write("approval.requested", {
      approvalId: "apr-1",
      stepId: "ask",
      question: "Save 2 stock changes to the store admin?",
      changes,
      undoable: true,
    }, 7400),
  );
  return { write, events };
}

function untilSaving() {
  const { write, events } = untilApproval();
  events.push(
    write("approval.answered", { approvalId: "apr-1", decision: "approved", by: "Aoi Tanaka" }, 20000),
    write("step.decided", { stepId: "ask", rung: "human", tookMs: 12600 }, 20000),
    write("step.done", { stepId: "ask", summary: "You approved 2 changes", tookMs: 12700 }, 20000),
    write("step.started", { stepId: "save", activity: "Saving 2 changes" }, 20100),
    write("step.decided", { stepId: "save", rung: "claude", tookMs: 900 }, 21000),
    write("write.done", { stepId: "save", written: 2 }, 22000),
  );
  return { write, events };
}

describe("applyEvent", () => {
  test("a new job has every planned step pending and no progress", () => {
    const { events } = firstSteps();
    const state = applyEvent(null, events[0]);
    expect(state.status).toBe("running");
    expect(state.clientName).toBe("Kumo Coffee Roasters");
    expect(state.steps.map((step) => [step.stepId, step.status])).toEqual(plan.map((step) => [step.stepId, "pending"]));
    expect(state.doneCount).toBe(0);
    expect(state.progress).toBe(0);
    expect(state.activeStepId).toBeNull();
    expect(state.result).toBeNull();
  });

  test("two of five steps done is 40% with the third step active", () => {
    const state = buildJobState(firstSteps().events);
    expect(state.doneCount).toBe(2);
    expect(state.progress).toBe(40);
    expect(state.activeStepId).toBe("compare");
    expect(state.stepIndex.compare).toBe(2);
    expect(state.steps[2]).toMatchObject({ status: "active", activity: "Comparing with the store admin", detail: "Matching by SKU" });
  });

  test("records who decided each step and how long it took", () => {
    const state = buildJobState(firstSteps().events);
    expect(state.steps[0]).toMatchObject({ status: "done", decidedBy: "claude", decidedInMs: 2200, summary: "Signed in to Maruyama Supply", tookMs: 2400 });
    expect(state.steps[1]).toMatchObject({ decidedBy: "claude", summary: "Read today's stock", detail: "42 products" });
    expect(state.steps[3].decidedBy).toBeUndefined();
    expect(state.claudeDecisions).toBe(2);
  });

  test("keeps the rungs a step climbed through", () => {
    const { write, events } = firstSteps();
    events.push(write("step.decided", { stepId: "compare", rung: "claude", tookMs: 800, escalatedFrom: ["script", "tree"] }, 5000));
    expect(buildJobState(events).steps[2]).toMatchObject({ decidedBy: "claude", escalatedFrom: ["script", "tree"] });
  });

  test("an approval request waits for the person and remembers how long getting ready took", () => {
    const state = buildJobState(untilApproval().events);
    expect(state.status).toBe("waiting");
    expect(state.pendingApproval).toMatchObject({ approvalId: "apr-1", stepId: "ask", question: "Save 2 stock changes to the store admin?", changes, undoable: true });
    expect(state.readyInMs).toBe(7400);
    expect(state.progress).toBe(60);
  });

  test("approving clears the request and the job runs again", () => {
    const { write, events } = untilApproval();
    events.push(write("approval.answered", { approvalId: "apr-1", decision: "approved", by: "Aoi Tanaka" }, 20000));
    const state = buildJobState(events);
    expect(state.status).toBe("running");
    expect(state.pendingApproval).toBeNull();
    expect(state.lastAnswer).toEqual({ approvalId: "apr-1", decision: "approved", by: "Aoi Tanaka" });
  });

  test("an unanswered approval keeps the job waiting, and it can still be approved later", () => {
    const { write, events } = untilApproval();
    const waiting = buildJobState(events);
    expect(waiting.status).toBe("waiting");
    expect(waiting.result).toBeNull();
    const later = applyEvent(waiting, write("approval.answered", { approvalId: "apr-1", decision: "approved", by: "Aoi Tanaka" }, 86_400_000));
    expect(later.status).toBe("running");
    expect(later.pendingApproval).toBeNull();
  });

  test("stopping while waiting for approval ends the job with nothing saved", () => {
    const { write, events } = untilApproval();
    events.push(write("job.failed", { reason: "stopped", message: "Stopped by Aoi Tanaka." }, 20000));
    const state = buildJobState(events);
    expect(state.status).toBe("stopped");
    expect(state.pendingApproval).toBeNull();
    expect(state.written).toBeNull();
    expect(state.steps[3].status).toBe("stopped");
    expect(state.result).toEqual({ kind: "stopped", reason: "stopped" });
  });

  test("the last step done with every row matching finishes the job", () => {
    const { write, events } = untilSaving();
    events.push(
      write("verify.done", { stepId: "save", checked: 2, matched: 2 }, 23000),
      write("step.done", { stepId: "save", summary: "Saved and checked 2 rows", tookMs: 2900 }, 23000),
    );
    const state = buildJobState(events);
    expect(state.status).toBe("done");
    expect(state.progress).toBe(100);
    expect(state.activeStepId).toBeNull();
    expect(state.result).toEqual({ kind: "done", written: 2, checked: 2, matched: 2 });
    expect(state.claudeDecisions).toBe(4);
    expect(state.elapsedMs).toBe(23000);
  });

  test("a read-back that does not match fails the job at that step", () => {
    const { write, events } = untilSaving();
    events.push(write("verify.done", { stepId: "save", checked: 2, matched: 1 }, 23000));
    const state = buildJobState(events);
    expect(state.status).toBe("failed");
    expect(state.steps[4].status).toBe("failed");
    expect(state.result).toEqual({ kind: "failed", reason: "verify-mismatch", stepId: "save" });
  });

  test("a saved procedure is kept, also after the job finished", () => {
    const { write, events } = untilSaving();
    events.push(
      write("verify.done", { stepId: "save", checked: 2, matched: 2 }, 23000),
      write("step.done", { stepId: "save", summary: "Saved and checked 2 rows", tookMs: 2900 }, 23000),
      write("procedure.saved", { procedureId: "kumo-stock-sync", version: 1, stepCount: 5, expectedReplayMs: 6000 }, 23500),
    );
    const state = buildJobState(events);
    expect(state.status).toBe("done");
    expect(state.procedure).toEqual({ procedureId: "kumo-stock-sync", version: 1, stepCount: 5, expectedReplayMs: 6000 });
  });

  test("a failure marks its step and ends the job", () => {
    const { write, events } = firstSteps();
    events.push(write("job.failed", { reason: "error", message: "The store admin did not load.", stepId: "compare" }, 9000));
    const state = buildJobState(events);
    expect(state.status).toBe("failed");
    expect(state.activeStepId).toBeNull();
    expect(state.steps[2].status).toBe("failed");
    expect(state.result).toEqual({ kind: "failed", reason: "error", message: "The store admin did not load.", stepId: "compare" });
  });

  test("a stop by the person is a stop, not a failure", () => {
    const { write, events } = firstSteps();
    events.push(write("job.failed", { reason: "stopped", message: "Stopped by Aoi Tanaka." }, 9000));
    const state = buildJobState(events);
    expect(state.status).toBe("stopped");
    expect(state.result).toEqual({ kind: "stopped", reason: "stopped" });
  });

  test("asking for another client's login is blocked and recorded", () => {
    const write = eventWriter({ jobId: "job-9", clientId: "hinata", startAt });
    const state = buildJobState([
      write("job.created", {
        clientName: "Hinata Skincare",
        title: "Supplier prices",
        request: "Use Kumo Coffee's Maruyama login to check Hinata's supplier prices too.",
        run: { kind: "learn" },
        plan: [],
      }, 0),
      write("boundary.blocked", {
        resource: "Maruyama Supply login",
        ownerClientId: "kumo",
        ownerClientName: "Kumo Coffee Roasters",
        auditId: "audit-1",
        offersOwnAccount: true,
      }, 400),
    ]);
    expect(state.status).toBe("blocked");
    expect(state.progress).toBe(0);
    expect(state.result).toEqual({
      kind: "blocked",
      resource: "Maruyama Supply login",
      ownerClientId: "kumo",
      ownerClientName: "Kumo Coffee Roasters",
      auditId: "audit-1",
      offersOwnAccount: true,
    });
  });

  test("adds only what the new event changes and never edits the previous state", () => {
    const { events } = firstSteps();
    const before = buildJobState(events.slice(0, -1));
    const frozen = structuredClone(before);
    const after = applyEvent(before, events.at(-1)!);
    expect(before).toEqual(frozen);
    expect(after).not.toBe(before);
    expect(after.steps[0]).toBe(before.steps[0]);
    expect(after.steps[1]).toBe(before.steps[1]);
    expect(after.steps[2]).not.toBe(before.steps[2]);
    expect(after.stepIndex).toBe(before.stepIndex);
  });

  test("an event seen again is ignored", () => {
    const { events } = firstSteps();
    const state = buildJobState(events);
    expect(applyEvent(state, events[3])).toBe(state);
    expect(applyEvent(state, events.at(-1)!)).toBe(state);
  });

  test("refuses events that do not belong to this job or come before it exists", () => {
    const { events } = firstSteps();
    const state = buildJobState(events);
    const other = eventWriter({ jobId: "job-2", clientId: "kumo", startAt });
    expect(() => applyEvent(null, events[1])).toThrow("job.created");
    expect(() => applyEvent(state, other("step.started", { stepId: "read", activity: "Reading" }, 9000))).toThrow("job-2");
  });

  test("refuses a step or approval the job does not have", () => {
    const { write, events } = untilApproval();
    const state = buildJobState(events);
    expect(() => applyEvent(state, write("step.started", { stepId: "pay", activity: "Paying" }, 8000))).toThrow("pay");
    expect(() => applyEvent(state, write("approval.answered", { approvalId: "apr-9", decision: "approved", by: "Aoi Tanaka" }, 8000))).toThrow("apr-9");
  });
});

describe("eventWriter", () => {
  test("numbers events from 1, stamps time from the start and validates them", () => {
    const write = eventWriter({ jobId: "job-1", clientId: "kumo", startAt });
    const first = write("step.started", { stepId: "read", activity: "Reading" }, 1500);
    const second = write("step.done", { stepId: "read", summary: "Read", tookMs: 10 }, 2000);
    expect(first).toEqual({ id: "job-1-1", seq: 1, jobId: "job-1", clientId: "kumo", at: "2026-11-14T09:00:01.500Z", type: "step.started", stepId: "read", activity: "Reading" });
    expect(second.seq).toBe(2);
    expect(() => write("step.decided", { stepId: "read", rung: "gpt" as never, tookMs: 1 }, 3000)).toThrow();
  });
});
