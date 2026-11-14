import { describe, expect, test } from "bun:test";
import { demoJobs } from "@/lib/demoJobs";
import type { ClientSetup } from "@/lib/setup";
import {
  activeRun,
  clientStatus,
  consoleReducer,
  initialConsoleState,
  nextEventOf,
  setupOf,
  setupProgress,
  type ConsoleAction,
  type ConsoleClientId,
  type ConsoleState,
} from "./consoleState";

const apply = (state: ConsoleState, ...actions: ConsoleAction[]) => actions.reduce(consoleReducer, state);

function fromServer(clientId: ConsoleClientId, computer: ClientSetup["computer"], supplier = false, store = false): ConsoleAction {
  return { type: "setupLoaded", clientId, setup: { clientId, computer, logins: { supplier, store }, useClaude: true } };
}

const kumoSetUp: ConsoleAction[] = [fromServer("kumo", "ready"), fromServer("kumo", "ready", true, true), { type: "finishSetup" }];

function playUntilGate(state: ConsoleState) {
  let current = state;
  for (let guard = 0; guard < 200; guard += 1) {
    const run = activeRun(current);
    if (!run || !nextEventOf(run)) return current;
    current = consoleReducer(current, { type: "play", runKey: run.key });
  }
  throw new Error("playback did not stop");
}

const stockRequest = "Sync today's supplier stock to the store admin";

function firstRunWaiting() {
  const setUp = apply(initialConsoleState, ...kumoSetUp);
  return playUntilGate(consoleReducer(setUp, { type: "startJob", text: stockRequest }));
}

describe("setup", () => {
  test("starts on Kumo's setup and knows nothing until the server answers", () => {
    expect(initialConsoleState.view).toEqual({ kind: "setup" });
    expect(initialConsoleState.selectedClientId).toBe("kumo");
    expect(setupOf(initialConsoleState, "kumo")).toBeNull();
    expect(setupProgress(initialConsoleState, "kumo")).toBeUndefined();
  });

  test("the server's answer sets the steps done and the one progress bar", () => {
    expect(setupProgress(apply(initialConsoleState, fromServer("kumo", "missing")), "kumo")).toEqual({ done: 0, total: 3 });
    const computerOnly = apply(initialConsoleState, fromServer("kumo", "ready", true, false));
    expect(setupOf(computerOnly, "kumo")).toBe(1);
    expect(setupProgress(computerOnly, "kumo")).toEqual({ done: 1, total: 3 });
    expect(computerOnly.view).toEqual({ kind: "setup" });
  });

  test("once the server confirms both logins, finishing setup opens a new job and the bar goes away", () => {
    const loginsAdded = apply(initialConsoleState, fromServer("kumo", "ready"), fromServer("kumo", "ready", true, true));
    expect(setupOf(loginsAdded, "kumo")).toBe(2);
    expect(loginsAdded.view).toEqual({ kind: "setup" });

    const state = apply(loginsAdded, { type: "finishSetup" });
    expect(setupOf(state, "kumo")).toBe(3);
    expect(setupProgress(state, "kumo")).toBeUndefined();
    expect(state.view).toEqual({ kind: "new" });
  });

  test("finishing is ignored until the server says the computer and logins are there", () => {
    const state = apply(initialConsoleState, fromServer("kumo", "ready", true, false), { type: "finishSetup" });
    expect(state.view).toEqual({ kind: "setup" });
    expect(setupOf(state, "kumo")).toBe(1);
  });

  test("a client the server already reports as ready opens straight to a new job", () => {
    const state = apply(initialConsoleState, fromServer("kumo", "ready", true, true));
    expect(state.view).toEqual({ kind: "new" });
    expect(setupProgress(state, "kumo")).toBeUndefined();
  });

  test("a client that cannot have a computer shows no progress bar", () => {
    const state = apply(initialConsoleState, { type: "selectClient", clientId: "sora" }, fromServer("sora", "unavailable"));
    expect(state.view).toEqual({ kind: "setup" });
    expect(setupProgress(state, "sora")).toBeUndefined();
  });

  test("the Use Claude switch follows what the server saved", () => {
    const state = apply(initialConsoleState, fromServer("kumo", "ready"), { type: "useClaudeChanged", clientId: "kumo", useClaude: false });
    expect(state.setup.kumo?.useClaude).toBe(false);
  });
});

