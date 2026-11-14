import { failure, type Command, type Failure, type PageSnapshot, type RunnerResult, type ScriptFailure, type ScriptStep, type ScriptTable } from "./commands";
import { placeholdersIn, type Environment } from "./placeholders";
import { sanitizeSnapshot } from "./sanitize";
import type { Vault } from "./vault";

export type ScriptCommand = Extract<Command, { command: "script" }>;
export type Spawned = { exitCode: number | null; stdout: string; stderr: string; timedOut: boolean };
export type Spawner = (argv: string[], options: { env: Record<string, string>; stdin: string; timeoutMs: number }) => Promise<Spawned>;
export type ScriptOptions = { lightpanda: string; spawn?: Spawner; base?: Environment; now?: () => number };

type Prepared = { ok: true; env: Record<string, string>; secrets: string[] } | { ok: false; result: Failure };
type Marker = {
  clientops?: unknown;
  stepId?: unknown;
  phase?: unknown;
  tookMs?: unknown;
  checks?: unknown;
  url?: unknown;
  title?: unknown;
  html?: unknown;
  values?: unknown;
  into?: unknown;
  columns?: unknown;
  rows?: unknown;
  pages?: unknown;
};

export const defaultScriptTimeoutMs = 60_000;
const passedThrough = ["PATH", "HOME", "TMPDIR"] as const;
const colour = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, "g");
const checkPrefix = "check failed: ";
const checkCode = /^([a-z][a-z-]*): ([\s\S]*)$/;

export function scriptEnvironment(source: string, vault: Vault, base: Environment): Prepared {
  const names = placeholdersIn(source);
  if (names.length > 0 && !vault.ok && vault.problem !== "missing") {
    return { ok: false, result: failure("script", "vault-unreadable", vault.message) };
  }
  const values = vault.ok ? vault.values : {};
  const missing = names.filter((name) => !values[name]);
  if (missing.length > 0) {
    const verb = missing.length === 1 ? "is" : "are";
    const why = vault.ok ? "" : ` (${vault.message})`;
    return {
      ok: false,
      result: failure("script", "placeholder-unset", `${missing.join(" and ")} ${verb} not in the vault on this computer${why}`, { missing }),
    };
  }
  const env: Record<string, string> = {};
  for (const name of passedThrough) {
    const value = base[name];
    if (value) env[name] = value;
  }
  env.LIGHTPANDA_DISABLE_TELEMETRY = "true";
  for (const name of names) env[name] = values[name];
  return { ok: true, env, secrets: Object.values(values).filter((value) => value !== "") };
}

function markerOf(line: string): Marker | null {
  const text = line.trim();
  if (!text.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(text) as unknown;
    return typeof parsed === "object" && parsed !== null && "clientops" in parsed ? (parsed as Marker) : null;
  } catch {
    return null;
  }
}

function count(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.round(value) : 0;
}

function snapshotOf(marker: Marker): PageSnapshot | null {
  if (typeof marker.url !== "string" || typeof marker.html !== "string") return null;
  let host: string;
  try {
    host = new URL(marker.url).host;
  } catch {
    return null;
  }
  const values =
    typeof marker.values === "object" && marker.values !== null
      ? Object.fromEntries(Object.entries(marker.values).filter((entry): entry is [string, string] => typeof entry[1] === "string"))
      : {};
  return { url: marker.url, host, title: typeof marker.title === "string" ? marker.title : "", ...sanitizeSnapshot(marker.html, { values }) };
}

const texts = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");

function tableOf(marker: Marker): ScriptTable | null {
  if (!texts(marker.columns) || !Array.isArray(marker.rows) || !marker.rows.every(texts)) return null;
  return { columns: marker.columns, rows: marker.rows, pages: Math.max(1, count(marker.pages)) };
}

function errorMessage(stderr: string, exitCode: number) {
  const lines = stderr.replace(colour, "").split("\n").map((line) => line.trim()).filter((line) => line !== "");
  const error = lines.findLast((line) => line.startsWith("Error: "));
  if (error) return error.slice("Error: ".length);
  return lines.at(-1) ?? `Lightpanda exited with code ${exitCode}`;
}

