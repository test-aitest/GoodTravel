import type { ClientSchedule } from "@/lib/schedule";

export function threadSubtitle(options: { clientName: string; jobsMode: "live" | "playback"; schedule: ClientSchedule | null; hasSavedSteps: boolean }) {
  const { clientName, jobsMode, schedule, hasSavedSteps } = options;
  if (jobsMode === "playback") return hasSavedSteps ? `${clientName} · runs daily at 18:00` : clientName;
  return schedule?.isOn ? `${clientName} · runs daily at ${schedule.time}` : clientName;
}
