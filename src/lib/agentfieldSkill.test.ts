import { describe, expect, test } from "bun:test";
import { AgentFieldError, runSkill, type AgentFieldConfig } from "./agentfieldClient";
import realStatus from "./fixtures/agentfield-execution-status.json";

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function fakeServer(answers: ((call: Call) => { status: number; body?: unknown } | Error)[]) {
  const calls: Call[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? "GET",
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const reply = answers[Math.min(calls.length - 1, answers.length - 1)](call);
    if (reply instanceof Error) throw reply;
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { calls, fetcher: fetcher as typeof fetch };
}

const config = (fetcher: typeof fetch, apiKey?: string): AgentFieldConfig => ({ baseUrl: "http://agentfield.test", apiKey, fetch: fetcher });
const queued = () => ({ status: 202, body: { execution_id: "exec_9", run_id: "run_9", status: "queued" } });
const statusOf = (status: string, result?: unknown) => () => ({ status: 200, body: { ...realStatus, execution_id: "exec_9", status, result } });
const noWait = { sleep: async () => {} };

describe("runSkill", () => {
  test("starts the clientops skill with its input, waits for it and returns its result", async () => {
    const answer = { clientId: "kumo", computer: "ready", logins: { present: [], missing: [] } };
    const server = fakeServer([queued, statusOf("running"), statusOf("succeeded", answer)]);

    const result = await runSkill(config(server.fetcher, "key"), "prepare_computer", { client_id: "kumo" }, noWait);

    expect(result).toEqual(answer);
    expect(server.calls[0]).toMatchObject({
      url: "http://agentfield.test/api/v1/execute/async/clientops.prepare_computer",
      method: "POST",
      body: { input: { client_id: "kumo" } },
    });
    expect(server.calls.slice(1).map((call) => [call.method, call.url])).toEqual([
      ["GET", "http://agentfield.test/api/v1/executions/exec_9"],
      ["GET", "http://agentfield.test/api/v1/executions/exec_9"],
    ]);
    expect(server.calls.every((call) => call.headers["x-api-key"] === "key")).toBe(true);
  });

  test("keeps waiting while the execution is not visible yet", async () => {
    const server = fakeServer([queued, () => ({ status: 404, body: { error: "not found" } }), statusOf("succeeded", { ok: true })]);
    expect(await runSkill(config(server.fetcher), "setup_status", { client_id: "kumo" }, noWait)).toEqual({ ok: true });
  });

  test("says so when the skill fails, is cancelled or times out on AgentField", async () => {
    for (const status of ["failed", "cancelled", "timeout"]) {
      const server = fakeServer([queued, statusOf(status)]);
      const error = await runSkill(config(server.fetcher), "setup_status", { client_id: "kumo" }, noWait).then(() => new Error("finished"), (caught: Error) => caught);
      expect(error).toBeInstanceOf(AgentFieldError);
      expect(error.message).toContain(status);
    }
  });

  test("gives up after its time limit", async () => {
    let clock = 0;
    const server = fakeServer([queued, statusOf("running")]);
    const error = await runSkill(config(server.fetcher), "prepare_computer", { client_id: "kumo" }, {
      timeoutMs: 1_000,
      now: () => clock,
      sleep: async (milliseconds) => {
        clock += milliseconds;
      },
    }).then(() => new Error("finished"), (caught: Error) => caught);
    expect(error).toBeInstanceOf(AgentFieldError);
    expect(error.message).toContain("did not finish");
  });

  test("says when AgentField cannot be reached or gives no execution id", async () => {
    const down = fakeServer([() => new TypeError("fetch failed")]);
    await expect(runSkill(config(down.fetcher), "setup_status", { client_id: "kumo" }, noWait)).rejects.toBeInstanceOf(AgentFieldError);
    const odd = fakeServer([() => ({ status: 202, body: { status: "queued" } })]);
    await expect(runSkill(config(odd.fetcher), "setup_status", { client_id: "kumo" }, noWait)).rejects.toBeInstanceOf(AgentFieldError);
  });
});
