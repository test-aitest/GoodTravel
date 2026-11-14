import { z } from "zod";
import type { KnownClient } from "./jobs";
import { loginFormSchema, loginValues, sitesAdded, type ClientSetup, type SetupWorker, type WorkerAnswer } from "./setup";

export type RequestLike = {
  headers: { get(name: string): string | null };
  text(): Promise<string>;
};

export type SetupDeps = {
  worker: SetupWorker;
  findClient: (clientId: string) => KnownClient | null;
  settings: {
    read(clientId: string): Promise<boolean>;
    write(clientId: string, useClaude: boolean): Promise<boolean>;
  };
  memory: {
    read(clientId: string): Promise<readonly string[] | null>;
    write(clientId: string, present: readonly string[]): Promise<void>;
  };
  warn?: (message: string) => void;
};

const LARGEST_BODY = 8 * 1024;
const switchSchema = z.strictObject({ useClaude: z.boolean() });

const copy = {
  unknownClient: "There is no such client.",
  notThisPage: "Only the ClientOps page can do this.",
  notJson: "Send this as JSON.",
  tooLarge: "That is too much to be a set of logins.",
  fillBoth: "Fill in the username and password.",
  sayOnOrOff: "Say whether to use Claude: true or false.",
  workerDown: "Couldn't reach the worker right now. Try again in a minute.",
  computerFailed: "Couldn't create the computer right now. Try again in a minute.",
  loginsFailed: "Couldn't add the logins. Try again in a minute.",
  settingsUnreadable: "The settings could not be read.",
  settingsUnsaved: "The setting could not be saved.",
};

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

function problem(status: number, error: string) {
  return reply({ error }, status);
}

export function isSameOrigin(request: RequestLike) {
  const site = request.headers.get("sec-fetch-site");
  if (site !== null) return site === "same-origin";
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

async function readJson(request: RequestLike): Promise<{ ok: true; value: unknown } | { ok: false; response: Response }> {
  const type = request.headers.get("content-type") ?? "";
  if (!type.toLowerCase().startsWith("application/json")) return { ok: false, response: problem(415, copy.notJson) };
  const text = await request.text();
  if (text.length > LARGEST_BODY) return { ok: false, response: problem(413, copy.tooLarge) };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false, response: problem(400, copy.notJson) };
  }
}

function snapshot(clientId: string, computer: WorkerAnswer["computer"], present: readonly string[], useClaude: boolean): ClientSetup {
  return { clientId, computer, logins: sitesAdded(present), useClaude };
}

function warn(deps: SetupDeps, clientId: string, error: unknown) {
  deps.warn?.(`Setup for ${clientId}: ${error instanceof Error ? error.message : "unknown error"}`);
}

async function confirmedNames(deps: SetupDeps, clientId: string, answer: WorkerAnswer) {
  try {
    if (answer.present === null) return (await deps.memory.read(clientId)) ?? [];
    await deps.memory.write(clientId, answer.present);
  } catch (error) {
    warn(deps, clientId, error);
  }
  return answer.present ?? [];
}

async function withSwitch(deps: SetupDeps, clientId: string, answer: WorkerAnswer) {
  const present = await confirmedNames(deps, clientId, answer);
  try {
    return reply(snapshot(clientId, answer.computer, present, await deps.settings.read(clientId)));
  } catch (error) {
    warn(deps, clientId, error);
    return problem(500, copy.settingsUnreadable);
  }
}

async function guarded(request: RequestLike, clientId: string, deps: SetupDeps, work: () => Promise<Response>) {
  if (!isSameOrigin(request)) return problem(403, copy.notThisPage);
  if (!deps.findClient(clientId)) return problem(404, copy.unknownClient);
  return work();
}

export async function handleReadSetup(_request: RequestLike, clientId: string, deps: SetupDeps) {
  if (!deps.findClient(clientId)) return problem(404, copy.unknownClient);
  let answer: WorkerAnswer;
  try {
    answer = await deps.worker.status(clientId);
  } catch (error) {
    warn(deps, clientId, error);
    return problem(502, copy.workerDown);
  }
  return withSwitch(deps, clientId, answer);
}

export function handleCheckLogins(request: RequestLike, clientId: string, deps: SetupDeps) {
  return guarded(request, clientId, deps, async () => {
    let answer: WorkerAnswer;
    try {
      answer = await deps.worker.status(clientId, { wake: true });
    } catch (error) {
      warn(deps, clientId, error);
      return problem(502, copy.workerDown);
    }
    return withSwitch(deps, clientId, answer);
  });
}

export function handlePrepareComputer(request: RequestLike, clientId: string, deps: SetupDeps) {
  return guarded(request, clientId, deps, async () => {
    let answer: WorkerAnswer;
    try {
      answer = await deps.worker.prepare(clientId);
    } catch (error) {
      warn(deps, clientId, error);
      return problem(502, copy.computerFailed);
    }
    return withSwitch(deps, clientId, answer);
  });
}

export function handleAddLogins(request: RequestLike, clientId: string, deps: SetupDeps) {
  return guarded(request, clientId, deps, async () => {
    const body = await readJson(request);
    if (!body.ok) return body.response;
    const form = loginFormSchema.safeParse(body.value);
    if (!form.success) return problem(400, copy.fillBoth);
    let present: readonly string[];
    try {
      present = (await deps.worker.putLogins(clientId, loginValues(form.data))).present;
    } catch (error) {
      warn(deps, clientId, error);
      return problem(502, copy.loginsFailed);
    }
    return withSwitch(deps, clientId, { computer: "ready", present });
  });
}

export function handleSetUseClaude(request: RequestLike, clientId: string, deps: SetupDeps) {
  return guarded(request, clientId, deps, async () => {
    const body = await readJson(request);
    if (!body.ok) return body.response.status === 415 ? body.response : problem(400, copy.sayOnOrOff);
    const parsed = switchSchema.safeParse(body.value);
    if (!parsed.success) return problem(400, copy.sayOnOrOff);
    try {
      return reply({ clientId, useClaude: await deps.settings.write(clientId, parsed.data.useClaude) });
    } catch (error) {
      warn(deps, clientId, error);
      return problem(500, copy.settingsUnsaved);
    }
  });
}
