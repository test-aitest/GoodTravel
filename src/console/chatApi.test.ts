import { describe, expect, test } from "bun:test";
import { createChatApi } from "./chatApi";

function browser(answer: (url: string, init?: RequestInit) => { status: number; body: unknown }) {
  const sent: { url: string; method: string; body: unknown }[] = [];
  const fetcher = async (input: string, init?: RequestInit) => {
    sent.push({ url: input, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const reply = answer(input, init);
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { sent, api: createChatApi(fetcher) };
}

describe("chatApi", () => {
  test("asks the worker with the message and the fact names only", async () => {
    const { sent, api } = browser(() => ({ status: 200, body: { action: "answer", facts: ["last.changes"], clarify: null } }));
    expect(await api.ask("kumo", "How many changed?", ["last.changes"])).toEqual({ action: "answer", facts: ["last.changes"], clarify: null });
    expect(sent).toEqual([{ url: "/api/clients/kumo/chat", method: "POST", body: { message: "How many changed?", facts: ["last.changes"] } }]);
  });

  test("passes the server's sentence through, and says the service is down when nothing answers", async () => {
    expect(await browser(() => ({ status: 502, body: { error: "The worker's answer could not be read." } })).api.ask("kumo", "hi", [])).toEqual({ error: "The worker's answer could not be read." });
    const failing = createChatApi(async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(await failing.ask("kumo", "hi", [])).toEqual({ error: "The worker service is not available right now." });
  });

  test("lists, reads and saves conversations", async () => {
    const thread = { version: 1, id: "t-1", clientId: "kumo", title: "Supplier stock sync", createdAt: "2026-11-14T09:00:00.000Z", updatedAt: "2026-11-14T09:00:00.000Z", turns: [] };
    const { sent, api } = browser((url, init) => (url.endsWith("/threads") ? { status: 200, body: { clientId: "kumo", threads: [{ id: "t-1", title: "Supplier stock sync", updatedAt: thread.updatedAt }] } } : init?.method === "PUT" ? { status: 200, body: { saved: true } } : { status: 200, body: thread }));
    expect((await api.list("kumo")).map((entry) => entry.id)).toEqual(["t-1"]);
    expect((await api.read("kumo", "t-1"))?.id).toBe("t-1");
    expect(await api.save({ ...thread, version: 1 })).toBe(true);
    expect(sent.at(-1)).toMatchObject({ url: "/api/clients/kumo/threads/t-1", method: "PUT" });
  });
});
