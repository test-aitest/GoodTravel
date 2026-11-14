import { beforeEach, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import contract from "../../agent/tests/contract/schedule.json";
import type { ScheduledRun } from "./schedule";
import { readSchedule, RUNS_DIRECTORY, scheduledRuns, ScheduleStoreError, SCHEDULES_FILE, writeSchedule } from "./scheduleStore";

const projectRoot = resolve(import.meta.dir, "..", "..");
let dataDir = "";
let env: Record<string, string | undefined> = {};

function mode(path: string) {
  return statSync(path).mode & 0o777;
}

function writeRaw(content: unknown) {
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, SCHEDULES_FILE), typeof content === "string" ? content : JSON.stringify(content));
}

function stored() {
  return JSON.parse(readFileSync(join(dataDir, SCHEDULES_FILE), "utf8"));
}

function putRun(clientId: string, name: string, record: unknown) {
  const folder = join(dataDir, RUNS_DIRECTORY, clientId);
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, name), typeof record === "string" ? record : JSON.stringify(record));
}

beforeEach(() => {
  dataDir = join(realpathSync(mkdtempSync(join(tmpdir(), "clientops-schedule-"))), "clientops-data");
  env = { CLIENTOPS_DATA_DIR: dataDir };
});

describe("reading a client's schedule", () => {
  test("without a file no client is scheduled until someone turns it on", async () => {
    expect(await readSchedule("kumo", env, projectRoot)).toEqual({ isOn: false, time: "18:00", timeZone: "Asia/Tokyo" });
  });

  test("the shared contract reads the same as in the worker", async () => {
    writeRaw(contract.schedulesFile);
    for (const [clientId, expected] of Object.entries(contract.expected)) {
      expect(await readSchedule(clientId, env, projectRoot)).toEqual(expected);
    }
  });

  test("a file that cannot be trusted is refused instead of guessed", async () => {
    for (const content of [
      "not json",
      { version: 2, clients: {} },
      { version: 1, clients: { kumo: { isOn: "yes", time: "18:00", timeZone: "Asia/Tokyo" } } },
      { version: 1, clients: { kumo: { isOn: true, time: "24:00", timeZone: "Asia/Tokyo" } } },
      { version: 1, clients: { kumo: { isOn: true, time: "18:00", timeZone: "Mars/Olympus" } } },
    ]) {
      writeRaw(content);
      await expect(readSchedule("kumo", env, projectRoot)).rejects.toThrow(ScheduleStoreError);
    }
  });

  test("the data directory must be outside the project", async () => {
    await expect(readSchedule("kumo", { CLIENTOPS_DATA_DIR: join(projectRoot, "data") }, projectRoot)).rejects.toThrow();
  });
});

describe("changing a client's schedule", () => {
  test("stores the time, the time zone and when it changed, in a private file", async () => {
    const saved = await writeSchedule("kumo", { isOn: true, time: "07:30", timeZone: "Asia/Tokyo" }, env, projectRoot, new Date("2026-11-14T00:40:00.000Z"));
    expect(saved).toEqual({ isOn: true, time: "07:30", timeZone: "Asia/Tokyo" });
    expect(stored()).toEqual({ version: 1, clients: { kumo: { isOn: true, time: "07:30", timeZone: "Asia/Tokyo", updatedAt: "2026-11-14T00:40:00.000Z" } } });
    expect(mode(join(dataDir, SCHEDULES_FILE))).toBe(0o600);
    expect(mode(dataDir)).toBe(0o700);
    expect(await readSchedule("kumo", env, projectRoot)).toEqual(saved);
  });

  test("turning it off keeps the time for when it is turned on again", async () => {
    await writeSchedule("kumo", { isOn: true, time: "07:30", timeZone: "Asia/Tokyo" }, env, projectRoot);
    await writeSchedule("kumo", { isOn: false, time: "07:30", timeZone: "Asia/Tokyo" }, env, projectRoot);
    expect(await readSchedule("kumo", env, projectRoot)).toEqual({ isOn: false, time: "07:30", timeZone: "Asia/Tokyo" });
  });

  test("other clients and other fields are left alone", async () => {
    writeRaw({ version: 1, clients: { hinata: { isOn: false, time: "09:00", timeZone: "Asia/Tokyo", note: "kept" } }, owner: "kept" });
    await writeSchedule("kumo", { isOn: true, time: "18:00", timeZone: "America/New_York" }, env, projectRoot, new Date("2026-11-14T00:40:00.000Z"));
    expect(stored()).toEqual({
      version: 1,
      owner: "kept",
      clients: {
        hinata: { isOn: false, time: "09:00", timeZone: "Asia/Tokyo", note: "kept" },
        kumo: { isOn: true, time: "18:00", timeZone: "America/New_York", updatedAt: "2026-11-14T00:40:00.000Z" },
      },
    });
  });

  test("two changes at once both land", async () => {
    await Promise.all([
      writeSchedule("kumo", { isOn: true, time: "07:30", timeZone: "Asia/Tokyo" }, env, projectRoot),
      writeSchedule("hinata", { isOn: false, time: "18:00", timeZone: "Asia/Tokyo" }, env, projectRoot),
    ]);
    expect(await readSchedule("kumo", env, projectRoot)).toEqual({ isOn: true, time: "07:30", timeZone: "Asia/Tokyo" });
    expect(await readSchedule("hinata", env, projectRoot)).toEqual({ isOn: false, time: "18:00", timeZone: "Asia/Tokyo" });
  });

  test("a file that cannot be trusted is not overwritten", async () => {
    writeRaw("not json");
    await expect(writeSchedule("kumo", { isOn: true, time: "07:30", timeZone: "Asia/Tokyo" }, env, projectRoot)).rejects.toThrow(ScheduleStoreError);
    expect(readFileSync(join(dataDir, SCHEDULES_FILE), "utf8")).toBe("not json");
  });
});

