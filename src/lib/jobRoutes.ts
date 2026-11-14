import { createJob, type JobStarter, type KnownClient } from "./jobs";
import { streamJob, toSse, type SnapshotReader } from "./jobStream";

export async function handleCreateJob(
  request: Request,
  deps: { start: JobStarter; findClient: (clientId: string) => KnownClient | null },
) {
  const raw: unknown = await request.json().catch(() => null);
  const reply = await createJob(raw, deps);
  return Response.json(reply.body, { status: reply.status });
}

function resumeAfter(request: Request) {
  const lastEventId = Number(request.headers.get("last-event-id") ?? "0");
  return Number.isInteger(lastEventId) && lastEventId > 0 ? lastEventId : 0;
}

export function handleJobEvents(
  request: Request,
  jobId: string,
  deps: { read: SnapshotReader; sleep?: (milliseconds: number) => Promise<void> },
) {
  const frames = streamJob({
    executionId: jobId,
    read: deps.read,
    sleep: deps.sleep,
    afterSeq: resumeAfter(request),
    signal: request.signal,
    onRejected: (problems) => console.warn(`Job ${jobId}: ${problems.join(" | ")}`),
  });
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { value, done } = await frames.next();
      if (done) controller.close();
      else controller.enqueue(encoder.encode(toSse(value)));
    },
    async cancel() {
      await frames.return(undefined);
    },
  });
  return new Response(body, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
