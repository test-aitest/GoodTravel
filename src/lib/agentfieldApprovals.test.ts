import { describe, expect, test } from "bun:test";
import vector from "../../agent/tests/contract/approval_signature.json";
import { AgentFieldError, cancelExecution, sendApprovalResponse, startUndoSync, type AgentFieldConfig } from "./agentfieldClient";

type Call = { url: string; method: string; headers: Record<string, string>; body: string | undefined };

function fakeServer(answer: (call: Call) => { status: number; body?: unknown } | Error) {
  const calls: Call[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? "GET",
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
      body: init?.body === undefined || init?.body === null ? undefined : String(init.body),
    };
    calls.push(call);
    const reply = answer(call);
    if (reply instanceof Error) throw reply;
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { calls, fetcher: fetcher as typeof fetch };
}

const config = (fetcher: typeof fetch, apiKey?: string): AgentFieldConfig => ({ baseUrl: "http://agentfield.test", apiKey, fetch: fetcher });

describe("sendApprovalResponse", () => {
  test("posts the exact signed bytes to the approval webhook with the signature header", async () => {
    const server = fakeServer(() => ({ status: 200, body: { status: "processed" } }));

    const reply = await sendApprovalResponse(config(server.fetcher), vector.body, vector.header);

    expect(reply).toEqual({ status: 200, body: { status: "processed" } });
    expect(server.calls).toHaveLength(1);
    expect(server.calls[0]).toMatchObject({ url: "http://agentfield.test/api/v1/webhooks/approval-response", method: "POST", body: vector.body });
    expect(server.calls[0].headers["x-webhook-signature"]).toBe(vector.header);
    expect(server.calls[0].headers["content-type"]).toBe("application/json");
  });

  test("hands back AgentField's error answers instead of throwing", async () => {
    const server = fakeServer(() => ({ status: 503, body: { error: "approval webhook not configured" } }));
    expect(await sendApprovalResponse(config(server.fetcher), vector.body, vector.header)).toEqual({ status: 503, body: { error: "approval webhook not configured" } });
  });

  test("throws AgentFieldError when AgentField cannot be reached", async () => {
    const server = fakeServer(() => new TypeError("fetch failed"));
    await expect(sendApprovalResponse(config(server.fetcher), vector.body, vector.header)).rejects.toBeInstanceOf(AgentFieldError);
  });
});

describe("cancelExecution", () => {
  test("asks AgentField to cancel one execution with the reason and the key header", async () => {
    const server = fakeServer(() => ({ status: 200, body: { execution_id: "exec_1", status: "cancelled" } }));

    const reply = await cancelExecution(config(server.fetcher, "k"), "exec_1", "Stopped from ClientOps");

    expect(reply.status).toBe(200);
    expect(server.calls[0]).toMatchObject({ url: "http://agentfield.test/api/v1/executions/exec_1/cancel", method: "POST" });
    expect(JSON.parse(server.calls[0].body ?? "null")).toEqual({ reason: "Stopped from ClientOps" });
    expect(server.calls[0].headers["x-api-key"]).toBe("k");
  });

  test("keeps the execution id inside its path segment", async () => {
    const server = fakeServer(() => ({ status: 404, body: {} }));
    await cancelExecution(config(server.fetcher), "../nodes/x", "Stopped");
    expect(server.calls[0].url).toBe("http://agentfield.test/api/v1/executions/..%2Fnodes%2Fx/cancel");
  });
});

describe("startUndoSync", () => {
  test("starts clientops.undo_sync asynchronously for the client", async () => {
    const server = fakeServer(() => ({ status: 202, body: { execution_id: "exec_undo", run_id: "run_undo", status: "queued" } }));

    expect(await startUndoSync(config(server.fetcher), "kumo")).toEqual({ executionId: "exec_undo", runId: "run_undo" });
    expect(server.calls[0]).toMatchObject({ url: "http://agentfield.test/api/v1/execute/async/clientops.undo_sync", method: "POST" });
    expect(JSON.parse(server.calls[0].body ?? "null")).toEqual({ input: { client_id: "kumo" } });
  });

  test("throws when AgentField does not answer with an execution", async () => {
    const server = fakeServer(() => ({ status: 500, body: {} }));
    await expect(startUndoSync(config(server.fetcher), "kumo")).rejects.toBeInstanceOf(AgentFieldError);
  });
});
