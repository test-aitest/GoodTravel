import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/stub_job_notes.json";
import { buildJobState } from "./jobState";
import { eventNoteTag, newEvents, normalizeStatus, type ExecutionNote, type ExecutionSnapshot } from "./agentfieldRecords";

const startSeconds = Date.UTC(2026, 10, 14, 6, 0, 0) / 1000;

function notesFor(payloads: readonly unknown[]): ExecutionNote[] {
  return payloads.map((payload, index) => ({
    message: JSON.stringify(payload),
    tags: [eventNoteTag],
    timestamp: startSeconds + index * 0.5,
  }));
}

function snapshot(overrides: Partial<ExecutionSnapshot> = {}): ExecutionSnapshot {
  return {
    executionId: "exec_1",
    clientId: "kumo",
    status: "running",
    notes: notesFor(contract.notes),
    ...overrides,
  };
}

describe("normalizeStatus", () => {
  test("keeps AgentField's statuses and folds the aliases the SDK knows", () => {
    expect(normalizeStatus("running")).toBe("running");
    expect(normalizeStatus("COMPLETED")).toBe("succeeded");
    expect(normalizeStatus("awaiting_approval")).toBe("waiting");
    expect(normalizeStatus("canceled")).toBe("cancelled");
    expect(normalizeStatus(undefined)).toBe("unknown");
    expect(normalizeStatus("something else")).toBe("unknown");
  });
});

