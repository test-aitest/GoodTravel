import { randomUUID } from "node:crypto";
import { agentFieldConfig, runSkill, type AgentFieldConfig } from "./agentfieldClient";
import { findClient } from "./clients";
import { fakeSetupWorker } from "./fakeSetupWorker";
import { loginBridge, putLoginsThroughBridge, type LoginBridge } from "./loginBridge";
import { rememberedLogins, rememberLogins } from "./loginMemory";
import { readUseClaude, writeUseClaude } from "./settings";
import { readAgentAnswer, type SetupWorker } from "./setup";
import type { RequestLike, SetupDeps } from "./setupRoutes";

type Env = Record<string, string | undefined>;

export const FAKE_WORLD_COOKIE = "clientops-fake-world";
const WORLD_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STATUS_WAIT_MS = 90_000;
const PREPARE_WAIT_MS = 150_000;

export function externalsMode(env: Env = process.env) {
  const value = env.CLIENTOPS_EXTERNALS ?? "";
  if (value === "" || value === "real") return "real" as const;
  if (value === "fake") return "fake" as const;
  throw new Error(`CLIENTOPS_EXTERNALS must be 'fake' or 'real' (unset means real), not '${value}'`);
}

export function agentSetupWorker(config: AgentFieldConfig, bridge: LoginBridge): SetupWorker {
  return {
    status: async (clientId, options) =>
      readAgentAnswer(clientId, await runSkill(config, "setup_status", { client_id: clientId, wake: options?.wake === true }, { timeoutMs: STATUS_WAIT_MS })),
    prepare: async (clientId) => readAgentAnswer(clientId, await runSkill(config, "prepare_computer", { client_id: clientId }, { timeoutMs: PREPARE_WAIT_MS })),
    putLogins: (clientId, values) => putLoginsThroughBridge(bridge, clientId, values),
  };
}

function worldIdIn(cookieHeader: string | null) {
  const value = (cookieHeader ?? "")
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${FAKE_WORLD_COOKIE}=`))
    ?.slice(FAKE_WORLD_COOKIE.length + 1);
  return value && WORLD_ID.test(value) ? value : null;
}

export function setupContext(request: RequestLike, env: Env = process.env, projectRoot = process.cwd()) {
  let setCookie: string | null = null;
  let worker: SetupWorker;
  if (externalsMode(env) === "fake") {
    const known = worldIdIn(request.headers.get("cookie"));
    const worldId = known ?? randomUUID();
    if (!known) setCookie = `${FAKE_WORLD_COOKIE}=${worldId}; Path=/; HttpOnly; SameSite=Strict`;
    worker = fakeSetupWorker(worldId);
  } else {
    worker = agentSetupWorker(agentFieldConfig(env), loginBridge(env, projectRoot));
  }
  const deps: SetupDeps = {
    worker,
    findClient,
    settings: {
      read: (clientId) => readUseClaude(clientId, env, projectRoot),
      write: (clientId, useClaude) => writeUseClaude(clientId, useClaude, env, projectRoot),
    },
    memory: {
      read: (clientId) => rememberedLogins(clientId, env, projectRoot),
      write: (clientId, present) => rememberLogins(clientId, present, env, projectRoot),
    },
    warn: (message) => console.warn(message),
  };
  return {
    deps,
    respond(response: Response) {
      if (setCookie) response.headers.append("set-cookie", setCookie);
      return response;
    },
  };
}
