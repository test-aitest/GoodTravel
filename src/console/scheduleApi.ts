import { z } from "zod";
import { clientScheduleSchema, scheduledRunSchema, type ClientSchedule, type ScheduledRun } from "@/lib/schedule";

export type ScheduleChange = Pick<ClientSchedule, "isOn" | "time">;

export type ScheduleReply = { clientId: string; schedule: ClientSchedule; text: string; runs: ScheduledRun[] };

export type ScheduleApi = {
  load(clientId: string): Promise<ScheduleReply>;
  save(clientId: string, change: ScheduleChange): Promise<ScheduleReply>;
};

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

const unreadable = "The schedule could not be read. Try again in a minute.";
const unsaved = "The schedule could not be saved. Try again in a minute.";

const replySchema = z.object({
  clientId: z.string(),
  schedule: clientScheduleSchema,
  text: z.string(),
  runs: z.array(scheduledRunSchema),
});
const problemSchema = z.object({ error: z.string() });

async function send(fetcher: Fetcher, clientId: string, fallback: string, init: RequestInit): Promise<ScheduleReply> {
  const response = await fetcher(`/api/clients/${encodeURIComponent(clientId)}/schedule`, { cache: "no-store", credentials: "same-origin", ...init }).catch(() => null);
  const body: unknown = response ? await response.json().catch(() => null) : null;
  if (!response?.ok) throw new Error(problemSchema.safeParse(body).data?.error ?? fallback);
  const parsed = replySchema.safeParse(body);
  if (!parsed.success) throw new Error(fallback);
  return parsed.data;
}

export function createScheduleApi(fetcher: Fetcher = (input, init) => fetch(input, init)): ScheduleApi {
  return {
    load: (clientId) => send(fetcher, clientId, unreadable, { method: "GET", headers: { accept: "application/json" } }),
    save: (clientId, change) =>
      send(fetcher, clientId, unsaved, {
        method: "PUT",
        headers: { accept: "application/json", "content-type": "application/json" },
        body: JSON.stringify(change),
      }),
  };
}

export const scheduleApi = createScheduleApi();

export function scheduledRunKey(run: ScheduledRun) {
  return run.status === "started" ? run.jobId : `skipped:${run.slot}`;
}

export function unseenScheduledRuns(runs: readonly ScheduledRun[], knownKeys: Iterable<string>) {
  const known = new Set(knownKeys);
  return runs.filter((run) => !known.has(scheduledRunKey(run)));
}
