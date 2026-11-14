import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/stub_job_notes.json";
import { eventNoteTag, type ExecutionSnapshot } from "./agentfieldRecords";
import { handleCreateJob, handleJobEvents } from "./jobRoutes";

const notes = contract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794636000 + index }));

function readerOf(snapshots: ExecutionSnapshot[]) {
  let reads = 0;
  return async () => snapshots[Math.min(reads++, snapshots.length - 1)];
}

function frames(text: string) {
  return text
    .trim()
    .split("\n\n")
    .map((block) => Object.fromEntries(block.split("\n").map((line) => [line.slice(0, line.indexOf(":")), line.slice(line.indexOf(":") + 2)])));
}

describe("handleCreateJob", () => {
  test("answers 201 with the job id for a known client", async () => {
    const response = await handleCreateJob(
      new Request("http://app.test/api/jobs", { method: "POST", body: JSON.stringify({ clientId: "kumo", request: "Check the sites." }) }),
      {
        start: async () => ({ executionId: "exec_1", runId: "run_1" }),
        findClient: (id) => (id === "kumo" ? { id, name: "Kumo Coffee Roasters" } : null),
      },
    );
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ jobId: "exec_1", runId: "run_1" });
  });

  test("answers 400 to a body that is not JSON", async () => {
    const response = await handleCreateJob(new Request("http://app.test/api/jobs", { method: "POST", body: "{" }), {
      start: async () => ({ executionId: "exec_1", runId: "run_1" }),
      findClient: () => null,
    });
    expect(response.status).toBe(400);
  });
});

describe("handleJobEvents", () => {
  test("streams the job as server-sent events and ends", async () => {
    const response = handleJobEvents(new Request("http://app.test/api/jobs/exec_1/events"), "exec_1", {
      read: readerOf([
        { executionId: "exec_1", clientId: "kumo", status: "running", notes: notes.slice(0, 4) },
        { executionId: "exec_1", clientId: "kumo", status: "succeeded", notes },
      ]),
      sleep: async () => {},
    });

    expect(response.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");
    const received = frames(await response.text());
    expect(received.filter((frame) => frame.event === "job").map((frame) => Number(frame.id))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(received.at(-1)).toEqual({ event: "end", data: '{"status":"succeeded"}' });
  });

  test("picks up after the Last-Event-ID the browser sends when it reconnects", async () => {
    const response = handleJobEvents(new Request("http://app.test/api/jobs/exec_1/events", { headers: { "Last-Event-ID": "8" } }), "exec_1", {
      read: readerOf([{ executionId: "exec_1", clientId: "kumo", status: "succeeded", notes }]),
      sleep: async () => {},
    });
    const ids = frames(await response.text()).flatMap((frame) => (frame.event === "job" ? [Number(frame.id)] : []));
    expect(ids).toEqual([9, 10]);
  });

  test("ignores a Last-Event-ID that is not a positive whole number", async () => {
    const response = handleJobEvents(new Request("http://app.test/api/jobs/exec_1/events", { headers: { "Last-Event-ID": "-3" } }), "exec_1", {
      read: readerOf([{ executionId: "exec_1", clientId: "kumo", status: "succeeded", notes: notes.slice(0, 1) }]),
      sleep: async () => {},
    });
    expect(frames(await response.text())[0].id).toBe("1");
  });
});
