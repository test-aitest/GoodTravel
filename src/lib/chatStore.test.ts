import { describe, expect, test } from "bun:test";
import { AgentFieldError } from "./agentfieldClient";
import { findClient } from "./clients";
import { createChatStore, handleChat, handleListThreads, handleReadThread, handleSaveThread, type ChatDeps, type ChatThread } from "./chatStore";
import type { RequestLike } from "./setupRoutes";

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

const thread: ChatThread = {
  version: 1,
  id: "a1b2c3d4-thread-1",
  clientId: "kumo",
  title: "Supplier stock sync",
  createdAt: "2026-11-14T09:00:00.000Z",
  updatedAt: "2026-11-14T09:01:00.000Z",
  turns: [
    { kind: "you", text: "Sync today's supplier stock to the store admin", at: "2026-11-14T09:00:00.000Z" },
    { kind: "job", jobId: "exec_1", trigger: "request", at: "2026-11-14T09:00:01.000Z" },
    { kind: "you", text: "How many products changed?", at: "2026-11-14T09:01:00.000Z" },
    { kind: "reply", action: "answer", facts: ["last.changes"], clarify: null, at: "2026-11-14T09:01:01.000Z" },
  ],
};

function post(body: unknown, headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers({ origin: "http://app.test", host: "app.test", ...headers });
  return { headers: { get: (name) => sent.get(name) }, text: async () => JSON.stringify(body) };
}

async function answer(response: Response) {
  return { status: response.status, body: (await response.json()) as Record<string, unknown> };
}

describe("conversations kept on the server", () => {
  test("a saved conversation comes back the same, and the list shows it newest first", async () => {
    const store = createChatStore(memory());
    await store.write({ ...thread, id: "older", updatedAt: "2026-11-14T08:00:00.000Z" });
    await store.write(thread);

    expect(await store.read("kumo", thread.id)).toEqual(thread);
    expect((await store.list("kumo")).map((entry) => entry.id)).toEqual([thread.id, "older"]);
  });

  test("an answer is kept with the words it was given", async () => {
    const store = createChatStore(memory());
    const answered: ChatThread = { ...thread, turns: [...thread.turns.slice(0, 3), { kind: "reply", action: "answer", facts: ["last.changes"], clarify: null, lines: ["12 products changed:", "Ethiopia Guji 200g: 18 → 6"], at: "2026-11-14T09:01:01.000Z" }] };
    await store.write(answered);
    expect(await store.read("kumo", thread.id)).toEqual(answered);
  });

  test("conversations of one client are not seen from another", async () => {
    const store = createChatStore(memory());
    await store.write(thread);
    expect(await store.list("hinata")).toEqual([]);
    expect(await store.read("hinata", thread.id)).toBeNull();
  });

  test("the routes save and read back, only from the ClientOps page, and refuse a conversation of another client", async () => {
    const deps = { findClient, store: createChatStore(memory()) };
    expect((await answer(await handleSaveThread(post(thread), "kumo", thread.id, deps))).status).toBe(200);
    expect((await answer(await handleReadThread("kumo", thread.id, deps))).body).toEqual(thread);
    expect((await answer(await handleListThreads("kumo", deps))).body).toEqual({ clientId: "kumo", threads: [{ id: thread.id, title: thread.title, updatedAt: thread.updatedAt }] });
    expect((await answer(await handleSaveThread(post(thread, { "sec-fetch-site": "cross-site" }), "kumo", thread.id, deps))).status).toBe(403);
    expect((await answer(await handleSaveThread(post({ ...thread, clientId: "hinata" }), "kumo", thread.id, deps))).status).toBe(400);
    expect((await answer(await handleReadThread("kumo", "missing", deps))).status).toBe(404);
  });
});

function chatDeps(reply: ChatDeps["reply"]): ChatDeps {
  return { findClient, reply };
}

describe("asking the worker what a message means", () => {
  test("sends the worker only the message and the names of the facts, never values", async () => {
    const sent: unknown[] = [];
    const deps = chatDeps(async (clientId, message, facts) => {
      sent.push({ clientId, message, facts });
      return { clientId, action: "answer", facts: ["last.changes"], clarify: null, decidedBy: "rules", claudeCalls: 0 };
    });

    const reply = await answer(await handleChat(post({ message: "How many products changed?", facts: ["last.changes", "last.result"] }), "kumo", deps));

    expect(reply).toEqual({ status: 200, body: { action: "answer", facts: ["last.changes"], clarify: null } });
    expect(sent).toEqual([
      {
        clientId: "kumo",
        message: "How many products changed?",
        facts: [
          { id: "last.changes", about: "the stock changes the last job found" },
          { id: "last.result", about: "how the last job ended" },
        ],
      },
    ]);
  });

  test("refuses facts it does not know, and an answer that is not in the agreed shape", async () => {
    const odd = chatDeps(async (clientId) => ({ clientId, action: "answer", facts: ["last.secret"], clarify: null, decidedBy: "claude", claudeCalls: 1 }));
    expect((await answer(await handleChat(post({ message: "hi", facts: ["last.values=18"] }), "kumo", odd))).status).toBe(400);
    expect(await answer(await handleChat(post({ message: "hi", facts: ["last.result"] }), "kumo", odd))).toEqual({ status: 502, body: { error: "The worker's answer could not be read." } });
  });

  test("answers 502 when the worker service is down", async () => {
    const down = chatDeps(async () => {
      throw new AgentFieldError("AgentField is not reachable");
    });
    expect(await answer(await handleChat(post({ message: "hi", facts: [] }), "kumo", down))).toEqual({ status: 502, body: { error: "The worker service is not available right now." } });
  });
});
