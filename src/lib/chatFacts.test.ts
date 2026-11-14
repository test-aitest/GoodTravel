import { describe, expect, test } from "bun:test";
import { demoJobs } from "./demoJobs";
import { buildJobState } from "./jobState";
import { splitAtApproval } from "./playback";
import { factCatalog, factLines, type FactSource } from "./chatFacts";

const done = buildJobState(demoJobs.firstRun.events);
const waiting = buildJobState(splitAtApproval(demoJobs.firstRun.events).untilApproval);
const doneRun: FactSource = { job: done, waitingJob: waiting, readyInMs: 2400, firstReadyMs: 2400, schedule: { isOn: true, time: "18:00", timeZone: "Asia/Tokyo" } };

describe("the facts a conversation offers", () => {
  test("list only what is there, by name, with no values", () => {
    const catalog = factCatalog(doneRun);
    expect(catalog.map((fact) => fact.id)).toEqual(["last.result", "last.changes", "last.steps", "last.timing", "schedule"]);
    expect(catalog.every((fact) => !/\d/.test(fact.about))).toBe(true);
    expect(JSON.stringify(catalog)).not.toContain("Ethiopia");
  });

  test("a conversation with no job yet offers only the daily run", () => {
    expect(factCatalog({ job: null, readyInMs: null, firstReadyMs: null, schedule: null }).map((fact) => fact.id)).toEqual([]);
  });
});

describe("the sentences the screen shows for each fact", () => {
  test("the changes come with the real names and numbers", () => {
    const lines = factLines("last.changes", doneRun);
    expect(lines[0]).toBe("12 products changed:");
    expect(lines).toContain("Ethiopia Guji 200g: 18 → 6");
    expect(lines).toHaveLength(13);
  });

  test("the result, the steps, the timing and the daily run", () => {
    expect(factLines("last.result", doneRun)).toEqual(["The last sync updated 12 products and checked each one."]);
    expect(factLines("last.steps", doneRun)[0]).toMatch(/^Signed in to Maruyama Supply/);
    expect(factLines("last.timing", doneRun)).toEqual(["It took 2.4 s to get the changes ready."]);
    expect(factLines("schedule", doneRun)).toEqual(["Runs daily at 18:00"]);
  });

  test("a fact that is not there says so instead of guessing", () => {
    expect(factLines("last.cost", doneRun)).toEqual(["I don't have that for this conversation yet."]);
  });
});
