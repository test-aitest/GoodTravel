import { describe, expect, test } from "bun:test";
import { AgentFieldError } from "./agentfieldClient";
import type { ClientMemoryAccess } from "./chatStore";
import { findClient } from "./clients";
import type { RequestLike } from "./setupRoutes";
import { seedTrips } from "./tripKnowledge";
import { createKnowledgeStore, handleReadKnowledge, handleRememberTrip, knowledgeMemoryKey } from "./tripKnowledgeRoutes";

function memory() {
  const values = new Map<string, unknown>();
  return {
    values,
    get: async (clientId: string, key: string) => values.get(`${clientId}/${key}`) ?? null,
    set: async (clientId: string, key: string, data: unknown) => {
      values.set(`${clientId}/${key}`, data);
    },
  };
}

function deps(kept: ClientMemoryAccess = memory()) {
  return { findClient, store: createKnowledgeStore(kept) };
}

function post(body: unknown, headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers(headers);
  return { headers: { get: (name) => sent.get(name) }, text: async () => JSON.stringify(body) };
}

async function answer(response: Response) {
  return { status: response.status, cache: response.headers.get("cache-control"), body: (await response.json()) as Record<string, unknown> };
}

const trip = { traveller: "sales-lead", date: "2026-11-21", said: "Singapore next Saturday, for the trade show", destination: "singapore", ride: "flight", seat: "aisle", exitRow: true };

describe("reading what a client's trips taught", () => {
  test("Hinata starts from the seed with no learned trips", async () => {
    const reply = await answer(await handleReadKnowledge("hinata", deps()));
    expect(reply.status).toBe(200);
    expect(reply.cache).toBe("no-store");
    expect(reply.body.trips).toEqual([]);
    expect(reply.body.graph).toMatchObject({ trips: seedTrips.length });
  });

  test("an unknown client is refused", async () => {
    expect((await answer(await handleReadKnowledge("nobody", deps()))).status).toBe(404);
  });

  test("an unreachable memory says the service is unavailable", async () => {
    const broken = { get: async () => Promise.reject(new AgentFieldError("down")), set: async () => {} };
    expect((await answer(await handleReadKnowledge("hinata", deps(broken)))).status).toBe(502);
  });
});

describe("remembering a booked trip", () => {
  test("keeps only the cue words of what was said, and the next read learns from it", async () => {
    const kept = memory();
    const saved = await answer(await handleRememberTrip(post({ trip }), "hinata", deps(kept)));

    expect(saved.status).toBe(201);
    expect(saved.body).toMatchObject({ clientId: "hinata", saved: true, graph: { trips: seedTrips.length + 1 } });
    expect(kept.values.get(`hinata/${knowledgeMemoryKey}`)).toEqual({ version: 1, trips: [{ ...trip, said: "business trade-show" }] });

    const read = await answer(await handleReadKnowledge("hinata", deps(kept)));
    expect(read.body.trips).toEqual([{ ...trip, said: "business trade-show" }]);
  });

  test("only the ClientOps page can teach it", async () => {
    expect((await answer(await handleRememberTrip(post({ trip }, { "sec-fetch-site": "cross-site" }), "hinata", deps()))).status).toBe(403);
  });

  test("refuses unknown clients and anything that is not a strict trip", async () => {
    expect((await answer(await handleRememberTrip(post({ trip }), "nobody", deps()))).status).toBe(404);
    const bad = [
      { trip: { ...trip, traveller: "Aiko Tanaka" } },
      { trip: { ...trip, said: "book for aiko@example.com" } },
      { trip: { ...trip, said: "call me on 090 1234 5678" } },
      { trip: { ...trip, destination: "paris" } },
      { trip: { ...trip, date: "21/11/2026" } },
      { trip: { ...trip, extra: "x" } },
      { trip },
    ];
    for (const body of bad.slice(0, -1)) {
      expect((await answer(await handleRememberTrip(post(body), "hinata", deps()))).status).toBe(400);
    }
    expect((await answer(await handleRememberTrip(post({ ...bad.at(-1), name: "x" }), "hinata", deps()))).status).toBe(400);
  });

  test("keeps at most the last 200 trips", async () => {
    const kept = memory();
    kept.values.set(`hinata/${knowledgeMemoryKey}`, { version: 1, trips: Array.from({ length: 200 }, () => ({ ...trip, said: "business" })) });
    await handleRememberTrip(post({ trip }), "hinata", deps(kept));
    const stored = kept.values.get(`hinata/${knowledgeMemoryKey}`) as { trips: { said: string }[] };
    expect(stored.trips.length).toBe(200);
    expect(stored.trips.at(-1)?.said).toBe("business trade-show");
  });
});
