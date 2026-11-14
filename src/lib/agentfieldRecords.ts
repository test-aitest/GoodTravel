import { safeParseJobEvent, type JobEvent } from "./events";

export const eventNoteTag = "clientops.event";

export const executionStatuses = [
  "pending",
  "queued",
  "waiting",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "timeout",
  "unknown",
] as const;

export type ExecutionStatus = (typeof executionStatuses)[number];

const statusAliases: Record<string, ExecutionStatus> = {
  success: "succeeded",
  successful: "succeeded",
  completed: "succeeded",
  complete: "succeeded",
  done: "succeeded",
  ok: "succeeded",
  error: "failed",
  failure: "failed",
  errored: "failed",
  canceled: "cancelled",
  cancel: "cancelled",
  timed_out: "timeout",
  wait: "queued",
  awaiting_approval: "waiting",
  awaiting_human: "waiting",
  approval_pending: "waiting",
  in_progress: "running",
  processing: "running",
};

const terminalStatuses = new Set<ExecutionStatus>(["succeeded", "failed", "cancelled", "timeout"]);

export type ExecutionNote = { message: string; tags: readonly string[]; timestamp: number | string };

export type ExecutionSnapshot = {
  executionId: string;
  clientId: string;
  status: ExecutionStatus;
  notes: readonly ExecutionNote[];
  completedAt?: string;
  expectedEvents?: number;
};

export type Conversion = { events: JobEvent[]; rejected: string[]; lastSeq: number; finished: boolean };

type Payload = { n: number; event: Record<string, unknown>; at: string };

export function normalizeStatus(raw: unknown): ExecutionStatus {
  if (typeof raw !== "string") return "unknown";
  const status = raw.trim().toLowerCase();
  if ((executionStatuses as readonly string[]).includes(status)) return status as ExecutionStatus;
  return statusAliases[status] ?? "unknown";
}

export function isTerminal(status: ExecutionStatus) {
  return terminalStatuses.has(status);
}

function noteTime(timestamp: number | string) {
  const milliseconds =
    typeof timestamp === "number"
      ? timestamp < 1e12
        ? timestamp * 1000
        : timestamp
      : Date.parse(timestamp.replace(/(\.\d{3})\d+/, "$1"));
  return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : null;
}

function readPayloads(notes: readonly ExecutionNote[], rejected: string[]) {
  const payloads = new Map<number, Payload>();
  for (const note of notes) {
    if (!note.tags.includes(eventNoteTag)) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(note.message);
    } catch {
      rejected.push("A worker note is not valid JSON");
      continue;
    }
    const candidate = parsed as { n?: unknown; event?: unknown };
    if (!Number.isInteger(candidate.n) || (candidate.n as number) < 1 || typeof candidate.event !== "object" || candidate.event === null) {
      rejected.push("A worker note has no sequence number or event");
      continue;
    }
    const n = candidate.n as number;
    const at = noteTime(note.timestamp);
    if (!at) {
      rejected.push(`Worker note ${n} has a time that cannot be read`);
      continue;
    }
    if (!payloads.has(n)) payloads.set(n, { n, event: candidate.event as Record<string, unknown>, at });
  }
  return [...payloads.values()].toSorted((a, b) => a.n - b.n);
}

function endedWithoutReason(snapshot: ExecutionSnapshot, payloads: readonly Payload[]) {
  if (snapshot.status === "succeeded" || !isTerminal(snapshot.status)) return false;
  return !payloads.some((payload) => payload.event.type === "job.failed" || payload.event.type === "boundary.blocked");
}

function hasEveryNote(payloads: readonly Payload[], expected: number | undefined) {
  if (expected === undefined) return true;
  const seen = new Set(payloads.map((payload) => payload.n));
  for (let n = 1; n <= expected; n += 1) if (!seen.has(n)) return false;
  return true;
}

export function newEvents(snapshot: ExecutionSnapshot, afterSeq: number, options: { flush?: boolean } = {}): Conversion {
  const rejected: string[] = [];
  const payloads = readPayloads(snapshot.notes, rejected);
  const terminal = isTerminal(snapshot.status) && (options.flush === true || hasEveryNote(payloads, snapshot.expectedEvents));
  const envelope = (n: number, at: string) => ({
    id: `${snapshot.executionId}-${n}`,
    seq: n,
    jobId: snapshot.executionId,
    clientId: snapshot.clientId,
    at,
  });

  const events: JobEvent[] = [];
  let lastSeq = afterSeq;
  for (const payload of payloads) {
    if (payload.n <= lastSeq) continue;
    if (!terminal && payload.n !== lastSeq + 1) break;
    const parsed = safeParseJobEvent({ ...payload.event, ...envelope(payload.n, payload.at) });
    if (parsed.success) events.push(parsed.data);
    else rejected.push(`Worker note ${payload.n} is not a valid event: ${parsed.error.issues.map((issue) => issue.message).join("; ")}`);
    lastSeq = payload.n;
  }

  const closingSeq = (payloads.at(-1)?.n ?? 0) + 1;
  if (terminal && afterSeq < closingSeq && endedWithoutReason(snapshot, payloads)) {
    const n = closingSeq;
    const at = snapshot.completedAt ? new Date(snapshot.completedAt).toISOString() : (payloads.at(-1)?.at ?? new Date(0).toISOString());
    const parsed = safeParseJobEvent({
      ...envelope(n, at),
      type: "job.failed",
      reason: snapshot.status === "cancelled" ? "stopped" : "error",
      message: "The worker stopped before finishing.",
    });
    if (parsed.success) events.push(parsed.data);
    lastSeq = n;
  }

  return { events, rejected, lastSeq, finished: terminal };
}
