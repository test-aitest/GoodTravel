import { describe, expect, test } from "bun:test";
import { accounts, catalogs, expectedChanges } from "../../sandbox/sites/catalog";
import { demoJobs, kumoFirstRunChanges, kumoRepairChanges, kumoReplayChanges, type DemoJob } from "./demoJobs";
import { parseJobEvent } from "./events";
import { buildJobState } from "./jobState";

const untilApproval = (job: DemoJob) => job.events.slice(0, job.events.findIndex((event) => event.type === "approval.requested") + 1);

describe("demo jobs", () => {
  test.each(Object.entries(demoJobs))("%s is a valid, ordered event list for one job", (_name, job) => {
    expect(job.events.length).toBeGreaterThan(1);
    job.events.forEach((event, index) => {
      expect(parseJobEvent(event)).toEqual(event);
      expect(event.seq).toBe(index + 1);
      expect(event.jobId).toBe(job.events[0].jobId);
      if (index > 0) expect(Date.parse(event.at)).toBeGreaterThanOrEqual(Date.parse(job.events[index - 1].at));
    });
    expect(job.events[0].type).toBe("job.created");
    expect(() => buildJobState(job.events)).not.toThrow();
  });

  test("the twelve first-run changes include three sold out at the supplier", () => {
    expect(kumoFirstRunChanges).toHaveLength(12);
    expect(new Set(kumoFirstRunChanges.map((change) => change.sku)).size).toBe(12);
    expect(kumoFirstRunChanges.filter((change) => change.supplier === 0)).toHaveLength(3);
    expect(kumoFirstRunChanges.every((change) => change.store !== change.supplier)).toBe(true);
  });
});

describe("demo jobs match the demo sites' seed data", () => {
  test("the three change lists are the sites' expected changes for days 1, 2 and 3", () => {
    expect(kumoFirstRunChanges).toEqual(expectedChanges.kumo[1]);
    expect(kumoReplayChanges).toEqual(expectedChanges.kumo[2]);
    expect(kumoRepairChanges).toEqual(expectedChanges.kumo[3]);
  });

  test("each approval asks about exactly the changes the sites will show that day", () => {
    const approvals = [
      [demoJobs.firstRun, 1],
      [demoJobs.replay, 2],
      [demoJobs.repair, 3],
    ] as const;
    for (const [job, day] of approvals) {
      const state = buildJobState(untilApproval(job));
      expect(state.pendingApproval?.changes).toEqual(expectedChanges.kumo[day]);
      expect(state.steps.find((step) => step.stepId === "compare")?.detail).toBe(`${expectedChanges.kumo[day].length} products differ`);
      expect(state.steps.find((step) => step.stepId === "read")?.detail).toBe(`${catalogs.kumo.products.length} products`);
    }
  });

  test("the boundary demo names the real owner and Hinata has its own supplier login", () => {
    const blocked = demoJobs.boundary.events.find((event) => event.type === "boundary.blocked");
    expect(blocked?.type === "boundary.blocked" && blocked.ownerClientName).toBe(catalogs.kumo.clientName);
    expect(buildJobState(demoJobs.boundary.events).clientName).toBe(catalogs.hinata.clientName);
    expect(accounts.some((account) => account.client === "kumo" && account.site === "supplier")).toBe(true);
    expect(accounts.some((account) => account.client === "hinata" && account.site === "supplier")).toBe(true);
  });
});

describe("demo job scenes", () => {
  test("every run uses the same six steps, with the save button checked before asking", () => {
    for (const job of [demoJobs.firstRun, demoJobs.replay, demoJobs.repair]) {
      expect(buildJobState(job.events).steps.map((step) => step.stepId)).toEqual(["sign-in", "read", "compare", "check-save", "ask", "save"]);
    }
  });

  test("first run: Claude decides the new steps, then waits for the OK on 12 changes", () => {
    const job = demoJobs.firstRun;
    const atThirdStep = buildJobState(job.events.slice(0, job.events.findIndex((event) => event.type === "step.started" && event.stepId === "compare") + 1));
    expect(atThirdStep.steps).toHaveLength(6);
    expect(atThirdStep.stepIndex[atThirdStep.activeStepId!]).toBe(2);
    expect(atThirdStep.progress).toBe(33);

    const waiting = buildJobState(untilApproval(job));
    expect(waiting.status).toBe("waiting");
    expect(waiting.pendingApproval?.changes).toEqual(kumoFirstRunChanges);
    expect(waiting.pendingApproval?.question).toBe("Save 12 stock changes to the store admin?");
    expect(waiting.readyInMs).toBe(48_600);
    expect(waiting.steps.slice(0, 4).map((step) => step.decidedBy)).toEqual(["claude", "claude", "claude", "claude"]);
    expect(waiting.steps[1].detail).toBe("42 products");
  });

  test("first run: after the OK it saves, re-reads every row and saves the procedure", () => {
    const state = buildJobState(demoJobs.firstRun.events);
    expect(state.status).toBe("done");
    expect(state.result).toEqual({ kind: "done", written: 12, checked: 12, matched: 12 });
    expect(state.steps[4].decidedBy).toBe("human");
    expect(state.procedure).toMatchObject({ version: 1, stepCount: 6 });
    expect(state.procedure?.expectedReplayMs).toBeLessThanOrEqual(6_500);
  });

  test("replay: ready in 6.2 s with no AI, waiting on 4 changes", () => {
    const waiting = buildJobState(untilApproval(demoJobs.replay));
    expect(waiting.run).toEqual({ kind: "replay", procedureVersion: 1 });
    expect(waiting.readyInMs).toBe(6_200);
    expect(waiting.claudeDecisions).toBe(0);
    expect(waiting.steps.filter((step) => step.status === "done").every((step) => step.decidedBy === "script")).toBe(true);
    expect(waiting.pendingApproval?.changes).toHaveLength(4);
    expect(waiting.pendingApproval?.question).toBe("Save 4 stock changes to the store admin?");
    expect(buildJobState(demoJobs.replay.events).result).toEqual({ kind: "done", written: 4, checked: 4, matched: 4 });
  });

  test("repair: only the renamed button goes to Claude, and the fix waits for the OK", () => {
    const waiting = buildJobState(untilApproval(demoJobs.repair));
    const climbed = waiting.steps.filter((step) => step.decidedBy === "claude");
    expect(climbed).toHaveLength(1);
    expect(climbed[0].escalatedFrom).toEqual(["script", "tree"]);
    expect(waiting.claudeDecisions).toBe(1);
    expect(waiting.pendingApproval?.question).toBe("Save 7 stock changes with the new button?");
    expect(waiting.pendingApproval?.changes).toHaveLength(7);
    expect(waiting.pendingApproval?.procedureChange).toEqual({
      stepId: climbed[0].stepId,
      fromVersion: 1,
      toVersion: 2,
      before: "Save changes",
      after: "Update inventory",
    });
    const finished = buildJobState(demoJobs.repair.events);
    expect(finished.result).toEqual({ kind: "done", written: 7, checked: 7, matched: 7 });
    expect(finished.procedure?.version).toBe(2);
  });

  test("boundary: Hinata cannot use Kumo's login", () => {
    const state = buildJobState(demoJobs.boundary.events);
    expect(state.clientId).toBe("hinata");
    expect(state.status).toBe("blocked");
    expect(state.claudeDecisions).toBe(0);
    expect(state.result).toMatchObject({ kind: "blocked", ownerClientId: "kumo", ownerClientName: "Kumo Coffee Roasters", offersOwnAccount: true });
  });
});
