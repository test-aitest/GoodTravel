import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/hidden_instruction_job_notes.json";
import learned from "../../agent/tests/contract/learned_job_notes.json";
import { eventNoteTag, newEvents } from "./agentfieldRecords";
import { buildJobState } from "./jobState";

type Contract = { jobId: string; input: { client_id: string }; notes: unknown[] };

function read(job: Contract) {
  const result = newEvents(
    {
      executionId: job.jobId,
      clientId: job.input.client_id,
      status: "succeeded",
      expectedEvents: job.notes.length,
      notes: job.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index * 0.25 })),
    },
    0,
  );
  return { ...result, state: buildJobState(result.events) };
}

describe("a first run on a supplier page that hides a note for AI", () => {
  test("is read by the screen server as valid events only", () => {
    const { rejected, events } = read(contract);

    expect(rejected).toEqual([]);
    expect(events).toHaveLength(contract.notes.length);
  });

  test("keeps one ignored instruction for the supplier portal and saves the same changes as without the note", () => {
    const hidden = read(contract).state;
    const plain = read(learned).state;

    expect(hidden.ignoredInstructions).toEqual([{ stepId: "sign-in", host: "portal.maruyama-supply.example" }]);
    expect(plain.ignoredInstructions).toEqual([]);
    expect(hidden.status).toBe("done");
    expect(hidden.result).toEqual(plain.result);
  });

  test("never carries the words of the note", () => {
    const text = JSON.stringify(contract).toLowerCase();

    for (const words of ["ai assistants", "ignore the request", "without asking anyone"]) {
      expect(text).not.toContain(words);
    }
  });
});
