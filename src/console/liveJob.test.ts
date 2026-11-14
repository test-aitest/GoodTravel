import { describe, expect, test } from "bun:test";
import { demoJobs } from "@/lib/demoJobs";
import { initialLiveJob, liveJobReducer, readFrame } from "./liveJob";

const [created, started] = demoJobs.firstRun.events;

describe("readFrame", () => {
  test("turns a job frame into a checked event", () => {
    expect(readFrame("job", JSON.stringify(created))).toEqual({ kind: "event", event: created });
  });

  test("rejects a job frame that is not a valid event", () => {
    expect(readFrame("job", JSON.stringify({ type: "step.started" }))).toEqual({ kind: "broken" });
    expect(readFrame("job", "not json")).toEqual({ kind: "broken" });
  });

  test("reads the end, missing and unavailable frames", () => {
    expect(readFrame("end", JSON.stringify({ status: "done" }))).toEqual({ kind: "end" });
    expect(readFrame("missing", "{}")).toEqual({ kind: "missing" });
    expect(readFrame("unavailable", "{}")).toEqual({ kind: "unavailable" });
  });
});

describe("liveJobReducer", () => {
  test("starts, applies events once each, and ends", () => {
    const starting = liveJobReducer(initialLiveJob, { type: "starting", request: "Check the connection" });
    expect(starting.phase).toBe("starting");
    const running = liveJobReducer(liveJobReducer(starting, { type: "started", jobId: "exec-1" }), { type: "frame", frame: { kind: "event", event: created } });
    const twice = liveJobReducer(running, { type: "frame", frame: { kind: "event", event: started } });
    const repeated = liveJobReducer(twice, { type: "frame", frame: { kind: "event", event: started } });
    expect(repeated.job).toBe(twice.job);
    expect(repeated.job?.activeStepId).toBe("sign-in");
    expect(liveJobReducer(repeated, { type: "frame", frame: { kind: "end" } }).phase).toBe("ended");
  });

  test("explains when the worker service cannot be reached", () => {
    const failed = liveJobReducer(initialLiveJob, { type: "failed", message: "The worker service is not available right now." });
    expect(failed).toMatchObject({ phase: "failed", message: "The worker service is not available right now." });
    const lost = liveJobReducer(liveJobReducer(initialLiveJob, { type: "started", jobId: "exec-1" }), { type: "frame", frame: { kind: "unavailable" } });
    expect(lost).toMatchObject({ phase: "failed", message: "The worker service stopped answering." });
  });
});
