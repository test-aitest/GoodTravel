import { existsSync, readFileSync, statSync } from "node:fs";

export const defaultVaultPath = "/home/daytona/.clientops/vault.json";

export type Vault =
  | { ok: true; values: Record<string, string> }
  | { ok: false; problem: "missing" | "unsafe" | "unreadable"; message: string };

const placeholderName = /^LP_[A-Za-z0-9_]+$/;

export function readVault(path: string): Vault {
  if (!existsSync(path)) return { ok: false, problem: "missing", message: `No vault at ${path}` };
  if ((statSync(path).mode & 0o077) !== 0) {
    return { ok: false, problem: "unsafe", message: `The vault at ${path} can be read by other users; it must be mode 600` };
  }
  const unreadable = (reason: string): Vault => ({ ok: false, problem: "unreadable", message: `The vault at ${path} ${reason}` });
  let data: unknown;
  try {
    data = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return unreadable("is not JSON");
  }
  if (typeof data !== "object" || data === null || (data as { version?: unknown }).version !== 1) return unreadable("is not a version 1 vault");
  const values = (data as { values?: unknown }).values;
  if (typeof values !== "object" || values === null || Array.isArray(values)) return unreadable("has no values object");
  const entries = Object.entries(values);
  if (entries.some(([name]) => !placeholderName.test(name))) return unreadable("has a name that does not start with LP_");
  if (entries.some(([, value]) => typeof value !== "string")) return unreadable("has a value that is not text");
  return { ok: true, values: Object.fromEntries(entries) as Record<string, string> };
}
