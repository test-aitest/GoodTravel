import type { ChatDecision, ChatThread, ChatTurn } from "@/lib/chatStore";
import { demoJobs } from "@/lib/demoJobs";
import type { JobEvent } from "@/lib/events";
import { applyEvent, type JobState } from "@/lib/jobState";
import { stopEvent } from "@/lib/playback";
import type { ScheduledRun } from "@/lib/schedule";
import { isReady, setupDone, type ClientSetup } from "@/lib/setup";

export type ConsoleClientId = "kumo" | "hinata" | "sora";
export type RunKind = keyof typeof demoJobs;
export type BoundaryChoice = "own-account" | "cancel";

export type RunSource = "playback" | "live";

export type Run = {
  key: string;
  source: RunSource;
  jobId?: string;
  liveError?: string;
  notice?: string;
  trigger?: "run-now" | "undo" | "reply";
  at?: string;
  scheduledFor?: { slot: string; timeZone: string };
  skipped?: { slot: string; timeZone: string };
  kind: RunKind;
  clientId: ConsoleClientId;
  threadId: string;
  request?: string;
  events: readonly JobEvent[];
  played: number;
  job: JobState | null;
  isApproved: boolean;
  isApprovalHidden: boolean;
  isStopped: boolean;
  answer?: string;
  waitingJob?: JobState;
  readyInMs: number | null;
  choice?: BoundaryChoice;
};

export type Thread = { id: string; clientId: ConsoleClientId; title: string; runKeys: string[] };

export type View = { kind: "setup" } | { kind: "new" } | { kind: "thread"; threadId: string };

export type Panel = "activity" | "what-changed" | null;

export type ClientSetupState = ClientSetup & { isFinished: boolean };

export type ChatEntry = {
  key: string;
  threadId: string;
  clientId: ConsoleClientId;
  text: string;
  at: string;
  afterRuns: number;
  decision: ChatDecision | null;
  decidedAt?: string;
  answer?: string[];
  whileWaiting?: boolean;
  problem?: string;
};

export type RunPart = "whole" | "before" | "after";

export type ThreadEntry = { kind: "run"; run: Run; part: RunPart } | { kind: "chat"; chat: ChatEntry };

export type ConsoleState = {
  idPrefix: string;
  chats: Record<string, ChatEntry>;
  selectedClientId: ConsoleClientId;
  view: View;
  setup: Partial<Record<ConsoleClientId, ClientSetupState>>;
  runs: Record<string, Run>;
  threads: Thread[];
  panel: Panel;
  panelRunKey?: string;
  counter: number;
  isViewChosen: boolean;
};

export type ConsoleAction =
  | { type: "selectClient"; clientId: ConsoleClientId }
  | { type: "newJob" }
  | { type: "openThread"; threadId: string }
  | { type: "setupLoaded"; clientId: ConsoleClientId; setup: ClientSetup }
  | { type: "useClaudeChanged"; clientId: ConsoleClientId; useClaude: boolean }
  | { type: "finishSetup" }
  | { type: "startJob"; text: string; threadId?: string; trigger?: "run-now" | "undo" | "reply" }
  | { type: "startLiveJob"; text: string; threadId?: string; trigger?: "run-now" | "undo" | "reply"; runKey?: string }
  | { type: "chatAsked"; threadId?: string; key: string; text: string; at: string }
  | { type: "chatAnswered"; key: string; decision: ChatDecision; lines?: string[]; at?: string }
  | { type: "chatFailed"; key: string; message: string }
  | { type: "threadRestored"; thread: ChatThread }
  | { type: "idPrefixSet"; prefix: string }
  | { type: "liveNotice"; runKey: string; message: string }
  | { type: "scheduledRunSeen"; clientId: ConsoleClientId; run: ScheduledRun }
  | { type: "waitingJobSeen"; clientId: ConsoleClientId; jobId: string }
  | { type: "jobEvent"; jobId: string; event: JobEvent }
  | { type: "liveStarted"; runKey: string; jobId: string }
  | { type: "liveEvent"; runKey: string; event: JobEvent }
  | { type: "liveFailed"; runKey: string; message: string }
  | { type: "play"; runKey: string }
  | { type: "approve"; runKey: string }
  | { type: "notNow"; runKey: string }
  | { type: "review"; runKey: string }
  | { type: "stop"; runKey: string; at: string }
  | { type: "runNow"; threadId: string }
  | { type: "choose"; runKey: string; choice: BoundaryChoice }
  | { type: "openPanel"; panel: Exclude<Panel, null>; runKey?: string }
  | { type: "closePanel" };

