import { normalizeStatus, type ExecutionNote, type ExecutionSnapshot } from "./agentfieldRecords";
import { clientMemoryActor, clientMemoryScope } from "./clientMemory";

export const agentNodeId = "clientops";

export type AgentFieldConfig = { baseUrl: string; apiKey?: string; fetch: typeof fetch };

export class AgentFieldError extends Error {
  override name = "AgentFieldError";

  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

export function agentFieldConfig(env: Record<string, string | undefined> = process.env, fetcher: typeof fetch = fetch): AgentFieldConfig {
  return {
    baseUrl: (env.AGENTFIELD_SERVER || "http://localhost:8080").replace(/\/+$/, ""),
    apiKey: env.AGENTFIELD_API_KEY || undefined,
    fetch: fetcher,
  };
}

async function call(config: AgentFieldConfig, path: string, init: { method: "GET" | "POST"; body?: unknown; headers?: Record<string, string> }) {
  const headers: Record<string, string> = { accept: "application/json", ...init.headers };
  if (init.body !== undefined) headers["content-type"] = "application/json";
  if (config.apiKey) headers["x-api-key"] = config.apiKey;
  try {
    return await config.fetch(`${config.baseUrl}${path}`, {
      method: init.method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
    });
  } catch {
    throw new AgentFieldError("AgentField is not reachable");
  }
}

async function json(response: Response) {
  if (!response.ok) throw new AgentFieldError(`AgentField answered ${response.status}`, response.status);
  return (await response.json()) as unknown;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function findStoredExecution(value: unknown, executionId: string): Record<string, unknown> | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findStoredExecution(item, executionId);
      if (found) return found;
    }
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (record.execution_id === executionId && ("notes" in record || "input" in record)) return record;
  return findStoredExecution(Object.values(record), executionId);
}

function readNotes(value: unknown): ExecutionNote[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const note = asRecord(item);
    const timestamp = note.timestamp ?? note.created_at;
    if (typeof note.message !== "string" || (typeof timestamp !== "number" && typeof timestamp !== "string")) return [];
    const tags = Array.isArray(note.tags) ? note.tags.filter((tag): tag is string => typeof tag === "string") : [];
    return [{ message: note.message, tags, timestamp }];
  });
}

function clientIdOf(input: unknown) {
  const stored = asRecord(input);
  const clientId = asRecord(stored.input).client_id ?? stored.client_id;
  return typeof clientId === "string" && clientId.length > 0 ? clientId : null;
}

function eventCount(value: unknown) {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

async function startExecution(config: AgentFieldConfig, skill: string, input: Record<string, string>) {
  const response = await call(config, `/api/v1/execute/async/${agentNodeId}.${skill}`, { method: "POST", body: { input } });
  const body = asRecord(await json(response));
  if (typeof body.execution_id !== "string" || typeof body.run_id !== "string") {
    throw new AgentFieldError("AgentField did not return an execution id");
  }
  return { executionId: body.execution_id, runId: body.run_id };
}

export async function startRunJob(config: AgentFieldConfig, job: { clientId: string; clientName: string; request: string }) {
  return startExecution(config, "run_job", { client_id: job.clientId, client_name: job.clientName, request: job.request });
}

export async function startTripSearchJob(config: AgentFieldConfig, search: { clientId: string; to: string; date: string }) {
  return startExecution(config, "trip_search", { client_id: search.clientId, to: search.to, date: search.date });
}

export async function startTripBookingJob(config: AgentFieldConfig, booking: { clientId: string; option: string; seat: string }) {
  return startExecution(config, "trip_book", { client_id: booking.clientId, option: booking.option, seat: booking.seat });
}

export type SkillWait = {
  timeoutMs?: number;
  intervalMs?: number;
  sleep?: (milliseconds: number) => Promise<void>;
  now?: () => number;
};

const endedBadly = new Set(["failed", "cancelled", "timeout"]);

export async function runSkill(config: AgentFieldConfig, skill: string, input: Record<string, unknown>, wait: SkillWait = {}) {
  const target = `${agentNodeId}.${skill}`;
  const started = asRecord(await json(await call(config, `/api/v1/execute/async/${target}`, { method: "POST", body: { input } })));
  if (typeof started.execution_id !== "string") throw new AgentFieldError("AgentField did not return an execution id");
  const path = `/api/v1/executions/${encodeURIComponent(started.execution_id)}`;
  const sleep = wait.sleep ?? ((milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds)));
  const now = wait.now ?? Date.now;
  const deadline = now() + (wait.timeoutMs ?? 60_000);
  for (;;) {
    const response = await call(config, path, { method: "GET" });
    if (response.status !== 404) {
      const execution = asRecord(await json(response));
      const status = normalizeStatus(execution.status);
      if (status === "succeeded") return execution.result;
      if (endedBadly.has(status)) throw new AgentFieldError(`${target} ended as ${status}`);
    }
    if (now() >= deadline) throw new AgentFieldError(`${target} did not finish in time`);
    await sleep(wait.intervalMs ?? 400);
  }
}

