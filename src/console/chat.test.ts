import { describe, expect, test } from "bun:test";
import type { ChatThread } from "@/lib/chatStore";
import { demoJobs } from "@/lib/demoJobs";
import { splitAtApproval } from "@/lib/playback";
import { activeRun, consoleReducer, initialConsoleState, recentThreads, restoreOrder, shouldSave, threadEntries, toChatThread, type ConsoleAction, type ConsoleState } from "./consoleState";

const apply = (state: ConsoleState, ...actions: ConsoleAction[]) => actions.reduce(consoleReducer, state);
const readyKumo: ConsoleAction = { type: "setupLoaded", clientId: "kumo", setup: { clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: true } };
const at = "2026-11-14T09:00:00.000Z";

function withJob() {
  const ready = apply(initialConsoleState, readyKumo, { type: "finishSetup" });
  const started = apply(ready, { type: "startLiveJob", text: "Sync today's supplier stock to the store admin" });
  const runKey = activeRun(started)?.key ?? "";
  let state = apply(started, { type: "liveStarted", runKey, jobId: "exec_1" });
  for (const event of demoJobs.firstRun.events) state = apply(state, { type: "liveEvent", runKey, event });
  const threadId = state.view.kind === "thread" ? state.view.threadId : "";
  return { state, threadId };
}

describe("a message in a conversation", () => {
  test("stays in the conversation after the job, waits for the worker, then shows its answer", () => {
    const { state, threadId } = withJob();
    const asked = apply(state, { type: "chatAsked", threadId, key: "chat-1", text: "How many products changed?", at });
    expect(threadEntries(asked, threadId).map((entry) => entry.kind)).toEqual(["run", "chat"]);
    expect(asked.chats["chat-1"]).toMatchObject({ text: "How many products changed?", decision: null, afterRuns: 1 });

    const answered = apply(asked, { type: "chatAnswered", key: "chat-1", decision: { action: "answer", facts: ["last.changes"], clarify: null } });
    expect(answered.chats["chat-1"]?.decision).toEqual({ action: "answer", facts: ["last.changes"], clarify: null });
    expect(activeRun(answered)?.jobId).toBe("exec_1");
  });

  test("a job the answer starts comes after the message, and does not repeat it", () => {
    const { state, threadId } = withJob();
    const asked = apply(state, { type: "chatAsked", threadId, key: "chat-1", text: "Run it again", at }, { type: "chatAnswered", key: "chat-1", decision: { action: "run", facts: [], clarify: null } });
    const started = apply(asked, { type: "startLiveJob", text: "Run it again", threadId, trigger: "reply" });
    expect(threadEntries(started, threadId).map((entry) => entry.kind)).toEqual(["run", "chat", "run"]);
    expect(activeRun(started)?.trigger).toBe("reply");
  });
});

describe("an answer that arrives after other messages", () => {
  test("a job it starts keeps the key its starter chose, so the worker's events still reach it in this conversation", () => {
    const { state, threadId } = withJob();
    const asked = apply(state, { type: "chatAsked", threadId, key: "chat-1", text: "Run it again", at }, { type: "chatAnswered", key: "chat-1", decision: { action: "run", facts: [], clarify: null } });
    const started = apply(asked, { type: "startLiveJob", text: "Run it again", threadId, trigger: "reply", runKey: "live-run-1" });
    const live = apply(started, { type: "liveStarted", runKey: "live-run-1", jobId: "exec_2" });
    expect(activeRun(live)?.key).toBe("live-run-1");
    const entries = threadEntries(live, threadId);
    expect(entries.map((entry) => entry.kind)).toEqual(["run", "chat", "run"]);
    expect(entries[2]?.kind === "run" ? entries[2].run.jobId : null).toBe("exec_2");
  });

  test("keeps the words it answered with, even after the job moves on, and they are saved and restored with it", () => {
    const { state, threadId } = withJob();
    const lines = ["The last job is waiting for your OK on 12 changes."];
    const answered = apply(state, { type: "chatAsked", threadId, key: "chat-1", text: "Did it work?", at }, { type: "chatAnswered", key: "chat-1", decision: { action: "answer", facts: ["last.result"], clarify: null }, lines, at });
    expect(answered.chats["chat-1"]?.answer).toEqual(lines);
    const saved = toChatThread(answered, threadId);
    expect(saved?.turns[3]).toEqual({ kind: "reply", action: "answer", facts: ["last.result"], clarify: null, lines, at });
    const restored = apply(apply(initialConsoleState, readyKumo, { type: "finishSetup" }), { type: "threadRestored", thread: saved! });
    expect(Object.values(restored.chats).map((chat) => chat.answer)).toEqual([lines]);
    expect(toChatThread(restored, threadId)?.turns).toEqual(saved?.turns);
  });
});

