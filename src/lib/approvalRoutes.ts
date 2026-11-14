import { z } from "zod";
import { AgentFieldError, readExecution, type AgentFieldConfig, type AgentFieldReply } from "./agentfieldClient";
import { isTerminal, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import { approvalBody, approvalSignature } from "./approvalSignature";
import type { JobEvent } from "./events";
import type { KnownClient } from "./jobs";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

export type JobRead = { status: ExecutionStatus; events: readonly JobEvent[] };
export type JobReader = (jobId: string) => Promise<JobRead | null>;

export type ApprovalDeps = {
  secret: string | undefined;
  readJob: JobReader;
  sendApproval: (rawBody: string, signature: string) => Promise<AgentFieldReply>;
};

export type StopDeps = { readJob: JobReader; cancel: (jobId: string) => Promise<{ status: number }> };

export type UndoDeps = {
  findClient: (clientId: string) => KnownClient | null;
  startUndo: (clientId: string) => Promise<{ executionId: string; runId: string }>;
};

const LARGEST_BODY = 2 * 1024;
const identifier = z.string().trim().min(1).max(200);
const approvalSchema = z.strictObject({ jobId: identifier, approvalId: identifier });

export const routeCopy = {
  notThisPage: "Only the ClientOps page can do this.",
  sayWhich: "Say which job and which approval.",
  noSecret: "Approvals aren't set up on this server. Put AGENTFIELD_APPROVAL_WEBHOOK_SECRET in .env.local and restart the app.",
  noJob: "There's no such job.",
  noApproval: "This job has no such approval.",
  answered: "This approval was already answered.",
  ended: "This job has already ended.",
  notReady: "This job isn't ready for your OK yet. Try again in a moment.",
  agentFieldNoSecret:
    "AgentField isn't set up to take approvals. Restart it with AGENTFIELD_APPROVAL_WEBHOOK_SECRET in .env.local: bun run agentfield:stop, then bun run agentfield.",
  badSignature: "AgentField didn't accept the approval's signature. AGENTFIELD_APPROVAL_WEBHOOK_SECRET must be the same here and in AgentField.",
  agentFieldNoJob: "AgentField has no job waiting on this approval.",
  unavailable: "The worker service is not available right now.",
  noClient: "There is no such client.",
};

export const stopReason = "Stopped from ClientOps";

export function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

export function problem(status: number, error: string) {
  return reply({ error }, status);
}

export async function readBody(request: RequestLike) {
  const text = await request.text().catch(() => "");
  if (text.length > LARGEST_BODY) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function approvalState(events: readonly JobEvent[], approvalId: string) {
  const asked = events.some((event) => event.type === "approval.requested" && event.approvalId === approvalId);
  if (!asked) return "unknown";
  if (events.some((event) => event.type === "approval.answered" && event.approvalId === approvalId)) return "answered";
  if (events.some((event) => event.type === "job.failed" || event.type === "boundary.blocked")) return "ended";
  return "open";
}

export function agentFieldAnswer(answer: AgentFieldReply, accepted: unknown, alreadyAnswered: string) {
  const status = typeof answer.body === "object" && answer.body !== null ? (answer.body as { status?: unknown }).status : undefined;
  if (answer.status === 200 && status === "already_processed") return problem(409, alreadyAnswered);
  if (answer.status >= 200 && answer.status < 300) return reply(accepted);
  if (answer.status === 503) return problem(503, routeCopy.agentFieldNoSecret);
  if (answer.status === 401) return problem(502, routeCopy.badSignature);
  if (answer.status === 404) return problem(404, routeCopy.agentFieldNoJob);
  return problem(502, routeCopy.unavailable);
}

export async function handleApprove(request: RequestLike, deps: ApprovalDeps) {
  if (!isSameOrigin(request)) return problem(403, routeCopy.notThisPage);
  const parsed = approvalSchema.safeParse(await readBody(request));
  if (!parsed.success) return problem(400, routeCopy.sayWhich);
  if (!deps.secret) return problem(503, routeCopy.noSecret);
  const { jobId, approvalId } = parsed.data;
  try {
    const job = await deps.readJob(jobId);
    if (!job) return problem(404, routeCopy.noJob);
    const state = approvalState(job.events, approvalId);
    if (state === "unknown") return problem(404, routeCopy.noApproval);
    if (state === "answered") return problem(409, routeCopy.answered);
    if (state === "ended" || isTerminal(job.status)) return problem(409, routeCopy.ended);
    if (job.status !== "waiting") return problem(409, routeCopy.notReady);
    const raw = approvalBody(approvalId);
    return agentFieldAnswer(await deps.sendApproval(raw, approvalSignature(deps.secret, raw)), { jobId, approvalId, approved: true }, routeCopy.answered);
  } catch (error) {
    if (error instanceof AgentFieldError) return problem(502, routeCopy.unavailable);
    throw error;
  }
}

export async function handleStop(request: RequestLike, jobId: string, deps: StopDeps) {
  if (!isSameOrigin(request)) return problem(403, routeCopy.notThisPage);
  try {
    const job = await deps.readJob(jobId);
    if (!job) return problem(404, routeCopy.noJob);
    if (isTerminal(job.status)) return problem(409, routeCopy.ended);
    const answer = await deps.cancel(jobId);
    if (answer.status >= 200 && answer.status < 300) return reply({ jobId, stopped: true });
    if (answer.status === 404) return problem(404, routeCopy.noJob);
    if (answer.status === 409) return problem(409, routeCopy.ended);
    return problem(502, routeCopy.unavailable);
  } catch (error) {
    if (error instanceof AgentFieldError) return problem(502, routeCopy.unavailable);
    throw error;
  }
}

export async function handleUndo(request: RequestLike, clientId: string, deps: UndoDeps) {
  if (!isSameOrigin(request)) return problem(403, routeCopy.notThisPage);
  const client = deps.findClient(clientId);
  if (!client) return problem(404, routeCopy.noClient);
  try {
    const started = await deps.startUndo(client.id);
    return reply({ jobId: started.executionId, runId: started.runId }, 201);
  } catch (error) {
    if (error instanceof AgentFieldError) return problem(502, routeCopy.unavailable);
    throw error;
  }
}

export async function readJob(config: AgentFieldConfig, jobId: string): Promise<JobRead | null> {
  const snapshot = await readExecution(config, jobId);
  if (!snapshot) return null;
  return { status: snapshot.status, events: newEvents(snapshot, 0, { flush: true }).events };
}
