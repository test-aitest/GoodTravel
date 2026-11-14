import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { BrowserTab } from "./cdp";
import { daemonAnswers, sendToDaemon, waitForDaemon } from "./client";
import { failure, parseCommand, prepareCommand, type CommandName, type RunnerResult } from "./commands";
import { startDaemon } from "./daemon";
import { scrub, secretsIn } from "./placeholders";
import { runScript } from "./script";
import { defaultVaultPath, readVault } from "./vault";

const commandNames: readonly CommandName[] = ["open", "tree", "find", "click", "fill", "wait", "readTable", "snapshot", "script", "close"];

function positive(value: string | undefined, fallback: number) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

const socketPath = process.env.CLIENTOPS_RUNNER_SOCKET || join(tmpdir(), "clientops-runner.sock");
const cdpUrl = process.env.LIGHTPANDA_CDP_WS || "ws://127.0.0.1:9222/";
const vaultPath = process.env.CLIENTOPS_VAULT || defaultVaultPath;
const lightpanda = process.env.CLIENTOPS_LIGHTPANDA || "lightpanda";
const idleMs = positive(process.env.CLIENTOPS_RUNNER_IDLE_MS, 600_000);
const timeoutMs = positive(process.env.CLIENTOPS_RUNNER_TIMEOUT_MS, 120_000);
const startMs = 5_000;

function nameOf(input: unknown): CommandName | "unknown" {
  const name = typeof input === "object" && input !== null ? (input as { command?: unknown }).command : undefined;
  return commandNames.find((known) => known === name) ?? "unknown";
}

function startDaemonProcess() {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith("LP_"))) as NodeJS.ProcessEnv;
  spawn(process.execPath, [import.meta.path, "--serve"], { detached: true, stdio: "ignore", env }).unref();
}

async function runOnce(raw: string): Promise<RunnerResult> {
  let input: unknown;
  try {
    input = JSON.parse(raw);
  } catch {
    return failure("unknown", "bad-command", "The command is not valid JSON");
  }
  const parsed = parseCommand(input);
  if (!parsed.ok) return failure(nameOf(input), "bad-command", parsed.message);
  if (parsed.command.command === "script") return runScript(parsed.command, readVault(vaultPath), { lightpanda });
  const prepared = prepareCommand(parsed.command, readVault(vaultPath), process.env);
  if (!prepared.ok) return prepared.result;
  const running = await daemonAnswers(socketPath);
  if (parsed.command.command === "close" && !running) return { ok: true, command: "close" };
  if (!running) {
    startDaemonProcess();
    if (!(await waitForDaemon(socketPath, startMs))) {
      return failure(parsed.command.command, "runner-error", `The runner did not start on ${socketPath}`);
    }
  }
  return sendToDaemon(socketPath, { command: prepared.command, secrets: prepared.secrets }, timeoutMs);
}

if (process.argv[2] === "--serve") {
  const daemon = await startDaemon({ socketPath, openTab: () => BrowserTab.open(cdpUrl), idleMs, onStop: () => process.exit(0) });
  process.on("SIGTERM", () => void daemon.stop());
  process.on("SIGINT", () => void daemon.stop());
} else {
  const raw = process.argv[2] ?? (await Bun.stdin.text());
  const result = await runOnce(raw).catch((error: unknown) =>
    failure("unknown", "runner-error", error instanceof Error ? error.message : String(error)),
  );
  const vault = readVault(vaultPath);
  const secrets = [...(vault.ok ? Object.values(vault.values) : []), ...secretsIn(process.env)];
  await Bun.write(Bun.stdout, `${JSON.stringify(scrub(result, secrets))}\n`);
  process.exit(result.ok ? 0 : 1);
}
