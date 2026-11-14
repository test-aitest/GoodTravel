import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import choiceContract from "../../agent/tests/contract/choice_job_notes.json";
import { eventNoteTag, newEvents } from "@/lib/agentfieldRecords";
import { defaultSchedule, scheduleText, type ClientSchedule } from "@/lib/schedule";
import { demoJobs } from "@/lib/demoJobs";
import { buildJobState } from "@/lib/jobState";
import { splitAtApproval } from "@/lib/playback";
import { clientById } from "./clients";
import type { Run } from "./consoleState";
import { RunMessages, type RunHandlers } from "./RunMessages";
import type { ScheduleApi } from "./scheduleApi";

afterEach(cleanup);

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

function waitingRun(leftAlone?: { onlyInStore: number; onlyAtSupplier: number }): Run {
  const events = splitAtApproval(demoJobs.firstRun.events).untilApproval.map((event) =>
    event.type === "approval.requested" && leftAlone ? { ...event, leftAlone } : event,
  );
  const job = buildJobState(events);
  return {
    key: "run-1",
    source: "playback",
    kind: "firstRun",
    clientId: "kumo",
    threadId: "thread-1",
    request: "Sync today's supplier stock to the store admin",
    events,
    played: events.length,
    job,
    isApproved: false,
    isApprovalHidden: false,
    isStopped: false,
    waitingJob: job,
    readyInMs: job.readyInMs,
  };
}

function show(run: Run, scheduleApi: ScheduleApi = scheduleOf(defaultSchedule)) {
  render(
    <RunMessages run={run} client={clientById("kumo")} owner="" isLast isActivityOpen={false} firstReadyMs={null} handlers={handlers} scheduleApi={scheduleApi} />,
  );
}

function scheduleOf(schedule: ClientSchedule): ScheduleApi {
  const reply = { clientId: "kumo", schedule, text: scheduleText(schedule), runs: [] };
  return { load: async () => reply, save: async () => reply };
}

function todayAt18InTokyo() {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  return `${today}T09:00:00Z`;
}

function doneRun(overrides: Partial<Run> = {}): Run {
  const events = demoJobs.firstRun.events;
  const job = buildJobState(events);
  return { ...waitingRun(), events, played: events.length, job, isApproved: true, answer: "Save changes", ...overrides };
}

