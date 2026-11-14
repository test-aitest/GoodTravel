import { AgentFieldError, agentFieldConfig, readClientMemory, startRunJob, waitingJobIds, type AgentFieldConfig } from "./agentfieldClient";
import { readJob, type JobReader } from "./approvalRoutes";
import { procedureMemoryKey } from "./clientMemory";
import { findClient } from "./clients";
import { createJob, type JobStarter, type KnownClient } from "./jobs";
import { isMcpCaller, McpTokenError, mcpToken } from "./mcpKey";
import { jobSummary, savedProcedureSummary, waitingApproval, type WaitingApproval } from "./mcpViews";
import type { RequestLike } from "./setupRoutes";

export type McpDeps = {
  token: () => Promise<string | null>;
  findClient: (clientId: string) => KnownClient | null;
  start: JobStarter;
  readJob: JobReader;
  waitingJobIds: (clientId: string) => Promise<readonly string[]>;
  savedProcedure: (clientId: string) => Promise<unknown>;
};

const LARGEST_BODY = 8 * 1024;

const copy = {
  notThisServer: "This path is only for the ClientOps MCP server on this computer.",
  notABrowser: "This path is only for the ClientOps MCP server, not for a browser.",
  tooLarge: "That is too much to be a job request.",
  noJob: "There's no such job.",
  noClient: "There is no such client.",
  unavailable: "The worker service is not available right now.",
};

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

function problem(status: number, error: string) {
  return reply({ error }, status);
}

function fromBrowser(request: RequestLike) {
  return ["origin", "sec-fetch-site", "sec-fetch-mode"].some((name) => request.headers.get(name) !== null);
}

async function guarded(request: RequestLike, deps: McpDeps, work: () => Promise<Response>) {
  if (fromBrowser(request)) return problem(403, copy.notABrowser);
  const token = await deps.token().catch((error: unknown) => {
    if (error instanceof McpTokenError) return null;
    throw error;
  });
  if (!isMcpCaller(request.headers, token)) return problem(401, copy.notThisServer);
  try {
    return await work();
  } catch (error) {
    if (error instanceof AgentFieldError) return problem(502, copy.unavailable);
    throw error;
  }
}

function parsed(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export function handleMcpStartJob(request: RequestLike, deps: McpDeps) {
  return guarded(request, deps, async () => {
    const text = await request.text().catch(() => "");
    if (text.length > LARGEST_BODY) return problem(413, copy.tooLarge);
    const startedFor: KnownClient[] = [];
    const answer = await createJob(parsed(text), {
      findClient: deps.findClient,
      start: (job) => {
        startedFor.push({ id: job.clientId, name: job.clientName });
        return deps.start(job);
      },
    });
    const [client] = startedFor;
    if (answer.status !== 201 || !client) return reply(answer.body, answer.status);
    return reply({ jobId: answer.body.jobId, clientId: client.id, clientName: client.name }, 201);
  });
}

export function handleMcpJob(request: RequestLike, jobId: string, deps: McpDeps) {
  return guarded(request, deps, async () => {
    const job = await deps.readJob(jobId);
    return job ? reply(jobSummary(jobId, job)) : problem(404, copy.noJob);
  });
}

export function handleMcpWaitingApprovals(request: RequestLike, clientId: string, deps: McpDeps) {
  return guarded(request, deps, async () => {
    const client = deps.findClient(clientId);
    if (!client) return problem(404, copy.noClient);
    const jobIds = await deps.waitingJobIds(client.id);
    const approvals = await Promise.all(
      jobIds.map(async (jobId) => {
        const job = await deps.readJob(jobId);
        return job ? waitingApproval(jobId, job) : null;
      }),
    );
    const waiting = approvals.filter((approval): approval is WaitingApproval => approval?.clientId === client.id);
    return reply({ clientId: client.id, clientName: client.name, waiting });
  });
}

export function handleMcpSavedProcedures(request: RequestLike, clientId: string, deps: McpDeps) {
  return guarded(request, deps, async () => {
    const client = deps.findClient(clientId);
    if (!client) return problem(404, copy.noClient);
    const summary = savedProcedureSummary(await deps.savedProcedure(client.id), client.id);
    return reply({ clientId: client.id, clientName: client.name, procedures: summary ? [summary] : [] });
  });
}

export function liveMcpDeps(config: AgentFieldConfig = agentFieldConfig()): McpDeps {
  return {
    token: () => mcpToken(),
    findClient,
    start: (job) => startRunJob(config, job),
    readJob: (jobId) => readJob(config, jobId),
    waitingJobIds: (clientId) => waitingJobIds(config, clientId),
    savedProcedure: (clientId) => readClientMemory(config, clientId, procedureMemoryKey),
  };
}