export const SETUP_STEPS = 3;

export const initialConsoleState: ConsoleState = {
  idPrefix: "",
  chats: {},
  selectedClientId: "kumo",
  view: { kind: "setup" },
  setup: {},
  runs: {},
  threads: [],
  panel: null,
  counter: 0,
  isViewChosen: false,
};

const firstJobOf: Record<ConsoleClientId, RunKind | null> = { kumo: "firstRun", hinata: "boundary", sora: null };
const scheduledKinds: RunKind[] = ["replay", "repair"];

export function setupOf(state: ConsoleState, clientId: ConsoleClientId) {
  const setup = state.setup[clientId];
  if (!setup) return null;
  return setup.isFinished ? SETUP_STEPS : setupDone(setup);
}

export function isSetUp(state: ConsoleState, clientId: ConsoleClientId) {
  return state.setup[clientId]?.isFinished === true;
}

export function setupProgress(state: ConsoleState, clientId: ConsoleClientId) {
  const setup = state.setup[clientId];
  const done = setupOf(state, clientId);
  if (!setup || done === null || setup.computer === "unavailable" || done >= SETUP_STEPS) return undefined;
  return { done, total: SETUP_STEPS };
}

export function threadsOf(state: ConsoleState, clientId: ConsoleClientId) {
  return state.threads.filter((thread) => thread.clientId === clientId);
}

export function recentThreads(state: ConsoleState, clientId: ConsoleClientId) {
  return threadsOf(state, clientId)
    .toReversed()
    .toSorted((a, b) => Date.parse(lastActivity(state, b)) - Date.parse(lastActivity(state, a)));
}

function newestUnlessChosen(state: ConsoleState): ConsoleState {
  if (state.isViewChosen || !isSetUp(state, state.selectedClientId)) return state;
  const newest = recentThreads(state, state.selectedClientId)[0];
  return newest ? { ...state, view: { kind: "thread", threadId: newest.id } } : state;
}

export function runsOf(state: ConsoleState, thread: Thread) {
  return thread.runKeys.map((key) => state.runs[key]);
}

export function activeRun(state: ConsoleState): Run | undefined {
  if (state.view.kind !== "thread") return undefined;
  const { threadId } = state.view;
  const key = state.threads.find((thread) => thread.id === threadId)?.runKeys.at(-1);
  return key === undefined ? undefined : state.runs[key];
}

const STOCK_SYNC_TITLE = "Supplier stock sync";
const RECENT_MS = 2 * 24 * 60 * 60 * 1000;

export function recentScheduledRuns(runs: readonly ScheduledRun[], now: Date) {
  return runs.filter((run) => now.getTime() - Date.parse(run.slot) < RECENT_MS);
}

function lastActivity(state: ConsoleState, thread: Thread) {
  const times = [
    ...thread.runKeys.map((key) => state.runs[key]).flatMap((run) => [run?.at, run?.job?.updatedAt, run?.scheduledFor?.slot]),
    ...Object.values(state.chats).filter((chat) => chat.threadId === thread.id).map((chat) => chat.decidedAt ?? chat.at),
  ].filter((time): time is string => Boolean(time));
  return times.reduce((latest, time) => (Date.parse(time) > Date.parse(latest) ? time : latest), new Date(0).toISOString());
}

function scheduledRunSeen(state: ConsoleState, clientId: ConsoleClientId, seen: ScheduledRun): ConsoleState {
  const already = Object.values(state.runs).some(
    (run) => run.clientId === clientId && (seen.status === "started" ? run.jobId === seen.jobId : run.skipped?.slot === seen.slot),
  );
  if (already) return state;
  const latest = state.threads.filter((thread) => thread.clientId === clientId && thread.title === STOCK_SYNC_TITLE).at(-1);
  const existing = latest && Date.parse(lastActivity(state, latest)) <= Date.parse(seen.slot) ? latest : undefined;
  const threadId = existing?.id ?? newThreadId(state);
  const when = { slot: seen.slot, timeZone: seen.timeZone };
  const run: Run = {
    key: `run-${state.counter + 1}`,
    source: "live",
    kind: "replay",
    clientId,
    threadId,
    events: [],
    played: 0,
    job: null,
    isApproved: false,
    isApprovalHidden: false,
    isStopped: seen.status === "skipped",
    readyInMs: null,
    ...(seen.status === "started" ? { jobId: seen.jobId, scheduledFor: when } : { skipped: when }),
  };
  return {
    ...state,
    counter: state.counter + 1,
    runs: { ...state.runs, [run.key]: run },
    threads: existing
      ? state.threads.map((thread) => (thread.id === threadId ? { ...thread, runKeys: [...thread.runKeys, run.key] } : thread))
      : [...state.threads, { id: threadId, clientId, title: STOCK_SYNC_TITLE, runKeys: [run.key] }],
  };
}

