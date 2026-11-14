import { z } from "zod";
import { isTerminal, type ExecutionStatus } from "./agentfieldRecords";
import type { JobRead } from "./approvalRoutes";
import { procedureIdFor } from "./clientMemory";
import { rungSchema, runSchema, type JobEvent } from "./events";
import { buildJobState, type JobState, type StepState } from "./jobState";

const text = z.string().min(1);
const count = z.number().int().nonnegative();
const milliseconds = z.number().nonnegative();
const version = z.number().int().positive();
const time = z.iso.datetime({ offset: true });

export const stepSummarySchema = z.strictObject({
  stepId: text,
  title: text,
  status: z.enum(["pending", "active", "done", "stopped", "failed"]),
  activity: text.optional(),
  summary: text.optional(),
  detail: text.optional(),
  decidedBy: rungSchema.optional(),
  decidedInMs: milliseconds.optional(),
  escalatedFrom: z.array(rungSchema).min(1).optional(),
  tookMs: milliseconds.optional(),
});
export type StepSummary = z.infer<typeof stepSummarySchema>;

export const pendingApprovalSummarySchema = z.strictObject({
  question: text,
  changeCount: count,
  soldOutCount: count,
  undoable: z.boolean(),
  changesSavedSteps: z.boolean(),
  askedAt: time,
});

export const jobResultSummarySchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("done"), written: count.optional(), checked: count.optional(), matched: count.optional() }),
  z.strictObject({ kind: z.literal("stopped") }),
  z.strictObject({ kind: z.literal("failed"), reason: z.enum(["error", "verify-mismatch"]), stepId: text.optional(), message: text.optional() }),
  z.strictObject({ kind: z.literal("blocked"), resource: text, auditId: text }),
]);

export const jobProcedureSummarySchema = z.strictObject({
  procedureId: text,
  version,
  stepCount: version,
  expectedReplayMs: milliseconds,
});

export const jobStatuses = ["starting", "running", "waiting", "done", "stopped", "failed", "blocked"] as const;

export const jobSummarySchema = z.strictObject({
  jobId: text,
  clientId: text.nullable(),
  clientName: text.nullable(),
  title: text.nullable(),
  run: runSchema.nullable(),
  status: z.enum(jobStatuses),
  progress: z.number().int().min(0).max(100),
  steps: z.array(stepSummarySchema),
  claudeDecisions: count,
  pendingApproval: pendingApprovalSummarySchema.nullable(),
  result: jobResultSummarySchema.nullable(),
  savedProcedure: jobProcedureSummarySchema.nullable(),
  startedAt: time.nullable(),
  updatedAt: time.nullable(),
});
export type JobSummary = z.infer<typeof jobSummarySchema>;

export const waitingApprovalSchema = z.strictObject({
  jobId: text,
  clientId: text,
  title: text,
  question: text,
  changeCount: count,
  soldOutCount: count,
  askedAt: time,
});
export type WaitingApproval = z.infer<typeof waitingApprovalSchema>;

export const savedProcedureSummarySchema = z.strictObject({
  procedureId: text,
  version,
  stepCount: version,
  expectedReplayMs: milliseconds,
  savedAt: time,
});
export type SavedProcedureSummary = z.infer<typeof savedProcedureSummarySchema>;

const storedProcedureSchema = z.looseObject({
  version: z.literal(1),
  procedureId: text,
  procedureVersion: version,
  clientId: text,
  savedAt: time,
  expectedReplayMs: count,
  steps: z.array(z.looseObject({ stepId: text })).min(1),
});

function withDefined<T extends object>(value: T) {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== undefined)) as T;
}

function endedWithoutEvents(jobId: string, status: ExecutionStatus): JobSummary {
  return {
    jobId,
    clientId: null,
    clientName: null,
    title: null,
    run: null,
    status: !isTerminal(status) ? "starting" : status === "cancelled" ? "stopped" : status === "succeeded" ? "done" : "failed",
    progress: 0,
    steps: [],
    claudeDecisions: 0,
    pendingApproval: null,
    result: null,
    savedProcedure: null,
    startedAt: null,
    updatedAt: null,
  };
}

