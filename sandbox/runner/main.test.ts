import { afterAll, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const main = join(import.meta.dir, "main.ts");
const vaults = mkdtempSync(join(tmpdir(), "cr-cli-vault-"));

function vaultWith(values: Record<string, string>) {
  const path = join(vaults, `${Math.random().toString(36).slice(2, 8)}.json`);
  writeFileSync(path, JSON.stringify({ version: 1, values }));
  chmodSync(path, 0o600);
  return path;
}

afterAll(() => {
  rmSync(vaults, { recursive: true, force: true });
});

function socketPath() {
  return join(tmpdir(), `cr-cli-${process.pid}-${Math.random().toString(36).slice(2, 8)}.sock`);
}

async function runner(argument: string | null, env: Record<string, string>, stdin?: string) {
  const child = Bun.spawn(["bun", main, ...(argument === null ? [] : [argument])], {
    env: { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "", ...env },
    stdin: stdin === undefined ? "ignore" : new Blob([stdin]),
    stdout: "pipe",
    stderr: "pipe",
  });
  const [output, exitCode] = await Promise.all([new Response(child.stdout).text(), child.exited]);
  return { exitCode, output, result: JSON.parse(output) as Record<string, unknown> };
}

describe("runner command line", () => {
  test("prints one JSON result for input that is not JSON, and fails", async () => {
    const { exitCode, result } = await runner("{open", { CLIENTOPS_RUNNER_SOCKET: socketPath() });
    expect(exitCode).toBe(1);
    expect(result).toEqual({ ok: false, command: "unknown", error: { code: "bad-command", message: "The command is not valid JSON" } });
  });

  test("names the command it could not run", async () => {
    const { exitCode, result } = await runner(JSON.stringify({ command: "open", url: "file:///etc/passwd" }), { CLIENTOPS_RUNNER_SOCKET: socketPath() });
    expect(exitCode).toBe(1);
    expect(result).toMatchObject({ ok: false, command: "open", error: { code: "bad-command" } });
  });

  test("refuses to fill a placeholder that is not in the vault, without starting anything", async () => {
    const path = socketPath();
    const command = { command: "fill", target: { role: "textbox", name: "Password", form: "/login" }, text: "$LP_STORE_PASSWORD" };
    const { exitCode, result } = await runner(JSON.stringify(command), {
      CLIENTOPS_RUNNER_SOCKET: path,
      CLIENTOPS_VAULT: vaultWith({ LP_STORE_USERNAME: "demo-user" }),
      LP_STORE_PASSWORD: "from-env",
    });
    expect(exitCode).toBe(1);
    expect(result).toEqual({
      ok: false,
      command: "fill",
      error: { code: "placeholder-unset", message: "LP_STORE_PASSWORD is not in the vault on this computer", missing: ["LP_STORE_PASSWORD"] },
    });
    expect(existsSync(path)).toBe(false);
  });

  test("never prints a value from the vault", async () => {
    const { output } = await runner(JSON.stringify({ command: "demo-pass" }), {
      CLIENTOPS_RUNNER_SOCKET: socketPath(),
      CLIENTOPS_VAULT: vaultWith({ LP_STORE_PASSWORD: "demo-pass" }),
    });
    expect(output).not.toContain("demo-pass");
    expect(output).toContain("*****");
  });

  test("closing when nothing is running is fine and starts nothing", async () => {
    const path = socketPath();
    const { exitCode, result } = await runner(JSON.stringify({ command: "close" }), { CLIENTOPS_RUNNER_SOCKET: path });
    expect(exitCode).toBe(0);
    expect(result).toEqual({ ok: true, command: "close" });
    expect(existsSync(path)).toBe(false);
  });

  test("reads the command from standard input, starts its daemon, reports a missing browser, and closes", async () => {
    const path = socketPath();
    const env = { CLIENTOPS_RUNNER_SOCKET: path, LIGHTPANDA_CDP_WS: "ws://127.0.0.1:9/" };
    const opened = await runner(null, env, JSON.stringify({ command: "open", url: "http://127.0.0.1:3002/login" }));
    expect(opened.exitCode).toBe(1);
    expect(opened.result).toMatchObject({ ok: false, command: "open", error: { code: "browser-unreachable" } });
    expect(existsSync(path)).toBe(true);
    const closed = await runner(JSON.stringify({ command: "close" }), env);
    expect(closed.result).toEqual({ ok: true, command: "close" });
    for (let tries = 0; tries < 100 && existsSync(path); tries += 1) await Bun.sleep(20);
    expect(existsSync(path)).toBe(false);
  }, 15_000);
});

function fakeLightpanda() {
  const path = join(vaults, `lightpanda-${Math.random().toString(36).slice(2, 8)}`);
  writeFileSync(
    path,
    [
      "#!/bin/sh",
      'source="$(cat)"',
      'echo "{\\"clientops\\":\\"step\\",\\"stepId\\":\\"sign-in\\",\\"phase\\":\\"start\\"}"',
      'echo "{\\"clientops\\":\\"step\\",\\"stepId\\":\\"sign-in\\",\\"phase\\":\\"done\\",\\"tookMs\\":3,\\"checks\\":4}"',
      'echo "{\\"clientops\\":\\"result\\",\\"typed\\":\\"$LP_STORE_USERNAME\\",\\"args\\":\\"$1 $2\\",\\"telemetry\\":\\"$LIGHTPANDA_DISABLE_TELEMETRY\\",\\"length\\":${#source}}"',
      "",
    ].join("\n"),
  );
  chmodSync(path, 0o700);
  return path;
}

describe("runner command line for saved scripts", () => {
  test("runs a script with lightpanda run, without the page daemon, and never prints a vault value", async () => {
    const path = socketPath();
    const source = 'page.fill("input", "$LP_STORE_USERNAME");';
    const { exitCode, output, result } = await runner(JSON.stringify({ command: "script", source }), {
      CLIENTOPS_RUNNER_SOCKET: path,
      CLIENTOPS_VAULT: vaultWith({ LP_STORE_USERNAME: "demo-user" }),
      CLIENTOPS_LIGHTPANDA: fakeLightpanda(),
    });
    expect(exitCode).toBe(0);
    expect(output).not.toContain("demo-user");
    expect(result).toMatchObject({
      ok: true,
      command: "script",
      outcome: "passed",
      steps: [{ stepId: "sign-in", status: "done", tookMs: 3, checks: 4, pages: [] }],
      result: { typed: "*****", args: "run -", telemetry: "true", length: source.length },
    });
    expect(existsSync(path)).toBe(false);
  });

  test("refuses a script whose login is not in the vault, before starting anything", async () => {
    const path = socketPath();
    const { exitCode, result } = await runner(JSON.stringify({ command: "script", source: '"$LP_STORE_PASSWORD"' }), {
      CLIENTOPS_RUNNER_SOCKET: path,
      CLIENTOPS_VAULT: vaultWith({ LP_STORE_USERNAME: "demo-user" }),
      CLIENTOPS_LIGHTPANDA: fakeLightpanda(),
      LP_STORE_PASSWORD: "from-env",
    });
    expect(exitCode).toBe(1);
    expect(result).toEqual({
      ok: false,
      command: "script",
      error: { code: "placeholder-unset", message: "LP_STORE_PASSWORD is not in the vault on this computer", missing: ["LP_STORE_PASSWORD"] },
    });
    expect(existsSync(path)).toBe(false);
  });
});