describe("first run", () => {
  test("starting a job opens its thread with the request and plays until the approval", () => {
    const state = firstRunWaiting();
    const run = activeRun(state);
    expect(state.view.kind).toBe("thread");
    expect(run?.request).toBe(stockRequest);
    expect(run?.job?.status).toBe("waiting");
    expect(run?.job?.pendingApproval?.changes).toHaveLength(12);
    expect(clientStatus(state, "kumo")).toEqual({ status: "waiting", approvals: 1 });
  });

  test("Not now keeps the job waiting and hides the card until reviewed again", () => {
    const waiting = firstRunWaiting();
    const runKey = activeRun(waiting)?.key ?? "";
    const later = apply(waiting, { type: "notNow", runKey });
    expect(activeRun(later)?.isApprovalHidden).toBe(true);
    expect(activeRun(later)?.job?.status).toBe("waiting");
    expect(clientStatus(later, "kumo").approvals).toBe(1);
    expect(nextEventOf(activeRun(later)!)).toBeUndefined();
    expect(activeRun(apply(later, { type: "review", runKey }))?.isApprovalHidden).toBe(false);
  });

  test("approving records the answer and plays to the end with the steps saved", () => {
    const waiting = firstRunWaiting();
    const runKey = activeRun(waiting)?.key ?? "";
    const done = playUntilGate(apply(waiting, { type: "approve", runKey }));
    const run = activeRun(done);
    expect(run?.answer).toBe("Save changes");
    expect(run?.job?.status).toBe("done");
    expect(run?.job?.procedure?.version).toBe(1);
    expect(run?.readyInMs).toBe(48600);
    expect(clientStatus(done, "kumo")).toEqual({ status: "idle", approvals: 0 });
  });

  test("Stop ends a running job as stopped", () => {
    const setUp = apply(initialConsoleState, ...kumoSetUp, { type: "startJob", text: stockRequest });
    const runKey = activeRun(setUp)?.key ?? "";
    const stopped = apply(setUp, { type: "play", runKey }, { type: "play", runKey }, { type: "stop", runKey, at: "2026-11-14T08:40:05.000Z" });
    expect(activeRun(stopped)?.job?.status).toBe("stopped");
    expect(nextEventOf(activeRun(stopped)!)).toBeUndefined();
  });
});

function firstRunDone() {
  const waiting = firstRunWaiting();
  return playUntilGate(apply(waiting, { type: "approve", runKey: activeRun(waiting)?.key ?? "" }));
}

describe("later runs in the same thread", () => {

  test("Run now replays with no AI and asks about 4 changes, then the next run repairs the button", () => {
    const done = firstRunDone();
    const threadId = done.view.kind === "thread" ? done.view.threadId : "";
    const replay = playUntilGate(apply(done, { type: "runNow", threadId }));
    const replayRun = activeRun(replay);
    expect(replayRun?.kind).toBe("replay");
    expect(replayRun?.job?.pendingApproval?.changes).toHaveLength(4);
    expect(replayRun?.job?.claudeDecisions).toBe(0);
    expect(replay.threads.find((thread) => thread.id === threadId)?.runKeys).toHaveLength(2);

    const replayDone = playUntilGate(apply(replay, { type: "approve", runKey: replayRun?.key ?? "" }));
    const repair = playUntilGate(apply(replayDone, { type: "runNow", threadId }));
    const repairRun = activeRun(repair);
    expect(repairRun?.kind).toBe("repair");
    expect(repairRun?.job?.pendingApproval?.procedureChange?.after).toBe("Update inventory");
    expect(repair.panel).toBe(null);
    expect(apply(repair, { type: "openPanel", panel: "what-changed" }).panel).toBe("what-changed");
  });
});

describe("another client's login", () => {
  test("Hinata asking for Kumo's login is blocked and offers Hinata's own account", () => {
    const done = apply(initialConsoleState, ...kumoSetUp, { type: "selectClient", clientId: "hinata" }, fromServer("hinata", "ready", true, true));
    expect(done.view).toEqual({ kind: "new" });
    const blocked = playUntilGate(consoleReducer(done, { type: "startJob", text: demoJobs.boundary.events[0].type === "job.created" ? demoJobs.boundary.events[0].request : "" }));
    const run = activeRun(blocked);
    expect(run?.kind).toBe("boundary");
    expect(run?.job?.status).toBe("blocked");
    const chosen = apply(blocked, { type: "choose", runKey: run?.key ?? "", choice: "own-account" });
    expect(activeRun(chosen)?.choice).toBe("own-account");
  });

  test("a client that is not set up opens its setup", () => {
    const state = apply(initialConsoleState, ...kumoSetUp, { type: "selectClient", clientId: "sora" });
    expect(state.view).toEqual({ kind: "setup" });
  });
});
