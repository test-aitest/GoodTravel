import { describe, expect, test } from "bun:test";
import { AgentFieldError } from "./agentfieldClient";
import type { RequestLike } from "./setupRoutes";
import { handleTripBooking, handleTripSearch, type TripDeps } from "./tripRoutes";

function post(body?: unknown, headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers({ "content-type": "application/json", origin: "http://app.test", host: "app.test", ...headers });
  const text = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
  return { headers: { get: (name) => sent.get(name) }, text: async () => text };
}

function deps(overrides: Partial<TripDeps> = {}) {
  const searches: unknown[] = [];
  const bookings: unknown[] = [];
  const built: TripDeps = {
    findClient: (clientId) => (clientId === "hinata" ? { id: "hinata", name: "Hinata Skincare" } : null),
    startSearch: async (search) => {
      searches.push(search);
      return { executionId: "exec_trip_search", runId: "run_1" };
    },
    startBooking: async (booking) => {
      bookings.push(booking);
      return { executionId: "exec_trip_book", runId: "run_2" };
    },
    ...overrides,
  };
  return { deps: built, searches, bookings };
}

async function answer(response: Response) {
  return { status: response.status, body: (await response.json()) as Record<string, unknown> };
}

describe("handleTripSearch", () => {
  test("starts the search for a known client and answers with the job id", async () => {
    const { deps: built, searches } = deps();

    const reply = await answer(await handleTripSearch(post({ clientId: "hinata", to: "Singapore", date: "2026-11-21" }), built));

    expect(reply).toEqual({ status: 201, body: { jobId: "exec_trip_search" } });
    expect(searches).toEqual([{ clientId: "hinata", to: "Singapore", date: "2026-11-21" }]);
  });

  test("trims the place before starting", async () => {
    const { deps: built, searches } = deps();
    await handleTripSearch(post({ clientId: "hinata", to: "  Kyoto ", date: "2026-11-21" }), built);
    expect(searches).toEqual([{ clientId: "hinata", to: "Kyoto", date: "2026-11-21" }]);
  });

  test("refuses a page from another site", async () => {
    const { deps: built, searches } = deps();
    const reply = await answer(await handleTripSearch(post({ clientId: "hinata", to: "Singapore", date: "2026-11-21" }, { "sec-fetch-site": "cross-site" }), built));
    expect(reply).toEqual({ status: 403, body: { error: "Only the ClientOps page can do this." } });
    expect(searches).toHaveLength(0);
  });

  test.each([
    ["{"],
    [{ clientId: "hinata", to: "Singapore" }],
    [{ clientId: "hinata", to: "singapore; rm", date: "2026-11-21" }],
    [{ clientId: "hinata", to: "Singapore", date: "2026-02-30" }],
    [{ clientId: "hinata", to: "Singapore", date: "next friday" }],
    [{ clientId: "hinata", to: "Singapore", date: "2026-11-21", request: "book everything" }],
  ])("answers 400 to a body that is not a client, a city and a day: %p", async (body) => {
    const { deps: built, searches } = deps();
    const reply = await answer(await handleTripSearch(post(body), built));
    expect(reply).toEqual({ status: 400, body: { error: "Say which client, where to, and which day." } });
    expect(searches).toHaveLength(0);
  });

  test("answers 404 for a client that is not set up", async () => {
    const { deps: built, searches } = deps();
    const reply = await answer(await handleTripSearch(post({ clientId: "nobody", to: "Singapore", date: "2026-11-21" }), built));
    expect(reply).toEqual({ status: 404, body: { error: "There is no such client." } });
    expect(searches).toHaveLength(0);
  });

  test("answers 502 when the worker service is down", async () => {
    const { deps: built } = deps({
      startSearch: async () => {
        throw new AgentFieldError("AgentField is not reachable");
      },
    });
    const reply = await answer(await handleTripSearch(post({ clientId: "hinata", to: "Singapore", date: "2026-11-21" }), built));
    expect(reply).toEqual({ status: 502, body: { error: "The worker service is not available right now." } });
  });
});

describe("handleTripBooking", () => {
  test("starts the booking for a known client and answers with the job id", async () => {
    const { deps: built, bookings } = deps();

    const reply = await answer(await handleTripBooking(post({ clientId: "hinata", option: "ka-631", seat: "12C" }), built));

    expect(reply).toEqual({ status: 201, body: { jobId: "exec_trip_book" } });
    expect(bookings).toEqual([{ clientId: "hinata", option: "ka-631", seat: "12C" }]);
  });

  test("refuses a page from another site", async () => {
    const { deps: built, bookings } = deps();
    const reply = await answer(await handleTripBooking(post({ clientId: "hinata", option: "ka-631", seat: "12C" }, { "sec-fetch-site": "cross-site" }), built));
    expect(reply.status).toBe(403);
    expect(bookings).toHaveLength(0);
  });

  test.each([
    ["{"],
    [{ clientId: "hinata", option: "ka-631" }],
    [{ clientId: "hinata", option: "KA 631", seat: "12C" }],
    [{ clientId: "hinata", option: "ka-631", seat: "12Z" }],
    [{ clientId: "hinata", option: "ka-631", seat: "121C" }],
    [{ clientId: "hinata", option: "ka-631", seat: "12C", name: "Haruka Mori" }],
  ])("answers 400 to a body that is not a client, a ride and a seat: %p", async (body) => {
    const { deps: built, bookings } = deps();
    const reply = await answer(await handleTripBooking(post(body), built));
    expect(reply).toEqual({ status: 400, body: { error: "Say which client, which ride, and which seat." } });
    expect(bookings).toHaveLength(0);
  });

  test("answers 404 for a client that is not set up", async () => {
    const { deps: built, bookings } = deps();
    const reply = await answer(await handleTripBooking(post({ clientId: "nobody", option: "ka-631", seat: "12C" }), built));
    expect(reply.status).toBe(404);
    expect(bookings).toHaveLength(0);
  });

  test("answers 502 when the worker service is down", async () => {
    const { deps: built } = deps({
      startBooking: async () => {
        throw new AgentFieldError("AgentField answered 503", 503);
      },
    });
    const reply = await answer(await handleTripBooking(post({ clientId: "hinata", option: "ka-631", seat: "12C" }), built));
    expect(reply).toEqual({ status: 502, body: { error: "The worker service is not available right now." } });
  });
});