function failureOf(message: string, stepId: string | null): ScriptFailure {
  if (!message.startsWith(checkPrefix)) return { stepId, kind: "step", code: null, message };
  const detail = message.slice(checkPrefix.length);
  const coded = checkCode.exec(detail);
  return coded ? { stepId, kind: "check", code: coded[1], message: coded[2] } : { stepId, kind: "check", code: null, message: detail };
}

export function readScriptRun(run: { exitCode: number; stdout: string; stderr: string; tookMs: number }): RunnerResult {
  const steps: ScriptStep[] = [];
  const tables: Record<string, ScriptTable> = {};
  let result: Record<string, unknown> | undefined;
  for (const line of run.stdout.split("\n")) {
    const marker = markerOf(line);
    if (!marker) continue;
    const stepId = typeof marker.stepId === "string" ? marker.stepId : null;
    if (marker.clientops === "result") {
      const { clientops: _clientops, ...rest } = marker as Record<string, unknown>;
      result = rest;
    } else if (marker.clientops === "step" && stepId && marker.phase === "start") {
      steps.push({ stepId, status: "failed", tookMs: 0, checks: 0, pages: [] });
    } else if (marker.clientops === "step" && stepId && marker.phase === "done") {
      const step = steps.findLast((candidate) => candidate.stepId === stepId);
      if (step) Object.assign(step, { status: "done", tookMs: count(marker.tookMs), checks: count(marker.checks) });
    } else if (marker.clientops === "table" && typeof marker.into === "string") {
      const table = tableOf(marker);
      if (table) tables[marker.into] = table;
    } else if (marker.clientops === "page" && stepId) {
      const snapshot = snapshotOf(marker);
      const step = steps.findLast((candidate) => candidate.stepId === stepId);
      if (snapshot && step) step.pages.push(snapshot);
    }
  }
  const open = steps.find((step) => step.status === "failed")?.stepId ?? null;
  if (run.exitCode === 0 && result !== undefined && open === null) {
    return { ok: true, command: "script", outcome: "passed", tookMs: run.tookMs, steps, tables, result };
  }
  const message = run.exitCode === 0 ? "The script ended without a result" : errorMessage(run.stderr, run.exitCode);
  const failed = failureOf(message, run.exitCode === 0 ? null : open);
  return { ok: true, command: "script", outcome: failed.kind === "check" ? "check-failed" : "step-failed", tookMs: run.tookMs, steps, tables, failure: failed };
}

export const spawnProcess: Spawner = async (argv, options) => {
  const child = Bun.spawn(argv, { env: options.env, stdin: new Blob([options.stdin]), stdout: "pipe", stderr: "pipe" });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill("SIGKILL");
  }, options.timeoutMs);
  try {
    const [stdout, stderr, exitCode] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
    return { exitCode: timedOut ? null : exitCode, stdout, stderr, timedOut };
  } finally {
    clearTimeout(timer);
  }
};

export async function runScript(command: ScriptCommand, vault: Vault, options: ScriptOptions): Promise<RunnerResult> {
  const prepared = scriptEnvironment(command.source, vault, options.base ?? process.env);
  if (!prepared.ok) return prepared.result;
  const timeoutMs = command.timeoutMs ?? defaultScriptTimeoutMs;
  const now = options.now ?? (() => performance.now());
  const spawn = options.spawn ?? spawnProcess;
  const started = now();
  let spawned: Spawned;
  try {
    spawned = await spawn([options.lightpanda, "run", "-"], { env: prepared.env, stdin: command.source, timeoutMs });
  } catch (error) {
    return failure("script", "runner-error", `Could not start Lightpanda: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (spawned.timedOut || spawned.exitCode === null) {
    return failure("script", "script-timeout", `The script did not finish within ${Math.ceil(timeoutMs / 1000)} seconds`);
  }
  return readScriptRun({ exitCode: spawned.exitCode, stdout: spawned.stdout, stderr: spawned.stderr, tookMs: Math.round(now() - started) });
}
