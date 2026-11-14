import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render, screen, within } from "@testing-library/react";
import { demoJobs } from "@/lib/demoJobs";
import { parseJobEvent } from "@/lib/events";
import { applyEvent, buildJobState, type JobCost, type JobState } from "@/lib/jobState";
import { clientById } from "./clients";
import { ActivityView } from "./Panels";
import type { Run } from "./consoleState";
import { RunMessages, type RunHandlers } from "./RunMessages";

afterEach(cleanup);

const finished = buildJobState(demoJobs.firstRun.events);
const billed: JobCost = {
  claudeCalls: 8,
  inputTokens: 41600,
  outputTokens: 5120,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  claudeUsd: 0.42,
  computerSeconds: 61,
  estimated: false,
};

function withCost(job: JobState, cost: JobCost) {
  return applyEvent(
    job,
    parseJobEvent({ id: `${job.jobId}-${job.lastSeq + 1}`, seq: job.lastSeq + 1, jobId: job.jobId, clientId: job.clientId, at: job.updatedAt, type: "cost.recorded", ...cost }),
  );
}

function valueOf(label: string) {
  return screen.getByText(label).closest("dt")?.nextElementSibling;
}

const ignore = () => {};
const handlers: RunHandlers = {
  onApprove: ignore,
  onNotNow: ignore,
  onReview: ignore,
  onStop: ignore,
  onRunNow: ignore,
  onOpenActivity: ignore,
  onOpenWhatChanged: ignore,
  onChoose: ignore,
  onPick: ignore,
};

function doneRun(job: JobState): Run {
  return {
    key: "run-1",
    source: "live",
    kind: "firstRun",
    clientId: "kumo",
    threadId: "thread-1",
    request: "Sync today's supplier stock to the store admin",
    events: [],
    played: 0,
    job,
    isApproved: true,
    isApprovalHidden: false,
    isStopped: false,
    answer: "Save changes",
    readyInMs: null,
  };
}

describe("ActivityView cost", () => {
  test("shows nothing about cost before the job's cost is recorded", () => {
    render(<ActivityView client={clientById("kumo")} job={finished} onStop={() => {}} />);
    expect(screen.queryByText("Claude")).toBeNull();
    expect(screen.queryByText("Computer")).toBeNull();
  });

  test("lists Claude's calls and dollars and the computer's time in the activity details", () => {
    render(<ActivityView client={clientById("kumo")} job={withCost(finished, billed)} onStop={() => {}} />);
    expect(valueOf("Claude")?.tagName).toBe("DD");
    expect(valueOf("Claude")?.textContent).toBe("8 calls · $0.42");
    expect(valueOf("Computer")?.textContent).toBe("1 min");
  });

  test("says when the computer time is an estimate from the local computer", () => {
    render(<ActivityView client={clientById("kumo")} job={withCost(finished, { ...billed, estimated: true })} onStop={() => {}} />);
    expect(screen.getByText("1 min (estimated)")).toBeTruthy();
  });

  test("says Claude was not used on a job that ran the saved steps", () => {
    render(
      <ActivityView
        client={clientById("kumo")}
        job={withCost(finished, { ...billed, claudeCalls: 0, inputTokens: 0, outputTokens: 0, claudeUsd: 0, computerSeconds: 6 })}
        onStop={() => {}}
      />,
    );
    expect(screen.getByText("Not used")).toBeTruthy();
    expect(screen.getByText("6 s")).toBeTruthy();
  });

  test("keeps the cost out of the conversation", () => {
    const { container } = render(
      <RunMessages run={doneRun(withCost(finished, billed))} client={clientById("kumo")} owner="" isLast isActivityOpen={false} firstReadyMs={null} handlers={handlers} />,
    );
    const body = within(container);
    expect(body.queryByText(/\$0\.42/)).toBeNull();
    expect(body.queryByText(/8 calls/)).toBeNull();
    expect(container.textContent).not.toContain("$");
  });
});
