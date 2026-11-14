import { describe, expect, test } from "bun:test";
import approvedContract from "../../agent/tests/contract/approved_job_notes.json";
import { AgentFieldError } from "./agentfieldClient";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import { handleApprove, handleStop, handleUndo, type ApprovalDeps, type JobRead } from "./approvalRoutes";
import { approvalSignature } from "./approvalSignature";
import type { JobEvent } from "./events";
import type { RequestLike } from "./setupRoutes";

const secret = "test-approval-secret-7f3a";
const jobId = approvedContract.jobId;
const approvalNoteIndex = approvedContract.notes.findIndex((note) => note.event.type === "approval.requested");
const approvalId = String((approvedContract.notes[approvalNoteIndex].event as { approvalId: string }).approvalId);

function eventsUpTo(count: number): JobEvent[] {
  const snapshot = {
    executionId: jobId,
    clientId: "kumo",
    status: "succeeded" as const,
    expectedEvents: count,
    notes: approvedContract.notes.slice(0, count).map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index })),
  };
  return newEvents(snapshot, 0).events;
}

const waiting: JobRead = { status: "waiting", events: eventsUpTo(approvalNoteIndex + 1) };

function post(path: string, body?: unknown, headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers({ "content-type": "application/json", origin: "http://app.test", host: "app.test", ...headers });
  const text = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
  expect(path.startsWith("/api/")).toBe(true);
  return { headers: { get: (name) => sent.get(name) }, text: async () => text };
}

function deps(overrides: Partial<ApprovalDeps> & { reply?: { status: number; body: unknown } } = {}) {
  const sent: { raw: string; signature: string }[] = [];
  const reply = overrides.reply ?? { status: 200, body: { status: "processed", execution_id: jobId, decision: "approved", new_status: "running" } };
  const built: ApprovalDeps = {
    secret,
    readJob: async (id) => (id === jobId ? waiting : null),
    sendApproval: async (raw, signature) => {
      sent.push({ raw, signature });
      return reply;
    },
    ...overrides,
  };
  return { deps: built, sent };
}

async function answer(response: Response) {
  const text = await response.text();
  expect(text).not.toContain(secret);
  return { status: response.status, body: JSON.parse(text) as Record<string, unknown> };
}

describe("handleApprove", () => {
  test("signs the raw approval body and sends only the ids it was given", async () => {
    const { deps: built, sent } = deps();

    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));

    expect(reply).toEqual({ status: 200, body: { jobId, approvalId, approved: true } });
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0].raw)).toEqual({ requestId: approvalId, decision: "approved", feedback: "" });
    expect(sent[0].signature).toBe(approvalSignature(secret, sent[0].raw));
  });

  test("refuses a page from another site", async () => {
    const { deps: built, sent } = deps();
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }, { "sec-fetch-site": "cross-site" }), built));
    expect(reply.status).toBe(403);
    expect(sent).toHaveLength(0);
  });

  test.each([["{"], [{ jobId }], [{ jobId, approvalId, decision: "rejected" }], [{ jobId: "", approvalId }], [{ jobId, approvalId: "x".repeat(300) }]])(
    "answers 400 to a body that is not just the two ids: %p",
    async (body) => {
      const { deps: built, sent } = deps();
      const reply = await answer(await handleApprove(post("/api/approvals", body), built));
      expect(reply.status).toBe(400);
      expect(sent).toHaveLength(0);
    },
  );

  test("explains how to set up approvals when the server has no secret", async () => {
    const { deps: built, sent } = deps({ secret: undefined });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply.status).toBe(503);
    expect(reply.body.error).toBe("Approvals aren't set up on this server. Put AGENTFIELD_APPROVAL_WEBHOOK_SECRET in .env.local and restart the app.");
    expect(sent).toHaveLength(0);
  });

  test("answers 404 for a job that does not exist", async () => {
    const { deps: built, sent } = deps();
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId: "exec_other", approvalId }), built));
    expect(reply).toEqual({ status: 404, body: { error: "There's no such job." } });
    expect(sent).toHaveLength(0);
  });

  test("answers 404 for an approval that belongs to no request in this job", async () => {
    const { deps: built, sent } = deps();
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId: "apr-someone-else" }), built));
    expect(reply).toEqual({ status: 404, body: { error: "This job has no such approval." } });
    expect(sent).toHaveLength(0);
  });

  test("answers 409 when the approval was already answered", async () => {
    const { deps: built, sent } = deps({ readJob: async () => ({ status: "running", events: eventsUpTo(approvalNoteIndex + 2) }) });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply).toEqual({ status: 409, body: { error: "This approval was already answered." } });
    expect(sent).toHaveLength(0);
  });

  test.each<[ExecutionStatus]>([["cancelled"], ["failed"], ["succeeded"], ["timeout"]])("answers 409 when the job has ended as %s", async (status) => {
    const { deps: built, sent } = deps({ readJob: async () => ({ status, events: waiting.events }) });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply).toEqual({ status: 409, body: { error: "This job has already ended." } });
    expect(sent).toHaveLength(0);
  });

  test("answers 409 while the worker has not started waiting yet", async () => {
    const { deps: built, sent } = deps({ readJob: async () => ({ status: "running", events: waiting.events }) });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply).toEqual({ status: 409, body: { error: "This job isn't ready for your OK yet. Try again in a moment." } });
    expect(sent).toHaveLength(0);
  });

  test("makes AgentField's missing secret clear", async () => {
    const { deps: built } = deps({ reply: { status: 503, body: { error: "approval webhook not configured", message: "set AGENTFIELD_APPROVAL_WEBHOOK_SECRET" } } });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply).toEqual({
      status: 503,
      body: { error: "AgentField isn't set up to take approvals. Restart it with AGENTFIELD_APPROVAL_WEBHOOK_SECRET in .env.local: bun run agentfield:stop, then bun run agentfield." },
    });
  });

  test("makes a secret that differs from AgentField's clear", async () => {
    const { deps: built } = deps({ reply: { status: 401, body: { error: "invalid webhook signature" } } });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply).toEqual({
      status: 502,
      body: { error: "AgentField didn't accept the approval's signature. AGENTFIELD_APPROVAL_WEBHOOK_SECRET must be the same here and in AgentField." },
    });
  });

  test("passes AgentField's not found through as 404", async () => {
    const { deps: built } = deps({ reply: { status: 404, body: { error: "no execution found" } } });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply).toEqual({ status: 404, body: { error: "AgentField has no job waiting on this approval." } });
  });

  test("treats AgentField's already processed as already answered", async () => {
    const { deps: built } = deps({ reply: { status: 200, body: { status: "already_processed" } } });
    const reply = await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), built));
    expect(reply).toEqual({ status: 409, body: { error: "This approval was already answered." } });
  });

  test("answers 502 when AgentField cannot be reached", async () => {
    const unreachable = deps({ readJob: async () => Promise.reject(new AgentFieldError("AgentField is not reachable")) });
    expect((await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), unreachable.deps))).status).toBe(502);
    const failing = deps({ sendApproval: async () => Promise.reject(new AgentFieldError("AgentField is not reachable")) });
    expect((await answer(await handleApprove(post("/api/approvals", { jobId, approvalId }), failing.deps))).status).toBe(502);
  });
});

