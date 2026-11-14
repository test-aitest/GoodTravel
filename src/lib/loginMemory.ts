import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { dataDirectory, writePrivateFile } from "./settings";
import { allLoginNames } from "./setup";

type Env = Record<string, string | undefined>;

export const LOGIN_NAMES_FILE = "login-names.json";

const memorySchema = z.strictObject({
  version: z.literal(1),
  clients: z.record(z.string(), z.strictObject({ present: z.array(z.enum(allLoginNames)), checkedAt: z.string() })),
});

type Memory = z.infer<typeof memorySchema>;

export function loginNamesPath(env: Env = process.env, projectRoot = process.cwd()) {
  return join(dataDirectory(env, projectRoot), LOGIN_NAMES_FILE);
}

async function readMemory(path: string): Promise<Memory> {
  try {
    const parsed = memorySchema.safeParse(JSON.parse(await readFile(path, "utf8")));
    if (parsed.success) return parsed.data;
  } catch {
    return { version: 1, clients: {} };
  }
  return { version: 1, clients: {} };
}

export async function rememberedLogins(clientId: string, env: Env = process.env, projectRoot = process.cwd()) {
  const memory = await readMemory(loginNamesPath(env, projectRoot));
  return Object.hasOwn(memory.clients, clientId) ? memory.clients[clientId].present : null;
}

export async function rememberLogins(clientId: string, present: readonly string[], env: Env = process.env, projectRoot = process.cwd(), now = new Date()) {
  const path = loginNamesPath(env, projectRoot);
  const memory = await readMemory(path);
  const names = allLoginNames.filter((name) => present.includes(name));
  const clients = { ...memory.clients, [clientId]: { present: names, checkedAt: now.toISOString() } };
  await writePrivateFile(path, `${JSON.stringify({ version: 1, clients }, null, 2)}\n`);
}
