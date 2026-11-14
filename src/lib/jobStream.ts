import type { JobEvent } from "./events";
import { isTerminal, newEvents, type ExecutionSnapshot, type ExecutionStatus } from "./agentfieldRecords";

export type StreamFrame =
  | { kind: "event"; event: JobEvent }
  | { kind: "end"; status: ExecutionStatus }
  | { kind: "missing" }
  | { kind: "unavailable" };

export type SnapshotReader = (executionId: string) => Promise<ExecutionSnapshot | null>;

const wait = (milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

export async function* streamJob(options: {
  executionId: string;
  read: SnapshotReader;
  afterSeq?: number;
  intervalMs?: number;
  maxFailures?: number;
  settleReads?: number;
  sleep?: (milliseconds: number) => Promise<void>;
  signal?: AbortSignal;
  onRejected?: (problems: string[]) => void;
}): AsyncGenerator<StreamFrame> {
  const sleep = options.sleep ?? wait;
  const intervalMs = options.intervalMs ?? 400;
  const maxFailures = options.maxFailures ?? 20;
  const settleReads = options.settleReads ?? 10;
  let afterSeq = options.afterSeq ?? 0;
  let failures = 0;
  let unsettledReads = 0;

  while (!options.signal?.aborted) {
    let snapshot: ExecutionSnapshot | null;
    try {
      snapshot = await options.read(options.executionId);
      failures = 0;
    } catch {
      failures += 1;
      if (failures >= maxFailures) {
        yield { kind: "unavailable" };
        return;
      }
      await sleep(intervalMs * Math.min(failures, 5));
      continue;
    }
    if (!snapshot) {
      yield { kind: "missing" };
      return;
    }

    const conversion = newEvents(snapshot, afterSeq, { flush: unsettledReads >= settleReads });
    if (conversion.rejected.length > 0) options.onRejected?.(conversion.rejected);
    afterSeq = conversion.lastSeq;
    for (const event of conversion.events) {
      if (options.signal?.aborted) return;
      yield { kind: "event", event };
    }
    if (conversion.finished) {
      yield { kind: "end", status: snapshot.status };
      return;
    }
    if (isTerminal(snapshot.status)) unsettledReads += 1;
    await sleep(intervalMs);
  }
}

export function toSse(frame: StreamFrame) {
  switch (frame.kind) {
    case "event":
      return `id: ${frame.event.seq}\nevent: job\ndata: ${JSON.stringify(frame.event)}\n\n`;
    case "end":
      return `event: end\ndata: ${JSON.stringify({ status: frame.status })}\n\n`;
    case "missing":
      return "event: missing\ndata: {}\n\n";
    case "unavailable":
      return "event: unavailable\ndata: {}\n\n";
  }
}
