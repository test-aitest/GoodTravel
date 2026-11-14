import { describe, expect, test } from "bun:test";
import { AgentFieldError, agentFieldConfig, readExecution, startRunJob, startTripBookingJob, startTripSearchJob, type AgentFieldConfig } from "./agentfieldClient";
import { eventNoteTag } from "./agentfieldRecords";
import realQuery from "./fixtures/agentfield-query-executions.json";
import realStatus from "./fixtures/agentfield-execution-status.json";

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
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  };
  return { calls, fetcher: fetcher as typeof fetch };
}

function config(fetcher: typeof fetch, apiKey?: string): AgentFieldConfig {
  return { baseUrl: "http://agentfield.test", apiKey, fetch: fetcher };
}

describe("agentFieldConfig", () => {
  test("reads the server and key from the environment with a local default", () => {
    expect(agentFieldConfig({}, fetch)).toMatchObject({ baseUrl: "http://localhost:8080", apiKey: undefined });
    expect(agentFieldConfig({ AGENTFIELD_SERVER: "http://af.local:9000/", AGENTFIELD_API_KEY: "k" }, fetch)).toMatchObject({
      baseUrl: "http://af.local:9000",
      apiKey: "k",
    });
  });
});

describe("startRunJob", () => {
  test("starts clientops.run_job asynchronously with the job as input and the key header", async () => {
    const server = fakeServer(() => ({ status: 202, body: { execution_id: "exec_1", run_id: "run_1", status: "queued" } }));

    const started = await startRunJob(config(server.fetcher, "secret"), {
      clientId: "kumo",
      clientName: "Kumo Coffee Roasters",
      request: "Check the sites.",
    });

    expect(started).toEqual({ executionId: "exec_1", runId: "run_1" });
    expect(server.calls).toHaveLength(1);
    expect(server.calls[0]).toMatchObject({
      url: "http://agentfield.test/api/v1/execute/async/clientops.run_job",
      method: "POST",
      body: { input: { client_id: "kumo", client_name: "Kumo Coffee Roasters", request: "Check the sites." } },
    });
    expect(server.calls[0].headers["x-api-key"]).toBe("secret");
    expect(server.calls[0].headers["content-type"]).toBe("application/json");
  });

  test("leaves the key header out when there is no key", async () => {
    const server = fakeServer(() => ({ status: 202, body: { execution_id: "exec_1", run_id: "run_1" } }));
    await startRunJob(config(server.fetcher), { clientId: "kumo", clientName: "Kumo", request: "Go." });
    expect(server.calls[0].headers["x-api-key"]).toBeUndefined();
  });

  test("says when AgentField refuses or cannot be reached", async () => {
    const refused = fakeServer(() => ({ status: 503, body: { error: "busy" } }));
    await expect(startRunJob(config(refused.fetcher), { clientId: "kumo", clientName: "Kumo", request: "Go." })).rejects.toMatchObject({
      name: "AgentFieldError",
      status: 503,
    });

    const down = fakeServer(() => new TypeError("fetch failed"));
    const error = await startRunJob(config(down.fetcher), { clientId: "kumo", clientName: "Kumo", request: "Go." }).catch((caught) => caught);
    expect(error).toBeInstanceOf(AgentFieldError);
    expect(error.message).toContain("not reachable");
  });
});

describe("trip jobs", () => {
  test("starts clientops.trip_search asynchronously with the client, the city and the day", async () => {
    const server = fakeServer(() => ({ status: 202, body: { execution_id: "exec_trip_search", run_id: "run_1" } }));

    const started = await startTripSearchJob(config(server.fetcher, "secret"), { clientId: "hinata", to: "Singapore", date: "2026-11-21" });

    expect(started).toEqual({ executionId: "exec_trip_search", runId: "run_1" });
    expect(server.calls[0]).toMatchObject({
      url: "http://agentfield.test/api/v1/execute/async/clientops.trip_search",
      method: "POST",
      body: { input: { client_id: "hinata", to: "Singapore", date: "2026-11-21" } },
    });
    expect(server.calls[0].headers["x-api-key"]).toBe("secret");
  });

  test("starts clientops.trip_book asynchronously with the client, the ride and the seat", async () => {
    const server = fakeServer(() => ({ status: 202, body: { execution_id: "exec_trip_book", run_id: "run_2" } }));

    const started = await startTripBookingJob(config(server.fetcher), { clientId: "hinata", option: "ka-631", seat: "12C" });

    expect(started).toEqual({ executionId: "exec_trip_book", runId: "run_2" });
    expect(server.calls[0]).toMatchObject({
      url: "http://agentfield.test/api/v1/execute/async/clientops.trip_book",
      method: "POST",
      body: { input: { client_id: "hinata", option: "ka-631", seat: "12C" } },
    });
  });

  test("says when AgentField gives back no execution id", async () => {
    const server = fakeServer(() => ({ status: 202, body: { status: "queued" } }));
    await expect(startTripSearchJob(config(server.fetcher), { clientId: "hinata", to: "Kyoto", date: "2026-11-21" })).rejects.toBeInstanceOf(AgentFieldError);
  });
});

