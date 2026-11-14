import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/schedule.json";
import { createScheduleApi, scheduledRunKey, unseenScheduledRuns, type ScheduleReply } from "./scheduleApi";
import type { ScheduledRun } from "@/lib/schedule";

const sharedReply = contract.reply as ScheduleReply;

const reply: ScheduleReply = {
  clientId: "kumo",
  schedule: { isOn: true, time: "18:00", timeZone: "Asia/Tokyo" },
  text: "Runs daily at 18:00",
  runs: [{ status: "started", slot: "2026-11-14T09:00:00Z", timeZone: "Asia/Tokyo", jobId: "exec_1" }],
};

function recording(status: number, body: unknown) {
  const sent: { url: string; init?: RequestInit }[] = [];
  const fetcher = async (url: string, init?: RequestInit) => {
    sent.push({ url, init });
    return Response.json(body, { status });
  };
  return { fetcher, sent };
}

describe("scheduleApi", () => {
  test("loads a client's schedule from its own address", async () => {
    const used = recording(200, reply);
    expect(await createScheduleApi(used.fetcher).load("kumo")).toEqual(reply);
    expect(used.sent[0].url).toBe("/api/clients/kumo/schedule");
    expect(used.sent[0].init?.method).toBe("GET");
  });

  test("saves a change as JSON with PUT", async () => {
    const used = recording(200, { ...reply, schedule: { ...reply.schedule, time: "07:30" }, text: "Runs daily at 07:30" });
    const saved = await createScheduleApi(used.fetcher).save("kumo", { isOn: true, time: "07:30" });
    expect(saved.text).toBe("Runs daily at 07:30");
    expect(used.sent[0].init?.method).toBe("PUT");
    expect(new Headers(used.sent[0].init?.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(String(used.sent[0].init?.body))).toEqual({ isOn: true, time: "07:30" });
  });

  test("passes on the server's own words when it refuses", async () => {
    const used = recording(400, { error: "Pick a time like 18:00." });
    await expect(createScheduleApi(used.fetcher).save("kumo", { isOn: true, time: "6pm" })).rejects.toThrow("Pick a time like 18:00.");
  });

  test("says something plain when the server cannot be reached or answers nonsense", async () => {
    const offline = createScheduleApi(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(offline.load("kumo")).rejects.toThrow("The schedule could not be read. Try again in a minute.");
    await expect(createScheduleApi(recording(200, { nonsense: true }).fetcher).load("kumo")).rejects.toThrow("The schedule could not be read. Try again in a minute.");
  });

  test("reads skipped runs in the shared shape", async () => {
    expect(await createScheduleApi(recording(200, sharedReply).fetcher).load("kumo")).toEqual(sharedReply);
  });

  test("finds the scheduled runs the screen does not show yet, started ones by job and skipped ones by slot", () => {
    const runs: ScheduledRun[] = [
      { status: "started", slot: "2026-11-13T09:00:00Z", timeZone: "Asia/Tokyo", jobId: "exec_old" },
      { status: "started", slot: "2026-11-14T09:00:00Z", timeZone: "Asia/Tokyo", jobId: "exec_new" },
      { status: "skipped", slot: "2026-11-15T09:00:00Z", timeZone: "Asia/Tokyo", reason: "waiting-for-approval", waitingJobId: "exec_new" },
    ];
    expect(runs.map(scheduledRunKey)).toEqual(["exec_old", "exec_new", "skipped:2026-11-15T09:00:00Z"]);
    expect(unseenScheduledRuns(runs, ["exec_old", "exec_other"])).toEqual([runs[1], runs[2]]);
    expect(unseenScheduledRuns(runs, new Set(["exec_old", "exec_new", "skipped:2026-11-15T09:00:00Z"]))).toEqual([]);
  });
});