export async function readExecution(config: AgentFieldConfig, executionId: string): Promise<ExecutionSnapshot | null> {
  const statusResponse = await call(config, `/api/v1/executions/${encodeURIComponent(executionId)}`, { method: "GET" });
  if (statusResponse.status === 404) return null;
  const status = asRecord(await json(statusResponse));
  if (status.agent_node_id !== agentNodeId) return null;

  const query = await call(config, "/api/v1/agentic/query", {
    method: "POST",
    body: { resource: "executions", filters: { execution_id: executionId } },
  });
  const stored = findStoredExecution(await json(query), executionId);
  const clientId = clientIdOf(stored?.input);
  if (!stored || clientId === null) {
    throw new AgentFieldError("AgentField has no stored record for this job yet");
  }

  return {
    executionId,
    clientId,
    status: normalizeStatus(status.status),
    notes: readNotes(stored.notes),
    completedAt: typeof status.completed_at === "string" ? status.completed_at : undefined,
    expectedEvents: eventCount(asRecord(status.result).events),
  };
}

export type AgentFieldReply = { status: number; body: unknown };

async function replyOf(response: Response): Promise<AgentFieldReply> {
  return { status: response.status, body: await response.json().catch(() => null) };
}

export async function sendApprovalResponse(config: AgentFieldConfig, rawBody: string, signature: string): Promise<AgentFieldReply> {
  const headers: Record<string, string> = { accept: "application/json", "content-type": "application/json", "x-webhook-signature": signature };
  if (config.apiKey) headers["x-api-key"] = config.apiKey;
  try {
    return await replyOf(await config.fetch(`${config.baseUrl}/api/v1/webhooks/approval-response`, { method: "POST", headers, body: rawBody, cache: "no-store" }));
  } catch {
    throw new AgentFieldError("AgentField is not reachable");
  }
}

export async function cancelExecution(config: AgentFieldConfig, executionId: string, reason: string): Promise<AgentFieldReply> {
  return replyOf(await call(config, `/api/v1/executions/${encodeURIComponent(executionId)}/cancel`, { method: "POST", body: { reason } }));
}

export async function startUndoSync(config: AgentFieldConfig, clientId: string) {
  return startExecution(config, "undo_sync", { client_id: clientId });
}

export async function waitingJobIds(config: AgentFieldConfig, clientId: string) {
  const response = await call(config, "/api/v1/agentic/query", {
    method: "POST",
    body: { resource: "executions", filters: { agent_id: agentNodeId, status: "waiting" }, limit: 100 },
  });
  const found = asRecord(asRecord(await json(response)).data).results;
  const results = found === null ? [] : found;
  if (!Array.isArray(results)) throw new AgentFieldError("AgentField did not return a list of executions");
  return results.flatMap((item) => {
    const execution = asRecord(item);
    const owned = execution.agent_node_id === agentNodeId && clientIdOf(execution.input) === clientId;
    return owned && typeof execution.execution_id === "string" ? [execution.execution_id] : [];
  });
}

export async function writeClientMemory(config: AgentFieldConfig, clientId: string, key: string, data: unknown) {
  const response = await call(config, "/api/v1/memory/set", {
    method: "POST",
    body: { key, data, scope: clientMemoryScope },
    headers: { "x-actor-id": clientMemoryActor(clientId), "x-agent-node-id": agentNodeId },
  });
  if (response.status < 200 || response.status >= 300) throw new AgentFieldError(`AgentField did not save ${key} (${response.status})`);
}

export async function readClientMemory(config: AgentFieldConfig, clientId: string, key: string): Promise<unknown> {
  const response = await call(config, "/api/v1/memory/get", {
    method: "POST",
    body: { key, scope: clientMemoryScope },
    headers: { "x-actor-id": clientMemoryActor(clientId), "x-agent-node-id": agentNodeId },
  });
  if (response.status === 404) return null;
  const data = asRecord(await json(response)).data;
  if (typeof data !== "string") return data ?? null;
  try {
    return JSON.parse(data) as unknown;
  } catch {
    return data;
  }
}
