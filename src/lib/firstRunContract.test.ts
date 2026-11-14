import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/first_run_notes.json";
import { eventNoteTag, newEvents, type ExecutionSnapshot } from "./agentfieldRecords";
import { demoJobs, kumoFirstRunChanges, kumoProductCount } from "./demoJobs";
import { buildJobState } from "./jobState";

const startSeconds = Date.UTC(2026, 10, 14, 8, 40, 0) / 1000;

function snapshot(): ExecutionSnapshot {
  return {
    executionId: contract.jobId,
    clientId: contract.input.client_id,
    status: "succeeded",
    expectedEvents: contract.notes.length,
    notes: contract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: startSeconds + index * 0.5 })),
  };
}

const demoFirstRun = demoJobs.firstRun.events;
const demoPlan = demoFirstRun[0].type === "job.created" ? demoFirstRun[0].plan : [];

describe("the agent's first run of the stock sync", () => {
  test("is read by the screen server as valid events only", () => {
    const result = newEvents(snapshot(), 0);

    expect(result.rejected).toEqual([]);
    expect(result.events).toHaveLength(contract.notes.length);
    expect(result.finished).toBe(true);
  });

  test("waits at the approval with the twelve changes of the demo, three of them sold out", () => {
    const state = buildJobState(newEvents(snapshot(), 0).events);

    expect(state.status).toBe("waiting");
    expect(state.pendingApproval?.changes).toEqual(kumoFirstRunChanges);
    expect(state.pendingApproval?.changes.filter((change) => change.supplier === 0)).toHaveLength(3);
    expect(state.pendingApproval?.question).toBe("Save 12 stock changes to the store admin?");
    expect(state.pendingApproval?.stepId).toBe("ask");
    expect(state.pendingApproval?.leftAlone).toBeUndefined();
    expect(state.result).toBeNull();
  });

  test("follows the demo's six-step plan with the first four steps decided by Claude", () => {
    const state = buildJobState(newEvents(snapshot(), 0).events);

    expect(state.steps.map((step) => ({ stepId: step.stepId, title: step.title }))).toEqual(demoPlan);
    expect(state.steps.map((step) => step.status)).toEqual(["done", "done", "done", "done", "active", "pending"]);
    expect(state.steps.slice(0, 4).map((step) => step.decidedBy)).toEqual(["claude", "claude", "claude", "claude"]);
    expect(state.claudeDecisions).toBe(4);
    expect(state.doneCount).toBe(4);
    expect(state.activeStepId).toBe("ask");
  });

  test("tells the same story as the demo narration", () => {
    const state = buildJobState(newEvents(snapshot(), 0).events);
    const demo = buildJobState(demoFirstRun.filter((event) => event.seq <= demoFirstRun.findIndex((e) => e.type === "approval.requested") + 1));

    expect(state.title).toBe(demo.title);
    expect(state.clientName).toBe(demo.clientName);
    expect(state.steps.map((step) => [step.summary ?? null, step.detail ?? null])).toEqual(
      demo.steps.map((step) => [step.summary ?? null, step.detail ?? null]),
    );
    expect(state.steps.find((step) => step.stepId === "read")?.detail).toBe(`${kumoProductCount} products`);
  });

  test("leaves the last page the worker saw on the store admin, under the client's own host", () => {
    const state = buildJobState(newEvents(snapshot(), 0).events);

    expect(state.page?.stepId).toBe("check-save");
    expect(state.page?.host).toBe("admin.kumo-coffee.example");
    expect(state.page?.html.startsWith("<!doctype html>")).toBe(true);
  });
});
