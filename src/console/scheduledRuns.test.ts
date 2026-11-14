import { describe, expect, test } from "bun:test";
import { demoJobs } from "@/lib/demoJobs";
import type { ScheduledRun } from "@/lib/schedule";
import { consoleReducer, initialConsoleState, recentScheduledRuns, type ConsoleAction, type ConsoleState } from "./consoleState";

const apply = (state: ConsoleState, ...actions: ConsoleAction[]) => actions.reduce(consoleReducer, state);
const readyKumo: ConsoleAction = {
  type: "setupLoaded",
  clientId: "kumo",
  setup: { clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: true },
};

const started: ScheduledRun = { status: "started", slot: "2026-11-14T09:00:00Z", timeZone: "Asia/Tokyo", jobId: "exec_1" };
const skipped: ScheduledRun = { status: "skipped", slot: "2026-11-15T09:00:00Z", timeZone: "Asia/Tokyo", reason: "waiting-for-approval", waitingJobId: "exec_1" };

describe("scheduled runs", () => {
  test("a started run joins the client's stock sync thread as a live replay with its time", () => {
    const state = apply(initialConsoleState, readyKumo, { type: "finishSetup" }, { type: "scheduledRunSeen", clientId: "kumo", run: started });
    expect(state.threads).toHaveLength(1);
    expect(state.threads[0]?.title).toBe("Supplier stock sync");
    const run = state.runs[state.threads[0]?.runKeys[0] ?? ""];
    expect(run).toMatchObject({ source: "live", kind: "replay", jobId: "exec_1", scheduledFor: { slot: started.slot, timeZone: "Asia/Tokyo" } });
  });

  test("it reuses the existing stock sync thread and does not add the same run twice", () => {
    const once = apply(initialConsoleState, readyKumo, { type: "finishSetup" }, { type: "startLiveJob", text: "Sync today's supplier stock to the store admin" });
    const titled = { ...once, threads: once.threads.map((thread) => ({ ...thread, title: "Supplier stock sync" })) };
    const twice = apply(titled, { type: "scheduledRunSeen", clientId: "kumo", run: started }, { type: "scheduledRunSeen", clientId: "kumo", run: started });
    expect(twice.threads).toHaveLength(1);
    expect(twice.threads[0]?.runKeys).toHaveLength(2);
  });

  test("events for a scheduled run are matched by its job id", () => {
    const seen = apply(initialConsoleState, readyKumo, { type: "finishSetup" }, { type: "scheduledRunSeen", clientId: "kumo", run: started });
    const created = demoJobs.replay.events[0];
    const next = apply(seen, { type: "jobEvent", jobId: "exec_1", event: created }, { type: "jobEvent", jobId: "exec_other", event: created });
    const run = next.runs[next.threads[0]?.runKeys[0] ?? ""];
    expect(run?.job?.run.kind).toBe("replay");
    expect(run?.events).toHaveLength(1);
  });

  test("a skipped run becomes one line in the thread, without a job", () => {
    const state = apply(initialConsoleState, readyKumo, { type: "finishSetup" }, { type: "scheduledRunSeen", clientId: "kumo", run: skipped });
    const run = state.runs[state.threads[0]?.runKeys[0] ?? ""];
    expect(run).toMatchObject({ source: "live", skipped: { slot: skipped.slot, timeZone: "Asia/Tokyo" } });
    expect(run?.job).toBeNull();
    expect(run?.isStopped).toBe(true);
  });

  test("on opening the page only runs from the last two days are shown", () => {
    const now = new Date("2026-11-15T09:05:00Z");
    const old: ScheduledRun = { ...started, slot: "2026-11-13T09:00:00Z", jobId: "exec_0" };
    expect(recentScheduledRuns([old, started, skipped], now).map((run) => run.slot)).toEqual([started.slot, skipped.slot]);
  });
});
