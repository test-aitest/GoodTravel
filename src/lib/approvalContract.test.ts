import { describe, expect, test } from "bun:test";
import approved from "../../agent/tests/contract/approved_job_notes.json";
import decoy from "../../agent/tests/contract/decoy_job_notes.json";
import stopped from "../../agent/tests/contract/stopped_job_notes.json";
import undone from "../../agent/tests/contract/undo_job_notes.json";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import { demoJobs } from "./demoJobs";
import { buildJobState } from "./jobState";
import { stepItems, workerSays } from "./jobView";

type Contract = { jobId: string; input: { client_id: string }; notes: unknown[] };

function read(contract: Contract, status: ExecutionStatus) {
  const result = newEvents(
    {
      executionId: contract.jobId,
      clientId: contract.input.client_id,
      status,
      expectedEvents: contract.notes.length,
      notes: contract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index * 0.5 })),
    },
    0,
  );
  return { ...result, state: buildJobState(result.events) };
}

describe("the agent's notes after the approval", () => {
  test("are all valid events for the screen", () => {
    for (const [contract, status] of [
      [approved, "succeeded"],
      [decoy, "succeeded"],
      [stopped, "cancelled"],
      [undone, "succeeded"],
    ] as const) {
      const { rejected, events } = read(contract, status);
      expect(rejected).toEqual([]);
      expect(events).toHaveLength(contract.notes.length);
    }
  });

  test("an approved sync ends done with 12 written and 12 of 12 matching", () => {
    const { state } = read(approved, "succeeded");

    expect(state.status).toBe("done");
    expect(state.result).toEqual({ kind: "done", written: 12, checked: 12, matched: 12 });
    expect(state.steps.map((step) => step.status)).toEqual(["done", "done", "done", "done", "done", "done"]);
    expect(state.lastAnswer).toMatchObject({ decision: "approved", by: "You" });
    expect(state.steps.find((step) => step.stepId === "ask")?.decidedBy).toBe("human");
    expect(state.steps.at(-1)).toMatchObject({ summary: "Saved and checked every row", detail: "12 products" });
    expect(state.page?.host).toBe("admin.kumo-coffee.example");
    expect(workerSays(state)).toBe("Done. 12 products updated, and each one checked.");
  });

  test("tells the same story after the approval as the demo narration", () => {
    const { state } = read(approved, "succeeded");
    const demo = buildJobState(demoJobs.firstRun.events.filter((event) => event.type !== "procedure.saved"));

    expect(state.steps.map((step) => [step.summary ?? null, step.detail ?? null])).toEqual(demo.steps.map((step) => [step.summary ?? null, step.detail ?? null]));
    expect([state.written, state.checked, state.matched]).toEqual([demo.written, demo.checked, demo.matched]);
  });

  test("the decoy save button ends as a verify mismatch with the reason, and no steps are kept", () => {
    const { state, events } = read(decoy, "succeeded");

    expect(state.status).toBe("failed");
    expect(state.result).toMatchObject({ kind: "failed", reason: "verify-mismatch", stepId: "save" });
    expect(state.result?.kind === "failed" ? state.result.message : "").toContain("don't match after saving");
    expect(state.checked).toBe(12);
    expect(state.matched).toBeLessThan(12);
    expect(state.procedure).toBeNull();
    expect(events.some((event) => event.type === "procedure.saved")).toBe(false);
  });

  test("a stop while waiting ends as stopped before saving", () => {
    const { state } = read(stopped, "cancelled");

    expect(state.status).toBe("stopped");
    expect(state.result).toEqual({ kind: "stopped", reason: "stopped" });
    expect(state.written).toBeNull();
    expect(workerSays(state)).toBe("Stopped before saving.");
  });

  test("an undo is its own job that puts back 12 products and checks them", () => {
    const { state } = read(undone, "succeeded");

    expect(state.run).toEqual({ kind: "undo" });
    expect(state.title).toBe("Undo stock sync");
    expect(state.status).toBe("done");
    expect(state.result).toEqual({ kind: "done", written: 12, checked: 12, matched: 12 });
    expect(stepItems(state).map((item) => item.label)).toEqual(["Signed in to the store admin", "Put back and checked every row, 12 products"]);
  });

  test("an undo in progress says what it is doing", () => {
    const { events } = read(undone, "succeeded");
    const running = buildJobState(events.slice(0, 5));

    expect(workerSays(running)).toBe("Putting back the stock from before the sync. I'll check every row after.");
    expect(stepItems(running)[1]).toMatchObject({ label: "Putting back 12 products", state: "now" });
  });
});
