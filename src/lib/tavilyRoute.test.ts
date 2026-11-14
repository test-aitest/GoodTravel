import { describe, expect, test } from "bun:test";
import type { RequestLike } from "./setupRoutes";
import { handleTripInsight, tavilySettings } from "./tavilyRoute";

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function tavily(reply: { status: number; body: unknown } | Error) {
  const calls: Call[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(input), method: init?.method ?? "GET", headers: Object.fromEntries(new Headers(init?.headers).entries()), body: JSON.parse(String(init?.body)) });
    if (reply instanceof Error) throw reply;
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { calls, fetcher: fetcher as typeof fetch };
}

function post(body: unknown, headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers(headers);
  return { headers: { get: (name) => sent.get(name) }, text: async () => JSON.stringify(body) };
}

async function answer(response: Response) {
  return { status: response.status, cache: response.headers.get("cache-control"), body: (await response.json()) as Record<string, unknown> };
}

const found = {
  query: "Singapore in November: weather and events",
  answer: "November is the start of Singapore's wet season, with warm days around 31°C and short afternoon showers. Christmas lights open on Orchard Road mid-month.",
  results: [
    { title: "Singapore weather in November", url: "https://www.example-weather.test/singapore/november", content: "Warm and humid with afternoon showers.", score: 0.9 },
    { title: "Events in Singapore", url: "https://events.example.test/sg", content: "Orchard Road lights.", score: 0.7 },
  ],
  images: [],
  response_time: 1.2,
};

describe("tavilySettings", () => {
  test("needs the key", () => {
    expect(tavilySettings({})).toBeNull();
    expect(tavilySettings({ TAVILY_API_KEY: "  " })).toBeNull();
    expect(tavilySettings({ TAVILY_API_KEY: "tvly-test" })).toBeNull();
    expect(tavilySettings({ TAVILY_API_KEY: "tvly-test", CLIENTOPS_TAVILY: "off" })).toBeNull();
    expect(tavilySettings({ TAVILY_API_KEY: "tvly-test", CLIENTOPS_TAVILY: "on" })).toEqual({ key: "tvly-test" });
  });
});

describe("handleTripInsight", () => {
  test("asks Tavily about the place and month only, and returns one short line with its source", async () => {
    const search = tavily({ status: 200, body: found });
    const reply = await answer(await handleTripInsight(post({ destination: "singapore", month: "november" }), { fetcher: search.fetcher, settings: { key: "tvly-test" } }));

    expect(reply.status).toBe(200);
    expect(reply.cache).toBe("no-store");
    expect(reply.body).toEqual({
      line: "November is the start of Singapore's wet season, with warm days around 31°C and short afternoon showers.",
      source: { title: "Singapore weather in November", url: "https://www.example-weather.test/singapore/november" },
    });
    expect(search.calls).toEqual([
      {
        url: "https://api.tavily.com/search",
        method: "POST",
        headers: { authorization: "Bearer tvly-test", "content-type": "application/json" },
        body: { query: "Singapore in November: weather and major events for visitors", topic: "general", search_depth: "basic", max_results: 3, include_answer: "basic" },
      },
    ]);
  });

  test("falls back to the first result when there is no answer, and keeps the line short", async () => {
    const long = "Warm ".repeat(80);
    const search = tavily({ status: 200, body: { ...found, answer: null, results: [{ ...found.results[0], content: long }] } });
    const reply = await answer(await handleTripInsight(post({ destination: "kyoto", month: "april" }), { fetcher: search.fetcher, settings: { key: "tvly-test" } }));
    expect(reply.status).toBe(200);
    expect(String(reply.body.line).length).toBeLessThanOrEqual(180);
  });

  test("says nothing when the key is missing", async () => {
    const search = tavily({ status: 200, body: found });
    expect((await answer(await handleTripInsight(post({ destination: "seoul", month: "february" }), { fetcher: search.fetcher, settings: null }))).status).toBe(503);
    expect(search.calls).toEqual([]);
  });

  test("refuses other pages and anything but a known place and month", async () => {
    const search = tavily({ status: 200, body: found });
    const deps = { fetcher: search.fetcher, settings: { key: "tvly-test" } };
    expect((await answer(await handleTripInsight(post({ destination: "seoul", month: "february" }, { "sec-fetch-site": "cross-site" }), deps))).status).toBe(403);
    expect((await answer(await handleTripInsight(post({ destination: "paris", month: "february" }), deps))).status).toBe(400);
    expect((await answer(await handleTripInsight(post({ destination: "seoul", month: "february", client: "hinata" }), deps))).status).toBe(400);
    expect(search.calls).toEqual([]);
  });

  test("reports a failed or empty search as unavailable", async () => {
    const settings = { key: "tvly-test" };
    expect((await answer(await handleTripInsight(post({ destination: "seoul", month: "february" }), { fetcher: tavily({ status: 401, body: {} }).fetcher, settings }))).status).toBe(502);
    expect((await answer(await handleTripInsight(post({ destination: "seoul", month: "february" }), { fetcher: tavily(new TypeError("offline")).fetcher, settings }))).status).toBe(502);
    expect((await answer(await handleTripInsight(post({ destination: "seoul", month: "february" }), { fetcher: tavily({ status: 200, body: { ...found, answer: "", results: [] } }).fetcher, settings }))).status).toBe(502);
  });
});
