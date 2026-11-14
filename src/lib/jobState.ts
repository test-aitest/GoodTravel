import type { JobEvent, JobEventBody, JobEventOf, Rung, Run, StockChange } from "./events";

export type JobStatus = "running" | "waiting" | "done" | "stopped" | "failed" | "blocked";
export type StepStatus = "pending" | "active" | "done" | "stopped" | "failed";

export type StepState = {
  stepId: string;
  title: string;
  status: StepStatus;
  activity?: string;
  detail?: string;
  summary?: string;
  decidedBy?: Rung;
  decidedInMs?: number;
  escalatedFrom?: Rung[];
  tookMs?: number;
};

export type PendingApproval = Omit<JobEventBody<"approval.requested">, "changes"> & { changes: StockChange[] };
export type ApprovalAnswer = JobEventBody<"approval.answered">;
export type PendingChoice = JobEventBody<"choice.requested">;
export type ChoiceAnswer = JobEventBody<"choice.answered">;
export type SavedProcedure = JobEventBody<"procedure.saved">;
export type BlockedResource = JobEventBody<"boundary.blocked">;
export type SeenPage = JobEventBody<"page.seen">;
export type JobCost = JobEventBody<"cost.recorded">;
export type IgnoredInstruction = JobEventBody<"instruction.ignored">;
export type TripOptions = JobEventBody<"trip.options">;
export type TripReview = JobEventBody<"trip.review">;
export type TripBooked = JobEventBody<"trip.booked">;

export type JobResult =
  | { kind: "done"; written?: number; checked?: number; matched?: number }
  | { kind: "stopped"; reason: "stopped" }
  | { kind: "failed"; reason: "error" | "verify-mismatch"; stepId?: string; message?: string }
  | ({ kind: "blocked" } & BlockedResource);

export type JobState = {
  jobId: string;
  clientId: string;
  clientName: string;
  title: string;
  request: string;
  run: Run;
  status: JobStatus;
  steps: readonly StepState[];
  stepIndex: Readonly<Record<string, number>>;
  doneCount: number;
  progress: number;
  activeStepId: string | null;
  pendingApproval: PendingApproval | null;
  lastAnswer: ApprovalAnswer | null;
  pendingChoice: PendingChoice | null;
  lastChoice: ChoiceAnswer | null;
  waitedForYouMs: number;
  choiceAskedAt: string | null;
  claudeDecisions: number;
  startedAt: string;
  updatedAt: string;
  elapsedMs: number;
  readyInMs: number | null;
  written: number | null;
  checked: number | null;
  matched: number | null;
  procedure: SavedProcedure | null;
  page: SeenPage | null;
  ignoredInstructions: readonly IgnoredInstruction[];
  cost: JobCost | null;
  tripOptions: TripOptions | null;
  tripReview: TripReview | null;
  tripBooked: TripBooked | null;
  result: JobResult | null;
  lastSeq: number;
};

function bodyOf<T extends JobEvent>(event: T) {
  const { id: _id, seq: _seq, jobId: _jobId, clientId: _clientId, at: _at, type: _type, ...body } = event;
  return body;
}

function withDefined<T extends object>(value: T) {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== undefined)) as T;
}

function createJob(event: JobEventOf<"job.created">): JobState {
  return {
    jobId: event.jobId,
    clientId: event.clientId,
    clientName: event.clientName,
    title: event.title,
    request: event.request,
    run: event.run,
    status: "running",
    steps: event.plan.map((step) => ({ stepId: step.stepId, title: step.title, status: "pending" })),
    stepIndex: Object.fromEntries(event.plan.map((step, index) => [step.stepId, index])),
    doneCount: 0,
    progress: 0,
    activeStepId: null,
    pendingApproval: null,
    lastAnswer: null,
    pendingChoice: null,
    lastChoice: null,
    waitedForYouMs: 0,
    choiceAskedAt: null,
    claudeDecisions: 0,
    startedAt: event.at,
    updatedAt: event.at,
    elapsedMs: 0,
    readyInMs: null,
    written: null,
    checked: null,
    matched: null,
    procedure: null,
    page: null,
    ignoredInstructions: [],
    cost: null,
    tripOptions: null,
    tripReview: null,
    tripBooked: null,
    result: null,
    lastSeq: event.seq,
  };
}

function indexOfStep(state: JobState, stepId: string) {
  const index = state.stepIndex[stepId];
  if (index === undefined) throw new Error(`Job ${state.jobId} has no step ${stepId}`);
  return index;
}

function updateStep(state: JobState, stepId: string, change: (step: StepState) => StepState) {
  const index = indexOfStep(state, stepId);
  const steps = state.steps.slice();
  steps[index] = change(steps[index]);
  return steps;
}

function progressOf(doneCount: number, total: number) {
  return total === 0 ? 0 : Math.round((doneCount / total) * 100);
}

function endJob(state: JobState, status: JobStatus, result: JobResult, stepId: string | null, stepStatus: StepStatus): JobState {
  return {
    ...state,
    status,
    result,
    steps: stepId === null ? state.steps : updateStep(state, stepId, (step) => ({ ...step, status: stepStatus })),
    activeStepId: null,
    pendingApproval: null,
    pendingChoice: null,
  };
}

