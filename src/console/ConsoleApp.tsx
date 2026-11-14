"use client";

import { ClickableCard } from "@astryxdesign/core/ClickableCard";
import { Heading } from "@astryxdesign/core/Heading";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type CSSProperties } from "react";
import type { ClientSchedule } from "@/lib/schedule";
import { scheduleApi, scheduledRunKey, unseenScheduledRuns, type ScheduleApi } from "./scheduleApi";
import { threadSubtitle } from "./threadSubtitle";
import { createJob, followJob, recheckLogins } from "./jobsClient";
import { approve, choose, stop, undo } from "@/lib/approvalsClient";
import { loadWaitingJobs } from "./waitingApi";
import { chatApi } from "./chatApi";
import { ChatMessages } from "./ChatMessages";
import { factCatalog, factLines, type FactSource } from "@/lib/chatFacts";
import { decideByRules } from "@/lib/chatRules";
import type { ChatThread } from "@/lib/chatStore";
import { AppFrame } from "@/components/AppFrame";
import { Composer } from "@/components/Composer";
import { ActivityView, WhatChangedView } from "./Panels";
import { RunMessages, type RunHandlers } from "./RunMessages";
import { SetupView } from "./SetupView";
import { setupApi } from "./setupApi";
import { clientById, consoleClients, type ConsoleClient } from "./clients";
import {
  activeRun,
  clientStatus,
  consoleReducer,
  initialConsoleState,
  nextEventOf,
  recentScheduledRuns,
  panelRun,
  runsOf,
  setupProgress,
  threadEntries,
  recentThreads,
  restoreOrder,
  shouldSave,
  toChatThread,
  type ConsoleAction,
  type ConsoleClientId,
  type ConsoleState,
  type Run,
} from "./consoleState";

const PLAYBACK_SPEED = 4;
const SCHEDULE_POLL_MS = 20_000;
const RESTORED_THREADS = 15;
const SAVE_AFTER_MS = 400;
const userName = "Aoi Tanaka";

const centeredColumn: CSSProperties = { width: "100%", maxWidth: 640, marginInline: "auto", flex: 1, justifyContent: "center" };

function delayBefore(run: Run) {
  const next = nextEventOf(run);
  const previous = run.events[run.played - 1];
  if (!next || !previous || next.type === "approval.answered") return 0;
  return Math.max(0, Date.parse(next.at) - Date.parse(previous.at)) / PLAYBACK_SPEED;
}

function ownerOf(run: Run) {
  const result = run.job?.result;
  if (result?.kind !== "blocked") return "";
  return consoleClients.find((candidate) => candidate.id === result.ownerClientId)?.nickname ?? result.ownerClientName;
}

function usePlayback(state: ConsoleState, dispatch: (action: ConsoleAction) => void) {
  useEffect(() => {
    const timers = Object.values(state.runs)
      .filter((run) => nextEventOf(run) !== undefined)
      .map((run) => setTimeout(() => dispatch({ type: "play", runKey: run.key }), delayBefore(run)));
    return () => timers.forEach(clearTimeout);
  }, [state.runs, dispatch]);
}

function useClientSetup(state: ConsoleState, dispatch: (action: ConsoleAction) => void) {
  const [problems, setProblems] = useState<Partial<Record<ConsoleClientId, string>>>({});
  const clientId = state.selectedClientId;
  const isKnown = state.setup[clientId] !== undefined;
  const problem = problems[clientId];
  useEffect(() => {
    if (isKnown || problem) return;
    let isCurrent = true;
    setupApi.load(clientId).then(
      (setup) => {
        if (isCurrent) dispatch({ type: "setupLoaded", clientId, setup });
      },
      (error: unknown) => {
        if (isCurrent) setProblems((known) => ({ ...known, [clientId]: error instanceof Error ? error.message : "Something went wrong." }));
      },
    );
    return () => {
      isCurrent = false;
    };
  }, [clientId, isKnown, problem, dispatch]);
  return { problem, retry: () => setProblems((known) => ({ ...known, [clientId]: undefined })) };
}

function NewJobView({ client, onStart, onSay }: { client: ConsoleClient; onStart: (text: string) => void; onSay?: (text: string) => void }) {
  const start = onStart;
  return (
    <VStack gap={6} padding={6} style={centeredColumn}>
      <Heading level={1} justify="center">{`What should ${client.shortName}'s worker do?`}</Heading>
      <Composer clientName={client.shortName} onSubmit={onSay ?? start} />
      {client.starters.length > 0 ? (
        <VStack gap={2}>
          <Text type="supporting">Start with one of these</Text>
          {client.starters.map((starter) => (
            <ClickableCard key={starter.text} label={starter.text} padding={3} onClick={() => start(starter.text)}>
              <HStack hAlign="between" vAlign="center" gap={3}>
                <Text>{starter.text}</Text>
                <Text type="supporting">{starter.note}</Text>
              </HStack>
            </ClickableCard>
          ))}
        </VStack>
      ) : null}
    </VStack>
  );
}

