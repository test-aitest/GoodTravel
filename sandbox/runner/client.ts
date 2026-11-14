import type { Command, RunnerResult } from "./commands";

export type DaemonRequest = { command: Command; secrets: readonly string[] };

const base = "http://clientops-runner";

export async function daemonAnswers(socketPath: string) {
  try {
    const response = await fetch(`${base}/health`, { unix: socketPath, signal: AbortSignal.timeout(1_000) });
    return response.ok;
  } catch {
    return false;
  }
}

export async function sendToDaemon(socketPath: string, request: DaemonRequest, timeoutMs = 120_000): Promise<RunnerResult> {
  const response = await fetch(`${base}/command`, {
    unix: socketPath,
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
    signal: AbortSignal.timeout(timeoutMs),
  });
  return (await response.json()) as RunnerResult;
}

export async function waitForDaemon(socketPath: string, timeoutMs: number) {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    if (await daemonAnswers(socketPath)) return true;
    await Bun.sleep(25);
  }
  return false;
}
