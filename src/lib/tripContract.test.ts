import { describe, expect, test } from "bun:test";
import trips from "../../agent/tests/contract/trip_job_notes.json";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import { handleApprove } from "./approvalRoutes";
import { approvalSignature } from "./approvalSignature";
import { buildJobState } from "./jobState";
import { workerSays } from "./jobView";
import type { RequestLike } from "./setupRoutes";

type Contract = { jobId: string; input: { client_id: string }; notes: { n: number; event: { type: string } }[] };

const search: Contract = trips;
const booked: Contract = trips.book;
const stopped: Contract = trips.stopped;

function read(contract: Contract, status: ExecutionStatus, count = contract.notes.length) {
  const result = newEvents(
    {
      executionId: contract.jobId,
      clientId: contract.input.client_id,
      status,
      expectedEvents: count,
      notes: contract.notes.slice(0, count).map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index * 0.5 })),
    },
    0,
  );
  return { ...result, state: buildJobState(result.events) };
}

const askedAt = booked.notes.findIndex((note) => note.event.type === "approval.requested") + 1;

describe("the agent's trip notes", () => {
  test("are all valid events for the screen", () => {
    for (const [contract, status] of [
      [search, "succeeded"],
      [booked, "succeeded"],
      [stopped, "cancelled"],
    ] as const) {
      const { rejected, events } = read(contract, status);
      expect(rejected).toEqual([]);
      expect(events).toHaveLength(contract.notes.length);
    }
  });

  test("a search ends done with the rides, their seats and one suggested seat each", () => {
    const { state } = read(search, "succeeded");

    expect(state.status).toBe("done");
    expect(state.title).toBe("Trip search");
    expect(state.tripOptions?.to).toBe("Singapore");
    expect(state.tripOptions?.options.map((option) => [option.id, option.kind, option.suggestion?.seat ?? null])).toEqual([
      ["ka-631", "flight", "12C"],
      ["mr-12", "flight", "1B"],
    ]);
    expect(state.tripOptions?.options[0].seats).toHaveLength(180);
    expect(state.pendingApproval).toBeNull();
  });

  test("a booking waits for the person with the held seat and no stock changes", () => {
    const { state } = read(booked, "waiting", askedAt);

    expect(state.status).toBe("waiting");
    expect(state.tripReview).toMatchObject({ option: "ka-631", seat: "12C", name: "Kestrel Air KA 631", price: 72800, hold: "h-1" });
    expect(state.pendingApproval).toMatchObject({ approvalId: "apr-exec_trip_book", stepId: "ask", undoable: false, booking: { option: "ka-631", seat: "12C" }, changes: [] });
    expect(state.tripBooked).toBeNull();
    expect(workerSays(state)).toBe("Seat 12C is held. Nothing is booked until you confirm.");
  });

  test("an approved booking ends done with the reference", () => {
    const { state } = read(booked, "succeeded");

    expect(state.status).toBe("done");
    expect(state.pendingApproval).toBeNull();
    expect(state.lastAnswer).toMatchObject({ decision: "approved", by: "You" });
    expect(state.tripBooked).toEqual({ option: "ka-631", seat: "12C", reference: "KQZTRA" });
  });

  test("a stop while waiting ends stopped with nothing booked", () => {
    const { state } = read(stopped, "cancelled");

    expect(state.status).toBe("stopped");
    expect(state.tripBooked).toBeNull();
    expect(state.tripReview?.seat).toBe("12C");
  });

  test("the approval route finds the booking's pending approval and sends it on", async () => {
    const secret = "test-approval-secret-7f3a";
    const sent: { raw: string; signature: string }[] = [];
    const headers = new Headers({ "content-type": "application/json", "sec-fetch-site": "same-origin" });
    const body = JSON.stringify({ jobId: booked.jobId, approvalId: "apr-exec_trip_book" });
    const request: RequestLike = { headers: { get: (name) => headers.get(name) }, text: async () => body };
    const { events } = read(booked, "waiting", askedAt);

    const response = await handleApprove(request, {
      secret,
      readJob: async (id) => (id === booked.jobId ? { status: "waiting", events } : null),
      sendApproval: async (raw, signature) => {
        sent.push({ raw, signature });
        return { status: 200, body: { status: "processed" } };
      },
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ jobId: booked.jobId, approvalId: "apr-exec_trip_book", approved: true });
    expect(sent).toHaveLength(1);
    expect(sent[0].signature).toBe(approvalSignature(secret, sent[0].raw));
  });
});
