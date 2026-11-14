import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import { demoJobs } from "@/lib/demoJobs";
import { buildJobState } from "@/lib/jobState";
import { splitAtApproval } from "@/lib/playback";
import { ChatMessages } from "./ChatMessages";
import { clientById } from "./clients";
import type { ChatEntry } from "./consoleState";

afterEach(cleanup);

const source = {
  job: buildJobState(demoJobs.firstRun.events),
  waitingJob: buildJobState(splitAtApproval(demoJobs.firstRun.events).untilApproval),
  readyInMs: 2400,
  firstReadyMs: 2400,
  schedule: null,
};

function chat(decision: ChatEntry["decision"], extra: Partial<ChatEntry> = {}): ChatEntry {
  return { key: "chat-1", threadId: "thread-1", clientId: "kumo", text: "How many products changed?", at: "2026-11-14T09:00:00.000Z", afterRuns: 1, decision, ...extra };
}

function show(entry: ChatEntry) {
  render(<ChatMessages chat={entry} source={source} client={clientById("kumo")} />);
}

describe("ChatMessages", () => {
  test("shows your message, then the worker thinking", () => {
    show(chat(null));
    expect(screen.getByText("How many products changed?")).toBeTruthy();
    expect(screen.getByText("Thinking…")).toBeTruthy();
  });

  test("answers with the real values the conversation holds", () => {
    show(chat({ action: "answer", facts: ["last.changes"], clarify: null }));
    expect(screen.getByText("12 products changed:")).toBeTruthy();
    expect(screen.getByText("Ethiopia Guji 200g: 18 → 6")).toBeTruthy();
  });

  test("a message that starts a job leaves the reply to the job, so a refused job is not announced as starting", () => {
    const { container } = render(<ChatMessages chat={chat({ action: "run", facts: [], clarify: null }, { text: "Run it again" })} source={source} client={clientById("kumo")} />);
    expect(screen.getByText("Run it again")).toBeTruthy();
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(container.textContent).toBe("Run it again");
  });

  test("asks back, or says what it can do", () => {
    show(chat({ action: "clarify", facts: [], clarify: "what-to-do" }, { text: "hmm" }));
    expect(screen.getByText(/^What would you like me to do\?/)).toBeTruthy();
    cleanup();
    show(chat({ action: "cannot", facts: [], clarify: null }, { text: "Book a flight" }));
    expect(screen.getByText(/^I can't do that here\./)).toBeTruthy();
  });

  test("an answer shows the words it was given at the time, not what the job says now", () => {
    show(chat({ action: "answer", facts: ["last.changes"], clarify: null }, { answer: ["3 products changed:", "House Blend 200g: 52 → 40"] }));
    expect(screen.getByText("3 products changed:")).toBeTruthy();
    expect(screen.getByText("House Blend 200g: 52 → 40")).toBeTruthy();
    expect(screen.queryByText("12 products changed:")).toBeNull();
  });

  test("a problem getting the answer is shown on the message", () => {
    show(chat(null, { problem: "The worker service is not available right now." }));
    expect(screen.getByText("The worker service is not available right now.")).toBeTruthy();
    expect(screen.queryByText("Thinking…")).toBeNull();
  });
});
