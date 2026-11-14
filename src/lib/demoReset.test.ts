import { describe, expect, test } from "bun:test";
import { AgentFieldError } from "./agentfieldClient";
import { handleDemoReset, readAgentReset, type DemoResetDeps, type AgentReset } from "./demoReset";
import type { RequestLike } from "./setupRoutes";

function post(headers: Record<string, string> = { "sec-fetch-site": "same-origin" }): RequestLike {
  const sent = new Headers({ origin: "http://app.test", host: "app.test", ...headers });
  return { headers: { get: (name) => sent.get(name) }, text: async () => "" };
}

const agent = (clientId: string): AgentReset => ({ clientId, ok: true, sites: clientId === "sora" ? "skipped" : "reset", forgotten: clientId === "kumo" ? 3 : 0 });

function world(overrides: Partial<DemoResetDeps> = {}) {
  const calls: string[] = [];
  const deps: DemoResetDeps = {
    clients: [{ id: "kumo" }, { id: "hinata" }, { id: "sora" }],
    resetAgent: async (clientId) => {
      calls.push(`agent:${clientId}`);
      return agent(clientId);
    },
    waitingJobIds: async (clientId) => (clientId === "kumo" ? ["exec_wait"] : []),
    stop: async (jobId) => {
      calls.push(`stop:${jobId}`);
    },
    scheduleOff: async (clientId) => {
      calls.push(`schedule:${clientId}`);
    },
    ...overrides,
  };
  return { deps, calls };
}

async function answer(response: Response) {
  return { status: response.status, body: (await response.json()) as Record<string, unknown> };
}

describe("handleDemoReset", () => {
  test("stops waiting jobs, has the worker reset the sites and forget what it learned, and turns daily runs off, for every client", async () => {
    const { deps, calls } = world();

    const reply = await answer(await handleDemoReset(post(), deps));

    expect(reply.status).toBe(200);
    expect(reply.body).toEqual({
      clients: [
        { clientId: "kumo", stopped: 1, sites: "reset", forgotten: 3, dailyRun: "off" },
        { clientId: "hinata", stopped: 0, sites: "reset", forgotten: 0, dailyRun: "off" },
        { clientId: "sora", stopped: 0, sites: "skipped", forgotten: 0, dailyRun: "off" },
      ],
    });
    expect(calls).toEqual(["agent:kumo", "stop:exec_wait", "schedule:kumo", "agent:hinata", "schedule:hinata", "agent:sora", "schedule:sora"]);
  });

  test("when the worker has demo reset turned off, nothing is stopped or changed and the answer says how to turn it on", async () => {
    const { deps, calls } = world({
      resetAgent: async (clientId) => {
        calls.push(`agent:${clientId}`);
        return { clientId, ok: false, problem: "not-enabled" };
      },
    });

    const reply = await answer(await handleDemoReset(post(), deps));

    expect(reply.status).toBe(409);
    expect(String(reply.body.error)).toContain("CLIENTOPS_DEMO_RESET=on");
    expect(calls).toEqual(["agent:kumo"]);
  });

  test("refuses a page from another site", async () => {
    const { deps, calls } = world();
    const reply = await answer(await handleDemoReset(post({ "sec-fetch-site": "cross-site" }), deps));
    expect(reply.status).toBe(403);
    expect(calls).toEqual([]);
  });

  test("answers 502 when the worker service cannot be reached", async () => {
    const { deps } = world({
      resetAgent: async () => {
        throw new AgentFieldError("AgentField is not reachable");
      },
    });
    const reply = await answer(await handleDemoReset(post(), deps));
    expect(reply).toEqual({ status: 502, body: { error: "The worker service is not available right now." } });
  });
});

describe("readAgentReset", () => {
  test("reads what the worker answers about one client", () => {
    expect(readAgentReset("kumo", { clientId: "kumo", ok: true, sites: "reset", forgotten: 2 })).toEqual({ clientId: "kumo", ok: true, sites: "reset", forgotten: 2 });
    expect(readAgentReset("kumo", { clientId: "kumo", ok: false, problem: "not-enabled" })).toEqual({ clientId: "kumo", ok: false, problem: "not-enabled" });
  });

  test("treats an answer about another client or in another shape as a failure", () => {
    expect(readAgentReset("kumo", { clientId: "hinata", ok: true, sites: "reset", forgotten: 0 })).toEqual({ clientId: "kumo", ok: false, problem: "unreadable" });
    expect(readAgentReset("kumo", { ok: "yes" })).toEqual({ clientId: "kumo", ok: false, problem: "unreadable" });
  });
});
