"use client";

import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { ChatSystemMessage } from "@astryxdesign/core/Chat";
import { Link } from "@astryxdesign/core/Link";
import { ProgressBar } from "@astryxdesign/core/ProgressBar";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Table } from "@astryxdesign/core/Table";
import { Text } from "@astryxdesign/core/Text";
import { useState } from "react";
import { ApprovalCard } from "@/components/ApprovalCard";
import { ChoiceCard } from "@/components/ChoiceCard";
import { Entrance } from "@/components/Motion";
import { ProgressCard } from "@/components/ProgressCard";
import { ResultLine } from "@/components/ResultLine";
import { UserMessage } from "@/components/UserMessage";
import { WorkerMessage } from "@/components/WorkerMessage";
import type { LeftAlone, StockChange } from "@/lib/events";
import type { JobState } from "@/lib/jobState";
import { leftAloneText, progressOf, replayEstimate, seconds, workerSays } from "@/lib/jobView";
import type { ConsoleClient } from "./clients";
import type { BoundaryChoice, Run, RunPart } from "./consoleState";
import type { ScheduleApi } from "./scheduleApi";
import { ScheduleControl, ScheduledDivider, SkippedRunLine } from "./ScheduleControl";

export type RunHandlers = {
  onApprove: (run: Run) => void;
  onNotNow: (run: Run) => void;
  onReview: (run: Run) => void;
  onStop: (run: Run) => void;
  onRunNow: () => void;
  onOpenActivity: (run: Run) => void;
  onOpenWhatChanged: () => void;
  onChoose: (run: Run, choice: BoundaryChoice) => void;
  onPick: (run: Run, ref: string | null) => void;
  onUndo?: () => void;
};

const scheduledAt: Partial<Record<Run["kind"], string>> = { replay: "Today, 18:00", repair: "Tomorrow, 18:00" };

type ChangeRow = StockChange & Record<string, unknown>;

const changeColumns = [
  { key: "name", header: "Product" },
  {
    key: "supplier",
    header: "Store → Supplier",
    align: "end" as const,
    renderCell: (row: ChangeRow) => `${row.store} → ${row.supplier === 0 ? "sold out" : row.supplier}`,
  },
];

function ApprovalDetail({
  changes,
  isOpenAtFirst,
  undoable,
  leftAlone,
}: {
  changes: StockChange[];
  isOpenAtFirst: boolean;
  undoable: boolean;
  leftAlone?: LeftAlone;
}) {
  const leftAloneNote = leftAloneText(leftAlone);
  const [shown, setShown] = useState(isOpenAtFirst ? 3 : 0);
  const rows = changes.slice(0, shown) as ChangeRow[];
  return (
    <VStack gap={2}>
      {rows.length > 0 ? <Table data={rows} columns={changeColumns} idKey="sku" density="compact" /> : null}
      <HStack hAlign="between" vAlign="center">
        {shown < changes.length ? (
          <Button
            label={shown === 0 ? "See changes" : `See all ${changes.length} changes`}
            variant="ghost"
            size="sm"
            onClick={() => setShown(changes.length)}
          />
        ) : (
          <Text type="supporting">{`${changes.length} changes`}</Text>
        )}
        {undoable ? <Text type="supporting">You can undo afterwards</Text> : null}
      </HStack>
      {leftAloneNote ? <Text type="supporting">{leftAloneNote}</Text> : null}
    </VStack>
  );
}

function TimeCompare({ firstMs, todayMs }: { firstMs: number; todayMs: number }) {
  const rows = [
    { label: "First time", ms: firstMs },
    { label: "Today", ms: todayMs },
  ];
  return (
    <Entrance>
      <Card padding={3} width="100%">
        <VStack gap={2} role="group" aria-label="Time compared with the first run">
          {rows.map((row) => (
            <HStack key={row.label} gap={3} vAlign="center">
              <Text type="supporting" style={{ minWidth: "5em" }}>
                {row.label}
              </Text>
              <VStack style={{ flex: 1 }}>
                <ProgressBar label={`${row.label}, ${seconds(row.ms)}`} isLabelHidden value={row.ms} max={firstMs} variant="neutral" />
              </VStack>
              <Text type="label" style={{ minWidth: "3.5em", textAlign: "end" }}>
                {seconds(row.ms)}
              </Text>
            </HStack>
          ))}
          <Text type="supporting">Time to get the changes ready. No AI was needed this time.</Text>
        </VStack>
      </Card>
    </Entrance>
  );
}

type ScheduleLineProps = { client: ConsoleClient; scheduleApi?: ScheduleApi; onRunNow?: () => void; onUndo?: () => void };

function ScheduleLine({ client, scheduleApi, onRunNow, onUndo }: ScheduleLineProps) {
  return (
    <Entrance>
      <HStack gap={3} vAlign="center" wrap="wrap">
        <ScheduleControl client={client} api={scheduleApi} />
        {onRunNow ? <Button label="Run now" variant="ghost" size="sm" onClick={onRunNow} /> : null}
        {onUndo ? (
          <Button label="Undo this sync" variant="ghost" size="sm" onClick={onUndo} />
        ) : (
          <Link isDisabled>Undo this sync</Link>
        )}
      </HStack>
    </Entrance>
  );
}

