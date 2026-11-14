import { describe, expect, test } from "bun:test";
import { demoJobs } from "./demoJobs";
import { parseJobEvent } from "./events";
import { buildJobState } from "./jobState";
import { delaysBetween, splitAtApproval, stopEvent } from "./playback";

describe("splitAtApproval", () => {
  test("plays up to and including the approval request, keeps the rest for after the OK", () => {
    const { untilApproval, afterApproval } = splitAtApproval(demoJobs.firstRun.events);
    expect(untilApproval.at(-1)?.type).toBe("approval.requested");
    expect(afterApproval[0]?.type).toBe("approval.answered");
    expect(untilApproval.length + afterApproval.length).toBe(demoJobs.firstRun.events.length);
  });

  test("a job without an approval plays everything at once", () => {
    const { untilApproval, afterApproval } = splitAtApproval(demoJobs.boundary.events);
    expect(untilApproval).toHaveLength(demoJobs.boundary.events.length);
    expect(afterApproval).toHaveLength(0);
  });
});

describe("delaysBetween", () => {
  test("waits the recorded time between events, divided by the speed", () => {
    const events = demoJobs.firstRun.events.slice(0, 3);
    expect(delaysBetween(events, 1)).toEqual([0, 300, 2200]);
    expect(delaysBetween(events, 2)).toEqual([0, 150, 1100]);
  });

  test("starts from a given time so playback can resume after the approval", () => {
    const { untilApproval, afterApproval } = splitAtApproval(demoJobs.firstRun.events);
    const resumeAt = untilApproval.at(-1)?.at ?? "";
    expect(delaysBetween(afterApproval.slice(0, 1), 1, resumeAt)).toEqual([11400]);
  });
});

describe("stopEvent", () => {
  test("ends a running job as stopped at the active step with the next sequence number", () => {
    const state = buildJobState(demoJobs.firstRun.events.slice(0, 8));
    const event = stopEvent(state, "2026-11-14T08:40:20.000Z");
    expect(parseJobEvent(event)).toEqual(event);
    expect(event).toMatchObject({ type: "job.failed", reason: "stopped", stepId: "compare", seq: state.lastSeq + 1, jobId: state.jobId });
    expect(buildJobState([...demoJobs.firstRun.events.slice(0, 8), event]).status).toBe("stopped");
  });

  test("stops a job that is waiting for approval at the ask step", () => {
    const { untilApproval } = splitAtApproval(demoJobs.firstRun.events);
    const state = buildJobState(untilApproval);
    expect(stopEvent(state, "2026-11-14T08:41:00.000Z").stepId).toBe("ask");
  });
});
