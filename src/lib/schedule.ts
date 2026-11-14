import { z } from "zod";

export const clockTimePattern = /^([01]\d|2[0-3]):[0-5]\d$/;

export type ClientSchedule = { isOn: boolean; time: string; timeZone: string };

export const defaultSchedule: ClientSchedule = { isOn: false, time: "18:00", timeZone: "Asia/Tokyo" };

export function isTimeZone(timeZone: string) {
  if (timeZone.trim() === "") return false;
  try {
    return new Intl.DateTimeFormat("en-US", { timeZone }).resolvedOptions().timeZone !== "";
  } catch {
    return false;
  }
}

export const clockTimeSchema = z.string().regex(clockTimePattern);
export const timeZoneSchema = z.string().refine(isTimeZone, "Not a time zone");

export const clientScheduleSchema = z.strictObject({ isOn: z.boolean(), time: clockTimeSchema, timeZone: timeZoneSchema });

export const skipReasons = ["waiting-for-approval", "no-saved-steps"] as const;
export type SkipReason = (typeof skipReasons)[number];

export type ScheduledRun =
  | { status: "started"; slot: string; timeZone: string; jobId: string }
  | { status: "skipped"; slot: string; timeZone: string; reason: SkipReason; waitingJobId?: string };

const startedRunSchema = z
  .object({ status: z.literal("started"), slot: z.iso.datetime(), timeZone: timeZoneSchema, jobId: z.string().min(1) })
  .transform(({ status, slot, timeZone, jobId }): ScheduledRun => ({ status, slot, timeZone, jobId }));

const skippedRunSchema = z
  .object({
    status: z.literal("skipped"),
    slot: z.iso.datetime(),
    timeZone: timeZoneSchema,
    reason: z.enum(skipReasons),
    waitingJobId: z.string().min(1).optional(),
  })
  .transform(({ status, slot, timeZone, reason, waitingJobId }): ScheduledRun =>
    waitingJobId === undefined ? { status, slot, timeZone, reason } : { status, slot, timeZone, reason, waitingJobId },
  );

export const scheduledRunSchema = z.union([startedRunSchema, skippedRunSchema]);

export function scheduleText(schedule: ClientSchedule) {
  return schedule.isOn ? `Runs daily at ${schedule.time}` : "Not scheduled";
}

export function timeZoneName(timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "long" }).formatToParts(new Date());
  return parts.find((part) => part.type === "timeZoneName")?.value ?? timeZone;
}

function calendarDay(moment: Date, timeZone: string) {
  const [year, month, day] = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" })
    .format(moment)
    .split("-")
    .map(Number);
  return Date.UTC(year, month - 1, day) / 86_400_000;
}

const relativeDays = new Map([
  [0, "Today"],
  [-1, "Yesterday"],
  [1, "Tomorrow"],
]);

function slotParts(slot: string, timeZone: string, now: Date | null) {
  const moment = new Date(slot);
  return {
    clock: new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(moment),
    relative: now === null ? undefined : relativeDays.get(calendarDay(moment, timeZone) - calendarDay(now, timeZone)),
    date: new Intl.DateTimeFormat("en-US", { timeZone, month: "short", day: "numeric" }).format(moment),
  };
}

export function slotLabel(slot: string, timeZone: string, now: Date | null) {
  const { clock, relative, date } = slotParts(slot, timeZone, now);
  return `${relative ?? date}, ${clock}`;
}

export function skippedRunText(slot: string, timeZone: string, now: Date | null) {
  const { clock, relative, date } = slotParts(slot, timeZone, now);
  const which = relative ? `${relative}'s ${clock} run` : `The ${date}, ${clock} run`;
  return `${which} was skipped because the last one is still waiting for your OK.`;
}
