import { describe, expect, test } from "bun:test";
import { readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { AgentFieldError } from "./agentfieldClient";
import { findClient } from "./clients";
import { handleCreateJob } from "./jobRoutes";
import { fixtureJobIds, fixtureJobs, fixtureSavedProcedures, fixtureWaitingJobIds, leaksIn, savedProcedureContract } from "./mcpFixtures";
import { handleMcpJob, handleMcpSavedProcedures, handleMcpStartJob, handleMcpWaitingApprovals, type McpDeps } from "./mcpRoutes";
import { jobSummary, savedProcedureSummary, waitingApproval } from "./mcpViews";
import type { RequestLike } from "./setupRoutes";

const key = "5e".repeat(32);

function request(options: { body?: unknown; headers?: Record<string, string> } = {}): RequestLike {
  const sent = new Headers({ "content-type": "application/json", host: "localhost:3000", authorization: `Bearer ${key}`, ...options.headers });
  const text = options.body === undefined ? "" : typeof options.body === "string" ? options.body : JSON.stringify(options.body);
  return { headers: { get: (name) => sent.get(name) }, text: async () => text };
}

function deps(overrides: Partial<McpDeps> = {}) {
  const started: { clientId: string; clientName: string; request: string }[] = [];
  const reads: string[] = [];
  const built: McpDeps = {
    token: async () => key,
    findClient,
    start: async (job) => {
      started.push(job);
      return { executionId: "exec_new", runId: "run_new" };
    },
    readJob: async (jobId) => {
      reads.push(jobId);
      return fixtureJobs[jobId] ?? null;
    },
    waitingJobIds: async (clientId) => fixtureWaitingJobIds[clientId] ?? [],
    savedProcedure: async (clientId) => fixtureSavedProcedures[clientId] ?? null,
    ...overrides,
  };
  return { deps: built, started, reads };
}

async function read(response: Response) {
  return { status: response.status, body: (await response.json()) as Record<string, unknown>, cache: response.headers.get("cache-control") };
}

const down = async (): Promise<never> => {
  throw new AgentFieldError("AgentField is not reachable");
};

const everyPath = [
  (r: RequestLike, d: McpDeps) => handleMcpStartJob(r, d),
  (r: RequestLike, d: McpDeps) => handleMcpJob(r, fixtureJobIds.waiting, d),
  (r: RequestLike, d: McpDeps) => handleMcpWaitingApprovals(r, "kumo", d),
  (r: RequestLike, d: McpDeps) => handleMcpSavedProcedures(r, "kumo", d),
];

describe("the MCP paths' key", () => {
  test("every path refuses a call without the key or with another key, and touches nothing", async () => {
    for (const handle of everyPath) {
      for (const headers of [{ authorization: "" }, { authorization: `Bearer ${"00".repeat(32)}` }, { authorization: `Bearer ${key}x` }]) {
        const { deps: built, started, reads } = deps();
        const answer = await read(await handle(request({ headers, body: { clientId: "kumo", request: "Sync" } }), built));
        expect(answer.status).toBe(401);
        expect(answer.body.error).toBe("This path is only for the ClientOps MCP server on this computer.");
        expect(started).toEqual([]);
        expect(reads).toEqual([]);
      }
    }
  });

  test("every path refuses a browser, even one that somehow has the key", async () => {
    for (const handle of everyPath) {
      for (const headers of [{ "sec-fetch-site": "same-origin" }, { origin: "http://localhost:3000" }] as Record<string, string>[]) {
        const { deps: built, started } = deps();
        const answer = await read(await handle(request({ headers, body: { clientId: "kumo", request: "Sync" } }), built));
        expect(answer.status).toBe(403);
        expect(started).toEqual([]);
      }
    }
  });
});

describe("handleMcpStartJob", () => {
  test("starts the job for the client named in config with the trimmed request", async () => {
    const { deps: built, started } = deps();

    const answer = await read(await handleMcpStartJob(request({ body: { clientId: "kumo", request: "  Sync today's supplier stock  " } }), built));

    expect(answer).toEqual({ status: 201, body: { jobId: "exec_new", clientId: "kumo", clientName: "Kumo Coffee Roasters" }, cache: "no-store" });
    expect(started).toEqual([{ clientId: "kumo", clientName: "Kumo Coffee Roasters", request: "Sync today's supplier stock" }]);
  });

  test("checks a request exactly as the screen's POST /api/jobs does", async () => {
    const bodies = [
      {},
      { clientId: "kumo" },
      { clientId: "kumo", request: "   " },
      { clientId: "kumo", request: "x".repeat(2001) },
      { clientId: "acme", request: "Sync" },
      { clientId: "", request: "Sync" },
      "{",
    ];
    for (const body of bodies) {
      const mcp = await read(await handleMcpStartJob(request({ body }), deps().deps));
      const screen = await handleCreateJob(new Request("http://localhost:3000/api/jobs", { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body) }), {
        start: async () => ({ executionId: "exec_new", runId: "run_new" }),
        findClient,
      });
      expect([mcp.status, mcp.body.error]).toEqual([screen.status, ((await screen.json()) as { error: string }).error]);
    }
  });

  test("says the worker service is down when AgentField can't take the job", async () => {
    const answer = await read(await handleMcpStartJob(request({ body: { clientId: "kumo", request: "Sync" } }), deps({ start: down }).deps));
    expect(answer).toMatchObject({ status: 502, body: { error: "The worker service is not available right now." } });
  });

  test("refuses a body too large to be a request", async () => {
    const { deps: built, started } = deps();
    const answer = await read(await handleMcpStartJob(request({ body: { clientId: "kumo", request: "x".repeat(40_000) } }), built));
    expect(answer.status).toBe(413);
    expect(started).toEqual([]);
  });
});

