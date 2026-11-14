import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/boundary_blocked_notes.json";
import { eventNoteTag, newEvents, type ExecutionSnapshot } from "./agentfieldRecords";
import { buildJobState } from "./jobState";

const startSeconds = Date.UTC(2026, 10, 14, 8, 45, 0) / 1000;

function snapshot(): ExecutionSnapshot {
  return {
    executionId: "exec_blocked",
    clientId: contract.input.client_id,
    status: "succeeded",
    expectedEvents: contract.notes.length,
    notes: contract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: startSeconds + index * 0.4 })),
  };
}

describe("the agent's refusal of another client's login", () => {
  test("is read by the screen server as valid events that end the job as blocked", () => {
    const result = newEvents(snapshot(), 0);

    expect(result.rejected).toEqual([]);
    expect(result.events.map((event) => event.type)).toEqual(["job.created", "boundary.blocked"]);
    expect(result.finished).toBe(true);

    const state = buildJobState(result.events);
    expect(state.status).toBe("blocked");
    expect(state.result).toMatchObject({
      kind: "blocked",
      resource: "Maruyama Supply login",
      ownerClientId: "kumo",
      ownerClientName: "Kumo Coffee Roasters",
      auditId: contract.auditLine.auditId,
      offersOwnAccount: true,
    });
  });

  test("names the same audit line the agent wrote", () => {
    const blocked = contract.notes[1].event;
    expect(blocked).toMatchObject({ auditId: contract.auditLine.auditId, ownerClientId: contract.auditLine.ownerClientId, resource: contract.auditLine.resource });
    expect(contract.auditLine.clientId).toBe(contract.input.client_id);
  });
});
