import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { chmod, link, mkdir, open, readFile, unlink } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { dataDirectory } from "./settings";

type Env = Record<string, string | undefined>;
type HeaderReader = { get: (name: string) => string | null };

export const MCP_TOKEN_FILE = "mcp-token";

const tokenPattern = /^[0-9a-f]{64}$/;

export class McpTokenError extends Error {}

export function mcpTokenPath(env: Env = process.env, projectRoot = process.cwd()) {
  return join(dataDirectory(env, projectRoot), MCP_TOKEN_FILE);
}

async function storedToken(path: string) {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  const token = text.trim();
  if (!tokenPattern.test(token)) throw new McpTokenError(`The MCP key file ${path} does not hold a key. Move it away and a new key will be made.`);
  return token;
}

async function createOnce(path: string) {
  const directory = dirname(path);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await chmod(directory, 0o700);
  const token = randomBytes(32).toString("hex");
  const temporary = join(directory, `.${basename(path)}.${randomBytes(6).toString("hex")}.tmp`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(`${token}\n`);
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await link(temporary, path);
    return token;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") return null;
    throw error;
  } finally {
    await unlink(temporary);
  }
}

export async function mcpToken(env: Env = process.env, projectRoot = process.cwd()) {
  const path = mcpTokenPath(env, projectRoot);
  const existing = await storedToken(path);
  if (existing) return existing;
  const made = await createOnce(path);
  if (made) return made;
  const kept = await storedToken(path);
  if (!kept) throw new McpTokenError(`The MCP key file ${path} disappeared while it was being made. Try again.`);
  return kept;
}

function digest(value: string) {
  return createHash("sha256").update(value).digest();
}

export function isMcpCaller(headers: HeaderReader, token: string | null) {
  const sent = /^bearer (\S+)$/i.exec(headers.get("authorization") ?? "")?.[1];
  if (!sent || !token) return false;
  return timingSafeEqual(digest(sent), digest(token));
}
