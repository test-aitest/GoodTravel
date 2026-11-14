import { parseJobEvent, type JobEvent, type JobEventOf } from "./events";
import type { JobState } from "./jobState";

export function splitAtApproval(events: readonly JobEvent[]) {
  const index = events.findIndex((event) => event.type === "approval.requested");
  if (index === -1) return { untilApproval: events, afterApproval: [] as readonly JobEvent[] };
  return { untilApproval: events.slice(0, index + 1), afterApproval: events.slice(index + 1) };
}

export function delaysBetween(events: readonly JobEvent[], speed: number, previousAt?: string) {
  let previous = previousAt === undefined ? undefined : Date.parse(previousAt);
  return events.map((event) => {
    const now = Date.parse(event.at);
    const delay = previous === undefined ? 0 : Math.max(0, now - previous) / speed;
    previous = now;
    return delay;
  });
}

export function stopEvent(state: JobState, at: string): JobEventOf<"job.failed"> {
  const stepId = state.activeStepId ?? state.pendingApproval?.stepId;
  return parseJobEvent({
    id: `${state.jobId}-${state.lastSeq + 1}`,
    seq: state.lastSeq + 1,
    jobId: state.jobId,
    clientId: state.clientId,
    at,
    type: "job.failed",
    reason: "stopped",
    message: "Stopped by you",
    ...(stepId ? { stepId } : {}),
  }) as JobEventOf<"job.failed">;
}