export type JobsMode = "live" | "playback";

function useStartJob(state: ConsoleState, dispatch: (action: ConsoleAction) => void, jobsMode: JobsMode) {
  const follows = useRef<(() => void)[]>([]);
  useEffect(() => () => follows.current.forEach((close) => close()), []);
  return (
    text: string,
    options: { threadId?: string; trigger?: "run-now" | "undo" | "reply"; begin?: (clientId: string) => Promise<{ jobId: string } | { error: string }> } = {},
  ) => {
    if (jobsMode === "playback") {
      dispatch({ type: "startJob", text, threadId: options.threadId, trigger: options.trigger });
      return;
    }
    const runKey = `${state.idPrefix}live-${crypto.randomUUID()}`;
    const clientId = state.selectedClientId;
    dispatch({ type: "startLiveJob", text, threadId: options.threadId, trigger: options.trigger, runKey });
    void (async () => {
      await recheckLogins(clientId);
      const created = await (options.begin ?? ((id: string) => createJob(id, text)))(clientId);
      if ("error" in created) {
        dispatch({ type: "liveFailed", runKey, message: created.error });
        return;
      }
      dispatch({ type: "liveStarted", runKey, jobId: created.jobId });
      follows.current.push(
        followJob(created.jobId, (frame) => {
          if (frame.kind === "event") dispatch({ type: "liveEvent", runKey, event: frame.event });
          if (frame.kind === "unavailable") dispatch({ type: "liveFailed", runKey, message: "The worker service stopped answering." });
          if (frame.kind === "missing") dispatch({ type: "liveFailed", runKey, message: "This job could not be found." });
        }),
      );
    })();
  };
}

