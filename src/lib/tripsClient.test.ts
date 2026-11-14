import { describe, expect, test } from "bun:test";
import { startTripBooking, startTripSearch } from "./tripsClient";

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

describe("startTripSearch", () => {
  test("posts the client, the city and the day and returns the job id", async () => {
    const page = browser({ status: 201, body: { jobId: "exec_trip_search" } });

    expect(await startTripSearch("hinata", "Singapore", "2026-11-21", page.fetcher)).toEqual({ jobId: "exec_trip_search" });
    expect(page.sent).toEqual([
      { url: "/api/trips/search", method: "POST", contentType: "application/json", body: { clientId: "hinata", to: "Singapore", date: "2026-11-21" } },
    ]);
  });

  test("passes the server's sentence through on failure", async () => {
    const page = browser({ status: 400, body: { error: "Say which client, where to, and which day." } });
    expect(await startTripSearch("hinata", "", "2026-11-21", page.fetcher)).toEqual({ error: "Say which client, where to, and which day." });
  });

  test("says the service is unavailable when nothing answers", async () => {
    const page = browser(new TypeError("Failed to fetch"));
    expect(await startTripSearch("hinata", "Kyoto", "2026-11-21", page.fetcher)).toEqual({ error: "The worker service is not available right now." });
  });
});

describe("startTripBooking", () => {
  test("posts the client, the ride and the seat and returns the job id", async () => {
    const page = browser({ status: 201, body: { jobId: "exec_trip_book" } });

    expect(await startTripBooking("hinata", "ka-631", "12C", page.fetcher)).toEqual({ jobId: "exec_trip_book" });
    expect(page.sent).toEqual([
      { url: "/api/trips/book", method: "POST", contentType: "application/json", body: { clientId: "hinata", option: "ka-631", seat: "12C" } },
    ]);
  });

  test("does not take a reply without a job id as a start", async () => {
    const page = browser({ status: 201, body: {} });
    expect(await startTripBooking("hinata", "ka-631", "12C", page.fetcher)).toEqual({ error: "The worker service is not available right now." });
  });

  test("passes the server's sentence through on failure", async () => {
    const page = browser({ status: 404, body: { error: "There is no such client." } });
    expect(await startTripBooking("nobody", "ka-631", "12C", page.fetcher)).toEqual({ error: "There is no such client." });
  });
});
