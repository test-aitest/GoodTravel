import { describe, expect, test } from "bun:test";
import { loadKnowledge, rememberTrip } from "./knowledgeClient";
import { knowledgeFor, seedTrips, type BookedTrip } from "./tripKnowledge";

type Sent = { url: string; method: string; contentType: string | null; body: unknown };

function browser(reply: { status: number; body: unknown } | Error) {
  const sent: Sent[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
    sent.push({
      url: String(input),
      method: init?.method ?? "GET",
      contentType: new Headers(init?.headers).get("content-type"),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (reply instanceof Error) throw reply;
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { sent, fetcher: fetcher as typeof fetch };
}

const trip: BookedTrip = { traveller: "sales-lead", date: "2026-11-21", said: "business trade-show", destination: "singapore", ride: "flight", seat: "aisle", exitRow: true };

describe("loadKnowledge", () => {
  test("builds the graph from the seed and the learned trips", async () => {
    const page = browser({ status: 200, body: { clientId: "hinata", trips: [trip], graph: { trips: seedTrips.length + 1 } } });
    const knowledge = await loadKnowledge("hinata", page.fetcher);

    expect(page.sent).toEqual([{ url: "/api/clients/hinata/knowledge", method: "GET", contentType: null, body: undefined }]);
    expect(knowledge).toEqual({ trips: [trip], graph: knowledgeFor("hinata", [trip]), live: true });
  });

  test("still ranks from the seed when the server cannot answer", async () => {
    expect(await loadKnowledge("hinata", browser(new TypeError("Failed to fetch")).fetcher)).toEqual({ trips: [], graph: knowledgeFor("hinata", []), live: false });
    expect(await loadKnowledge("hinata", browser({ status: 200, body: { trips: [{ traveller: "Aiko" }] } }).fetcher)).toMatchObject({ trips: [], live: false });
  });
});

describe("rememberTrip", () => {
  test("posts the booked trip", async () => {
    const page = browser({ status: 201, body: { clientId: "hinata", saved: true } });
    expect(await rememberTrip("hinata", trip, page.fetcher)).toEqual({ saved: true });
    expect(page.sent).toEqual([{ url: "/api/clients/hinata/knowledge", method: "POST", contentType: "application/json", body: { trip } }]);
  });

  test("passes the server's sentence through on failure", async () => {
    expect(await rememberTrip("hinata", trip, browser({ status: 400, body: { error: "Send one booked trip." } }).fetcher)).toEqual({ error: "Send one booked trip." });
    expect(await rememberTrip("hinata", trip, browser(new TypeError("Failed to fetch")).fetcher)).toEqual({ error: "The worker service is not available right now." });
  });
});