export function applyEvent(state: JobState | null, event: JobEvent): JobState {
  if (state === null) {
    if (event.type !== "job.created") throw new Error(`The first event of a job must be job.created, got ${event.type}`);
    return createJob(event);
  }
  if (event.jobId !== state.jobId) throw new Error(`Event for ${event.jobId} cannot change job ${state.jobId}`);
  if (event.seq <= state.lastSeq) return state;

  const next: JobState = {
    ...state,
    lastSeq: event.seq,
    updatedAt: event.at,
    elapsedMs: Date.parse(event.at) - Date.parse(state.startedAt),
  };

  switch (event.type) {
    case "job.created":
      throw new Error(`Job ${state.jobId} was already created`);

    case "step.started":
      return {
        ...next,
        activeStepId: event.stepId,
        steps: updateStep(next, event.stepId, ({ detail: _detail, ...step }) =>
          withDefined({ ...step, status: "active" as const, activity: event.activity, detail: event.detail }),
        ),
      };

    case "step.decided":
      return {
        ...next,
        claudeDecisions: next.claudeDecisions + (event.rung === "claude" ? 1 : 0),
        steps: updateStep(next, event.stepId, (step) =>
          withDefined({ ...step, decidedBy: event.rung, decidedInMs: event.tookMs, escalatedFrom: event.escalatedFrom }),
        ),
      };

    case "step.done": {
      const index = indexOfStep(next, event.stepId);
      const doneCount = next.doneCount + (next.steps[index].status === "done" ? 0 : 1);
      const steps = updateStep(next, event.stepId, ({ detail: _detail, ...step }) =>
        withDefined({ ...step, status: "done" as const, summary: event.summary, detail: event.detail, tookMs: event.tookMs }),
      );
      const finished = doneCount === steps.length && next.result === null;
      return {
        ...next,
        steps,
        doneCount,
        progress: progressOf(doneCount, steps.length),
        activeStepId: next.activeStepId === event.stepId ? null : next.activeStepId,
        status: finished ? "done" : next.status,
        result: finished
          ? withDefined({ kind: "done" as const, written: next.written ?? undefined, checked: next.checked ?? undefined, matched: next.matched ?? undefined })
          : next.result,
      };
    }

    case "approval.requested":
      indexOfStep(next, event.stepId);
      return {
        ...next,
        status: "waiting",
        pendingApproval: withDefined({ ...bodyOf(event), changes: event.changes ?? [] }),
        readyInMs: next.readyInMs ?? next.elapsedMs - next.waitedForYouMs,
      };

    case "approval.answered": {
      if (next.pendingApproval?.approvalId !== event.approvalId) {
        throw new Error(`Job ${next.jobId} is not waiting on approval ${event.approvalId}`);
      }
      return { ...next, lastAnswer: withDefined(bodyOf(event)), pendingApproval: null, status: "running" };
    }

    case "choice.requested":
      indexOfStep(next, event.stepId);
      return { ...next, status: "waiting", pendingChoice: withDefined(bodyOf(event)), choiceAskedAt: event.at };

    case "choice.answered": {
      if (next.pendingChoice?.choiceId !== event.choiceId) {
        throw new Error(`Job ${next.jobId} is not waiting on choice ${event.choiceId}`);
      }
      const waited = next.choiceAskedAt === null ? 0 : Math.max(0, Date.parse(event.at) - Date.parse(next.choiceAskedAt));
      return { ...next, lastChoice: bodyOf(event), pendingChoice: null, choiceAskedAt: null, status: "running", waitedForYouMs: next.waitedForYouMs + waited };
    }

    case "instruction.ignored":
      indexOfStep(next, event.stepId);
      if (next.ignoredInstructions.some((seen) => seen.host === event.host)) return next;
      return { ...next, ignoredInstructions: [...next.ignoredInstructions, bodyOf(event)] };

    case "write.done":
      indexOfStep(next, event.stepId);
      return { ...next, written: event.written };

    case "verify.done": {
      indexOfStep(next, event.stepId);
      const verified = { ...next, checked: event.checked, matched: event.matched };
      if (event.matched === event.checked) return verified;
      return endJob(verified, "failed", { kind: "failed", reason: "verify-mismatch", stepId: event.stepId }, event.stepId, "failed");
    }

    case "procedure.saved":
      return { ...next, procedure: bodyOf(event) };

    case "job.failed": {
      if (event.reason === "stopped") {
        return endJob(next, "stopped", { kind: "stopped", reason: "stopped" }, next.activeStepId, "stopped");
      }
      const stepId = event.stepId ?? next.activeStepId;
      return endJob(
        next,
        "failed",
        withDefined({ kind: "failed" as const, reason: event.reason, message: event.message, stepId: event.stepId }),
        stepId,
        "failed",
      );
    }

    case "boundary.blocked":
      return endJob(next, "blocked", { kind: "blocked", ...bodyOf(event) }, next.activeStepId, "stopped");

    case "page.seen":
      indexOfStep(next, event.stepId);
      return { ...next, page: bodyOf(event) };

    case "cost.recorded":
      return { ...next, cost: bodyOf(event) };

    case "trip.options":
      return { ...next, tripOptions: bodyOf(event) };

    case "trip.review":
      return { ...next, tripReview: bodyOf(event) };

    case "trip.booked":
      return { ...next, tripBooked: bodyOf(event) };
  }
}

export function buildJobState(events: readonly JobEvent[]) {
  const state = events.reduce<JobState | null>(applyEvent, null);
  if (state === null) throw new Error("A job needs at least its job.created event");
  return state;
}