describe("readExecution", () => {
  const note = { message: JSON.stringify({ n: 1, event: { type: "step.started" } }), tags: [eventNoteTag], timestamp: 1794600000.5 };

  test("combines the status with the notes and the client from the stored record", async () => {
    const server = fakeServer((call) =>
      call.method === "GET"
        ? { status: 200, body: { execution_id: "exec_1", run_id: "run_1", agent_node_id: "clientops", status: "running", started_at: "2026-11-14T06:00:00Z" } }
        : { status: 200, body: { resource: "executions", results: [{ execution_id: "exec_1", input: { client_id: "kumo" }, notes: [note] }] } },
    );

    const snapshot = await readExecution(config(server.fetcher), "exec_1");

    expect(snapshot).toEqual({ executionId: "exec_1", clientId: "kumo", status: "running", notes: [note], completedAt: undefined, expectedEvents: undefined });
    expect(server.calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      "GET http://agentfield.test/api/v1/executions/exec_1",
      "POST http://agentfield.test/api/v1/agentic/query",
    ]);
    expect(server.calls[1].body).toEqual({ resource: "executions", filters: { execution_id: "exec_1" } });
  });

  test("returns nothing for an unknown execution or one that is not a ClientOps job", async () => {
    const missing = fakeServer(() => ({ status: 404, body: { error: "not found" } }));
    expect(await readExecution(config(missing.fetcher), "exec_x")).toBeNull();

    const other = fakeServer(() => ({ status: 200, body: { execution_id: "exec_2", agent_node_id: "someone-else", status: "running" } }));
    expect(await readExecution(config(other.fetcher), "exec_2")).toBeNull();
    expect(other.calls).toHaveLength(1);
  });

  test("passes the completion time on and escapes the id in the path", async () => {
    const server = fakeServer((call) =>
      call.method === "GET"
        ? { status: 200, body: { execution_id: "a/b", agent_node_id: "clientops", status: "completed", completed_at: "2026-11-14T06:00:09Z" } }
        : { status: 200, body: [{ execution_id: "a/b", input: { client_id: "kumo" }, notes: [] }] },
    );
    const snapshot = await readExecution(config(server.fetcher), "a/b");
    expect(server.calls[0].url).toBe("http://agentfield.test/api/v1/executions/a%2Fb");
    expect(snapshot).toMatchObject({ status: "succeeded", completedAt: "2026-11-14T06:00:09Z", notes: [] });
  });

  test("treats a record that is not stored yet as a passing problem", async () => {
    const server = fakeServer((call) =>
      call.method === "GET"
        ? { status: 200, body: { execution_id: "exec_1", agent_node_id: "clientops", status: "queued" } }
        : { status: 200, body: { results: [] } },
    );
    await expect(readExecution(config(server.fetcher), "exec_1")).rejects.toBeInstanceOf(AgentFieldError);
  });
});

function realServer(status: unknown) {
  return fakeServer((call) => (call.method === "GET" ? { status: 200, body: status } : { status: 200, body: realQuery }));
}

describe("readExecution against the shapes AgentField v0.1.145 really returns", () => {
  const executionId = realStatus.execution_id;

  test("finds the client inside the stored input wrapper and keeps every note", async () => {
    const snapshot = await readExecution(config(realServer(realStatus).fetcher), executionId);
    expect(snapshot?.clientId).toBe("kumo");
    expect(snapshot?.status).toBe("succeeded");
    expect(snapshot?.notes).toHaveLength(10);
    expect(snapshot?.notes[0].timestamp).toBe("2026-11-13T16:47:57.253768718Z");
    expect(snapshot?.expectedEvents).toBeUndefined();
  });

  test("takes the number of event notes from the job result when the agent reports it", async () => {
    const withCount = { ...realStatus, result: { ...realStatus.result, events: 10 } };
    const snapshot = await readExecution(config(realServer(withCount).fetcher), executionId);
    expect(snapshot?.expectedEvents).toBe(10);
  });
});
