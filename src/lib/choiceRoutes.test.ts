import { describe, expect, test } from "bun:test";
import choiceContract from "../../agent/tests/contract/choice_job_notes.json";
import { AgentFieldError } from "./agentfieldClient";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import type { ApprovalDeps, JobRead } from "./approvalRoutes";
import { approvalSignature } from "./approvalSignature";
import { handleChoose } from "./choiceRoutes";
import type { JobEvent } from "./events";
import type { RequestLike } from "./setupRoutes";

const secret = "test-approval-secret-7f3a";
const jobId = choiceContract.jobId;
const askedIndex = choiceContract.notes.findIndex((note) => note.event.type === "choice.requested");
const asked = choiceContract.notes[askedIndex].event as { choiceId: string; candidates: { ref: string }[] };
const choiceId = asked.choiceId;
const [first, second] = asked.candidates.map((candidate) => candidate.ref);

function eventsUpTo(count: number): JobEvent[] {
  const snapshot = {
    executionId: jobId,
    clientId: "kumo",
    status: "succeeded" as const,
    expectedEvents: count,
    notes: choiceContract.notes.slice(0, count).map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index })),
  };
  return newEvents(snapshot, 0).events;
}

const waiting: JobRead = { status: "waiting", events: eventsUpTo(askedIndex + 1) };

function post(body?: unknown, headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers({ "content-type": "application/json", origin: "http://app.test", host: "app.test", ...headers });
  const text = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
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

describe("handleChoose", () => {
  test("signs your pick and sends only the element's ref, as an answer to the job's question", async () => {
    const { deps: built, sent } = deps();

    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: second }), built));

    expect(reply).toEqual({ status: 200, body: { jobId, choiceId, ref: second } });
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0].raw)).toEqual({ requestId: choiceId, decision: "approved", feedback: second });
    expect(sent[0].signature).toBe(approvalSignature(secret, sent[0].raw));
  });

  test("picking none of them is sent as a turned-down question, so the job stops without saving", async () => {
    const { deps: built, sent } = deps();

    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: null }), built));

    expect(reply).toEqual({ status: 200, body: { jobId, choiceId, ref: null } });
    expect(JSON.parse(sent[0].raw)).toEqual({ requestId: choiceId, decision: "rejected", feedback: "" });
  });

  test("refuses a page from another site", async () => {
    const { deps: built, sent } = deps();
    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: first }, { "sec-fetch-site": "cross-site" }), built));
    expect(reply).toEqual({ status: 403, body: { error: "Only the ClientOps page can do this." } });
    expect(sent).toHaveLength(0);
  });

  test("refuses an element that was not one of the choices", async () => {
    const { deps: built, sent } = deps();
    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: "9999" }), built));
    expect(reply).toEqual({ status: 400, body: { error: "Pick one of the elements the worker showed you." } });
    expect(sent).toHaveLength(0);
  });

  test.each<[string, unknown]>([
    ["no body", undefined],
    ["a missing ref", { jobId, choiceId }],
    ["extra fields", { jobId, choiceId, ref: first, decision: "approved" }],
    ["text that is not JSON", "ref=1016"],
  ])("answers 400 for %s", async (_label, body) => {
    const { deps: built, sent } = deps();
    const reply = await answer(await handleChoose(post(body), built));
    expect(reply).toEqual({ status: 400, body: { error: "Say which job, which question and which element." } });
    expect(sent).toHaveLength(0);
  });

  test("explains how to set up answers when the server has no secret", async () => {
    const { deps: built } = deps({ secret: undefined });
    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: first }), built));
    expect(reply.status).toBe(503);
    expect(reply.body.error).toContain("AGENTFIELD_APPROVAL_WEBHOOK_SECRET");
  });

  test("answers 404 for a question this job never asked", async () => {
    const { deps: built, sent } = deps();
    const reply = await answer(await handleChoose(post({ jobId, choiceId: "cho-someone-else", ref: first }), built));
    expect(reply).toEqual({ status: 404, body: { error: "This job has no such question." } });
    expect(sent).toHaveLength(0);
  });

  test("answers 409 when the question was already answered", async () => {
    const { deps: built, sent } = deps({ readJob: async () => ({ status: "running", events: eventsUpTo(askedIndex + 2) }) });
    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: first }), built));
    expect(reply).toEqual({ status: 409, body: { error: "This question was already answered." } });
    expect(sent).toHaveLength(0);
  });

  test.each<[ExecutionStatus]>([["cancelled"], ["failed"], ["succeeded"]])("answers 409 when the job has ended as %s", async (status) => {
    const { deps: built, sent } = deps({ readJob: async () => ({ status, events: waiting.events }) });
    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: first }), built));
    expect(reply).toEqual({ status: 409, body: { error: "This job has already ended." } });
    expect(sent).toHaveLength(0);
  });

  test("answers 409 while the worker has not started waiting yet", async () => {
    const { deps: built, sent } = deps({ readJob: async () => ({ status: "running", events: waiting.events }) });
    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: first }), built));
    expect(reply).toEqual({ status: 409, body: { error: "This job isn't ready for your answer yet. Try again in a moment." } });
    expect(sent).toHaveLength(0);
  });

  test("answers 502 when AgentField cannot be reached", async () => {
    const { deps: built } = deps({
      sendApproval: async () => {
        throw new AgentFieldError("AgentField is not reachable");
      },
    });
    const reply = await answer(await handleChoose(post({ jobId, choiceId, ref: first }), built));
    expect(reply).toEqual({ status: 502, body: { error: "The worker service is not available right now." } });
  });
});
