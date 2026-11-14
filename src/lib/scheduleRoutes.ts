import { z } from "zod";
import type { KnownClient } from "./jobs";
import { clockTimeSchema, scheduleText, timeZoneSchema, type ClientSchedule, type ScheduledRun } from "./schedule";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

export type ScheduleDeps = {
  findClient: (clientId: string) => KnownClient | null;
  read(clientId: string): Promise<ClientSchedule>;
  write(clientId: string, schedule: ClientSchedule): Promise<ClientSchedule>;
  runs(clientId: string): Promise<ScheduledRun[]>;
  warn?: (message: string) => void;
};

const LARGEST_BODY = 8 * 1024;

const changeSchema = z.strictObject({ isOn: z.boolean(), time: clockTimeSchema, timeZone: timeZoneSchema.optional() });

const copy = {
  unknownClient: "There is no such client.",
  notThisPage: "Only the ClientOps page can do this.",
  notJson: "Send this as JSON.",
  tooLarge: "That is too much to be a schedule.",
  pickTime: "Pick a time like 18:00.",
  unreadable: "The schedule could not be read.",
  unsaved: "The schedule could not be saved.",
};

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

function problem(status: number, error: string) {
  return reply({ error }, status);
}

function warn(deps: ScheduleDeps, clientId: string, error: unknown) {
  deps.warn?.(`Schedule for ${clientId}: ${error instanceof Error ? error.message : "unknown error"}`);
}

async function answer(clientId: string, schedule: ClientSchedule, deps: ScheduleDeps) {
  return reply({ clientId, schedule, text: scheduleText(schedule), runs: await deps.runs(clientId) });
}

export async function handleReadSchedule(clientId: string, deps: ScheduleDeps) {
  if (!deps.findClient(clientId)) return problem(404, copy.unknownClient);
  try {
    return await answer(clientId, await deps.read(clientId), deps);
  } catch (error) {
    warn(deps, clientId, error);
    return problem(500, copy.unreadable);
  }
}

async function readChange(request: RequestLike): Promise<{ ok: true; value: unknown } | { ok: false; response: Response }> {
  const type = request.headers.get("content-type") ?? "";
  if (!type.toLowerCase().startsWith("application/json")) return { ok: false, response: problem(415, copy.notJson) };
  const text = await request.text();
  if (text.length > LARGEST_BODY) return { ok: false, response: problem(413, copy.tooLarge) };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false, response: problem(400, copy.notJson) };
  }
}

export async function handleSetSchedule(request: RequestLike, clientId: string, deps: ScheduleDeps) {
  if (!deps.findClient(clientId)) return problem(404, copy.unknownClient);
  if (!isSameOrigin(request)) return problem(403, copy.notThisPage);
  const sent = await readChange(request);
  if (!sent.ok) return sent.response;
  const change = changeSchema.safeParse(sent.value);
  if (!change.success) return problem(400, copy.pickTime);
  try {
    const timeZone = change.data.timeZone ?? (await deps.read(clientId)).timeZone;
    const saved = await deps.write(clientId, { isOn: change.data.isOn, time: change.data.time, timeZone });
    return await answer(clientId, saved, deps);
  } catch (error) {
    warn(deps, clientId, error);
    return problem(500, copy.unsaved);
  }
}
