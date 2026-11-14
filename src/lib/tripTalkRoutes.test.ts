import { describe, expect, test } from "bun:test";
import { AgentFieldError } from "./agentfieldClient";
import type { RequestLike } from "./setupRoutes";
import { talkAboutTrip } from "./tripTalkClient";
import { handleTripTalk, type TripTalk, type TripTalkContext, type TripTalkDeps } from "./tripTalkRoutes";

function post(body?: unknown, headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers({ "content-type": "application/json", origin: "http://app.test", host: "app.test", ...headers });
  const text = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
  return { headers: { get: (name) => sent.get(name) }, text: async () => text };
}

const context: TripTalkContext = {
  month: "november",
  scene: "ride",
  destination: "kyoto",
  ranked: [{ destination: "kyoto", score: 0.92, reasons: ["Autumn leaves peak in November"] }],
  options: [
    { id: "nozomi-7", name: "Nozomi 7", kind: "rail", depart: "07:00", arrive: "09:15", price: 14170, currency: "JPY" },
    { id: "nozomi-21", name: "Nozomi 21", kind: "rail", depart: "10:30", arrive: "12:45", price: 14170, currency: "JPY" },
  ],
  suggestion: { option: "nozomi-7", seat: "12E", reason: "window" },
  seatPreference: { position: "window", exitRow: false },
  chosen: null,
  history: [{ from: "worker", text: "Where would you like to go, and when?" }],
};

const decision = {
  reply: "The Nozomi 7 gets you there before lunch. Shall I take it?",
  action: "show-rides",
  destination: "kyoto",
  option: "nozomi-7",
  seat: null,
  speaker: "worker",
  decidedBy: "claude",
  claudeCalls: 1,
};

function deps(respond: (talk: TripTalk) => Promise<unknown> = async () => decision) {
  const talks: TripTalk[] = [];
  const built: TripTalkDeps = {
    findClient: (clientId) => (clientId === "hinata" ? { id: "hinata", name: "Hinata Skincare" } : null),
    talk: async (talk) => {
      talks.push(talk);
      return respond(talk);
    },
  };
  return { deps: built, talks };
}

async function answer(response: Response) {
  return { status: response.status, body: (await response.json()) as Record<string, unknown> };
}

describe("handleTripTalk", () => {
  test("runs the talk for a known client and answers with the decision", async () => {
    const { deps: built, talks } = deps();

    const reply = await answer(await handleTripTalk(post({ clientId: " hinata ", said: "which train?", context }), built));

    expect(reply).toEqual({ status: 200, body: decision });
    expect(talks).toEqual([{ clientId: "hinata", said: "which train?", context }]);
  });

  test("refuses another site", async () => {
    const { deps: built, talks } = deps();

    const reply = await answer(await handleTripTalk(post({ clientId: "hinata", said: "hi", context }, { "sec-fetch-site": "cross-site" }), built));

    expect(reply.status).toBe(403);
    expect(talks).toEqual([]);
  });

  test("refuses an unknown client", async () => {
    const { deps: built, talks } = deps();

    expect((await handleTripTalk(post({ clientId: "nobody", said: "hi", context }), built)).status).toBe(404);
    expect(talks).toEqual([]);
  });

  const personalOrBroken: [string, unknown][] = [
    ["an email in what was said", { clientId: "hinata", said: "mail aiko@example.com", context }],
    ["a password in the history", { clientId: "hinata", said: "hi", context: { ...context, history: [{ from: "you", text: "my password is x" }] } }],
    ["an email in a reason", { clientId: "hinata", said: "hi", context: { ...context, ranked: [{ destination: "kyoto", score: 1, reasons: ["aiko@example.com"] }] } }],
    ["an extra field", { clientId: "hinata", said: "hi", context: { ...context, traveller: "Aiko" } }],
    ["an unknown destination", { clientId: "hinata", said: "hi", context: { ...context, destination: "paris" } }],
    ["a suggestion for a ride not listed", { clientId: "hinata", said: "hi", context: { ...context, suggestion: { option: "ghost-1", seat: "12E", reason: "window" } } }],
    ["a bad seat", { clientId: "hinata", said: "hi", context: { ...context, chosen: { option: "nozomi-7", seat: "99Z" } } }],
    ["too much history", { clientId: "hinata", said: "hi", context: { ...context, history: Array.from({ length: 7 }, () => ({ from: "you", text: "hi" })) } }],
    ["a missing context", { clientId: "hinata", said: "hi" }],
    ["text that is not JSON", "not json"],
  ];

  for (const [name, body] of personalOrBroken) {
    test(`refuses ${name} before anything runs`, async () => {
      const { deps: built, talks } = deps();

      const reply = await answer(await handleTripTalk(post(body), built));

      expect(reply).toEqual({ status: 400, body: { error: "Say which client, what you said, and where the trip stands." } });
      expect(talks).toEqual([]);
    });
  }

  test("answers 502 when AgentField is not there", async () => {
    const { deps: built } = deps(async () => {
      throw new AgentFieldError("down");
    });

    expect((await handleTripTalk(post({ clientId: "hinata", said: "hi", context }), built)).status).toBe(502);
  });

  test("refuses a decision outside the contract or naming a ride that is not listed", async () => {
    for (const broken of [{ ...decision, action: "fly-now" }, { ...decision, option: "ghost-9" }, { ...decision, extra: 1 }, { ...decision, speaker: "london" }, null]) {
      const { deps: built } = deps(async () => broken);

      expect((await handleTripTalk(post({ clientId: "hinata", said: "hi", context }), built)).status).toBe(502);
    }
  });
});

describe("talkAboutTrip", () => {
  test("posts to the talk route and returns the decision", async () => {
    const sent: { url: string; body: unknown }[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      sent.push({ url, body: JSON.parse(String(init.body)) });
      return Response.json(decision);
    }) as unknown as typeof fetch;

    expect(await talkAboutTrip("hinata", "which train?", context, fetcher)).toEqual(decision as never);
    expect(sent).toEqual([{ url: "/api/trips/talk", body: { clientId: "hinata", said: "which train?", context } }]);
  });

  test("passes on the route's error or says the service is down", async () => {
    const refused = (async () => Response.json({ error: "There is no such client." }, { status: 404 })) as unknown as typeof fetch;
    const down = (async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;

    expect(await talkAboutTrip("x", "hi", context, refused)).toEqual({ error: "There is no such client." });
    expect(await talkAboutTrip("x", "hi", context, down)).toEqual({ error: "The worker service is not available right now." });
  });
});