function waitingJobSeen(state: ConsoleState, clientId: ConsoleClientId, jobId: string): ConsoleState {
  if (Object.values(state.runs).some((run) => run.jobId === jobId)) return state;
  const threadId = newThreadId(state);
  const run: Run = {
    key: `run-${state.counter + 1}`,
    source: "live",
    kind: "firstRun",
    clientId,
    threadId,
    jobId,
    events: [],
    played: 0,
    job: null,
    isApproved: false,
    isApprovalHidden: false,
    isStopped: false,
    readyInMs: null,
  };
  return {
    ...state,
    counter: state.counter + 1,
    runs: { ...state.runs, [run.key]: run },
    threads: [...state.threads, { id: threadId, clientId, title: "New job", runKeys: [run.key] }],
  };
}

export function chatsOf(state: ConsoleState, threadId: string) {
  return Object.values(state.chats).filter((chat) => chat.threadId === threadId);
}

export function threadEntries(state: ConsoleState, threadId: string): ThreadEntry[] {
  const thread = state.threads.find((candidate) => candidate.id === threadId);
  if (!thread) return [];
  const chats = chatsOf(state, threadId);
  const entries: ThreadEntry[] = chats.filter((chat) => chat.afterRuns === 0).map((chat) => ({ kind: "chat", chat }));
  thread.runKeys.forEach((key, index) => {
    const run = state.runs[key];
    const after = chats.filter((candidate) => candidate.afterRuns === index + 1);
    const between = run ? after.filter((chat) => askedBeforeTheAnswer(chat, run)) : [];
    if (run && between.length > 0) {
      entries.push({ kind: "run", run, part: "before" }, ...between.map((chat) => ({ kind: "chat" as const, chat })), { kind: "run", run, part: "after" });
      for (const chat of after.filter((candidate) => !between.includes(candidate))) entries.push({ kind: "chat", chat });
      return;
    }
    if (run) entries.push({ kind: "run", run, part: "whole" });
    for (const chat of after) entries.push({ kind: "chat", chat });
  });
  return entries;
}

function askedBeforeTheAnswer(chat: ChatEntry, run: Run) {
  if (!run.answer) return false;
  if (chat.whileWaiting) return true;
  const answeredAt = run.source === "live" ? run.events.find((event) => event.type === "approval.answered")?.at : undefined;
  return answeredAt !== undefined && Date.parse(chat.at) < Date.parse(answeredAt);
}

function jobTurns(run: Run): ChatTurn[] {
  if (!run.jobId || run.scheduledFor || run.skipped) return [];
  const at = run.at ?? run.job?.startedAt ?? new Date(0).toISOString();
  const trigger = run.trigger ?? "request";
  const you: ChatTurn[] = trigger === "request" && run.request ? [{ kind: "you", text: run.request, at }] : [];
  return [...you, { kind: "job", jobId: run.jobId, trigger, at }];
}

function chatTurns(chat: ChatEntry): ChatTurn[] {
  const you: ChatTurn = { kind: "you", text: chat.text, at: chat.at };
  if (!chat.decision) return [you];
  return [you, { kind: "reply", action: chat.decision.action, facts: chat.decision.facts, clarify: chat.decision.clarify, ...(chat.answer ? { lines: chat.answer } : {}), at: chat.decidedAt ?? chat.at }];
}

export function toChatThread(state: ConsoleState, threadId: string): ChatThread | null {
  const thread = state.threads.find((candidate) => candidate.id === threadId);
  if (!thread) return null;
  const entries = threadEntries(state, threadId).filter((entry) => entry.kind === "chat" || (entry.run.source === "live" && entry.part !== "after"));
  const turns = entries.flatMap((entry) => (entry.kind === "chat" ? chatTurns(entry.chat) : jobTurns(entry.run)));
  if (turns.length === 0) return null;
  return { version: 1, id: thread.id, clientId: thread.clientId, title: thread.title, createdAt: turns[0].at, updatedAt: turns.at(-1)?.at ?? turns[0].at, turns };
}

