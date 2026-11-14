import { spawn } from "node:child_process";
import { z } from "zod";
import { allLoginNames } from "./setup";

export type LoginBridge = {
  command: readonly string[];
  cwd: string;
  env: Record<string, string | undefined>;
  timeoutMs: number;
};

export class LoginBridgeError extends Error {
  override name = "LoginBridgeError";
}

const CLIENT_ID = /^[a-z0-9][a-z0-9-]{0,62}$/;
const LARGEST_ANSWER = 64 * 1024;
const LONGEST_ERROR = 500;
const NOT_ADDED = "The logins could not be added.";

const loginName = z.enum(allLoginNames);
const answerSchema = z.discriminatedUnion("ok", [
  z.object({ ok: z.literal(true), present: z.array(loginName), missing: z.array(loginName) }),
  z.object({ ok: z.literal(false), error: z.string().min(1).max(LONGEST_ERROR) }),
]);

export function loginBridge(env: Record<string, string | undefined> = process.env, projectRoot = process.cwd()): LoginBridge {
  return {
    command: ["uv", "run", "--directory", "agent", "python", "-m", "clientops.setup_logins"],
    cwd: projectRoot,
    env: { ...env, LITELLM_LOCAL_MODEL_COST_MAP: "True" },
    timeoutMs: 150_000,
  };
}

function lastLine(output: string) {
  return output.trim().split("\n").at(-1) ?? "";
}

function readAnswer(output: string) {
  try {
    return answerSchema.safeParse(JSON.parse(lastLine(output)));
  } catch {
    return null;
  }
}

function run(bridge: LoginBridge, clientId: string, input: string) {
  return new Promise<{ code: number | null; output: string }>((resolve, reject) => {
    const [program, ...rest] = bridge.command;
    if (!program) {
      reject(new LoginBridgeError("The worker program is not configured."));
      return;
    }
    const child = spawn(program, [...rest, clientId], { cwd: bridge.cwd, env: bridge.env as NodeJS.ProcessEnv, stdio: ["pipe", "pipe", "ignore"] });
    let output = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), bridge.timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      output = (output + chunk).slice(-LARGEST_ANSWER);
    });
    child.stdin.on("error", () => {});
    child.on("error", () => {
      clearTimeout(timer);
      reject(new LoginBridgeError("The worker program could not be started."));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code, output });
    });
    child.stdin.end(input);
  });
}

export async function putLoginsThroughBridge(bridge: LoginBridge, clientId: string, values: Readonly<Record<string, string>>) {
  if (!CLIENT_ID.test(clientId)) throw new LoginBridgeError("That is not a client id.");
  const { code, output } = await run(bridge, clientId, JSON.stringify(values));
  const parsed = readAnswer(output);
  if (!parsed?.success) throw new LoginBridgeError(NOT_ADDED);
  const answer = parsed.data;
  if (!answer.ok) {
    const carriesValue = Object.values(values).some((value) => value !== "" && answer.error.includes(value));
    throw new LoginBridgeError(carriesValue ? NOT_ADDED : answer.error);
  }
  if (code !== 0) throw new LoginBridgeError(NOT_ADDED);
  return { present: answer.present, missing: answer.missing };
}
