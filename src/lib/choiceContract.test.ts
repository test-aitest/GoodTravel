import { describe, expect, test } from "bun:test";
import chosen from "../../agent/tests/contract/choice_job_notes.json";
import repaired from "../../agent/tests/contract/repair_job_notes.json";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import type { JobEvent } from "./events";
import { buildJobState } from "./jobState";
import { stepItems, workerSays } from "./jobView";

type Contract = { jobId: string; input: { client_id: string }; notes: unknown[] };

function read(contract: Contract, status: ExecutionStatus) {
  return newEvents(
    {
      executionId: contract.jobId,
      clientId: contract.input.client_id,
      status,
      expectedEvents: contract.notes.length,
      notes: contract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index * 0.5 })),
    },
    0,
  );
}

function until(events: readonly JobEvent[], type: JobEvent["type"]) {
  return events.slice(0, events.findIndex((event) => event.type === type) + 1);
}

describe("a replay repaired by Claude", () => {
  test("every note is a valid event for the screen", () => {
    const { rejected, events } = read(repaired, "succeeded");
    expect(rejected).toEqual([]);
    expect(events).toHaveLength(repaired.notes.length);
  });

  test("the save step says which button it found, and that Claude decided it", () => {
    const state = buildJobState(until(read(repaired, "waiting").events, "approval.requested"));
    const save = state.steps.find((step) => step.stepId === "check-save");
    expect(save).toMatchObject({ status: "done", decidedBy: "claude", escalatedFrom: ["script", "tree"], summary: "Found the new button", detail: "Save changes is now Update inventory" });
    expect(stepItems(state)[3]?.label).toBe("Found the new button, Save changes is now Update inventory");
    expect(state.pendingApproval?.procedureChange).toEqual({ stepId: "check-save", fromVersion: 1, toVersion: 2, before: "Save changes", after: "Update inventory" });
  });
});

describe("a replay that asks you which button to use", () => {
  test("every note is a valid event for the screen", () => {
    const { rejected, events } = read(chosen, "succeeded");
    expect(rejected).toEqual([]);
    expect(events).toHaveLength(chosen.notes.length);
  });

  test("while it asks, the job waits on you with the question and the buttons to pick from", () => {
    const state = buildJobState(until(read(chosen, "waiting").events, "choice.requested"));
    expect(state.status).toBe("waiting");
    expect(state.pendingApproval).toBeNull();
    expect(state.pendingChoice).toEqual({
      choiceId: "cho-exec_choice-check-save",
      stepId: "check-save",
      question: "Which button saves the changes now?",
      before: "Save changes",
      candidates: [
        { ref: "1016", role: "button", name: "Update inventory" },
        { ref: "1100", role: "button", name: "Save changes to all locations" },
      ],
    });
    expect(workerSays(state)).toBe("The page changed today, and more than one button could be the one I used before. Which one should I use?");
    expect(stepItems(state)[3]).toMatchObject({ state: "now", detail: expect.stringContaining("You decide") });
  });

  test("with only one button to offer, the worker asks whether that one is right", () => {
    const state = buildJobState(until(read(chosen, "waiting").events, "choice.requested"));
    const one = { ...state, pendingChoice: state.pendingChoice && { ...state.pendingChoice, candidates: state.pendingChoice.candidates.slice(0, 1) } };
    expect(workerSays(one)).toBe("The page changed today, and I'm not sure this button is the one I used before. Should I use it?");
  });

  test("after you pick, the step is decided by you and the job goes on to ask before saving", () => {
    const state = buildJobState(until(read(chosen, "waiting").events, "approval.requested"));
    expect(state.pendingChoice).toBeNull();
    expect(state.lastChoice).toEqual({ choiceId: "cho-exec_choice-check-save", ref: "1016", by: "You" });
    expect(state.steps.find((step) => step.stepId === "check-save")).toMatchObject({ decidedBy: "human", escalatedFrom: ["script", "tree", "claude"] });
    expect(state.pendingApproval?.question).toBe("Save 4 stock changes with the new button?");
  });

  test("the finished job saved version 2 of the steps", () => {
    const state = buildJobState(read(chosen, "succeeded").events);
    expect(state.status).toBe("done");
    expect(state.procedure?.version).toBe(2);
  });
});

describe("time to get the changes ready", () => {
  test("does not count the time the job waited for your pick", () => {
    const events = until(read(chosen, "waiting").events, "approval.requested");
    const asked = events.find((event) => event.type === "choice.requested");
    const answered = events.find((event) => event.type === "choice.answered");
    const state = buildJobState(events);
    const waited = Date.parse(answered?.at ?? "") - Date.parse(asked?.at ?? "");
    expect(waited).toBeGreaterThan(0);
    expect(state.readyInMs).toBe(state.elapsedMs - waited);
  });
});
