import { describe, expect, test } from "bun:test";
import { demoJobs } from "@/lib/demoJobs";
import { splitAtApproval } from "@/lib/playback";
import { activeRun, clientStatus, consoleReducer, initialConsoleState, nextEventOf, panelRun, type ConsoleAction, type ConsoleState } from "./consoleState";

const readyKumo: ConsoleAction = {
  type: "setupLoaded",
  clientId: "kumo",
  setup: { clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: true },
};

const apply = (state: ConsoleState, ...actions: ConsoleAction[]) => actions.reduce(consoleReducer, state);

function startedLive() {
  const ready = apply(initialConsoleState, readyKumo, { type: "finishSetup" });
  return apply(ready, { type: "startLiveJob", text: "Sync today's supplier stock to the store admin" });
}

describe("live runs", () => {
  test("starting opens a thread with the request and waits for the worker's events", () => {
    const state = startedLive();
    const run = activeRun(state);
    expect(state.view.kind).toBe("thread");
    expect(run?.source).toBe("live");
    expect(run?.request).toBe("Sync today's supplier stock to the store admin");
    expect(run?.job).toBeNull();
    expect(nextEventOf(run!)).toBeUndefined();
    expect(state.panel).toBe("activity");
  });

  test("events from the worker build the job once each, and the thread takes the job's title", () => {
    const started = startedLive();
    const runKey = activeRun(started)?.key ?? "";
    const events = splitAtApproval(demoJobs.firstRun.events).untilApproval;
    let state = apply(started, { type: "liveStarted", runKey, jobId: "exec_1" });
    for (const event of events) state = apply(state, { type: "liveEvent", runKey, event });
    state = apply(state, { type: "liveEvent", runKey, event: events[3] });
    const run = activeRun(state);
    expect(run?.jobId).toBe("exec_1");
    expect(run?.job?.status).toBe("waiting");
    expect(run?.events).toHaveLength(events.length);
    expect(run?.waitingJob?.pendingApproval?.changes).toHaveLength(12);
    expect(state.threads[0]?.title).toBe("Supplier stock sync");
    expect(clientStatus(state, "kumo")).toEqual({ status: "waiting", approvals: 1 });
  });

  test("the OK is recorded when the worker reports it", () => {
    const started = startedLive();
    const runKey = activeRun(started)?.key ?? "";
    let state = started;
    for (const event of demoJobs.firstRun.events) state = apply(state, { type: "liveEvent", runKey, event });
    const run = activeRun(state);
    expect(run?.answer).toBe("Save changes");
    expect(run?.job?.status).toBe("done");
    expect(state.panel).toBe(null);
  });

  test("Run now and Undo start another live run in the same thread", () => {
    const started = startedLive();
    const threadId = started.view.kind === "thread" ? started.view.threadId : "";
    const again = apply(started, { type: "startLiveJob", text: "Undo this sync", threadId });
    expect(again.threads).toHaveLength(1);
    expect(again.threads[0]?.runKeys).toHaveLength(2);
    expect(activeRun(again)?.request).toBe("Undo this sync");
    expect(again.view).toEqual({ kind: "thread", threadId });
  });

  test("a replay run takes its kind from the worker, and the thread keeps its first title", () => {
    const started = startedLive();
    const threadId = started.view.kind === "thread" ? started.view.threadId : "";
    let state = started;
    const firstKey = activeRun(state)?.key ?? "";
    for (const event of demoJobs.firstRun.events) state = apply(state, { type: "liveEvent", runKey: firstKey, event });
    state = apply(state, { type: "startLiveJob", text: "Sync today's supplier stock to the store admin", threadId, trigger: "run-now" });
    const replayKey = activeRun(state)?.key ?? "";
    const replayCreated = demoJobs.replay.events[0];
    const undoCreated = { ...replayCreated, title: "Undo stock sync" } as typeof replayCreated;
    state = apply(state, { type: "liveEvent", runKey: replayKey, event: undoCreated });
    expect(activeRun(state)?.kind).toBe("replay");
    expect(activeRun(state)?.trigger).toBe("run-now");
    expect(state.threads[0]?.title).toBe("Supplier stock sync");
  });

  test("the Activity of a finished run can be opened again, and follows that run", () => {
    const started = startedLive();
    const firstKey = activeRun(started)?.key ?? "";
    let state = started;
    for (const event of demoJobs.firstRun.events) state = apply(state, { type: "liveEvent", runKey: firstKey, event });
    expect(state.panel).toBe(null);
    const reopened = apply(state, { type: "openPanel", panel: "activity", runKey: firstKey });
    expect(reopened.panel).toBe("activity");
    expect(panelRun(reopened)?.key).toBe(firstKey);
    expect(panelRun(apply(reopened, { type: "closePanel" }))).toBeUndefined();
  });

  test("a problem answering the approval is shown without ending the job", () => {
    const started = startedLive();
    const runKey = activeRun(started)?.key ?? "";
    const noticed = apply(started, { type: "liveNotice", runKey, message: "The approval could not be sent. Try again." });
    expect(activeRun(noticed)?.notice).toBe("The approval could not be sent. Try again.");
    expect(activeRun(noticed)?.isStopped).toBe(false);
  });

  test("a failure to reach the worker is shown on the run", () => {
    const started = startedLive();
    const runKey = activeRun(started)?.key ?? "";
    const failed = apply(started, { type: "liveFailed", runKey, message: "The worker service is not available right now." });
    expect(activeRun(failed)?.liveError).toBe("The worker service is not available right now.");
    expect(failed.panel).toBe(null);
  });
});