describe("handleMcpJob", () => {
  test("answers with the job's summary built from its events", async () => {
    for (const jobId of Object.values(fixtureJobIds)) {
      const answer = await read(await handleMcpJob(request(), jobId, deps().deps));
      expect(answer.status).toBe(200);
      expect(answer.cache).toBe("no-store");
      expect(answer.body).toEqual(JSON.parse(JSON.stringify(jobSummary(jobId, fixtureJobs[jobId]))));
      expect(leaksIn(answer.body)).toEqual([]);
    }
  });

  test("answers 404 for a job AgentField doesn't know and 502 when it can't be read", async () => {
    expect(await read(await handleMcpJob(request(), "exec_unknown", deps().deps))).toMatchObject({ status: 404, body: { error: "There's no such job." } });
    expect(await read(await handleMcpJob(request(), fixtureJobIds.waiting, deps({ readJob: down }).deps))).toMatchObject({
      status: 502,
      body: { error: "The worker service is not available right now." },
    });
  });
});

describe("handleMcpWaitingApprovals", () => {
  test("lists only the client's own jobs that are waiting for the OK", async () => {
    const kumo = await read(await handleMcpWaitingApprovals(request(), "kumo", deps().deps));
    const hinata = await read(await handleMcpWaitingApprovals(request(), "hinata", deps().deps));
    const sora = await read(await handleMcpWaitingApprovals(request(), "sora", deps().deps));

    expect(kumo).toEqual({
      status: 200,
      cache: "no-store",
      body: { clientId: "kumo", clientName: "Kumo Coffee Roasters", waiting: [JSON.parse(JSON.stringify(waitingApproval(fixtureJobIds.waiting, fixtureJobs[fixtureJobIds.waiting])))] },
    });
    expect((hinata.body.waiting as { jobId: string }[]).map((item) => item.jobId)).toEqual([fixtureJobIds.waitingHinata]);
    expect(sora.body).toEqual({ clientId: "sora", clientName: "Sora Outdoor", waiting: [] });
    expect(leaksIn(kumo.body)).toEqual([]);
  });

  test("answers 404 for an unknown client and 502 when AgentField can't be read", async () => {
    expect(await read(await handleMcpWaitingApprovals(request(), "acme", deps().deps))).toMatchObject({ status: 404, body: { error: "There is no such client." } });
    expect(await read(await handleMcpWaitingApprovals(request(), "kumo", deps({ waitingJobIds: down }).deps))).toMatchObject({ status: 502 });
    expect(await read(await handleMcpWaitingApprovals(request(), "kumo", deps({ readJob: down }).deps))).toMatchObject({ status: 502 });
  });
});

describe("handleMcpSavedProcedures", () => {
  test("lists the client's saved steps by version, step count and expected replay time only", async () => {
    const kumo = await read(await handleMcpSavedProcedures(request(), "kumo", deps().deps));

    expect(kumo).toEqual({
      status: 200,
      cache: "no-store",
      body: { clientId: "kumo", clientName: "Kumo Coffee Roasters", procedures: [savedProcedureSummary(savedProcedureContract.record, "kumo")] },
    });
    expect(leaksIn(kumo.body)).toEqual([]);
  });

  test("lists nothing for a client with no saved steps or with a record that isn't its own", async () => {
    expect((await read(await handleMcpSavedProcedures(request(), "sora", deps().deps))).body).toEqual({ clientId: "sora", clientName: "Sora Outdoor", procedures: [] });
    expect((await read(await handleMcpSavedProcedures(request(), "hinata", deps().deps))).body).toEqual({
      clientId: "hinata",
      clientName: "Hinata Skincare",
      procedures: [],
    });
  });

  test("answers 404 for an unknown client and 502 when AgentField can't be read", async () => {
    expect(await read(await handleMcpSavedProcedures(request(), "acme", deps().deps))).toMatchObject({ status: 404 });
    expect(await read(await handleMcpSavedProcedures(request(), "kumo", deps({ savedProcedure: down }).deps))).toMatchObject({ status: 502 });
  });
});

describe("the MCP paths in the app", () => {
  const root = join(process.cwd(), "src/app/api/mcp");

  function routeFiles(directory: string): string[] {
    return readdirSync(directory, { withFileTypes: true }).flatMap((entry) =>
      entry.isDirectory() ? routeFiles(join(directory, entry.name)) : [relative(root, join(directory, entry.name))],
    );
  }

  test("are exactly start, read a job, list waiting approvals and list saved steps: nothing approves, undoes or stops", async () => {
    const methods: Record<string, string[]> = {};
    for (const file of routeFiles(root).toSorted()) {
      const loaded = (await import(join(root, file))) as Record<string, unknown>;
      methods[file] = Object.keys(loaded).toSorted();
    }
    expect(methods).toEqual({
      "clients/[clientId]/approvals/route.ts": ["GET"],
      "clients/[clientId]/procedures/route.ts": ["GET"],
      "jobs/[jobId]/route.ts": ["GET"],
      "jobs/route.ts": ["POST"],
    });
  });
});
