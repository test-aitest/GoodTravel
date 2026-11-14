import { existsSync, unlinkSync } from "node:fs";
import type { Tab } from "./cdp";
import { daemonAnswers, type DaemonRequest } from "./client";
import { failure, parseCommand, Runner } from "./commands";

export type DaemonOptions = { socketPath: string; openTab: () => Promise<Tab>; idleMs: number; onStop?: () => void };

function removeSocket(path: string) {
  try {
    if (existsSync(path)) unlinkSync(path);
  } catch {
    return;
  }
}

export async function startDaemon(options: DaemonOptions) {
  if (existsSync(options.socketPath)) {
    if (await daemonAnswers(options.socketPath)) throw new Error(`A runner is already running on ${options.socketPath}`);
    removeSocket(options.socketPath);
  }

  const runner = new Runner(options.openTab);
  let queue: Promise<unknown> = Promise.resolve();
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  let stopping: Promise<void> | null = null;

  const stop = () => {
    stopping ??= (async () => {
      clearTimeout(idleTimer);
      await queue;
      await runner.run({ command: "close" });
      await server.stop();
      removeSocket(options.socketPath);
      options.onStop?.();
    })();
    return stopping;
  };

  const touch = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => void stop(), options.idleMs);
  };

  const server = Bun.serve({
    unix: options.socketPath,
    async fetch(request) {
      const path = new URL(request.url).pathname;
      if (path === "/health") return new Response("ok");
      if (path !== "/command" || request.method !== "POST") return new Response("Not found", { status: 404 });
      touch();
      const body = (await request.json().catch(() => null)) as Partial<DaemonRequest> | null;
      const parsed = parseCommand(body?.command);
      if (!parsed.ok) return Response.json(failure("unknown", "bad-command", parsed.message));
      const secrets = Array.isArray(body?.secrets) ? body.secrets.filter((secret): secret is string => typeof secret === "string") : [];
      const answer = queue.then(() => runner.run(parsed.command, secrets));
      queue = answer.catch(() => undefined);
      const result = await answer;
      if (parsed.command.command === "close") setTimeout(() => void stop(), 0);
      return Response.json(result);
    },
  });
  touch();
  return { stop };
}
