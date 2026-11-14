import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/stub_job_notes.json";
import { eventNoteTag, type ExecutionSnapshot } from "./agentfieldRecords";
import { streamJob, toSse, type StreamFrame } from "./jobStream";

const notes = contract.notes.map((payload, index) => ({
  message: JSON.stringify(payload),
  tags: [eventNoteTag],
  timestamp: 1794636000 + index,
}));

function snapshot(count: number, status: ExecutionSnapshot["status"]): ExecutionSnapshot {
  return { executionId: "exec_1", clientId: "kumo", status, notes: notes.slice(0, count) };
}

function readerOf(answers: Array<ExecutionSnapshot | null | Error>) {
  let reads = 0;
  const read = async () => {
    const answer = answers[Math.min(reads, answers.length - 1)];
    reads += 1;
    if (answer instanceof Error) throw answer;
    return answer;
  };
  return { read, reads: () => reads };
}

async function collect(stream: AsyncGenerator<StreamFrame>) {
  const frames: StreamFrame[] = [];
  for await (const frame of stream) frames.push(frame);
  return frames;
}

const noWait = async () => {};

describe("streamJob", () => {
  test("sends each event once, only when it is new, and ends when the job is finished", async () => {
    const reader = readerOf([snapshot(2, "running"), snapshot(2, "running"), snapshot(6, "running"), snapshot(10, "succeeded")]);

    const frames = await collect(streamJob({ executionId: "exec_1", read: reader.read, sleep: noWait }));

    const seqs = frames.flatMap((frame) => (frame.kind === "event" ? [frame.event.seq] : []));
    expect(seqs).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(frames.at(-1)).toEqual({ kind: "end", status: "succeeded" });
    expect(reader.reads()).toBe(4);
  });

  test("resumes after the last event the browser already has", async () => {
    const reader = readerOf([snapshot(10, "succeeded")]);
    const frames = await collect(streamJob({ executionId: "exec_1", read: reader.read, afterSeq: 7, sleep: noWait }));
    expect(frames.flatMap((frame) => (frame.kind === "event" ? [frame.event.seq] : []))).toEqual([8, 9, 10]);
  });

  test("says when the job does not exist", async () => {
    const frames = await collect(streamJob({ executionId: "exec_x", read: readerOf([null]).read, sleep: noWait }));
    expect(frames).toEqual([{ kind: "missing" }]);
  });

  test("rides out short outages and gives up after too many in a row", async () => {
    const outage = new Error("down");
    const recovered = await collect(
      streamJob({ executionId: "exec_1", read: readerOf([outage, outage, snapshot(10, "succeeded")]).read, sleep: noWait, maxFailures: 3 }),
    );
    expect(recovered.at(-1)).toEqual({ kind: "end", status: "succeeded" });

    const gone = await collect(streamJob({ executionId: "exec_1", read: readerOf([outage]).read, sleep: noWait, maxFailures: 3 }));
    expect(gone).toEqual([{ kind: "unavailable" }]);
  });

  test("stops reading when the browser leaves", async () => {
    const leave = new AbortController();
    const reader = readerOf([snapshot(2, "running")]);
    const frames: StreamFrame[] = [];
    for await (const frame of streamJob({ executionId: "exec_1", read: reader.read, sleep: noWait, signal: leave.signal })) {
      frames.push(frame);
      if (frames.length === 2) leave.abort();
    }
    expect(frames).toHaveLength(2);
    expect(reader.reads()).toBe(1);
  });
});

describe("toSse", () => {
  test("writes events with their sequence number as the id so the browser can resume", async () => {
    const [first] = await collect(streamJob({ executionId: "exec_1", read: readerOf([snapshot(1, "succeeded")]).read, sleep: noWait }));
    const text = toSse(first);
    expect(text.startsWith("id: 1\nevent: job\ndata: ")).toBe(true);
    expect(text.endsWith("\n\n")).toBe(true);
    expect(JSON.parse(text.split("data: ")[1])).toMatchObject({ seq: 1, type: "job.created" });
  });

  test("writes the end, missing and unavailable frames as named events", () => {
    expect(toSse({ kind: "end", status: "succeeded" })).toBe('event: end\ndata: {"status":"succeeded"}\n\n');
    expect(toSse({ kind: "missing" })).toBe("event: missing\ndata: {}\n\n");
    expect(toSse({ kind: "unavailable" })).toBe("event: unavailable\ndata: {}\n\n");
  });
});

describe("streamJob when notes trail the end of the job", () => {
  test("keeps reading until the last note arrives", async () => {
    const reader = readerOf([
      { ...snapshot(7, "succeeded"), expectedEvents: 10 },
      { ...snapshot(10, "succeeded"), expectedEvents: 10 },
    ]);
    const frames = await collect(streamJob({ executionId: "exec_1", read: reader.read, sleep: noWait }));
    expect(frames.flatMap((frame) => (frame.kind === "event" ? [frame.event.seq] : []))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(frames.at(-1)).toEqual({ kind: "end", status: "succeeded" });
  });

  test("ends with what it has after a few reads if a note never comes", async () => {
    const reader = readerOf([{ ...snapshot(7, "succeeded"), expectedEvents: 10 }]);
    const frames = await collect(streamJob({ executionId: "exec_1", read: reader.read, sleep: noWait, settleReads: 3 }));
    expect(frames.flatMap((frame) => (frame.kind === "event" ? [frame.event.seq] : []))).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(frames.at(-1)).toEqual({ kind: "end", status: "succeeded" });
    expect(reader.reads()).toBe(4);
  });
});