describe("later runs", () => {
  test("a run started with Run now opens with a divider, not a message from you", () => {
    show({ ...doneRun(), source: "live", trigger: "run-now" });
    expect(screen.getAllByText("Run now").some((element) => !element.closest("button"))).toBe(true);
    expect(screen.queryByText("Sync today's supplier stock to the store admin")).toBeNull();
  });

  test("a run started on schedule opens with its time, like Today, 18:00, not a message from you", () => {
    show({ ...doneRun(), source: "live", scheduledFor: { slot: todayAt18InTokyo(), timeZone: "Asia/Tokyo" } });
    expect(screen.getByText("Today, 18:00")).toBeTruthy();
    expect(screen.queryByText("Sync today's supplier stock to the store admin")).toBeNull();
  });

  test("the saved card shows the client's real schedule and lets you change it", async () => {
    show(doneRun(), scheduleOf({ isOn: false, time: "18:00", timeZone: "Asia/Tokyo" }));
    const card = screen.getByRole("group", { name: "Saved for next time" });
    expect(await within(card).findByText("Not scheduled")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Change" })).toBeTruthy();
    expect(within(card).queryByText("Runs daily at 18:00")).toBeNull();
  });

  test("the saved card says how long the next run should take, from the saved steps", () => {
    show(doneRun());
    expect(screen.getByText("Next time it takes about 6 seconds and needs no AI.")).toBeTruthy();
  });
});

describe("schedule and updated steps", () => {
  test("a skipped scheduled run is one plain line", () => {
    show({ ...waitingRun(), source: "live", job: null, waitingJob: undefined, events: [], played: 0, isStopped: true, skipped: { slot: todayAt18InTokyo(), timeZone: "Asia/Tokyo" } });
    expect(screen.getByText("Today's 18:00 run was skipped because the last one is still waiting for your OK.")).toBeTruthy();
    expect(screen.queryByRole("group")).toBeNull();
  });

  test("updated steps after a page change do not talk about a new button", () => {
    const done = doneRun();
    const job = { ...done.job!, procedure: { ...done.job!.procedure!, version: 2 } };
    show({ ...done, job });
    expect(screen.getByText("The page changed. The saved steps now follow it.")).toBeTruthy();
  });

  test("updated steps after a renamed button say so", () => {
    const done = doneRun();
    const job = { ...done.job!, procedure: { ...done.job!.procedure!, version: 2 } };
    const change = { stepId: "check-save", fromVersion: 1, toVersion: 2, before: "Save changes", after: "Update inventory" };
    const waitingJob = { ...done.waitingJob!, pendingApproval: { ...done.waitingJob!.pendingApproval!, procedureChange: change } };
    show({ ...done, job, waitingJob });
    expect(screen.getByText("From now on they use the new button.")).toBeTruthy();
  });
});

describe("finished runs", () => {
  test("offer to show what the worker did", () => {
    const opened: string[] = [];
    const run = doneRun();
    render(
      <RunMessages
        run={run}
        client={clientById("kumo")}
        owner=""
        isLast
        isActivityOpen={false}
        firstReadyMs={null}
        handlers={{ ...handlers, onOpenActivity: (chosen) => opened.push(chosen.key) }}
        scheduleApi={scheduleOf(defaultSchedule)}
      />,
    );
    screen.getByRole("button", { name: "Show activity" }).click();
    expect(opened).toEqual([run.key]);
  });
});

describe("approval card", () => {
  test("says which products were left as they are", () => {
    show(waitingRun({ onlyInStore: 2, onlyAtSupplier: 0 }));
    const card = screen.getByRole("group", { name: "Save 12 stock changes to the store admin?" });
    expect(card.textContent).toContain("2 products weren't on the supplier's list. They stay as they are.");
  });

  test("adds nothing when every product was matched", () => {
    show(waitingRun());
    const card = screen.getByRole("group", { name: "Save 12 stock changes to the store admin?" });
    expect(card.textContent).not.toContain("supplier's list");
  });
});

function choiceRun(until: "choice.requested" | "approval.requested") {
  const { events: all } = newEvents(
    {
      executionId: choiceContract.jobId,
      clientId: "kumo",
      status: "waiting",
      expectedEvents: choiceContract.notes.length,
      notes: choiceContract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index })),
    },
    0,
  );
  const events = all.slice(0, all.findIndex((event) => event.type === until) + 1);
  const job = buildJobState(events);
  return { ...waitingRun(), source: "live" as const, kind: "replay" as const, jobId: choiceContract.jobId, trigger: "run-now" as const, events, played: events.length, job, waitingJob: job };
}

describe("a question about which element to use", () => {
  test("shows the worker's question and the buttons to pick from, and sends the one you pick", () => {
    const picked: (string | null)[] = [];
    render(
      <RunMessages
        run={choiceRun("choice.requested")}
        client={clientById("kumo")}
        owner=""
        isLast
        isActivityOpen={false}
        firstReadyMs={null}
        handlers={{ ...handlers, onPick: (_run, ref) => picked.push(ref) }}
        scheduleApi={scheduleOf(defaultSchedule)}
      />,
    );
    expect(screen.getByText("The page changed today, and more than one button could be the one I used before. Which one should I use?")).toBeTruthy();
    const card = screen.getByRole("group", { name: "Which button saves the changes now?" });
    expect(within(card).getByText("It used Save changes before. Nothing has been saved yet.")).toBeTruthy();
    fireEvent.click(within(card).getByText("Update inventory"));
    fireEvent.click(within(card).getByRole("button", { name: "Use this button" }));
    expect(picked).toEqual(["1016"]);
  });

  test("after your pick, the conversation shows which one you picked, then asks before saving", () => {
    show(choiceRun("approval.requested"));
    expect(screen.getByText("Use Update inventory")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Which button saves the changes now?" })).toBeNull();
    expect(screen.getByRole("group", { name: "Save 4 stock changes with the new button?" })).toBeTruthy();
    expect(screen.getByText("The store admin renamed its Save button today. I'll use Update inventory, the one you picked.")).toBeTruthy();
  });
});