describe("newEvents", () => {
  test("turns the agent's notes into valid 006 events that replay to a finished job", () => {
    const result = newEvents(snapshot({ status: "succeeded" }), 0);

    expect(result.rejected).toEqual([]);
    expect(result.events.map((event) => event.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(result.events[0]).toMatchObject({ id: "exec_1-1", jobId: "exec_1", clientId: "kumo", type: "job.created" });
    expect(result.events[0].at).toBe("2026-11-14T06:00:00.000Z");
    expect(result.lastSeq).toBe(10);
    expect(result.finished).toBe(true);

    const state = buildJobState(result.events);
    expect(state.status).toBe("done");
    expect(state.progress).toBe(100);
    expect(state.steps.map((step) => step.decidedBy)).toEqual(["script", "tree", "script"]);
  });

  test("gives only what is new since the last sequence number", () => {
    const first = newEvents(snapshot({ notes: notesFor(contract.notes.slice(0, 4)) }), 0);
    expect(first.events.map((event) => event.seq)).toEqual([1, 2, 3, 4]);
    expect(first.finished).toBe(false);

    const again = newEvents(snapshot({ notes: notesFor(contract.notes.slice(0, 4)) }), first.lastSeq);
    expect(again.events).toEqual([]);
    expect(again.lastSeq).toBe(4);

    const later = newEvents(snapshot(), first.lastSeq);
    expect(later.events.map((event) => event.seq)).toEqual([5, 6, 7, 8, 9, 10]);
  });

  test("waits at a gap while running, so events never arrive out of order", () => {
    const withGap = contract.notes.filter((note) => note.n !== 3);
    const running = newEvents(snapshot({ notes: notesFor(withGap) }), 0);
    expect(running.events.map((event) => event.seq)).toEqual([1, 2]);
    expect(running.lastSeq).toBe(2);
  });

  test("ignores notes that are not ClientOps events and reports broken ones", () => {
    const notes: ExecutionNote[] = [
      ...notesFor(contract.notes.slice(0, 2)),
      { message: "free text from a person", tags: ["debug"], timestamp: startSeconds },
      { message: "{not json", tags: [eventNoteTag], timestamp: startSeconds },
      { message: JSON.stringify({ n: 3, event: { type: "step.decided", stepId: "open-computer", rung: "guess", tookMs: 1 } }), tags: [eventNoteTag], timestamp: startSeconds },
    ];
    const result = newEvents(snapshot({ notes }), 0);
    expect(result.events.map((event) => event.seq)).toEqual([1, 2]);
    expect(result.rejected).toHaveLength(2);
    expect(result.lastSeq).toBe(3);
  });

  test("adds a stop or failure when the execution ended without saying why", () => {
    const partial = notesFor(contract.notes.slice(0, 4));
    const cancelled = newEvents(snapshot({ status: "cancelled", notes: partial, completedAt: "2026-11-14T06:00:09.000Z" }), 4);
    expect(cancelled.events).toHaveLength(1);
    expect(cancelled.events[0]).toMatchObject({ seq: 5, type: "job.failed", reason: "stopped", at: "2026-11-14T06:00:09.000Z" });
    expect(cancelled.finished).toBe(true);

    const failed = newEvents(snapshot({ status: "failed", notes: partial }), 4);
    expect(failed.events[0]).toMatchObject({ type: "job.failed", reason: "error" });
    expect(buildJobState([...newEvents(snapshot({ notes: partial }), 0).events, ...failed.events]).status).toBe("failed");
  });

  test("does not add a failure when the agent already reported one", () => {
    const reported = [
      ...contract.notes.slice(0, 2),
      { n: 3, event: { type: "job.failed", reason: "error", message: "The worker could not find this step on the page.", stepId: "open-computer" } },
    ];
    const result = newEvents(snapshot({ status: "succeeded", notes: notesFor(reported) }), 0);
    expect(result.events.map((event) => event.type)).toEqual(["job.created", "step.started", "job.failed"]);
    expect(result.finished).toBe(true);
  });

  test("accepts note times written as ISO text", () => {
    const notes = [{ message: JSON.stringify(contract.notes[0]), tags: [eventNoteTag], timestamp: "2026-11-14T06:00:00Z" }];
    expect(newEvents(snapshot({ notes }), 0).events[0].at).toBe("2026-11-14T06:00:00.000Z");
  });
});

describe("newEvents with what AgentField v0.1.145 really stores", () => {
  test("reads nanosecond times and notes stored out of order", async () => {
    const realQuery = (await import("./fixtures/agentfield-query-executions.json")).default;
    const stored = realQuery.data.results[0];
    const result = newEvents(
      { executionId: stored.execution_id, clientId: "kumo", status: "succeeded", notes: stored.notes, expectedEvents: 10 },
      0,
    );
    expect(result.rejected).toEqual([]);
    expect(result.events.map((event) => event.seq)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(result.events[1].at).toBe("2026-11-13T16:47:57.253Z");
    expect(result.finished).toBe(true);
    expect(buildJobState(result.events).status).toBe("done");
  });

  test("rejects a note whose time cannot be read instead of failing", () => {
    const notes = [{ message: JSON.stringify(contract.notes[0]), tags: [eventNoteTag], timestamp: "yesterday" }];
    const result = newEvents(snapshot({ notes }), 0);
    expect(result.events).toEqual([]);
    expect(result.rejected).toHaveLength(1);
  });
});

describe("newEvents when notes arrive after the job has ended", () => {
  test("keeps waiting while the agent's count says notes are still on their way", () => {
    const early = newEvents(snapshot({ status: "succeeded", notes: notesFor(contract.notes.slice(0, 7)), expectedEvents: 10 }), 0);
    expect(early.events.map((event) => event.seq)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(early.finished).toBe(false);

    const gap = contract.notes.filter((note) => note.n !== 8);
    const waiting = newEvents(snapshot({ status: "succeeded", notes: notesFor(gap), expectedEvents: 10 }), 7);
    expect(waiting.events).toEqual([]);
    expect(waiting.finished).toBe(false);

    const complete = newEvents(snapshot({ status: "succeeded", notes: notesFor(contract.notes), expectedEvents: 10 }), 7);
    expect(complete.events.map((event) => event.seq)).toEqual([8, 9, 10]);
    expect(complete.finished).toBe(true);
  });

  test("gives what it has and finishes when told the wait is over", () => {
    const gap = contract.notes.filter((note) => note.n !== 8);
    const flushed = newEvents(snapshot({ status: "succeeded", notes: notesFor(gap), expectedEvents: 10 }), 7, { flush: true });
    expect(flushed.events.map((event) => event.seq)).toEqual([9, 10]);
    expect(flushed.finished).toBe(true);
  });
});