export function shouldSave(record: ChatThread, known: { server: ChatThread | undefined; lastSaved: string | undefined }) {
  const text = JSON.stringify(record);
  if (known.lastSaved === text) return false;
  if (!known.server) return true;
  return record.turns.length > known.server.turns.length;
}

function talks(thread: ChatThread) {
  return thread.turns.some((turn) => turn.kind === "you" || turn.kind === "reply");
}

export function restoreOrder(threads: readonly ChatThread[]): ChatThread[] {
  return [...threads.filter(talks), ...threads.filter((thread) => !talks(thread))];
}

function shownJobIds(state: ConsoleState) {
  return new Set(Object.values(state.runs).flatMap((run) => (run.jobId ? [run.jobId] : [])));
}

function restoreThread(state: ConsoleState, saved: ChatThread): ConsoleState {
  if (state.threads.some((candidate) => candidate.id === saved.id)) return catchUpThread(state, saved);
  const shown = shownJobIds(state);
  const thread = { ...saved, turns: saved.turns.filter((turn) => turn.kind !== "job" || !shown.has(turn.jobId)) };
  if (!thread.turns.some((turn) => turn.kind === "job" || turn.kind === "reply")) return state;
  const clientId = thread.clientId as ConsoleClientId;
  const added = turnsInto(state, thread.id, clientId, thread.turns, 0);
  return {
    ...state,
    counter: added.counter,
    runs: { ...state.runs, ...added.runs },
    chats: { ...state.chats, ...added.chats },
    threads: [...state.threads, { id: thread.id, clientId, title: thread.title, runKeys: added.runKeys }],
  };
}

function catchUpThread(state: ConsoleState, saved: ChatThread): ConsoleState {
  const open = state.threads.find((candidate) => candidate.id === saved.id);
  const here = toChatThread(state, saved.id)?.turns ?? [];
  if (!open || saved.turns.length <= here.length || JSON.stringify(saved.turns.slice(0, here.length)) !== JSON.stringify(here)) return state;
  const shown = shownJobIds(state);
  const extra = saved.turns.slice(here.length).filter((turn) => turn.kind !== "job" || !shown.has(turn.jobId));
  const added = turnsInto(state, open.id, open.clientId, extra, open.runKeys.length);
  return {
    ...state,
    counter: added.counter,
    runs: { ...state.runs, ...added.runs },
    chats: { ...state.chats, ...added.chats },
    threads: state.threads.map((thread) => (thread.id === open.id ? { ...thread, runKeys: [...thread.runKeys, ...added.runKeys] } : thread)),
  };
}

function turnsInto(state: ConsoleState, threadId: string, clientId: ConsoleClientId, turns: readonly ChatTurn[], runsBefore: number) {
  const thread = { id: threadId };
  let counter = state.counter;
  const runs: Record<string, Run> = {};
  const chats: Record<string, ChatEntry> = {};
  const runKeys: string[] = [];
  let said: { text: string; at: string } | null = null;
  let lastChat: string | undefined;
  for (const turn of turns) {
    if (turn.kind === "you") {
      said = { text: turn.text, at: turn.at };
      continue;
    }
    if (turn.kind === "reply") {
      counter += 1;
      const key = `${state.idPrefix}chat-${counter}`;
      chats[key] = { key, threadId: thread.id, clientId, text: said?.text ?? "", at: said?.at ?? turn.at, afterRuns: runsBefore + runKeys.length, decision: { action: turn.action, facts: turn.facts, clarify: turn.clarify }, decidedAt: turn.at, ...(turn.lines ? { answer: turn.lines } : {}) };
      lastChat = chats[key].text;
      said = null;
      continue;
    }
    counter += 1;
    const key = `${state.idPrefix}run-${counter}`;
    const trigger = turn.trigger === "request" ? undefined : turn.trigger;
    const request = turn.trigger === "request" ? said?.text : turn.trigger === "reply" ? lastChat : undefined;
    runs[key] = {
      key,
      source: "live",
      jobId: turn.jobId,
      ...(trigger ? { trigger } : {}),
      at: turn.at,
      kind: "firstRun",
      clientId,
      threadId: thread.id,
      request,
      events: [],
      played: 0,
      job: null,
      isApproved: false,
      isApprovalHidden: false,
      isStopped: false,
      readyInMs: null,
    };
    runKeys.push(key);
    said = null;
  }
  if (said) {
    counter += 1;
    const key = `${state.idPrefix}chat-${counter}`;
    chats[key] = { key, threadId: thread.id, clientId, text: said.text, at: said.at, afterRuns: runsBefore + runKeys.length, decision: null, problem: "This message was not answered." };
  }
  return { counter, runs, chats, runKeys };
}