function SavedCard({ job, renamedButton, ...line }: { job: JobState; renamedButton: boolean } & ScheduleLineProps) {
  const isFirstVersion = job.procedure?.version === 1;
  return (
    <Entrance>
      <Card padding={4} width="100%">
        <VStack gap={3} role="group" aria-label="Saved for next time">
          <VStack gap={0.5}>
            <Text type="label" weight="semibold">
              {isFirstVersion ? "I saved how to do this" : "I updated the saved steps"}
            </Text>
            <Text type="supporting">
              {isFirstVersion
                ? `Next time it takes ${replayEstimate(job.procedure?.expectedReplayMs ?? 6000)} and needs no AI.`
                : renamedButton
                  ? "From now on they use the new button."
                  : "The page changed. The saved steps now follow it."}
            </Text>
          </VStack>
          <ScheduleLine {...line} />
        </VStack>
      </Card>
    </Entrance>
  );
}

function BoundaryReply({ run, job, client, owner, onChoose }: { run: Run; job: JobState; client: ConsoleClient; owner: string; onChoose: RunHandlers["onChoose"] }) {
  return (
    <>
      <WorkerMessage
        attachment={
          <VStack gap={3}>
            <Text>{`I can check prices with ${client.nickname}'s own supplier account instead.`}</Text>
            {run.choice ? null : (
              <HStack gap={1.5}>
                <Button label={`Use ${client.nickname}'s account`} variant="primary" onClick={() => onChoose(run, "own-account")} />
                <Button label="Cancel" variant="ghost" onClick={() => onChoose(run, "cancel")} />
              </HStack>
            )}
            <Text type="supporting">This request was recorded in the audit log.</Text>
          </VStack>
        }
      >
        <Card padding={4} width="100%">
          <VStack gap={1} role="group" aria-label="Not allowed">
            <ResultLine kind="blocked">{workerSays(job)}</ResultLine>
            <Text type="supporting">{`Each client has its own computer, logins and files. ${client.nickname}'s worker can't reach ${owner}'s.`}</Text>
          </VStack>
        </Card>
      </WorkerMessage>
      {run.choice ? <UserMessage>{run.choice === "own-account" ? `Use ${client.nickname}'s account` : "Cancel"}</UserMessage> : null}
      {run.choice === "own-account" ? (
        <WorkerMessage>
          <ResultLine kind="done">{`I'll use ${client.nickname}'s own supplier account for this.`}</ResultLine>
        </WorkerMessage>
      ) : null}
      {run.choice === "cancel" ? (
        <WorkerMessage>
          <ResultLine kind="stopped">Cancelled. Nothing was used.</ResultLine>
        </WorkerMessage>
      ) : null}
    </>
  );
}

function nounOf(role: string | undefined) {
  return role === "button" || role === "link" ? role : "field";
}

function pickedName(run: Run, job: JobState) {
  const answer = job.lastChoice;
  if (!answer?.ref) return null;
  const asked = run.events.find((event) => event.type === "choice.requested" && event.choiceId === answer.choiceId);
  return asked?.type === "choice.requested" ? (asked.candidates.find((candidate) => candidate.ref === answer.ref)?.name ?? null) : null;
}

function Waiting({ run, job, firstReadyMs, handlers }: { run: Run; job: JobState; firstReadyMs: number | null; handlers: RunHandlers }) {
  const choice = job.pendingChoice;
  if (choice) {
    return (
      <ChoiceCard
        question={choice.question}
        detail={`It used ${choice.before} before. Nothing has been saved yet.`}
        candidates={choice.candidates}
        confirmLabel={`Use this ${nounOf(choice.candidates[0]?.role)}`}
        onPick={(ref) => handlers.onPick(run, ref)}
      />
    );
  }
  const approval = job.pendingApproval;
  if (!approval) return null;
  if (run.isApprovalHidden) {
    return (
      <HStack gap={2} vAlign="center">
        <Text type="supporting">Waiting for your OK.</Text>
        <Button label="Review changes" variant="secondary" size="sm" onClick={() => handlers.onReview(run)} />
      </HStack>
    );
  }
  return (
    <VStack gap={3}>
      {run.kind === "replay" && firstReadyMs !== null && run.readyInMs !== null ? <TimeCompare firstMs={firstReadyMs} todayMs={run.readyInMs} /> : null}
      <ApprovalCard
        question={approval.question}
        detail={<ApprovalDetail changes={approval.changes} isOpenAtFirst={run.kind === "firstRun"} undoable={approval.undoable} leftAlone={approval.leftAlone} />}
        confirmLabel="Save changes"
        onConfirm={() => handlers.onApprove(run)}
        onDecline={() => handlers.onNotNow(run)}
      />
      {approval.procedureChange ? (
        <HStack>
          <Button label="What changed" variant="ghost" size="sm" onClick={handlers.onOpenWhatChanged} />
        </HStack>
      ) : null}
    </VStack>
  );
}