function useLiveSchedule(clientId: ConsoleClientId, isLive: boolean, dispatch: (action: ConsoleAction) => void, fromServer: Map<string, ChatThread>) {
  const [schedule, setSchedule] = useState<ClientSchedule | null>(null);
  const seen = useRef(new Set<string>());
  const followed = useRef(new Map<string, () => void>());
  const restored = useRef(new Set<string>());
  const read = useRef(new Map<string, string>());
  useEffect(() => () => followed.current.forEach((close) => close()), []);
  const refresh = useCallback(async () => {
    if (!isLive) return;
    const follow = (jobId: string) => {
      if (followed.current.has(jobId)) return;
      followed.current.set(jobId, followJob(jobId, (frame) => {
        if (frame.kind === "event") dispatch({ type: "jobEvent", jobId, event: frame.event });
      }));
    };
    const readSaved = async (id: ConsoleClientId) => {
      const fresh: ChatThread[] = [];
      for (const entry of (await chatApi.list(id)).slice(0, RESTORED_THREADS)) {
        if (read.current.get(entry.id) === entry.updatedAt) continue;
        const thread = await chatApi.read(id, entry.id);
        if (!thread) continue;
        read.current.set(entry.id, entry.updatedAt);
        fromServer.set(thread.id, thread);
        fresh.push(thread);
      }
      for (const thread of restoreOrder(fresh)) {
        dispatch({ type: "threadRestored", thread });
        for (const turn of thread.turns) if (turn.kind === "job") follow(turn.jobId);
      }
    };
    if (!restored.current.has(clientId)) {
      restored.current.add(clientId);
      await readSaved(clientId);
    }
    const reply = await scheduleApi.load(clientId).catch(() => null);
    if (reply) {
      setSchedule(reply.schedule);
      for (const run of recentScheduledRuns(unseenScheduledRuns(reply.runs, seen.current), new Date())) {
        seen.current.add(scheduledRunKey(run));
        dispatch({ type: "scheduledRunSeen", clientId, run });
        if (run.status === "started") follow(run.jobId);
      }
    }
    for (const other of consoleClients) {
      const waiting = (await loadWaitingJobs(other.id)) ?? [];
      if (waiting.some((jobId) => !followed.current.has(jobId))) await readSaved(other.id);
      for (const jobId of waiting) {
        if (followed.current.has(jobId)) continue;
        dispatch({ type: "waitingJobSeen", clientId: other.id, jobId });
        follow(jobId);
      }
    }
  }, [clientId, isLive, dispatch, fromServer]);
  useEffect(() => {
    if (!isLive) return;
    const first = setTimeout(() => void refresh(), 0);
    const timer = setInterval(() => void refresh(), SCHEDULE_POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, isLive]);
  const api = useMemo<ScheduleApi>(
    () => ({
      load: (id) => scheduleApi.load(id),
      save: async (id, change) => {
        const reply = await scheduleApi.save(id, change);
        setSchedule(reply.schedule);
        return reply;
      },
    }),
    [],
  );
  return { schedule, api };
}

function useSavedThreads(state: ConsoleState, isLive: boolean, fromServer: ReadonlyMap<string, ChatThread>) {
  const saved = useRef(new Map<string, string>());
  useEffect(() => {
    if (!isLive) return;
    const timer = setTimeout(() => {
      for (const thread of state.threads) {
        const record = toChatThread(state, thread.id);
        if (!record || !shouldSave(record, { server: fromServer.get(thread.id), lastSaved: saved.current.get(thread.id) })) continue;
        const text = JSON.stringify(record);
        saved.current.set(thread.id, text);
        void chatApi.save(record).then((ok) => {
          if (!ok) saved.current.delete(thread.id);
        });
      }
    }, SAVE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [state, isLive, fromServer]);
}

function factSourceFor(state: ConsoleState, threadId: string, afterRuns: number, schedule: ClientSchedule | null): FactSource {
  const thread = state.threads.find((candidate) => candidate.id === threadId);
  const runs = thread ? runsOf(state, thread).slice(0, afterRuns) : [];
  const last = runs.at(-1);
  const first = runs.find((run) => run.kind === "firstRun" && run.readyInMs !== null);
  return { job: last?.job ?? null, waitingJob: last?.waitingJob, readyInMs: last?.readyInMs ?? null, firstReadyMs: first?.readyInMs ?? null, schedule };
}

function stamp() {
  return new Date().toISOString();
}

export function ConsoleApp({ jobsMode }: { jobsMode: JobsMode }) {
  const [state, dispatch] = useReducer(consoleReducer, initialConsoleState);
  useEffect(() => dispatch({ type: "idPrefixSet", prefix: `${crypto.randomUUID().slice(0, 8)}-` }), []);
  usePlayback(state, dispatch);
  const startJob = useStartJob(state, dispatch, jobsMode);
  const [fromServer] = useState(() => new Map<string, ChatThread>());
  const liveSchedule = useLiveSchedule(state.selectedClientId, jobsMode === "live", dispatch, fromServer);
  useSavedThreads(state, jobsMode === "live", fromServer);

  const sendMessage = (text: string, threadId?: string) => {
    const thread = state.threads.find((candidate) => candidate.id === threadId);
    const conversationId = thread?.id ?? `${state.idPrefix}thread-${state.counter + 1}`;
    const key = `${state.idPrefix}chat-${state.counter + 1}`;
    const source = factSourceFor(state, conversationId, thread?.runKeys.length ?? 0, liveSchedule.schedule);
    const facts = factCatalog(source).map((fact) => fact.id);
    dispatch({ type: "chatAsked", threadId: conversationId, key, text, at: stamp() });
    const act = (decision: Exclude<Awaited<ReturnType<typeof chatApi.ask>>, { error: string }>) => {
      const lines = decision.action === "answer" ? decision.facts.flatMap((id) => factLines(id, source)) : undefined;
      dispatch({ type: "chatAnswered", key, decision, at: stamp(), ...(lines ? { lines } : {}) });
      if (decision.action === "run") startJob(text, { threadId: conversationId, trigger: "reply" });
      if (decision.action === "undo" && jobsMode === "live") startJob("Undo this sync", { threadId: conversationId, trigger: "undo", begin: (clientId) => undo(clientId) });
    };
    if (jobsMode === "playback") {
      act(decideByRules(text, facts, thread?.clientId ?? state.selectedClientId));
      return;
    }
    void chatApi.ask(state.selectedClientId, text, facts).then((answer) => {
      if ("error" in answer) dispatch({ type: "chatFailed", key, message: answer.error });
      else act(answer);
    });
  };
  const setupLoad = useClientSetup(state, dispatch);

  const client = clientById(state.selectedClientId);
  const thread = state.view.kind === "thread" ? state.threads.find((candidate) => state.view.kind === "thread" && candidate.id === state.view.threadId) : undefined;
  const runs = thread ? runsOf(state, thread) : [];
  const current = activeRun(state);
  const job = current?.job ?? null;
  const firstReadyMs = runs.find((run) => run.kind === "firstRun")?.readyInMs ?? null;
  const hasSchedule = runs.some((run) => run.job?.procedure || run.job?.run.kind === "replay");

  const notify = (run: Run, result: { ok: true } | { error: string }) => {
    if ("error" in result) dispatch({ type: "liveNotice", runKey: run.key, message: result.error });
  };
  const handlers: RunHandlers = {
    onApprove: (run) => {
      const approvalId = run.job?.pendingApproval?.approvalId;
      if (run.source === "live" && run.jobId && approvalId) void approve(run.jobId, approvalId).then((result) => notify(run, result));
      else dispatch({ type: "approve", runKey: run.key });
    },
    onNotNow: (run) => dispatch({ type: "notNow", runKey: run.key }),
    onReview: (run) => dispatch({ type: "review", runKey: run.key }),
    onStop: (run) => {
      if (run.source === "live" && run.jobId) void stop(run.jobId).then((result) => notify(run, result));
      else dispatch({ type: "stop", runKey: run.key, at: new Date(Date.parse(run.job?.updatedAt ?? stamp()) + 1).toISOString() });
    },
    onRunNow: () => {
      if (!thread) return;
      const first = runs[0];
      if (first?.source === "live") startJob(first.request ?? first.job?.request ?? thread.title, { threadId: thread.id, trigger: "run-now" });
      else dispatch({ type: "runNow", threadId: thread.id });
    },
    onUndo:
      jobsMode === "live" && thread ? () => startJob("Undo this sync", { threadId: thread.id, trigger: "undo", begin: (clientId) => undo(clientId) }) : undefined,
    onOpenActivity: (run) => dispatch({ type: "openPanel", panel: "activity", runKey: run.key }),
    onOpenWhatChanged: () => dispatch({ type: "openPanel", panel: "what-changed" }),
    onChoose: (run, choice) => dispatch({ type: "choose", runKey: run.key, choice }),
    onPick: (run, ref) => {
      const choiceId = run.job?.pendingChoice?.choiceId;
      if (run.source === "live" && run.jobId && choiceId) void choose(run.jobId, choiceId, ref).then((result) => notify(run, result));
    },
  };

  const clients = consoleClients.map((candidate) => {
    const { status, approvals } = clientStatus(state, candidate.id);
    return {
      name: candidate.name,
      href: `/?client=${candidate.id}`,
      status,
      approvals,
      isSelected: candidate.id === state.selectedClientId,
      onSelect: () => dispatch({ type: "selectClient", clientId: candidate.id }),
    };
  });
  const recent = recentThreads(state, state.selectedClientId).map((candidate) => ({
      label: candidate.title,
      href: `/?job=${candidate.id}`,
      isSelected: candidate.id === thread?.id,
      onSelect: () => dispatch({ type: "openThread", threadId: candidate.id }),
    }));

  const shown = panelRun(state);
  const shownJob = shown?.job ?? null;
  const panel =
    shown && shownJob && state.panel === "activity" ? (
      <ActivityView client={client} job={shownJob} onStop={() => handlers.onStop(shown)} />
    ) : shownJob && state.panel === "what-changed" ? (
      <WhatChangedView job={shownJob} />
    ) : undefined;

  const common = {
    userName,
    clients,
    recent,
    onNewJob: () => dispatch({ type: "newJob" }),
    setup: setupProgress(state, state.selectedClientId),
    viewKey: `${state.selectedClientId}:${state.view.kind === "thread" ? state.view.threadId : state.view.kind}`,
  };

  if (state.view.kind === "setup") {
    return <AppFrame {...common} title={client.name} isTitleHeading={false} main={
          <SetupView client={client} setup={state.setup[client.id]} problem={setupLoad.problem} onRetry={setupLoad.retry} dispatch={dispatch} api={setupApi} />
        } />;
  }
  if (state.view.kind === "new" || !thread) {
    return <AppFrame {...common} title={client.name} isTitleHeading={false} main={<NewJobView client={client} onStart={(text) => startJob(text)} onSay={(text) => sendMessage(text)} />} />;
  }

  const isWorking = job?.status === "running";
  return (
    <AppFrame
      {...common}
      title={thread.title}
      subtitle={threadSubtitle({ clientName: client.name, jobsMode, schedule: liveSchedule.schedule, hasSavedSteps: hasSchedule })}
      composer={<Composer clientName={client.shortName} isWorking={isWorking} isFollowUp={!isWorking} onSubmit={(text) => sendMessage(text, thread.id)} />}
      activity={panel}
      panelTitle={state.panel === "what-changed" ? "What changed" : "Activity"}
      onCloseActivity={() => dispatch({ type: "closePanel" })}
    >
      {threadEntries(state, thread.id).map((entry) =>
        entry.kind === "chat" ? (
          <ChatMessages
            key={entry.chat.key}
            chat={entry.chat}
            source={factSourceFor(state, thread.id, entry.chat.afterRuns, liveSchedule.schedule)}
            client={client}
            scheduleApi={liveSchedule.api}
          />
        ) : (
          <RunMessages
            key={entry.part === "after" ? `${entry.run.key}-after` : entry.run.key}
            part={entry.part}
            run={entry.run}
            client={client}
            owner={ownerOf(entry.run)}
            isLast={entry.run.key === runs.at(-1)?.key}
            isActivityOpen={state.panel === "activity"}
            firstReadyMs={firstReadyMs}
            handlers={handlers}
            scheduleApi={liveSchedule.api}
          />
        ),
      )}
    </AppFrame>
  );
}