describe("a conversation kept on the server", () => {
  test("is saved as the person's words, the jobs and the answers, in order", () => {
    const { state, threadId } = withJob();
    const asked = apply(state, { type: "chatAsked", threadId, key: "chat-1", text: "How many products changed?", at }, { type: "chatAnswered", key: "chat-1", decision: { action: "answer", facts: ["last.changes"], clarify: null } });

    const saved = toChatThread(asked, threadId);

    expect(saved?.clientId).toBe("kumo");
    expect(saved?.title).toBe("Supplier stock sync");
    expect(saved?.turns.map((turn) => turn.kind)).toEqual(["you", "job", "you", "reply"]);
    expect(saved?.turns[1]).toMatchObject({ kind: "job", jobId: "exec_1", trigger: "request" });
    expect(saved?.turns[3]).toMatchObject({ kind: "reply", action: "answer", facts: ["last.changes"] });
  });

  test("comes back as the same conversation, with its jobs to follow", () => {
    const thread: ChatThread = {
      version: 1,
      id: "a1-thread-1",
      clientId: "kumo",
      title: "Supplier stock sync",
      createdAt: at,
      updatedAt: at,
      turns: [
        { kind: "you", text: "Sync today's supplier stock to the store admin", at },
        { kind: "job", jobId: "exec_1", trigger: "request", at },
        { kind: "you", text: "Run it again", at },
        { kind: "reply", action: "run", facts: [], clarify: null, at },
        { kind: "job", jobId: "exec_2", trigger: "reply", at },
      ],
    };
    const ready = apply(initialConsoleState, readyKumo, { type: "finishSetup" });

    const restored = apply(ready, { type: "threadRestored", thread }, { type: "threadRestored", thread });

    expect(restored.threads.map((candidate) => candidate.id)).toEqual(["a1-thread-1"]);
    const entries = threadEntries(restored, "a1-thread-1");
    expect(entries.map((entry) => entry.kind)).toEqual(["run", "chat", "run"]);
    const runs = Object.values(restored.runs);
    expect(runs.map((run) => [run.jobId, run.request, run.trigger])).toEqual([
      ["exec_1", "Sync today's supplier stock to the store admin", undefined],
      ["exec_2", "Run it again", "reply"],
    ]);
    expect(toChatThread(restored, "a1-thread-1")?.turns).toEqual(thread.turns);
  });
});

describe("ids for new conversations", () => {
  test("take a prefix set once after the page opens, so they don't collide with saved ones", () => {
    const prefixed = apply(initialConsoleState, { type: "idPrefixSet", prefix: "ab12cd34-" }, { type: "idPrefixSet", prefix: "zz-" }, readyKumo, { type: "finishSetup" }, { type: "startLiveJob", text: "Sync" });
    expect(prefixed.idPrefix).toBe("ab12cd34-");
    expect(prefixed.threads[0]?.id.startsWith("ab12cd34-thread-")).toBe(true);
  });
});

const conversation = (id: string, turns: ChatThread["turns"]): ChatThread => ({ version: 1, id, clientId: "kumo", title: "Supplier stock sync", createdAt: at, updatedAt: at, turns });
const ready = () => apply(initialConsoleState, readyKumo, { type: "finishSetup" });

describe("restoring and daily runs together", () => {
  test("a job already shown in one conversation is not shown again from another", () => {
    const full = conversation("a-thread-1", [
      { kind: "you", text: "Sync today's supplier stock to the store admin", at },
      { kind: "job", jobId: "exec_1", trigger: "request", at },
    ]);
    const leftover = conversation("b-thread-1", [{ kind: "job", jobId: "exec_1", trigger: "request", at }]);
    const restored = apply(ready(), { type: "threadRestored", thread: full }, { type: "threadRestored", thread: leftover });
    expect(restored.threads.map((thread) => thread.id)).toEqual(["a-thread-1"]);
  });

  test("a daily run older than the latest conversation gets its own conversation instead of landing after newer messages", () => {
    const newer = conversation("a-thread-1", [
      { kind: "you", text: "Sync today's supplier stock to the store admin", at: "2026-11-14T05:00:00.000Z" },
      { kind: "job", jobId: "exec_1", trigger: "request", at: "2026-11-14T05:00:00.000Z" },
    ]);
    const restored = apply(ready(), { type: "threadRestored", thread: newer });
    const seen = apply(restored, { type: "scheduledRunSeen", clientId: "kumo", run: { status: "started", slot: "2026-11-14T01:11:00Z", timeZone: "Asia/Tokyo", jobId: "exec_0" } });
    expect(seen.threads).toHaveLength(2);
    expect(seen.threads.find((thread) => thread.id === "a-thread-1")?.runKeys).toHaveLength(1);
  });
});

