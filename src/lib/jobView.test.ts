import { describe, expect, test } from "bun:test";
import { demoJobs } from "./demoJobs";
import { eventWriter } from "./events";
import { buildJobState } from "./jobState";
import { decidedBy, progressOf, replayEstimate, stepItems, workerSays } from "./jobView";
import { splitAtApproval } from "./playback";

const firstRun = demoJobs.firstRun.events;
const at = (count: number) => buildJobState(firstRun.slice(0, count));
const waiting = (events: typeof firstRun) => buildJobState(splitAtApproval(events).untilApproval);

describe("decidedBy", () => {
  test("names who decided in plain words", () => {
    expect(decidedBy("claude")).toBe("Decided by Claude");
    expect(decidedBy("human")).toBe("Decided by you");
    expect(decidedBy("script")).toBe("Saved steps, no AI");
    expect(decidedBy("tree")).toBe("Saved steps, found again by name");
  });
});

describe("progressOf", () => {
  test("says what is happening now and counts finished steps out of six", () => {
    expect(progressOf(at(8))).toEqual({ title: "Comparing with the store admin", done: 2, total: 6 });
  });

  test("waits for the OK at the ask step", () => {
    expect(progressOf(waiting(firstRun))).toEqual({ title: "Waiting for your OK", done: 4, total: 6 });
  });
});

describe("stepItems", () => {
  test("shows done steps with who decided and how long, the active step, and what comes next", () => {
    const items = stepItems(at(8));
    expect(items[0]).toEqual({ label: "Signed in to Maruyama Supply", detail: "Decided by Claude · 2.2 s", state: "done" });
    expect(items[1]).toEqual({ label: "Read today's stock, 42 products", detail: "Decided by Claude · 1.1 s", state: "done" });
    expect(items[2]).toEqual({ label: "Comparing with the store admin", detail: "Claude is deciding · Matching by SKU", state: "now" });
    expect(items[3]).toEqual({ label: "Check the save button", state: "next" });
    expect(items[4]).toEqual({ label: "Ask you before saving", detail: "You decide", state: "next" });
    expect(items[5]).toEqual({ label: "Save and check every row", state: "next" });
  });

  test("only the ask step says you decide, whatever the number of steps", () => {
    const first = firstRun[0];
    if (first.type !== "job.created") throw new Error("the first demo event must create the job");
    const created = {
      ...first,
      plan: [
        { stepId: "open-computer", title: "Open the client's computer" },
        { stepId: "read-supplier", title: "Read the supplier portal" },
        { stepId: "read-store", title: "Read the store admin" },
      ],
    };
    expect(stepItems(buildJobState([created])).map((item) => item.detail)).toEqual([undefined, undefined, undefined]);
  });

  test("replayed steps say no AI was used", () => {
    const items = stepItems(waiting(demoJobs.replay.events));
    expect(items[0]?.detail).toBe("Saved steps, no AI · < 0.1 s");
    expect(items[4]).toEqual({ label: "Waiting for your OK", detail: "You decide", state: "now" });
  });
});

describe("workerSays", () => {
  test("first run: says what it will do, then how many products differ", () => {
    expect(workerSays(at(3))).toBe("I'll read Maruyama Supply's stock and compare it with your store admin. I'll ask before saving anything.");
    expect(workerSays(waiting(firstRun))).toBe("12 products are different. Nothing is saved yet.");
  });

  test("first run done: every product updated and checked", () => {
    expect(workerSays(buildJobState(firstRun))).toBe("Done. 12 products updated, and each one checked.");
  });

  test("after the OK: saving and checking every row", () => {
    const answered = firstRun.findIndex((event) => event.type === "approval.answered");
    expect(workerSays(buildJobState(firstRun.slice(0, answered + 4)))).toBe("Saving 12 changes. I'll check every row after.");
  });

  test("undo: says the products were put back and checked", () => {
    const write = eventWriter({ jobId: "job-undo", clientId: "kumo", startAt: "2026-11-14T10:00:00.000Z" });
    const state = buildJobState([
      write("job.created", { clientName: "Kumo Coffee Roasters", title: "Undo stock sync", request: "Undo this sync", run: { kind: "undo" }, plan: [{ stepId: "undo", title: "Put back the last sync" }] }, 0),
      write("step.started", { stepId: "undo", activity: "Putting back the last sync" }, 10),
      write("write.done", { stepId: "undo", written: 4 }, 20),
      write("verify.done", { stepId: "undo", checked: 4, matched: 4 }, 30),
      write("step.done", { stepId: "undo", summary: "Put back the last sync", tookMs: 30 }, 40),
    ]);
    expect(workerSays(state)).toBe("Put back 4 products, and checked each one.");
  });

  test("nothing to change: says the store already matches", () => {
    const write = eventWriter({ jobId: "job-same", clientId: "kumo", startAt: "2026-11-14T10:00:00.000Z" });
    const state = buildJobState([
      write("job.created", { clientName: "Kumo Coffee Roasters", title: "Supplier stock sync", request: "Sync", run: { kind: "replay", procedureVersion: 1 }, plan: [{ stepId: "compare", title: "Compare with the store admin" }] }, 0),
      write("step.started", { stepId: "compare", activity: "Comparing with the store admin" }, 10),
      write("step.done", { stepId: "compare", summary: "Compared with the store admin", detail: "No products differ", tookMs: 10 }, 20),
    ]);
    expect(workerSays(state)).toBe("Nothing to save. The store already matches the supplier.");
  });

  test("replay estimate reads naturally", () => {
    expect(replayEstimate(5500)).toBe("about 6 seconds");
    expect(replayEstimate(1200)).toBe("about 1 second");
    expect(replayEstimate(117)).toBe("under a second");
  });

  test("replay: ready quickly with no AI", () => {
    expect(workerSays(waiting(demoJobs.replay.events))).toBe("4 products changed at the supplier. Ready in 6.2 s, with no AI.");
  });

  test("repair: stopped before changing anything and found the new button", () => {
    expect(workerSays(waiting(demoJobs.repair.events))).toBe(
      "The store admin renamed its Save button today. I stopped before changing anything and found the new button.",
    );
  });

  test("boundary: refuses another client's login", () => {
    expect(workerSays(buildJobState(demoJobs.boundary.events))).toBe("I can't use Kumo Coffee Roasters' login here");
  });

  test("never more than two sentences and never a step id", () => {
    const states = [at(3), at(8), waiting(firstRun), buildJobState(firstRun), waiting(demoJobs.replay.events), waiting(demoJobs.repair.events)];
    for (const state of states) {
      const said = workerSays(state);
      expect(said.split(/(?<=\.)\s/).length).toBeLessThanOrEqual(2);
      expect(said).not.toMatch(/check-save|sign-in|apr-|job-/);
    }
  });
});
