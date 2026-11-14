import { safeParseJobEvent, type JobEvent } from "@/lib/events";
import { applyEvent, type JobState } from "@/lib/jobState";

export type LiveFrame = { kind: "event"; event: JobEvent } | { kind: "end" } | { kind: "missing" } | { kind: "unavailable" } | { kind: "broken" };

export type LiveJob = {
  phase: "idle" | "starting" | "running" | "ended" | "failed";
  request: string | null;
  jobId: string | null;
  job: JobState | null;
  message: string | null;
};

export type LiveJobAction =
  | { type: "starting"; request: string }
  | { type: "started"; jobId: string }
  | { type: "frame"; frame: LiveFrame }
  | { type: "failed"; message: string };

export const initialLiveJob: LiveJob = { phase: "idle", request: null, jobId: null, job: null, message: null };

export function readFrame(type: string, data: string): LiveFrame {
  if (type === "end") return { kind: "end" };
  if (type === "missing") return { kind: "missing" };
  if (type === "unavailable") return { kind: "unavailable" };
  try {
    const parsed = safeParseJobEvent(JSON.parse(data));
    return parsed.success ? { kind: "event", event: parsed.data } : { kind: "broken" };
  } catch {
    return { kind: "broken" };
  }
}

function applyFrame(state: LiveJob, frame: LiveFrame): LiveJob {
  switch (frame.kind) {
    case "event":
      return { ...state, job: applyEvent(state.job, frame.event) };
    case "end":
      return { ...state, phase: "ended" };
    case "missing":
      return { ...state, phase: "failed", message: "This job could not be found." };
    case "unavailable":
      return { ...state, phase: "failed", message: "The worker service stopped answering." };
    case "broken":
      return state;
  }
}

export function liveJobReducer(state: LiveJob, action: LiveJobAction): LiveJob {
  switch (action.type) {
    case "starting":
      return { ...initialLiveJob, phase: "starting", request: action.request };
    case "started":
      return { ...state, phase: "running", jobId: action.jobId };
    case "frame":
      return applyFrame(state, action.frame);
    case "failed":
      return { ...state, phase: "failed", message: action.message };
  }
}
