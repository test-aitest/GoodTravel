import { describe, expect, test } from "bun:test";
import { AgentFieldError, readClientMemory, waitingJobIds, writeClientMemory, type AgentFieldConfig } from "./agentfieldClient";
import { procedureMemoryKey } from "./clientMemory";
import { savedProcedureContract } from "./mcpFixtures";

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function fakeServer(answer: (call: Call) => { status: number; body?: unknown } | Error) {
  const calls: Call[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? "GET",
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const reply = answer(call);
    if (reply instanceof Error) throw reply;
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { calls, fetcher: fetcher as typeof fetch };
}

function config(fetcher: typeof fetch, apiKey?: string): AgentFieldConfig {
  return { baseUrl: "http://agentfield.test", apiKey, fetch: fetcher };
}

const execution = (id: string, input: unknown, node = "clientops") => ({ execution_id: id, agent_node_id: node, status: "waiting", input, notes: [] });

describe("waitingJobIds", () => {
  test("asks AgentField for this node's waiting executions and keeps only the client's own", async () => {
    const server = fakeServer(() => ({
      status: 200,
      body: {
        ok: true,
        data: {
          resource: "executions",
          total: 5,
          results: [
            execution("exec_kumo_nested", { input: { client_id: "kumo", client_name: "Kumo Coffee Roasters", request: "Sync" } }),
            execution("exec_kumo_flat", { client_id: "kumo", client_name: "Kumo Coffee Roasters", request: "Sync" }),
            execution("exec_hinata", { input: { client_id: "hinata" } }),
            execution("exec_other_node", { input: { client_id: "kumo" } }, "someone-else"),
            { agent_node_id: "clientops", input: { input: { client_id: "kumo" } } },
          ],
        },
      },
    }));

    const ids = await waitingJobIds(config(server.fetcher, "secret"), "kumo");

    expect(ids).toEqual(["exec_kumo_nested", "exec_kumo_flat"]);
    expect(server.calls).toHaveLength(1);
    expect(server.calls[0]).toMatchObject({
      url: "http://agentfield.test/api/v1/agentic/query",
      method: "POST",
      body: { resource: "executions", filters: { agent_id: "clientops", status: "waiting" }, limit: 100 },
    });
    expect(server.calls[0].headers["x-api-key"]).toBe("secret");
  });

  test("is empty when AgentField has nothing waiting", async () => {
    const server = fakeServer(() => ({ status: 200, body: { ok: true, data: { resource: "executions", total: 0, results: [] } } }));
    expect(await waitingJobIds(config(server.fetcher), "kumo")).toEqual([]);
  });

  test("throws AgentFieldError when AgentField fails or cannot be reached", async () => {
    await expect(waitingJobIds(config(fakeServer(() => ({ status: 500, body: {} })).fetcher), "kumo")).rejects.toBeInstanceOf(AgentFieldError);
    await expect(waitingJobIds(config(fakeServer(() => new TypeError("fetch failed")).fetcher), "kumo")).rejects.toBeInstanceOf(AgentFieldError);
  });
});

describe("readClientMemory", () => {
  test("reads one key from the client's actor memory, as the agent node", async () => {
    const stored = { scope: "actor", scope_id: "clientops-client:kumo", key: procedureMemoryKey, data: savedProcedureContract.record };
    const server = fakeServer(() => ({ status: 200, body: stored }));

    const value = await readClientMemory(config(server.fetcher, "secret"), "kumo", procedureMemoryKey);

    expect(value).toEqual(savedProcedureContract.record);
    expect(server.calls[0]).toMatchObject({
      url: "http://agentfield.test/api/v1/memory/get",
      method: "POST",
      body: { key: procedureMemoryKey, scope: "actor" },
    });
    expect(server.calls[0].headers["x-actor-id"]).toBe(savedProcedureContract.memory.scopeId);
    expect(server.calls[0].headers["x-agent-node-id"]).toBe("clientops");
    expect(server.calls[0].headers["x-api-key"]).toBe("secret");
  });

  test("decodes a value AgentField hands back as a JSON string", async () => {
    const server = fakeServer(() => ({ status: 200, body: { key: procedureMemoryKey, data: JSON.stringify({ procedureVersion: 3 }) } }));
    expect(await readClientMemory(config(server.fetcher), "kumo", procedureMemoryKey)).toEqual({ procedureVersion: 3 });
  });

  test("is null when the key was never saved for this client", async () => {
    const server = fakeServer(() => ({ status: 404, body: { error: "not_found" } }));
    expect(await readClientMemory(config(server.fetcher), "hinata", procedureMemoryKey)).toBeNull();
    expect(server.calls[0].headers["x-actor-id"]).toBe("clientops-client:hinata");
  });

  test("throws AgentFieldError when AgentField refuses or cannot be reached", async () => {
    await expect(readClientMemory(config(fakeServer(() => ({ status: 403, body: {} })).fetcher), "kumo", procedureMemoryKey)).rejects.toBeInstanceOf(AgentFieldError);
    await expect(readClientMemory(config(fakeServer(() => new TypeError("fetch failed")).fetcher), "kumo", procedureMemoryKey)).rejects.toBeInstanceOf(AgentFieldError);
  });
});

describe("writeClientMemory", () => {
  test("writes one key into the client's actor memory, as the agent node", async () => {
    const server = fakeServer(() => ({ status: 200, body: { key: "chat/threads" } }));

    await writeClientMemory(config(server.fetcher, "secret"), "kumo", "chat/threads", { version: 1, threads: [] });

    expect(server.calls[0]).toMatchObject({ url: "http://agentfield.test/api/v1/memory/set", method: "POST", body: { key: "chat/threads", data: { version: 1, threads: [] }, scope: "actor" } });
    expect(server.calls[0].headers["x-actor-id"]).toBe("clientops-client:kumo");
    expect(server.calls[0].headers["x-agent-node-id"]).toBe("clientops");
  });

  test("a refusal from AgentField is an error, not a quiet loss", async () => {
    const server = fakeServer(() => ({ status: 500, body: { error: "boom" } }));
    await expect(writeClientMemory(config(server.fetcher), "kumo", "chat/threads", {})).rejects.toBeInstanceOf(AgentFieldError);
  });
});