describe("the runs the worker started on schedule", () => {
  test("lists started and skipped runs for this client, oldest first, from the worker's records", async () => {
    putRun("kumo", contract.runFileName, contract.runRecord);
    putRun("kumo", contract.skippedFileName, contract.skippedRecord);
    putRun("kumo", "20261113T0900Z.json", { ...contract.runRecord, slot: "2026-11-13T09:00:00Z", jobId: "exec_older" });
    putRun("kumo", "20261112T0900Z.json", { ...contract.runRecord, slot: "2026-11-12T09:00:00Z", status: "skipped", jobId: undefined, reason: "no-saved-steps" });
    putRun("kumo", "20261111T0900Z.json", { ...contract.runRecord, slot: "2026-11-11T09:00:00Z", status: "claimed", jobId: undefined });
    putRun("kumo", "20261108T0900Z.json", { ...contract.runRecord, slot: "2026-11-08T09:00:00Z", status: "failed", jobId: undefined, reason: "TimeoutError: timed out" });
    putRun("kumo", "20261110T0900Z.json", "");
    putRun("kumo", "20261109T0900Z.json", { ...contract.runRecord, clientId: "hinata", slot: "2026-11-09T09:00:00Z" });
    putRun("hinata", contract.runFileName, { ...contract.runRecord, clientId: "hinata", jobId: "exec_hinata" });

    expect(await scheduledRuns("kumo", env, projectRoot)).toEqual([
      { status: "skipped", slot: "2026-11-12T09:00:00Z", timeZone: "Asia/Tokyo", reason: "no-saved-steps" },
      { status: "started", slot: "2026-11-13T09:00:00Z", timeZone: "Asia/Tokyo", jobId: "exec_older" },
      ...(contract.reply.runs as ScheduledRun[]),
    ]);
  });

  test("no records means no runs", async () => {
    expect(await scheduledRuns("kumo", env, projectRoot)).toEqual([]);
  });

  test("keeps only the latest twenty", async () => {
    for (let day = 1; day <= 25; day += 1) {
      const date = `2026-10-${String(day).padStart(2, "0")}`;
      putRun("kumo", `${date.replaceAll("-", "")}T0900Z.json`, { ...contract.runRecord, slot: `${date}T09:00:00Z`, jobId: `exec_${day}` });
    }
    const runs = await scheduledRuns("kumo", env, projectRoot);
    expect(runs).toHaveLength(20);
    expect(runs[0]).toMatchObject({ status: "started", jobId: "exec_6" });
    expect(runs.at(-1)).toMatchObject({ status: "started", jobId: "exec_25" });
  });

  test("a client id that is not a plain id is refused", async () => {
    await expect(scheduledRuns("../kumo", env, projectRoot)).rejects.toThrow(ScheduleStoreError);
  });
});