function stepSummary(step: StepState): StepSummary {
  return withDefined({
    stepId: step.stepId,
    title: step.title,
    status: step.status,
    activity: step.status === "active" ? step.activity : undefined,
    summary: step.summary,
    detail: step.detail,
    decidedBy: step.decidedBy,
    decidedInMs: step.decidedInMs,
    escalatedFrom: step.escalatedFrom,
    tookMs: step.tookMs,
  });
}

function pendingApprovalSummary(state: JobState, events: readonly JobEvent[]) {
  const pending = state.pendingApproval;
  if (!pending) return null;
  const asked = events.findLast((event) => event.type === "approval.requested" && event.approvalId === pending.approvalId);
  return {
    question: pending.question,
    changeCount: pending.changes.length,
    soldOutCount: pending.changes.filter((change) => change.supplier === 0).length,
    undoable: pending.undoable,
    changesSavedSteps: pending.procedureChange !== undefined,
    askedAt: asked?.at ?? state.updatedAt,
  };
}

function resultSummary(state: JobState): JobSummary["result"] {
  const result = state.result;
  switch (result?.kind) {
    case undefined:
      return null;
    case "done":
      return withDefined({ kind: "done" as const, written: result.written, checked: result.checked, matched: result.matched });
    case "stopped":
      return { kind: "stopped" };
    case "failed":
      return withDefined({ kind: "failed" as const, reason: result.reason, stepId: result.stepId, message: result.message });
    case "blocked":
      return { kind: "blocked", resource: result.resource, auditId: result.auditId };
  }
}

function procedureSummary(state: JobState): JobSummary["savedProcedure"] {
  const procedure = state.procedure;
  if (!procedure) return null;
  return { procedureId: procedure.procedureId, version: procedure.version, stepCount: procedure.stepCount, expectedReplayMs: procedure.expectedReplayMs };
}

export function jobSummary(jobId: string, job: JobRead): JobSummary {
  if (job.events.length === 0) return endedWithoutEvents(jobId, job.status);
  const state = buildJobState(job.events);
  return {
    jobId,
    clientId: state.clientId,
    clientName: state.clientName,
    title: state.title,
    run: state.run,
    status: state.status,
    progress: state.progress,
    steps: state.steps.map(stepSummary),
    claudeDecisions: state.claudeDecisions,
    pendingApproval: pendingApprovalSummary(state, job.events),
    result: resultSummary(state),
    savedProcedure: procedureSummary(state),
    startedAt: state.startedAt,
    updatedAt: state.updatedAt,
  };
}

export function waitingApproval(jobId: string, job: JobRead): WaitingApproval | null {
  if (job.status !== "waiting") return null;
  const summary = jobSummary(jobId, job);
  const { clientId, title, pendingApproval } = summary;
  if (clientId === null || title === null || pendingApproval === null) return null;
  return {
    jobId,
    clientId,
    title,
    question: pendingApproval.question,
    changeCount: pendingApproval.changeCount,
    soldOutCount: pendingApproval.soldOutCount,
    askedAt: pendingApproval.askedAt,
  };
}

export function savedProcedureSummary(record: unknown, clientId: string): SavedProcedureSummary | null {
  const parsed = storedProcedureSchema.safeParse(record);
  if (!parsed.success) return null;
  const stored = parsed.data;
  if (stored.clientId !== clientId || stored.procedureId !== procedureIdFor(clientId)) return null;
  return {
    procedureId: stored.procedureId,
    version: stored.procedureVersion,
    stepCount: stored.steps.length,
    expectedReplayMs: stored.expectedReplayMs,
    savedAt: stored.savedAt,
  };
}
