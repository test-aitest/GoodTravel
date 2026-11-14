import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { clientScheduleSchema, defaultSchedule, scheduledRunSchema, type ClientSchedule, type ScheduledRun } from "./schedule";
import { dataDirectory, writePrivateFile } from "./settings";

type Env = Record<string, string | undefined>;

export const SCHEDULES_FILE = "schedules.json";
export const SCHEDULES_VERSION = 1;
export const RUNS_DIRECTORY = "schedule-runs";
export const RUNS_SHOWN = 20;

const clientIdPattern = /^[a-z0-9][a-z0-9-]{0,62}$/;

export class ScheduleStoreError extends Error {
  override name = "ScheduleStoreError";
}

const storedEntrySchema = z.looseObject({ ...clientScheduleSchema.shape, updatedAt: z.iso.datetime().optional() });

const schedulesFileSchema = z.looseObject({
  version: z.literal(SCHEDULES_VERSION),
  clients: z.record(z.string(), storedEntrySchema).optional(),
});

type SchedulesFile = z.infer<typeof schedulesFileSchema>;

function schedulesPath(env: Env, projectRoot: string) {
  return join(dataDirectory(env, projectRoot), SCHEDULES_FILE);
}

async function readSchedules(path: string): Promise<SchedulesFile> {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { version: SCHEDULES_VERSION };
    throw new ScheduleStoreError(`The schedules file ${path} could not be read.`);
  }
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new ScheduleStoreError(`The schedules file ${path} is not JSON. Fix or move it; nothing was guessed.`);
  }
  const parsed = schedulesFileSchema.safeParse(raw);
  if (!parsed.success) throw new ScheduleStoreError(`The schedules file ${path} is not schedules version ${SCHEDULES_VERSION}. Fix or move it; nothing was guessed.`);
  return parsed.data;
}

function entryOf(file: SchedulesFile, clientId: string) {
  const clients = file.clients ?? {};
  return Object.hasOwn(clients, clientId) ? clients[clientId] : undefined;
}

export async function readSchedule(clientId: string, env: Env = process.env, projectRoot = process.cwd()): Promise<ClientSchedule> {
  const entry = entryOf(await readSchedules(schedulesPath(env, projectRoot)), clientId);
  return entry ? { isOn: entry.isOn, time: entry.time, timeZone: entry.timeZone } : { ...defaultSchedule };
}

let writing: Promise<unknown> = Promise.resolve();

export function writeSchedule(clientId: string, schedule: ClientSchedule, env: Env = process.env, projectRoot = process.cwd(), now = new Date()) {
  const write = async () => {
    const path = schedulesPath(env, projectRoot);
    const file = await readSchedules(path);
    const clients = { ...file.clients, [clientId]: { ...entryOf(file, clientId), ...schedule, updatedAt: now.toISOString() } };
    await writePrivateFile(path, `${JSON.stringify({ ...file, version: SCHEDULES_VERSION, clients }, null, 2)}\n`);
    return { isOn: schedule.isOn, time: schedule.time, timeZone: schedule.timeZone };
  };
  const written = writing.then(write, write);
  writing = written.catch(() => undefined);
  return written;
}

async function readRun(path: string) {
  try {
    return JSON.parse(await readFile(path, "utf8")) as unknown;
  } catch {
    return null;
  }
}

export async function scheduledRuns(clientId: string, env: Env = process.env, projectRoot = process.cwd()): Promise<ScheduledRun[]> {
  if (!clientIdPattern.test(clientId)) throw new ScheduleStoreError(`${clientId} is not a client id.`);
  const folder = join(dataDirectory(env, projectRoot), RUNS_DIRECTORY, clientId);
  let names: string[];
  try {
    names = await readdir(folder);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw new ScheduleStoreError(`The scheduled runs in ${folder} could not be read.`);
  }
  const records = await Promise.all(names.filter((name) => name.endsWith(".json") && !name.startsWith(".")).map((name) => readRun(join(folder, name))));
  return records
    .flatMap((record) => {
      if (typeof record !== "object" || record === null || (record as { clientId?: unknown }).clientId !== clientId) return [];
      const parsed = scheduledRunSchema.safeParse(record);
      return parsed.success ? [parsed.data] : [];
    })
    .toSorted((first, second) => Date.parse(first.slot) - Date.parse(second.slot))
    .slice(-RUNS_SHOWN);
}
