import { describe, expect, test } from "bun:test";
import refound from "../../agent/tests/contract/refind_job_notes.json";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import { kumoReplayChanges } from "./demoJobs";
import { buildJobState } from "./jobState";
import { stepItems, workerSays } from "./jobView";

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

describe("the agent's notes when a replay finds one step again by name", () => {
  test("are all valid events for the screen", () => {
    const { rejected, events } = read(refound, "succeeded");

    expect(rejected).toEqual([]);
    expect(events).toHaveLength(refound.notes.length);
  });

  test("only the save button check climbs to the tree, with no AI, and the job waits for the OK with day two's four changes", () => {
    const { state } = read(refound, "running", untilApproval(refound));

    expect(state.run).toEqual({ kind: "replay", procedureVersion: 1 });
    expect(state.status).toBe("waiting");
    expect(state.claudeDecisions).toBe(0);
    expect(state.steps.slice(0, 4).map((step) => step.decidedBy)).toEqual(["script", "script", "script", "tree"]);
    expect(state.steps[3].escalatedFrom).toEqual(["script"]);
    expect(stepItems(state)[3].detail).toStartWith("Saved steps, found again by name");
    expect(state.pendingApproval?.changes).toEqual(kumoReplayChanges);
    expect(state.pendingApproval?.procedureChange).toBeUndefined();
    expect(workerSays(state)).toMatch(/^4 products changed at the supplier\. Ready in .+, with no AI\.$/);
  });

  test("after the OK it saves four, reads all four back and keeps the rebuilt steps as version 2", () => {
    const { state } = read(refound, "succeeded");

    expect(state.status).toBe("done");
    expect(state.result).toEqual({ kind: "done", written: 4, checked: 4, matched: 4 });
    expect(state.steps.map((step) => step.decidedBy)).toEqual(["script", "script", "script", "tree", "human", "script"]);
    expect(state.procedure).toMatchObject({ procedureId: "kumo-stock-sync", version: 2, stepCount: 6 });
    expect(workerSays(state)).toBe("Done. 4 products updated, and each one checked.");
  });
});
