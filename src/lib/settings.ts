import { randomBytes } from "node:crypto";
import { existsSync, realpathSync } from "node:fs";
import { chmod, mkdir, open, readFile, rename } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import { z } from "zod";

export const SETTINGS_VERSION = 1;
export const SETTINGS_FILE = "settings.json";
export const DATA_DIR_VARIABLE = "CLIENTOPS_DATA_DIR";

type Env = Record<string, string | undefined>;

export class SettingsError extends Error {
  override name = "SettingsError";
}

const settingsSchema = z.looseObject({
  version: z.literal(SETTINGS_VERSION),
  clients: z.record(z.string(), z.looseObject({ useClaude: z.boolean().optional() })).optional(),
});

type Settings = z.infer<typeof settingsSchema>;

function expandHome(path: string, home: string) {
  return path === "~" || path.startsWith("~/") ? join(home, path.slice(1)) : path;
}

function realResolve(path: string): string {
  const absolute = resolve(path);
  let existing = absolute;
  while (!existsSync(existing) && dirname(existing) !== existing) existing = dirname(existing);
  return realpathSync(existing) + absolute.slice(existing.length);
}

function isInside(path: string, root: string) {
  const away = relative(root, path);
  return away === "" || (!away.startsWith("..") && !isAbsolute(away));
}

export function dataDirectory(env: Env = process.env, projectRoot = process.cwd(), home = homedir()) {
  const configured = env[DATA_DIR_VARIABLE];
  const chosen = configured ? expandHome(configured, home) : join(env.XDG_STATE_HOME || join(home, ".local", "state"), "clientops");
  const directory = realResolve(chosen);
  const root = realResolve(projectRoot);
  if (isInside(directory, root)) {
    throw new SettingsError(`${DATA_DIR_VARIABLE} must point outside the project (${root}), so settings are never copied into a snapshot.`);
  }
  return directory;
}

export function settingsPath(env: Env = process.env, projectRoot = process.cwd(), home = homedir()) {
  return join(dataDirectory(env, projectRoot, home), SETTINGS_FILE);
}

async function readSettings(path: string): Promise<Settings> {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return { version: SETTINGS_VERSION };
    throw new SettingsError(`The settings file ${path} could not be read.`);
  }
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new SettingsError(`The settings file ${path} is not JSON. Fix or move it; nothing was guessed.`);
  }
  const parsed = settingsSchema.safeParse(raw);
  if (!parsed.success) throw new SettingsError(`The settings file ${path} is not settings version ${SETTINGS_VERSION}. Fix or move it; nothing was guessed.`);
  return parsed.data;
}

function clientEntry(settings: Settings, clientId: string) {
  const clients = settings.clients ?? {};
  return Object.hasOwn(clients, clientId) ? clients[clientId] : undefined;
}

export async function readUseClaude(clientId: string, env: Env = process.env, projectRoot = process.cwd()) {
  const settings = await readSettings(settingsPath(env, projectRoot));
  return clientEntry(settings, clientId)?.useClaude ?? true;
}

export async function writePrivateFile(path: string, content: string) {
  const directory = dirname(path);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700);
  const temporary = join(directory, `.${basename(path)}.${randomBytes(6).toString("hex")}.tmp`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(content);
    await handle.sync();
  } finally {
    await handle.close();
  }
  await chmod(temporary, 0o600);
  await rename(temporary, path);
}

export async function writeUseClaude(clientId: string, useClaude: boolean, env: Env = process.env, projectRoot = process.cwd()) {
  const path = settingsPath(env, projectRoot);
  const settings = await readSettings(path);
  const clients = { ...settings.clients, [clientId]: { ...clientEntry(settings, clientId), useClaude } };
  await writePrivateFile(path, `${JSON.stringify({ ...settings, version: SETTINGS_VERSION, clients }, null, 2)}\n`);
  return useClaude;
}