function Running({ run, job, isActivityOpen, handlers }: { run: Run; job: JobState; isActivityOpen: boolean; handlers: RunHandlers }) {
  const progress = progressOf(job);
  return (
    <ProgressCard
      title={progress.title}
      done={progress.done}
      total={progress.total}
      onStop={() => handlers.onStop(run)}
      onOpenActivity={() => handlers.onOpenActivity(run)}
      isActivityOpen={isActivityOpen}
    />
  );
}

export function RunMessages({
  run,
  client,
  owner,
  isLast,
  isActivityOpen,
  firstReadyMs,
  handlers,
  scheduleApi,
  part = "whole",
}: {
  part?: RunPart;
  run: Run;
  client: ConsoleClient;
  owner: string;
  isLast: boolean;
  isActivityOpen: boolean;
  firstReadyMs: number | null;
  handlers: RunHandlers;
  scheduleApi?: ScheduleApi;
}) {
  if (run.skipped) return <SkippedRunLine slot={run.skipped.slot} timeZone={run.skipped.timeZone} />;
  const job = run.job;
  const divider = run.trigger === "run-now" ? "Run now" : run.source === "playback" ? scheduledAt[run.kind] : undefined;
  const opening = run.trigger === "reply" ? null : run.scheduledFor ? (
    <Entrance>
        <ScheduledDivider slot={run.scheduledFor.slot} timeZone={run.scheduledFor.timeZone} />
    </Entrance>
  ) : divider ? (
    <Entrance>
        <ChatSystemMessage variant="divider">{divider}</ChatSystemMessage>
    </Entrance>
  ) : (
    <UserMessage>{run.request ?? job?.request}</UserMessage>
  );
  const lost = run.liveError ? (
    <WorkerMessage>
      <ResultLine kind="stopped">{run.liveError}</ResultLine>
    </WorkerMessage>
  ) : null;
  if (!job) {
    return (
      <>
        {opening}
        {lost}
      </>
    );
  }
  if (job.status === "blocked") {
    return (
      <>
        {opening}
        <BoundaryReply run={run} job={job} client={client} owner={owner} onChoose={handlers.onChoose} />
      </>
    );
  }

  const before = run.answer && run.waitingJob ? run.waitingJob : job;
  const beforeAttachment =
    run.answer && run.waitingJob ? (
      run.kind === "replay" && firstReadyMs !== null && run.readyInMs !== null ? <TimeCompare firstMs={firstReadyMs} todayMs={run.readyInMs} /> : undefined
    ) : job.status === "waiting" ? (
      <Waiting run={run} job={job} firstReadyMs={firstReadyMs} handlers={handlers} />
    ) : job.status === "running" ? (
      <Running run={run} job={job} isActivityOpen={isActivityOpen} handlers={handlers} />
    ) : job.status === "done" && isLast && run.trigger ? (
      <ScheduleLine client={client} scheduleApi={scheduleApi} onRunNow={handlers.onRunNow} onUndo={run.trigger === "undo" ? undefined : handlers.onUndo} />
    ) : undefined;

  const ended = job.status === "done" || job.status === "stopped" || job.status === "failed";
  const result = ended ? <ResultLine kind={job.status === "done" ? "done" : "stopped"}>{workerSays(job)}</ResultLine> : null;
  const afterAnswer = run.answer ? (
    <>
      <UserMessage>{run.answer}</UserMessage>
      <WorkerMessage
        attachment={
          job.status === "running" ? (
            <Running run={run} job={job} isActivityOpen={isActivityOpen} handlers={handlers} />
          ) : job.procedure && job.status === "done" ? (
            <SavedCard
              job={job}
              renamedButton={Boolean(run.waitingJob?.pendingApproval?.procedureChange)}
              client={client}
              scheduleApi={scheduleApi}
              onRunNow={isLast ? handlers.onRunNow : undefined}
              onUndo={isLast ? handlers.onUndo : undefined}
            />
          ) : job.status === "done" && isLast ? (
            <ScheduleLine client={client} scheduleApi={scheduleApi} onRunNow={handlers.onRunNow} onUndo={handlers.onUndo} />
          ) : undefined
        }
      >
        {result ?? workerSays(job)}
      </WorkerMessage>
    </>
  ) : null;

  const picked = pickedName(run, before);
  const beforePart = (
    <>
      {opening}
      {picked ? <UserMessage>{`Use ${picked}`}</UserMessage> : null}
      <WorkerMessage attachment={beforeAttachment}>{!run.answer && result ? result : workerSays(before)}</WorkerMessage>
    </>
  );
  if (part === "before") return beforePart;
  return (
    <>
      {part === "whole" ? beforePart : null}
      {afterAnswer}
      {ended && !isActivityOpen ? (
        <Entrance>
          <HStack>
            <Button label="Show activity" variant="ghost" size="sm" onClick={() => handlers.onOpenActivity(run)} />
          </HStack>
        </Entrance>
      ) : null}
      {run.notice ? (
        <Entrance>
          <Text type="supporting">{run.notice}</Text>
        </Entrance>
      ) : null}
      {lost}
    </>
  );
}
