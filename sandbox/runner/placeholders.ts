export const hidden = "*****";

const placeholder = /\$(LP_[A-Za-z0-9_]+)/g;

export type Environment = Record<string, string | undefined>;

export type Resolved = { ok: true; text: string; names: string[]; secrets: string[] } | { ok: false; missing: string[] };

export function placeholdersIn(text: string) {
  return [...new Set([...text.matchAll(placeholder)].map((match) => match[1]))];
}

export function resolvePlaceholders(text: string, env: Environment): Resolved {
  const names = placeholdersIn(text);
  const missing = names.filter((name) => !env[name]);
  if (missing.length > 0) return { ok: false, missing };
  return {
    ok: true,
    text: text.replace(placeholder, (_, name: string) => env[name] as string),
    names,
    secrets: names.map((name) => env[name] as string),
  };
}

export function secretsIn(env: Environment) {
  return Object.entries(env).flatMap(([name, value]) => (name.startsWith("LP_") && value ? [value] : []));
}

function hide(text: string, secrets: readonly string[]) {
  return secrets.reduce((current, secret) => current.split(secret).join(hidden), text);
}

export function scrub<T>(value: T, secrets: readonly string[]): T {
  const ordered = [...new Set(secrets.filter((secret) => secret !== ""))].toSorted((a, b) => b.length - a.length);
  if (ordered.length === 0) return value;
  const walk = (item: unknown): unknown => {
    if (typeof item === "string") return hide(item, ordered);
    if (Array.isArray(item)) return item.map(walk);
    if (item !== null && typeof item === "object") return Object.fromEntries(Object.entries(item).map(([key, entry]) => [key, walk(entry)]));
    return item;
  };
  return walk(value) as T;
}
