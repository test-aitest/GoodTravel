import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/cost_job_notes.json";
import approved from "../../agent/tests/contract/approved_job_notes.json";
import { eventNoteTag, newEvents, type ExecutionSnapshot } from "./agentfieldRecords";
import { eventWriter, jobEventTypes, safeParseJobEvent, type JobEvent } from "./events";
import { applyEvent, buildJobState } from "./jobState";

const startAt = "2026-11-14T09:00:00.000Z";
const base = { id: "evt-1", seq: 1, jobId: "job-1", clientId: "kumo", at: startAt };
const recorded = {
  ...base,
  type: "cost.recorded",
  claudeCalls: 8,
  inputTokens: 41600,
  outputTokens: 5120,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  claudeUsd: 0.2688,
  computerSeconds: 61,
  estimated: false,
};

function finishedJob() {
  const write = eventWriter({ jobId: "job-1", clientId: "kumo", startAt });
  const events: JobEvent[] = [
    write(
      "job.created",
      {
        clientName: "Kumo Coffee Roasters",
        title: "Supplier stock sync",
        request: "Sync today's stock from Maruyama Supply to our store admin.",
        run: { kind: "replay", procedureVersion: 1 },
        plan: [{ stepId: "read", title: "Read today's stock" }],
      },
      0,
    ),
    write("step.started", { stepId: "read", activity: "Reading today's stock" }, 100),
    write("step.done", { stepId: "read", summary: "Read today's stock", tookMs: 900 }, 1000),
  ];
  return { write, events };
}

function snapshot(): ExecutionSnapshot {
  const startSeconds = Date.UTC(2026, 10, 14, 8, 40, 0) / 1000;
  return {
    executionId: contract.jobId,
    clientId: contract.input.client_id,
    status: "succeeded",
    expectedEvents: contract.events,
    notes: contract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: startSeconds + index * 0.5 })),
  };
}

describe("cost.recorded event", () => {
  test("is one of the job events", () => {
    expect(jobEventTypes).toContain("cost.recorded");
  });

  test("accepts a job's Claude calls, tokens, dollars and computer seconds", () => {
    expect(safeParseJobEvent(recorded).success).toBe(true);
    expect(safeParseJobEvent({ ...recorded, estimated: true }).success).toBe(true);
    expect(
      safeParseJobEvent({ ...recorded, claudeCalls: 0, inputTokens: 0, outputTokens: 0, claudeUsd: 0, computerSeconds: 0 }).success,
    ).toBe(true);
    expect(safeParseJobEvent({ ...recorded, cacheReadTokens: 2000, cacheWriteTokens: 1000 }).success).toBe(true);
  });

  const malformed: Array<[string, unknown]> = [
    ["negative calls", { ...recorded, claudeCalls: -1 }],
    ["fractional tokens", { ...recorded, inputTokens: 1.5 }],
    ["negative dollars", { ...recorded, claudeUsd: -0.01 }],
    ["dollars as text", { ...recorded, claudeUsd: "0.27" }],
    ["fractional seconds", { ...recorded, computerSeconds: 1.5 }],
    ["no estimate flag", { ...recorded, estimated: undefined }],
    ["tokens without any call", { ...recorded, claudeCalls: 0, claudeUsd: 0 }],
    ["dollars without any call", { ...recorded, claudeCalls: 0, inputTokens: 0, outputTokens: 0 }],
    ["the client's name as an extra field", { ...recorded, clientName: "Kumo Coffee Roasters" }],
    ["a monthly total as an extra field", { ...recorded, monthUsd: 4.2 }],
  ];

  test.each(malformed)("rejects %s", (_label, input) => {
    expect(safeParseJobEvent(input).success).toBe(false);
  });
});

describe("job state cost", () => {
  test("a job has no cost until the worker records it", () => {
    expect(buildJobState(finishedJob().events).cost).toBeNull();
  });

  test("keeps the recorded cost without changing how the job ended", () => {
    const { write, events } = finishedJob();
    const before = buildJobState(events);
    events.push(
      write(
        "cost.recorded",
        { claudeCalls: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, claudeUsd: 0, computerSeconds: 6, estimated: true },
        1200,
      ),
    );
    const after = buildJobState(events);

    expect(after.cost).toEqual({ claudeCalls: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, claudeUsd: 0, computerSeconds: 6, estimated: true });
    expect(after.status).toBe(before.status);
    expect(after.result).toEqual(before.result);
    expect(after.steps).toEqual(before.steps);
    expect(after.progress).toBe(before.progress);
    expect(after.lastSeq).toBe(4);
  });

  test("a repeated cost note changes nothing", () => {
    const { write, events } = finishedJob();
    const note = write(
      "cost.recorded",
      { claudeCalls: 1, inputTokens: 10, outputTokens: 10, cacheReadTokens: 0, cacheWriteTokens: 0, claudeUsd: 0.0002, computerSeconds: 6, estimated: false },
      1200,
    );
    const once = buildJobState([...events, note]);
    expect(applyEvent(once, note)).toBe(once);
  });
});

describe("the agent's billed job", () => {
  test("is the approved job plus one cost note at the end", () => {
    expect(contract.notes.slice(0, -1)).toEqual(approved.notes);
    expect(contract.notes.at(-1)?.event.type).toBe("cost.recorded");
    expect(contract.events).toBe(contract.notes.length);
  });

  test("is read by the screen server as valid events only, and finishes only once the cost note is in", () => {
    const result = newEvents(snapshot(), 0);
    expect(result.rejected).toEqual([]);
    expect(result.events).toHaveLength(contract.notes.length);
    expect(result.finished).toBe(true);

    const withoutCost = newEvents({ ...snapshot(), notes: snapshot().notes.slice(0, -1) }, 0);
    expect(withoutCost.finished).toBe(false);
  });

  test("ends done with the cost of the whole job", () => {
    const state = buildJobState(newEvents(snapshot(), 0).events);
    expect(state.status).toBe("done");
    expect(state.result).toEqual({ kind: "done", written: 12, checked: 12, matched: 12 });
    expect(state.cost).toEqual({
      claudeCalls: 8,
      inputTokens: 41600,
      outputTokens: 5120,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
      claudeUsd: 0.2688,
      computerSeconds: 60,
      estimated: false,
    });
  });
});
