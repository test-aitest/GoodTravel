const ANTHROPIC_API = "https://api.anthropic.com";
const ENDPOINT_VARIABLES = ["ANTHROPIC_API_BASE", "ANTHROPIC_BASE_URL"] as const;
const UNREACHABLE_CONTROL_PLANE = "http://127.0.0.1:9";

const probes: Record<string, string[]> = {
  claude: ["-m", "clientops.check", "--only", "claude"],
  chat: ["-m", "live.chat_reply"],
  computers: ["-m", "live.computers"],
  pytest: ["-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", "-W", "error::DeprecationWarning"],
};

export function endpointProblem(env: Record<string, string | undefined>): string | null {
  for (const name of ENDPOINT_VARIABLES) {
    const address = env[name]?.trim().replace(/\/+$/, "");
    if (address && address !== ANTHROPIC_API) return `${name} points somewhere other than ${ANTHROPIC_API}. Unset it first.`;
  }
  return null;
}

if (import.meta.main) {
  const chosen = probes[process.argv[2] ?? ""];
  if (!chosen) {
    console.error(`Say which probe to run: ${Object.keys(probes).join(" or ")}.`);
    process.exit(2);
  }
  const problem = endpointProblem(process.env);
  if (problem) {
    console.error(problem);
    process.exit(1);
  }
  const child = Bun.spawn(["uv", "run", "--directory", "agent", "python", ...chosen, ...process.argv.slice(3)], {
    env: { ...process.env, LITELLM_LOCAL_MODEL_COST_MAP: "True", AGENTFIELD_SERVER: UNREACHABLE_CONTROL_PLANE },
    stdin: "ignore",
    stdout: "inherit",
    stderr: "inherit",
  });
  process.exit(await child.exited);
}