function askChat(state: ConsoleState, action: Extract<ConsoleAction, { type: "chatAsked" }>): ConsoleState {
  const existing = state.threads.find((thread) => thread.id === action.threadId);
  const threadId = existing?.id ?? newThreadId(state);
  const lastRun = existing ? state.runs[existing.runKeys.at(-1) ?? ""] : undefined;
  const whileWaiting = Boolean(lastRun && !lastRun.answer && lastRun.job?.status === "waiting");
  const chat: ChatEntry = { key: action.key, threadId, clientId: existing?.clientId ?? state.selectedClientId, text: action.text.trim(), at: action.at, afterRuns: existing?.runKeys.length ?? 0, decision: null, ...(whileWaiting ? { whileWaiting } : {}) };
  return {
    ...state,
    counter: state.counter + 1,
    chats: { ...state.chats, [chat.key]: chat },
    threads: existing ? state.threads : [...state.threads, { id: threadId, clientId: state.selectedClientId, title: "New conversation", runKeys: [] }],
    view: { kind: "thread", threadId },
  };
}

function updateChat(state: ConsoleState, key: string, change: Partial<ChatEntry>): ConsoleState {
  const chat = state.chats[key];
  return chat ? { ...state, chats: { ...state.chats, [key]: { ...chat, ...change } } } : state;
}

export function panelRun(state: ConsoleState): Run | undefined {
  if (state.panel === null) return undefined;
  const chosen = state.panelRunKey ? state.runs[state.panelRunKey] : undefined;
  return chosen ?? activeRun(state);
}

export function nextEventOf(run: Run): JobEvent | undefined {
  if (run.source === "live" || run.isStopped) return undefined;
  const next = run.events[run.played];
  if (next?.type === "approval.answered" && !run.isApproved) return undefined;
  return next;
}

export function clientStatus(state: ConsoleState, clientId: ConsoleClientId) {
  const jobs = Object.values(state.runs)
    .filter((run) => run.clientId === clientId)
    .map((run) => run.job);
  const approvals = jobs.filter((job) => job?.status === "waiting").length;
  if (approvals > 0) return { status: "waiting" as const, approvals };
  if (jobs.some((job) => job?.status === "running")) return { status: "working" as const, approvals: 0 };
  return { status: "idle" as const, approvals: 0 };
}

function viewForClient(state: ConsoleState, clientId: ConsoleClientId): View {
  if (!isSetUp(state, clientId)) return { kind: "setup" };
  const latest = recentThreads(state, clientId)[0];
  return latest ? { kind: "thread", threadId: latest.id } : { kind: "new" };
}

function loadSetup(state: ConsoleState, clientId: ConsoleClientId, setup: ClientSetup): ConsoleState {
  const previous = state.setup[clientId];
  const isFinished = isReady(setup) && (previous ? previous.isFinished : true);
  const loaded = { ...state, setup: { ...state.setup, [clientId]: { ...setup, isFinished } } };
  if (clientId !== state.selectedClientId) return loaded;
  if (isFinished && state.view.kind === "setup") return { ...loaded, view: viewForClient(loaded, clientId) };
  if (!isFinished && state.view.kind === "new") return { ...loaded, view: { kind: "setup" } };
  return loaded;
}

function finishSetup(state: ConsoleState): ConsoleState {
  const setup = state.setup[state.selectedClientId];
  if (!setup || !isReady(setup)) return state;
  return { ...state, setup: { ...state.setup, [state.selectedClientId]: { ...setup, isFinished: true } }, view: { kind: "new" } };
}

function updateRun(state: ConsoleState, runKey: string, change: (run: Run) => Partial<Run>): ConsoleState {
  const run = state.runs[runKey];
  if (!run) return state;
  return { ...state, runs: { ...state.runs, [runKey]: { ...run, ...change(run) } } };
}

