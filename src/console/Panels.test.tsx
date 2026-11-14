import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import choiceContract from "../../agent/tests/contract/choice_job_notes.json";
import repairContract from "../../agent/tests/contract/repair_job_notes.json";
import { eventNoteTag, newEvents } from "@/lib/agentfieldRecords";
import { demoJobs } from "@/lib/demoJobs";
import { applyEvent, buildJobState } from "@/lib/jobState";
import { parseJobEvent } from "@/lib/events";
import { ActivityView, WhatChangedView } from "./Panels";
import { clientById } from "./clients";

afterEach(cleanup);

const running = buildJobState(demoJobs.firstRun.events.slice(0, 8));

describe("ActivityView page snapshot", () => {
  test("shows a placeholder until the worker has seen a page", () => {
    render(<ActivityView client={clientById("kumo")} job={running} onStop={() => {}} />);
    expect(screen.queryByTitle(/page the worker saw/)).toBeNull();
  });

  test("shows the latest page the worker saw in a frame that cannot run scripts", () => {
    const seen = parseJobEvent({
      id: `${running.jobId}-${running.lastSeq + 1}`,
      seq: running.lastSeq + 1,
      jobId: running.jobId,
      clientId: running.clientId,
      at: running.updatedAt,
      type: "page.seen",
      stepId: "compare",
      host: "admin.kumo-coffee.example",
      html: "<html><body><h1>Inventory</h1></body></html>",
    });
    const job = applyEvent(running, seen);
    render(<ActivityView client={clientById("kumo")} job={job} onStop={() => {}} />);
    const frame = screen.getByTitle("The page the worker saw on admin.kumo-coffee.example");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("srcdoc")).toContain("<h1>Inventory</h1>");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(screen.getByText("Kumo's computer · admin.kumo-coffee.example")).toBeTruthy();
  });
});

function repairedJob(contract: { jobId: string; notes: unknown[] }) {
  const { events } = newEvents(
    {
      executionId: contract.jobId,
      clientId: "kumo",
      status: "waiting",
      expectedEvents: contract.notes.length,
      notes: contract.notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: 1794646800 + index })),
    },
    0,
  );
  return buildJobState(events.slice(0, events.findIndex((event) => event.type === "approval.requested") + 1));
}

describe("WhatChangedView", () => {
  test("a button Claude found again says Claude decided it and saw only names", () => {
    render(<WhatChangedView job={repairedJob(repairContract)} />);
    expect(screen.getByText("Save changes")).toBeTruthy();
    expect(screen.getByText("Update inventory")).toBeTruthy();
    expect(screen.getByText("Claude, for this one step")).toBeTruthy();
    expect(screen.getByText("Button and field names only")).toBeTruthy();
    expect(screen.getByText("Become v2 after you approve")).toBeTruthy();
  });

  test("a button you picked says you decided it", () => {
    render(<WhatChangedView job={repairedJob(choiceContract)} />);
    expect(screen.getByText("You, for this one step")).toBeTruthy();
    expect(screen.queryByText("Claude, for this one step")).toBeNull();
    expect(screen.getByText("Same button in the same form, with a new name. You picked it from the buttons in that form.")).toBeTruthy();
  });
});

describe("ActivityView ignored instructions", () => {
  test("says once per site that an instruction on the page was ignored, without repeating its words", () => {
    const base = running;
    const job = { ...base, ignoredInstructions: [{ stepId: "read", host: "supplier.maruyama.example" }] };
    render(<ActivityView client={clientById("kumo")} job={job} onStop={() => {}} />);
    expect(screen.getByText("Ignored a suspicious instruction on supplier.maruyama.example. Nothing it asked for was done.")).toBeTruthy();
  });

  test("says nothing when there was none", () => {
    render(<ActivityView client={clientById("kumo")} job={running} onStop={() => {}} />);
    expect(screen.queryByText(/suspicious instruction/)).toBeNull();
  });
});
