import { afterEach, describe, expect, test } from "bun:test";
import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";
import { fixtureJobIds, fixtureJobs, leaksIn, savedProcedureContract } from "../lib/mcpFixtures";
import { jobSummary, savedProcedureSummary, waitingApproval } from "../lib/mcpViews";
import { clientOpsMcpServer } from "./tools";

const key = "7c".repeat(32);
const appUrl = "http://localhost:3000";

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };
type Answer = { status: number; body: unknown } | Error;

const opened: Client[] = [];

afterEach(async () => {
  for (const client of opened.splice(0)) await client.close();
});

async function connect(answer: (call: Call) => Answer) {
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
    return Response.json(reply.body, { status: reply.status });
  };
  const server = clientOpsMcpServer({ appUrl, key: async () => key, fetch: fetcher as typeof fetch });
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  await server.connect(serverSide);
  const client = new Client({ name: "clientops-test", version: "1.0.0" });
  await client.connect(clientSide);
  opened.push(client);
  return { client, calls };
}

function textOf(result: { content?: unknown }) {
  return (result.content as { type: string; text?: string }[]).map((block) => block.text ?? "").join("\n");
}

const summary = JSON.parse(JSON.stringify(jobSummary(fixtureJobIds.waiting, fixtureJobs[fixtureJobIds.waiting])));
const approval = JSON.parse(JSON.stringify(waitingApproval(fixtureJobIds.waiting, fixtureJobs[fixtureJobIds.waiting])));
const procedure = savedProcedureSummary(savedProcedureContract.record, "kumo");

describe("the ClientOps MCP server", () => {
  test("offers exactly four tools: start a job, read a job, list waiting approvals and list saved steps", async () => {
    const { client, calls } = await connect(() => ({ status: 500, body: {} }));

    const { tools } = await client.listTools();

    expect(tools.map((tool) => tool.name).toSorted()).toEqual(["job_status", "saved_procedures", "start_job", "waiting_approvals"]);
    for (const tool of tools) {
      expect(tool.name).not.toMatch(/approve|undo|stop|cancel|answer/);
      expect(tool.outputSchema).toBeDefined();
    }
    for (const name of ["job_status", "saved_procedures", "waiting_approvals"]) {
      expect(tools.find((tool) => tool.name === name)?.annotations?.readOnlyHint).toBe(true);
    }
    expect(tools.find((tool) => tool.name === "start_job")?.description).toContain("ClientOps screen");
    expect(calls).toEqual([]);
  });

  test("start_job sends the request to the app's MCP path with the key, and says where to approve", async () => {
    const { client, calls } = await connect(() => ({ status: 201, body: { jobId: "exec_new", clientId: "kumo", clientName: "Kumo Coffee Roasters" } }));

    const result = await client.callTool({ name: "start_job", arguments: { clientId: "kumo", request: "Sync today's supplier stock" } });

    expect(calls).toEqual([
      {
        url: "http://localhost:3000/api/mcp/jobs",
        method: "POST",
        headers: expect.objectContaining({ authorization: `Bearer ${key}`, "content-type": "application/json" }),
        body: { clientId: "kumo", request: "Sync today's supplier stock" },
      },
    ]);
    expect(result.isError).toBeFalsy();
    expect(result.structuredContent).toEqual({ jobId: "exec_new", clientId: "kumo", clientName: "Kumo Coffee Roasters", screen: "http://localhost:3000/" });
    expect(JSON.parse(textOf(result))).toEqual(result.structuredContent);
  });

  test("job_status reads the job's summary from the app and links to the screen", async () => {
    const { client, calls } = await connect(() => ({ status: 200, body: summary }));

    const result = await client.callTool({ name: "job_status", arguments: { jobId: "exec/odd id" } });

    expect(calls.map((call) => [call.method, call.url, call.headers.authorization])).toEqual([
      ["GET", "http://localhost:3000/api/mcp/jobs/exec%2Fodd%20id", `Bearer ${key}`],
    ]);
    expect(result.structuredContent).toEqual({ ...summary, screen: "http://localhost:3000/" });
    expect(leaksIn(result)).toEqual([]);
  });

  test("waiting_approvals lists the client's waiting jobs with the screen where a person approves them", async () => {
    const { client, calls } = await connect(() => ({ status: 200, body: { clientId: "kumo", clientName: "Kumo Coffee Roasters", waiting: [approval] } }));

    const result = await client.callTool({ name: "waiting_approvals", arguments: { clientId: "kumo" } });

    expect(calls.map((call) => [call.method, call.url])).toEqual([["GET", "http://localhost:3000/api/mcp/clients/kumo/approvals"]]);
    expect(result.structuredContent).toEqual({ clientId: "kumo", clientName: "Kumo Coffee Roasters", waiting: [approval], screen: "http://localhost:3000/" });
    expect(textOf(result)).toContain("http://localhost:3000/");
    expect(leaksIn(result)).toEqual([]);
  });

  test("saved_procedures lists the client's saved steps", async () => {
    const { client, calls } = await connect(() => ({ status: 200, body: { clientId: "kumo", clientName: "Kumo Coffee Roasters", procedures: [procedure] } }));

    const result = await client.callTool({ name: "saved_procedures", arguments: { clientId: "kumo" } });

    expect(calls.map((call) => [call.method, call.url])).toEqual([["GET", "http://localhost:3000/api/mcp/clients/kumo/procedures"]]);
    expect(result.structuredContent).toEqual({ clientId: "kumo", clientName: "Kumo Coffee Roasters", procedures: [procedure] });
    expect(leaksIn(result)).toEqual([]);
  });

  test("hands back the app's own message when the app refuses", async () => {
    const { client } = await connect(() => ({ status: 404, body: { error: "There is no such client." } }));

    const result = await client.callTool({ name: "waiting_approvals", arguments: { clientId: "acme" } });

    expect(result.isError).toBe(true);
    expect(textOf(result)).toBe("There is no such client.");
    expect(result.structuredContent).toBeUndefined();
  });

  test("says the app isn't running when it can't be reached", async () => {
    const { client } = await connect(() => new TypeError("fetch failed"));

    const result = await client.callTool({ name: "job_status", arguments: { jobId: "exec_1" } });

    expect(result.isError).toBe(true);
    expect(textOf(result)).toContain("http://localhost:3000");
  });

  test("passes on nothing the strict shape has no place for, such as the changes or the page", async () => {
    const leaky = { ...summary, page: { html: "<html></html>" }, pendingApproval: { ...summary.pendingApproval, changes: [{ sku: "KC-ETH-200" }] } };
    const { client } = await connect(() => ({ status: 200, body: leaky }));

    const result = await client.callTool({ name: "job_status", arguments: { jobId: fixtureJobIds.waiting } });

    expect(result.isError).toBe(true);
    expect(result.structuredContent).toBeUndefined();
    expect(textOf(result)).not.toContain("KC-ETH-200");
    expect(textOf(result)).not.toContain("<html");
  });
});