function newRun(state: ConsoleState, kind: RunKind, threadId: string, request?: string, trigger?: "run-now" | "undo" | "reply"): Run {
  return {
    key: `run-${state.counter + 1}`,
    source: "playback",
    ...(trigger ? { trigger } : {}),
    kind,
    clientId: state.selectedClientId,
    threadId,
    request,
    events: demoJobs[kind].events,
    played: 0,
    job: null,
    isApproved: false,
    isApprovalHidden: false,
    isStopped: false,
    readyInMs: null,
  };
}

function titleOf(kind: RunKind) {
  const created = demoJobs[kind].events[0];
  return created.type === "job.created" ? created.title : "New job";
}

function withEvent(state: ConsoleState, run: Run, event: JobEvent, live: boolean): ConsoleState {
  const job = applyEvent(run.job, event);
  if (job === run.job) return state;
  const reachedApproval = event.type === "approval.requested";
  const answered = live && event.type === "approval.answered";
  const updated = updateRun(state, run.key, () => ({
    job,
    played: run.played + 1,
    ...(live ? { events: [...run.events, event] } : {}),
    ...(reachedApproval ? { waitingJob: job, readyInMs: job.readyInMs } : {}),
    ...(answered ? { isApproved: true, isApprovalHidden: false, answer: "Save changes" } : {}),
    ...(live && event.type === "job.created" && event.run.kind === "replay" ? { kind: "replay" as const } : {}),
  }));
  const titled =
    live && event.type === "job.created"
      ? { ...updated, threads: updated.threads.map((thread) => (thread.id === run.threadId && (thread.title === "New job" || thread.title === "New conversation") ? { ...thread, title: event.title } : thread)) }
      : updated;
  const ended = job.status === "done" || job.status === "blocked" || job.status === "failed" || job.status === "stopped";
  return ended && titled.panel === "activity" ? { ...titled, panel: null } : titled;
}

function play(state: ConsoleState, runKey: string): ConsoleState {
  const run = state.runs[runKey];
  const event = run ? nextEventOf(run) : undefined;
  if (!run || !event) return state;
  return withEvent(state, run, event, false);
}

function newThreadId(state: ConsoleState) {
  return `${state.idPrefix}thread-${state.counter + 1}`;
}

function startLiveJob(state: ConsoleState, action: Extract<ConsoleAction, { type: "startLiveJob" }>): ConsoleState {
  const { text, trigger } = action;
  const existing = state.threads.find((thread) => thread.id === action.threadId);
  const threadId = existing?.id ?? newThreadId(state);
  const run: Run = {
    key: action.runKey ?? `run-${state.counter + 1}`,
    source: "live",
    ...(trigger ? { trigger } : {}),
    kind: "firstRun",
    clientId: existing?.clientId ?? state.selectedClientId,
    threadId,
    request: text.trim() || undefined,
    events: [],
    played: 0,
    job: null,
    isApproved: false,
    isApprovalHidden: false,
    isStopped: false,
    readyInMs: null,
  };
  return {
    ...state,
    counter: state.counter + 1,
    runs: { ...state.runs, [run.key]: run },
    threads: existing
      ? state.threads.map((thread) => (thread.id === threadId ? { ...thread, runKeys: [...thread.runKeys, run.key] } : thread))
      : [...state.threads, { id: threadId, clientId: state.selectedClientId, title: "New job", runKeys: [run.key] }],
    view: { kind: "thread", threadId },
    panel: "activity",
  };
}

const chosenByThePerson = new Set<ConsoleAction["type"]>(["startJob", "startLiveJob", "chatAsked", "runNow"]);

export function consoleReducer(state: ConsoleState, action: ConsoleAction): ConsoleState {
  const next = reduce(state, action);
  return chosenByThePerson.has(action.type) && next !== state && !next.isViewChosen ? { ...next, isViewChosen: true } : next;
}