const ready = () => apply(initialConsoleState, readyKumo, { type: "finishSetup" });

describe("jobs waiting for you that this page did not start", () => {
  test("each one opens its own thread that follows the job by its id", () => {
    const state = apply(ready(), { type: "waitingJobSeen", clientId: "kumo", jobId: "exec_mcp" });
    expect(state.threads).toHaveLength(1);
    const run = state.runs[state.threads[0]?.runKeys[0] ?? ""];
    expect(run).toMatchObject({ source: "live", jobId: "exec_mcp", clientId: "kumo", job: null });
    const created = demoJobs.firstRun.events[0];
    const followed = apply(state, { type: "jobEvent", jobId: "exec_mcp", event: created });
    expect(followed.threads[0]?.title).toBe("Supplier stock sync");
    expect(followed.runs[run?.key ?? ""]?.job?.request).toBe(created.type === "job.created" ? created.request : "");
  });

  test("a job already on the page is not added again", () => {
    const started = startedLive();
    const runKey = activeRun(started)?.key ?? "";
    const known = apply(started, { type: "liveStarted", runKey, jobId: "exec_1" });
    const again = apply(known, { type: "waitingJobSeen", clientId: "kumo", jobId: "exec_1" }, { type: "waitingJobSeen", clientId: "kumo", jobId: "exec_1" });
    expect(again.threads).toHaveLength(1);
    expect(Object.keys(again.runs)).toHaveLength(1);
  });

  test("opening such a job does not move you away from what you are looking at", () => {
    const started = startedLive();
    const seen = apply(started, { type: "waitingJobSeen", clientId: "kumo", jobId: "exec_mcp" });
    expect(seen.view).toEqual(started.view);
    expect(seen.threads).toHaveLength(2);
  });
});

describe("a request typed under a conversation", () => {
  test("continues in the same conversation instead of opening a new one", () => {
    const started = startedLive();
    const threadId = started.view.kind === "thread" ? started.view.threadId : "";
    const followed = apply(started, { type: "startLiveJob", text: "Check the price list too", threadId });
    expect(followed.threads).toHaveLength(1);
    expect(followed.view).toEqual({ kind: "thread", threadId });
    expect(followed.threads[0]?.runKeys.map((key) => followed.runs[key]?.request)).toEqual(["Sync today's supplier stock to the store admin", "Check the price list too"]);
  });

  test("in the recorded demo also stays in the same conversation", () => {
    const first = apply(ready(), { type: "startJob", text: "Sync today's supplier stock to the store admin" });
    const threadId = first.view.kind === "thread" ? first.view.threadId : "";
    const followed = apply(first, { type: "startJob", text: "Sync it again", threadId });
    expect(followed.threads).toHaveLength(1);
    expect(followed.view).toEqual({ kind: "thread", threadId });
    expect(followed.threads[0]?.runKeys).toHaveLength(2);
  });
});
