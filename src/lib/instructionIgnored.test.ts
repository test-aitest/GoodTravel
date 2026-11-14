import { describe, expect, test } from "bun:test";
import { demoJobs } from "./demoJobs";
import { parseJobEvent, type JobEvent } from "./events";
import { buildJobState } from "./jobState";

function ignored(after: JobEvent, seq: number, host: string): JobEvent {
  return parseJobEvent({ id: `${after.jobId}-${seq}`, seq, jobId: after.jobId, clientId: after.clientId, at: after.at, type: "instruction.ignored", stepId: "read", host });
}

describe("an instruction for AI found on a page", () => {
  const events = demoJobs.firstRun.events.slice(0, 6);
  const last = events.at(-1)!;

  test("is kept once per site, and the job carries on", () => {
    const state = buildJobState([...events, ignored(last, last.seq + 1, "127.0.0.1:3001"), ignored(last, last.seq + 2, "127.0.0.1:3001")]);
    expect(state.ignoredInstructions).toEqual([{ stepId: "read", host: "127.0.0.1:3001" }]);
    expect(state.status).toBe("running");
  });

  test("a job without one has none", () => {
    expect(buildJobState(events).ignoredInstructions).toEqual([]);
  });
});