function stopDeps(status: ExecutionStatus | null, cancelStatus = 200) {
  const cancelled: string[] = [];
  return {
    cancelled,
    deps: {
      readJob: async (id: string) => (status === null || id !== jobId ? null : { status, events: waiting.events }),
      cancel: async (id: string) => {
        cancelled.push(id);
        return { status: cancelStatus };
      },
    },
  };
}

describe("handleStop", () => {
  test.each<[ExecutionStatus]>([["waiting"], ["running"], ["queued"]])("asks AgentField to cancel a %s job", async (status) => {
    const { deps: built, cancelled } = stopDeps(status);
    const reply = await answer(await handleStop(post(`/api/jobs/${jobId}/stop`), jobId, built));
    expect(reply).toEqual({ status: 200, body: { jobId, stopped: true } });
    expect(cancelled).toEqual([jobId]);
  });

  test("answers 404 for a job that does not exist", async () => {
    const { deps: built, cancelled } = stopDeps(null);
    expect((await answer(await handleStop(post("/api/jobs/x/stop"), "x", built))).status).toBe(404);
    expect(cancelled).toEqual([]);
  });

  test("answers 409 for a job that already ended", async () => {
    const { deps: built, cancelled } = stopDeps("succeeded");
    expect(await answer(await handleStop(post(`/api/jobs/${jobId}/stop`), jobId, built))).toEqual({ status: 409, body: { error: "This job has already ended." } });
    expect(cancelled).toEqual([]);
  });

  test("answers 409 when AgentField says it ended in the meantime", async () => {
    const { deps: built } = stopDeps("waiting", 409);
    expect((await answer(await handleStop(post(`/api/jobs/${jobId}/stop`), jobId, built))).status).toBe(409);
  });

  test("refuses a page from another site", async () => {
    const { deps: built, cancelled } = stopDeps("waiting");
    expect((await answer(await handleStop(post(`/api/jobs/${jobId}/stop`, undefined, { "sec-fetch-site": "cross-site" }), jobId, built))).status).toBe(403);
    expect(cancelled).toEqual([]);
  });
});

const findClient = (id: string) => (id === "kumo" ? { id, name: "Kumo Coffee Roasters" } : null);

describe("handleUndo", () => {
  test("starts the undo job for a known client; the click is the approval", async () => {
    const started: string[] = [];
    const reply = await answer(
      await handleUndo(post("/api/clients/kumo/undo"), "kumo", {
        findClient,
        startUndo: async (clientId) => {
          started.push(clientId);
          return { executionId: "exec_undo", runId: "run_undo" };
        },
      }),
    );
    expect(reply).toEqual({ status: 201, body: { jobId: "exec_undo", runId: "run_undo" } });
    expect(started).toEqual(["kumo"]);
  });

  test("answers 404 for an unknown client", async () => {
    const reply = await answer(await handleUndo(post("/api/clients/nobody/undo"), "nobody", { findClient, startUndo: async () => ({ executionId: "x", runId: "y" }) }));
    expect(reply).toEqual({ status: 404, body: { error: "There is no such client." } });
  });

  test("answers 502 when the worker service is down", async () => {
    const reply = await answer(
      await handleUndo(post("/api/clients/kumo/undo"), "kumo", { findClient, startUndo: async () => Promise.reject(new AgentFieldError("AgentField is not reachable")) }),
    );
    expect(reply.status).toBe(502);
  });

  test("refuses a page from another site", async () => {
    const reply = await answer(
      await handleUndo(post("/api/clients/kumo/undo", undefined, { "sec-fetch-site": "cross-site" }), "kumo", { findClient, startUndo: async () => ({ executionId: "x", runId: "y" }) }),
    );
    expect(reply.status).toBe(403);
  });
});
