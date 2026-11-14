import { describe, expect, test } from "bun:test";
import learned from "../../agent/tests/contract/learned_job_notes.json";
import renamed from "../../agent/tests/contract/replay_renamed_button_job_notes.json";
import replayed from "../../agent/tests/contract/replay_job_notes.json";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import { demoJobs, kumoReplayChanges } from "./demoJobs";
import { buildJobState } from "./jobState";
import { workerSays } from "./jobView";

type Contract = { jobId: string; input: { client_id: string }; notes: unknown[] };

function read(contract: Contract, status: ExecutionStatus, upTo = contract.notes.length) {
  const notes = contract.notes.slice(0, upTo);
  const result = newEvents(
    {
      executionId: contract.jobId,
      clientId: contract.input.client_id,
      status,
      expectedEvents: notes.length,
      notes: notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index * 0.25 })),
    },
    0,
  );
  return { ...result, state: buildJobState(result.events) };
}

const untilApproval = (contract: Contract) =>
  contract.notes.findIndex((note) => (note as { event: { type: string } }).event.type === "approval.requested") + 1;

describe("the agent's notes for learning and replaying", () => {
  test("are all valid events for the screen", () => {
    for (const [contract, status] of [
      [learned, "succeeded"],
      [replayed, "succeeded"],
      [renamed, "succeeded"],
    ] as const) {
      const { rejected, events } = read(contract, status);
      expect(rejected).toEqual([]);
      expect(events).toHaveLength(contract.notes.length);
    }
  });

  test("a first run that matched every row ends with the steps saved as version 1", () => {
    const { state } = read(learned, "succeeded");

    expect(state.status).toBe("done");
    expect(state.run).toEqual({ kind: "learn" });
    expect(state.procedure).toMatchObject({ procedureId: "kumo-stock-sync", version: 1, stepCount: 6 });
    expect(state.procedure?.expectedReplayMs).toBeGreaterThan(0);
  });

  test("a replay is decided by the saved script at every step, with no AI, and waits for the OK with day two's four changes", () => {
    const { state } = read(replayed, "running", untilApproval(replayed));

    expect(state.run).toEqual({ kind: "replay", procedureVersion: 1 });
    expect(state.status).toBe("waiting");
    expect(state.claudeDecisions).toBe(0);
    expect(state.steps.slice(0, 4).map((step) => step.decidedBy)).toEqual(["script", "script", "script", "script"]);
    expect(state.pendingApproval?.changes).toEqual(kumoReplayChanges);
    expect(state.readyInMs).not.toBeNull();
    expect(workerSays(state)).toMatch(/^4 products changed at the supplier\. Ready in .+, with no AI\.$/);
  });

  test("after the OK the replay saves four and reads all four back, like the demo narration", () => {
    const { state } = read(replayed, "succeeded");
    const demo = buildJobState(demoJobs.replay.events);

    expect(state.status).toBe("done");
    expect(state.result).toEqual({ kind: "done", written: 4, checked: 4, matched: 4 });
    expect(state.steps.map((step) => step.decidedBy)).toEqual(["script", "script", "script", "script", "human", "script"]);
    expect(state.steps.map((step) => [step.summary ?? null, step.detail ?? null])).toEqual(demo.steps.map((step) => [step.summary ?? null, step.detail ?? null]));
    expect(state.procedure).toBeNull();
    expect(workerSays(state)).toBe("Done. 4 products updated, and each one checked.");
  });

  test("a renamed save button stops the replay at that step before anything is asked or saved", () => {
    const { state } = read(renamed, "succeeded");

    expect(state.status).toBe("failed");
    expect(state.result).toMatchObject({ kind: "failed", reason: "error", stepId: "check-save" });
    expect(state.steps.find((step) => step.stepId === "check-save")?.status).toBe("failed");
    expect(state.pendingApproval).toBeNull();
    expect(state.written).toBeNull();
  });
});
