import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/schedule.json";
import { defaultSchedule, type ClientSchedule, type ScheduledRun } from "./schedule";
import { handleReadSchedule, handleSetSchedule, type ScheduleDeps } from "./scheduleRoutes";
import type { RequestLike } from "./setupRoutes";
import { ScheduleStoreError } from "./scheduleStore";

const sameOrigin = { "sec-fetch-site": "same-origin", origin: "http://app.test", host: "app.test", "content-type": "application/json" };
const run: ScheduledRun = { status: "started", slot: "2026-11-14T09:00:00Z", timeZone: "Asia/Tokyo", jobId: "exec_1" };

function request(body: unknown, headers: Record<string, string> = sameOrigin): RequestLike {
  const lower = Object.fromEntries(Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]));
  return { headers: { get: (name: string) => lower[name.toLowerCase()] ?? null }, text: async () => (typeof body === "string" ? body : JSON.stringify(body)) };
}

function deps(options: { stored?: ClientSchedule; runs?: ScheduledRun[]; failing?: "read" | "write" | "runs" } = {}) {
  const writes: { clientId: string; schedule: ClientSchedule }[] = [];
  const warnings: string[] = [];
  let current = options.stored ?? defaultSchedule;
  const built: ScheduleDeps = {
    findClient: (clientId) => (clientId === "kumo" ? { id: "kumo", name: "Kumo Coffee Roasters" } : null),
    read: async () => {
      if (options.failing === "read") throw new ScheduleStoreError("The schedules file is not JSON.");
      return current;
    },
    write: async (clientId, schedule) => {
      if (options.failing === "write") throw new ScheduleStoreError("The schedules file is not JSON.");
      writes.push({ clientId, schedule });
      current = schedule;
      return schedule;
    },
    runs: async () => {
      if (options.failing === "runs") throw new ScheduleStoreError("The runs could not be read.");
      return options.runs ?? [run];
    },
    warn: (message) => warnings.push(message),
  };
  return { built, writes, warnings };
}

async function bodyOf(response: Response) {
  return (await response.json()) as Record<string, unknown>;
}

describe("GET the schedule", () => {
  test("answers the client's schedule, the line to show and the runs started on schedule", async () => {
    const response = await handleReadSchedule("kumo", deps().built);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await bodyOf(response)).toEqual({ clientId: "kumo", schedule: defaultSchedule, text: "Not scheduled", runs: [run] });
  });

  test("a skipped run is in the reply with why and which run is still waiting, in the shared shape", async () => {
    const used = deps({ stored: contract.reply.schedule, runs: contract.reply.runs as ScheduledRun[] });
    expect(await bodyOf(await handleReadSchedule("kumo", used.built))).toEqual(contract.reply);
  });

  test("an unknown client is not found", async () => {
    expect((await handleReadSchedule("sora-2", deps().built)).status).toBe(404);
  });

  test("a schedule that cannot be read is said plainly", async () => {
    const used = deps({ failing: "read" });
    const response = await handleReadSchedule("kumo", used.built);
    expect(response.status).toBe(500);
    expect(await bodyOf(response)).toEqual({ error: "The schedule could not be read." });
    expect(used.warnings).toHaveLength(1);
  });
});

describe("PUT the schedule", () => {
  test("changes the time and answers the new line", async () => {
    const used = deps();
    const response = await handleSetSchedule(request({ isOn: true, time: "07:30" }), "kumo", used.built);
    expect(response.status).toBe(200);
    expect(await bodyOf(response)).toEqual({ clientId: "kumo", schedule: { isOn: true, time: "07:30", timeZone: "Asia/Tokyo" }, text: "Runs daily at 07:30", runs: [run] });
    expect(used.writes).toEqual([{ clientId: "kumo", schedule: { isOn: true, time: "07:30", timeZone: "Asia/Tokyo" } }]);
  });

  test("turns it off and says Not scheduled", async () => {
    const response = await handleSetSchedule(request({ isOn: false, time: "18:00" }), "kumo", deps().built);
    expect((await bodyOf(response)).text).toBe("Not scheduled");
  });

  test("keeps the client's time zone unless a new one is given", async () => {
    const used = deps({ stored: { isOn: true, time: "18:00", timeZone: "America/New_York" } });
    await handleSetSchedule(request({ isOn: true, time: "09:00" }), "kumo", used.built);
    await handleSetSchedule(request({ isOn: true, time: "09:00", timeZone: "Europe/London" }), "kumo", used.built);
    expect(used.writes.map((write) => write.schedule.timeZone)).toEqual(["America/New_York", "Europe/London"]);
  });

  test("only the ClientOps page can change it", async () => {
    for (const headers of [{ ...sameOrigin, "sec-fetch-site": "cross-site" }, { ...sameOrigin, "sec-fetch-site": "same-site" }]) {
      const used = deps();
      expect((await handleSetSchedule(request({ isOn: true, time: "07:30" }, headers), "kumo", used.built)).status).toBe(403);
      expect(used.writes).toEqual([]);
    }
  });

  test("refuses anything that is not a time like 18:00 or a real time zone, and changes nothing", async () => {
    const cases: [unknown, number, Record<string, string>?][] = [
      [{ isOn: true, time: "6pm" }, 400],
      [{ isOn: true, time: "24:00" }, 400],
      [{ isOn: true }, 400],
      [{ isOn: "yes", time: "18:00" }, 400],
      [{ isOn: true, time: "18:00", timeZone: "Mars/Olympus" }, 400],
      [{ isOn: true, time: "18:00", runAs: "hinata" }, 400],
      ["{not json", 400],
      [{ isOn: true, time: "18:00" }, 415, { ...sameOrigin, "content-type": "text/plain" }],
      [{ isOn: true, time: "18:00", padding: "x".repeat(9000) }, 413],
    ];
    for (const [sent, status, headers] of cases) {
      const used = deps();
      expect((await handleSetSchedule(request(sent, headers ?? sameOrigin), "kumo", used.built)).status).toBe(status);
      expect(used.writes).toEqual([]);
    }
    const bad = await handleSetSchedule(request({ isOn: true, time: "6pm" }), "kumo", deps().built);
    expect(await bodyOf(bad)).toEqual({ error: "Pick a time like 18:00." });
  });

  test("an unknown client is not found", async () => {
    const used = deps();
    expect((await handleSetSchedule(request({ isOn: true, time: "07:30" }), "sora-2", used.built)).status).toBe(404);
    expect(used.writes).toEqual([]);
  });

  test("a schedule that cannot be saved is said plainly", async () => {
    const used = deps({ failing: "write" });
    const response = await handleSetSchedule(request({ isOn: true, time: "07:30" }), "kumo", used.built);
    expect(response.status).toBe(500);
    expect(await bodyOf(response)).toEqual({ error: "The schedule could not be saved." });
  });
});