function reduce(state: ConsoleState, action: ConsoleAction): ConsoleState {
  switch (action.type) {
    case "selectClient":
      return { ...state, selectedClientId: action.clientId, view: viewForClient(state, action.clientId), panel: null, isViewChosen: true };
    case "newJob":
      return { ...state, view: isSetUp(state, state.selectedClientId) ? { kind: "new" } : { kind: "setup" }, panel: null, isViewChosen: true };
    case "openThread":
      return { ...state, view: { kind: "thread", threadId: action.threadId }, panel: null, isViewChosen: true };
    case "setupLoaded":
      return loadSetup(state, action.clientId, action.setup);
    case "useClaudeChanged": {
      const setup = state.setup[action.clientId];
      return setup ? { ...state, setup: { ...state.setup, [action.clientId]: { ...setup, useClaude: action.useClaude } } } : state;
    }
    case "finishSetup":
      return finishSetup(state);
    case "startJob": {
      const kind = firstJobOf[state.selectedClientId];
      if (!kind) return state;
      const existing = state.threads.find((thread) => thread.id === action.threadId);
      const threadId = existing?.id ?? newThreadId(state);
      const run = newRun(state, kind, threadId, action.text.trim() || undefined, action.trigger);
      return {
        ...state,
        counter: state.counter + 1,
        runs: { ...state.runs, [run.key]: run },
        threads: existing
          ? state.threads.map((thread) => (thread.id === threadId ? { ...thread, runKeys: [...thread.runKeys, run.key] } : thread))
          : [...state.threads, { id: threadId, clientId: state.selectedClientId, title: titleOf(kind), runKeys: [run.key] }],
        view: { kind: "thread", threadId },
        panel: kind === "boundary" ? null : "activity",
      };
    }
    case "startLiveJob":
      return startLiveJob(state, action);
    case "chatAsked":
      return askChat(state, action);
    case "chatAnswered":
      return updateChat(state, action.key, { decision: action.decision, decidedAt: action.at, problem: undefined, ...(action.lines ? { answer: action.lines } : {}) });
    case "chatFailed":
      return updateChat(state, action.key, { problem: action.message });
    case "threadRestored":
      return newestUnlessChosen(restoreThread(state, action.thread));
    case "idPrefixSet":
      return state.idPrefix ? state : { ...state, idPrefix: action.prefix };
    case "jobEvent": {
      const run = Object.values(state.runs).find((candidate) => candidate.source === "live" && candidate.jobId === action.jobId);
      return run ? withEvent(state, run, action.event, true) : state;
    }
    case "scheduledRunSeen":
      return scheduledRunSeen(state, action.clientId, action.run);
    case "waitingJobSeen":
      return waitingJobSeen(state, action.clientId, action.jobId);
    case "liveNotice":
      return updateRun(state, action.runKey, () => ({ notice: action.message }));
    case "liveStarted":
      return updateRun(state, action.runKey, () => ({ jobId: action.jobId }));
    case "liveEvent": {
      const run = state.runs[action.runKey];
      return run ? withEvent(state, run, action.event, true) : state;
    }
    case "liveFailed": {
      const failed = updateRun(state, action.runKey, () => ({ liveError: action.message, isStopped: true }));
      return { ...failed, panel: null };
    }
    case "play":
      return play(state, action.runKey);
    case "approve":
      return updateRun(state, action.runKey, () => ({ isApproved: true, isApprovalHidden: false, answer: "Save changes" }));
    case "notNow":
      return updateRun(state, action.runKey, () => ({ isApprovalHidden: true }));
    case "review":
      return updateRun(state, action.runKey, () => ({ isApprovalHidden: false }));
    case "stop": {
      const stopped = updateRun(state, action.runKey, (run) => ({
        isStopped: true,
        job: run.job ? applyEvent(run.job, stopEvent(run.job, action.at)) : null,
      }));
      return { ...stopped, panel: null };
    }
    case "runNow": {
      const thread = state.threads.find((candidate) => candidate.id === action.threadId);
      if (!thread) return state;
      const scheduled = runsOf(state, thread).filter((run) => scheduledKinds.includes(run.kind)).length;
      const run = { ...newRun(state, scheduledKinds[scheduled % scheduledKinds.length], thread.id), clientId: thread.clientId };
      return {
        ...state,
        counter: state.counter + 1,
        runs: { ...state.runs, [run.key]: run },
        threads: state.threads.map((candidate) => (candidate.id === thread.id ? { ...candidate, runKeys: [...candidate.runKeys, run.key] } : candidate)),
        view: { kind: "thread", threadId: thread.id },
        panel: null,
      };
    }
    case "choose":
      return updateRun(state, action.runKey, () => ({ choice: action.choice }));
    case "openPanel":
      return { ...state, panel: action.panel, panelRunKey: action.runKey };
    case "closePanel":
      return { ...state, panel: null, panelRunKey: undefined };
  }
}