describe("the same conversation open in two places", () => {
  const started = conversation("a-thread-1", [
    { kind: "you", text: "Sync today's supplier stock to the store admin", at },
    { kind: "job", jobId: "exec_1", trigger: "request", at },
  ]);
  const later = "2026-11-14T09:05:00.000Z";
  const extended = conversation("a-thread-1", [
    ...started.turns,
    { kind: "you", text: "Run it again", at: later },
    { kind: "reply", action: "run", facts: [], clarify: null, at: later },
    { kind: "job", jobId: "exec_2", trigger: "reply", at: later },
  ]);
  const jobOnly = conversation("b-thread-7", [{ kind: "job", jobId: "exec_2", trigger: "request", at: later }]);

  test("conversations with messages come back before threads that only hold a job, so a job stays in the conversation it was asked for in", () => {
    const ordered = restoreOrder([jobOnly, extended]);
    expect(ordered.map((thread) => thread.id)).toEqual(["a-thread-1", "b-thread-7"]);
    const restored = ordered.reduce((state, thread) => apply(state, { type: "threadRestored", thread }), ready());
    expect(restored.threads.map((thread) => thread.id)).toEqual(["a-thread-1"]);
    expect(threadEntries(restored, "a-thread-1").map((entry) => entry.kind)).toEqual(["run", "chat", "run"]);
  });

  test("a newer saved copy of a conversation already open here adds what was said and started elsewhere", () => {
    const open = apply(ready(), { type: "threadRestored", thread: started });
    const caughtUp = apply(open, { type: "threadRestored", thread: extended }, { type: "waitingJobSeen", clientId: "kumo", jobId: "exec_2" });
    expect(caughtUp.threads.map((thread) => thread.id)).toEqual(["a-thread-1"]);
    expect(threadEntries(caughtUp, "a-thread-1").map((entry) => entry.kind)).toEqual(["run", "chat", "run"]);
    expect(toChatThread(caughtUp, "a-thread-1")?.turns).toEqual(extended.turns);
  });

  test("a saved copy that does not continue the conversation open here changes nothing", () => {
    const open = apply(ready(), { type: "threadRestored", thread: extended });
    const other = conversation("a-thread-1", [{ kind: "you", text: "Undo", at }, { kind: "reply", action: "undo", facts: [], clarify: null, at }]);
    expect(apply(open, { type: "threadRestored", thread: other })).toEqual(open);
    expect(apply(open, { type: "threadRestored", thread: started })).toEqual(open);
  });
});

describe("saving a conversation that came from the server", () => {
  const server = conversation("a-thread-1", [
    { kind: "you", text: "Sync today's supplier stock to the store admin", at },
    { kind: "job", jobId: "exec_1", trigger: "request", at },
  ]);

  test("is not written back just because the page opened it", () => {
    expect(shouldSave(server, { server, lastSaved: undefined })).toBe(false);
  });

  test("is written when this page added to it", () => {
    const added = { ...server, turns: [...server.turns, { kind: "you" as const, text: "Run it again", at }] };
    expect(shouldSave(added, { server, lastSaved: undefined })).toBe(true);
    expect(shouldSave(added, { server, lastSaved: JSON.stringify(added) })).toBe(false);
  });

  test("never replaces the server's copy with one that has lost part of it", () => {
    const shorter = { ...server, turns: server.turns.slice(0, 1) };
    expect(shouldSave(shorter, { server, lastSaved: undefined })).toBe(false);
  });

  test("a conversation started on this page is saved as it grows", () => {
    expect(shouldSave(server, { server: undefined, lastSaved: undefined })).toBe(true);
    expect(shouldSave(server, { server: undefined, lastSaved: JSON.stringify(server) })).toBe(false);
  });
});

describe("the conversations in the sidebar", () => {
  const older = conversation("a-thread-1", [
    { kind: "you", text: "Sync today's supplier stock to the store admin", at: "2026-11-14T05:00:00.000Z" },
    { kind: "job", jobId: "exec_1", trigger: "request", at: "2026-11-14T05:00:00.000Z" },
  ]);
  const newer = conversation("b-thread-1", [
    { kind: "you", text: "Sync today's supplier stock to the store admin", at: "2026-11-14T06:00:00.000Z" },
    { kind: "job", jobId: "exec_2", trigger: "request", at: "2026-11-14T06:00:00.000Z" },
  ]);

  test("are listed newest first by their last message or job", () => {
    const restored = apply(ready(), { type: "threadRestored", thread: newer }, { type: "threadRestored", thread: older });
    expect(recentThreads(restored, "kumo").map((thread) => thread.id)).toEqual(["b-thread-1", "a-thread-1"]);
    const asked = apply(restored, { type: "chatAsked", threadId: "a-thread-1", key: "chat-9", text: "Did it work?", at: "2026-11-14T07:00:00.000Z" });
    expect(recentThreads(asked, "kumo").map((thread) => thread.id)).toEqual(["a-thread-1", "b-thread-1"]);
  });

  test("the newest one opens when the page comes back, until the person picks something", () => {
    const restored = apply(ready(), { type: "threadRestored", thread: older }, { type: "threadRestored", thread: newer });
    expect(restored.view).toEqual({ kind: "thread", threadId: "b-thread-1" });
    const picked = apply(ready(), { type: "openThread", threadId: "none" }, { type: "newJob" }, { type: "threadRestored", thread: newer });
    expect(picked.view).toEqual({ kind: "new" });
  });
});

