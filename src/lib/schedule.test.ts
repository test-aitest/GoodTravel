import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/schedule.json";
import { clientScheduleSchema, defaultSchedule, isTimeZone, scheduledRunSchema, scheduleText, skippedRunText, slotLabel, timeZoneName, type ScheduledRun } from "./schedule";

const sharedRuns = contract.reply.runs as ScheduledRun[];

const tokyo = "Asia/Tokyo";

describe("the schedule line", () => {
  test("says the daily time when it is on, and Not scheduled when it is off", () => {
    expect(scheduleText({ isOn: true, time: "18:00", timeZone: tokyo })).toBe("Runs daily at 18:00");
    expect(scheduleText({ isOn: true, time: "07:30", timeZone: tokyo })).toBe("Runs daily at 07:30");
    expect(scheduleText({ isOn: false, time: "18:00", timeZone: tokyo })).toBe("Not scheduled");
  });

  test("by default nothing is scheduled until someone turns it on, the same default as the worker", () => {
    expect(defaultSchedule).toEqual({ isOn: false, time: "18:00", timeZone: tokyo });
    expect(defaultSchedule).toEqual(contract.expected.sora);
    expect(scheduleText(defaultSchedule)).toBe("Not scheduled");
  });

  test("names the time zone in words", () => {
    expect(timeZoneName(tokyo)).toBe("Japan Standard Time");
  });
});

describe("what a schedule may hold", () => {
  test("a 24-hour HH:MM time and a real time zone", () => {
    expect(clientScheduleSchema.safeParse({ isOn: true, time: "00:00", timeZone: tokyo }).success).toBe(true);
    expect(clientScheduleSchema.safeParse({ isOn: true, time: "23:59", timeZone: "America/New_York" }).success).toBe(true);
    for (const time of ["24:00", "6pm", "18:0", "18:00:00", ""]) {
      expect(clientScheduleSchema.safeParse({ isOn: true, time, timeZone: tokyo }).success).toBe(false);
    }
    expect(clientScheduleSchema.safeParse({ isOn: true, time: "18:00", timeZone: "Mars/Olympus" }).success).toBe(false);
    expect(clientScheduleSchema.safeParse({ isOn: "yes", time: "18:00", timeZone: tokyo }).success).toBe(false);
  });

  test("time zones are checked against the runtime's list", () => {
    expect(isTimeZone(tokyo)).toBe(true);
    expect(isTimeZone("Europe/London")).toBe(true);
    expect(isTimeZone("Not/AZone")).toBe(false);
    expect(isTimeZone("")).toBe(false);
  });

  test("the worker's run records read as the runs in the reply", () => {
    expect(scheduledRunSchema.parse(contract.runRecord)).toEqual(sharedRuns[0]);
    expect(scheduledRunSchema.parse(contract.skippedRecord)).toEqual(sharedRuns[1]);
  });

  test("a skipped run says why and, when another run waits, which one", () => {
    expect(scheduledRunSchema.parse({ ...contract.skippedRecord, reason: "no-saved-steps", waitingJobId: undefined })).toEqual({
      status: "skipped",
      slot: "2026-11-15T09:00:00Z",
      timeZone: tokyo,
      reason: "no-saved-steps",
    });
    expect(scheduledRunSchema.safeParse({ ...contract.skippedRecord, reason: "felt like it" }).success).toBe(false);
    expect(scheduledRunSchema.safeParse({ ...contract.runRecord, status: "claimed" }).success).toBe(false);
    expect(scheduledRunSchema.safeParse({ ...contract.runRecord, status: "failed", reason: "TimeoutError" }).success).toBe(false);
  });
});

describe("the line for a run skipped while the last one waits", () => {
  const slot = "2026-11-15T09:00:00Z";

  test("names the day and time in the client's time zone", () => {
    expect(skippedRunText(slot, tokyo, new Date("2026-11-15T09:01:00Z"))).toBe("Today's 18:00 run was skipped because the last one is still waiting for your OK.");
    expect(skippedRunText(slot, tokyo, new Date("2026-11-16T01:00:00Z"))).toBe("Yesterday's 18:00 run was skipped because the last one is still waiting for your OK.");
    expect(skippedRunText(slot, tokyo, new Date("2026-11-20T01:00:00Z"))).toBe("The Nov 15, 18:00 run was skipped because the last one is still waiting for your OK.");
    expect(skippedRunText(slot, tokyo, null)).toBe("The Nov 15, 18:00 run was skipped because the last one is still waiting for your OK.");
  });
});

describe("the divider over a scheduled run", () => {
  const slot = "2026-11-14T09:00:00Z";

  test("reads Today, 18:00 on the same Tokyo day", () => {
    expect(slotLabel(slot, tokyo, new Date("2026-11-14T09:00:30Z"))).toBe("Today, 18:00");
    expect(slotLabel(slot, tokyo, new Date("2026-11-14T14:59:00Z"))).toBe("Today, 18:00");
  });

  test("turns into Yesterday after midnight in Tokyo, even when UTC is still on the same date", () => {
    expect(slotLabel(slot, tokyo, new Date("2026-11-14T15:01:00Z"))).toBe("Yesterday, 18:00");
  });

  test("reads Tomorrow for a slot on the next Tokyo day", () => {
    expect(slotLabel("2026-11-15T09:00:00Z", tokyo, new Date("2026-11-14T10:00:00Z"))).toBe("Tomorrow, 18:00");
  });

  test("shows the date when it is further away", () => {
    expect(slotLabel("2026-11-12T09:00:00Z", tokyo, new Date("2026-11-14T10:00:00Z"))).toBe("Nov 12, 18:00");
  });

  test("before the screen knows the time it shows the date instead of guessing Today", () => {
    expect(slotLabel(slot, tokyo, null)).toBe("Nov 14, 18:00");
  });

  test("uses the client's time zone, not the screen's", () => {
    expect(slotLabel("2026-11-14T23:00:00Z", "America/New_York", new Date("2026-11-14T23:30:00Z"))).toBe("Today, 18:00");
  });
});