describe("the demo playback", () => {
  test("a job started by an answer is marked as started by the reply, so the message is not shown twice", () => {
    const first = apply(ready(), { type: "startJob", text: "Sync today's supplier stock to the store admin" });
    const threadId = first.view.kind === "thread" ? first.view.threadId : "";
    const again = apply(first, { type: "chatAsked", threadId, key: "chat-1", text: "Run it again", at }, { type: "chatAnswered", key: "chat-1", decision: { action: "run", facts: [], clarify: null } }, { type: "startJob", text: "Run it again", threadId, trigger: "reply" });
    expect(activeRun(again)?.trigger).toBe("reply");
    expect(threadEntries(again, threadId).map((entry) => entry.kind)).toEqual(["run", "chat", "run"]);
  });
});

function parts(state: ConsoleState, threadId: string) {
  return threadEntries(state, threadId).map((entry) => (entry.kind === "chat" ? "chat" : `run:${entry.part}`));
}

function waitingForTheOk() {
  const prepared = apply(initialConsoleState, readyKumo, { type: "finishSetup" });
  const started = apply(prepared, { type: "startLiveJob", text: "Sync today's supplier stock to the store admin", runKey: "live-1" });
  let state = apply(started, { type: "liveStarted", runKey: "live-1", jobId: "exec_1" });
  for (const event of splitAtApproval(demoJobs.firstRun.events).untilApproval) state = apply(state, { type: "liveEvent", runKey: "live-1", event });
  const threadId = state.view.kind === "thread" ? state.view.threadId : "";
  return { state, threadId };
}

describe("a question asked while the job waits for the OK", () => {
  test("comes before the OK and the result, which stay after it, and is saved once", () => {
    const { state, threadId } = waitingForTheOk();
    let asked = apply(state, { type: "chatAsked", threadId, key: "chat-1", text: "How many products changed?", at: "2026-11-14T08:40:50.000Z" }, { type: "chatAnswered", key: "chat-1", decision: { action: "answer", facts: ["last.changes"], clarify: null } });
    expect(parts(asked, threadId)).toEqual(["run:whole", "chat"]);
    for (const event of splitAtApproval(demoJobs.firstRun.events).afterApproval) asked = apply(asked, { type: "liveEvent", runKey: "live-1", event });
    expect(parts(asked, threadId)).toEqual(["run:before", "chat", "run:after"]);
    expect(toChatThread(asked, threadId)?.turns.map((turn) => turn.kind)).toEqual(["you", "job", "you", "reply"]);
  });

  test("a question asked after the OK stays after the whole job", () => {
    const { state, threadId } = waitingForTheOk();
    let done = state;
    for (const event of splitAtApproval(demoJobs.firstRun.events).afterApproval) done = apply(done, { type: "liveEvent", runKey: "live-1", event });
    const asked = apply(done, { type: "chatAsked", threadId, key: "chat-1", text: "Did it work?", at: "2026-11-14T08:42:00.000Z" });
    expect(parts(asked, threadId)).toEqual(["run:whole", "chat"]);
  });

  test("after the page comes back, the times put a question asked before the OK between the two parts", () => {
    const thread = conversation("a-thread-1", [
      { kind: "you", text: "Sync today's supplier stock to the store admin", at: "2026-11-14T08:40:00.000Z" },
      { kind: "job", jobId: "exec_1", trigger: "request", at: "2026-11-14T08:40:00.000Z" },
      { kind: "you", text: "How many products changed?", at: "2026-11-14T08:40:50.000Z" },
      { kind: "reply", action: "answer", facts: ["last.changes"], clarify: null, at: "2026-11-14T08:40:52.000Z" },
    ]);
    let restored = apply(ready(), { type: "threadRestored", thread });
    for (const event of demoJobs.firstRun.events) restored = apply(restored, { type: "jobEvent", jobId: "exec_1", event });
    expect(parts(restored, "a-thread-1")).toEqual(["run:before", "chat", "run:after"]);
  });
});
