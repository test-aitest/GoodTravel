import { describe, expect, test } from "bun:test";
import { parseCommand } from "./commands";
import { readScriptRun, runScript, scriptEnvironment, type Spawner } from "./script";

const vault = { ok: true, values: { LP_STORE_USERNAME: "demo-user", LP_STORE_PASSWORD: "demo-pass", LP_SUPPLIER_USERNAME: "supply-user" } } as const;
const base = { PATH: "/usr/bin", HOME: "/home/daytona", TMPDIR: "/tmp", LP_STORE_PASSWORD: "from-the-agent", AWS_SECRET: "nope" };
const red = (text: string) => `\x1b[1m\x1b[31m${text}\x1b[0m\n`;
const marker = (body: Record<string, unknown>) => `${JSON.stringify({ clientops: body.clientops ?? "step", ...body })}\n`;
const started = (stepId: string) => marker({ clientops: "step", stepId, phase: "start" });
const done = (stepId: string, tookMs: number, checks: number) => marker({ clientops: "step", stepId, phase: "done", tookMs, checks });
const signInSource = 'const plan = {"login":"$LP_STORE_USERNAME","password":"$LP_STORE_PASSWORD"};';

const missingLightpanda: Spawner = async () => {
  throw new Error("ENOENT: no such file or directory, posix_spawn '/missing/lightpanda'");
};

function recordingSpawner(answer: { exitCode: number | null; stdout?: string; stderr?: string; timedOut?: boolean }) {
  const calls: { argv: string[]; env: Record<string, string>; stdin: string; timeoutMs: number }[] = [];
  const spawn: Spawner = async (argv, options) => {
    calls.push({ argv, ...options });
    return { exitCode: answer.exitCode, stdout: answer.stdout ?? "", stderr: answer.stderr ?? "", timedOut: answer.timedOut ?? false };
  };
  return { calls, spawn };
}

describe("parseCommand for scripts", () => {
  test("takes the script source and an optional time limit", () => {
    expect(parseCommand({ command: "script", source: "return 1;" })).toEqual({ ok: true, command: { command: "script", source: "return 1;" } });
    expect(parseCommand({ command: "script", source: "return 1;", timeoutMs: 30_000 }).ok).toBe(true);
  });

  test("refuses a script without source or with other fields", () => {
    expect(parseCommand({ command: "script" })).toEqual({ ok: false, message: "script needs source" });
    expect(parseCommand({ command: "script", source: " " })).toEqual({ ok: false, message: "source must be the text of a script" });
    expect(parseCommand({ command: "script", source: "return 1;", env: { LP_STORE_PASSWORD: "x" } })).toEqual({ ok: false, message: "script does not take env" });
  });
});

describe("scriptEnvironment", () => {
  test("gives the child process only the vault values the script names, plus the basics, with telemetry off", () => {
    const prepared = scriptEnvironment(signInSource, vault, base);
    expect(prepared).toEqual({
      ok: true,
      env: {
        PATH: "/usr/bin",
        HOME: "/home/daytona",
        TMPDIR: "/tmp",
        LIGHTPANDA_DISABLE_TELEMETRY: "true",
        LP_STORE_USERNAME: "demo-user",
        LP_STORE_PASSWORD: "demo-pass",
      },
      secrets: ["demo-user", "demo-pass", "supply-user"],
    });
  });

  test("never takes a login from the agent's own environment", () => {
    const prepared = scriptEnvironment('page.fill("input", "$LP_SUPPLIER_PASSWORD");', vault, { ...base, LP_SUPPLIER_PASSWORD: "from-the-agent" });
    expect(prepared.ok).toBe(false);
    expect(JSON.stringify(prepared)).not.toContain("from-the-agent");
  });

  test("refuses before running when a placeholder is not in the vault, naming every missing one", () => {
    const prepared = scriptEnvironment('"$LP_SUPPLIER_PASSWORD" + "$LP_STORE_USERNAME" + "$LP_NOT_SAVED"', vault, base);
    expect(prepared).toEqual({
      ok: false,
      result: {
        ok: false,
        command: "script",
        error: {
          code: "placeholder-unset",
          message: "LP_SUPPLIER_PASSWORD and LP_NOT_SAVED are not in the vault on this computer",
          missing: ["LP_SUPPLIER_PASSWORD", "LP_NOT_SAVED"],
        },
      },
    });
  });

  test("refuses when the vault cannot be read, and runs a script without placeholders anyway", () => {
    const unsafe = { ok: false, problem: "unsafe", message: "The vault at /v can be read by other users; it must be mode 600" } as const;
    expect(scriptEnvironment(signInSource, unsafe, base)).toEqual({
      ok: false,
      result: { ok: false, command: "script", error: { code: "vault-unreadable", message: unsafe.message } },
    });
    const missing = { ok: false, problem: "missing", message: "No vault at /v" } as const;
    expect(scriptEnvironment("return 1;", missing, base)).toMatchObject({ ok: true, secrets: [] });
  });
});

describe("readScriptRun", () => {
  const page = { clientops: "page", stepId: "compare", url: "http://127.0.0.1:3002/inventory", title: "Inventory", html: '<html><body><script>x()</script><input name="available-KC-ETH-200" value="18"></body></html>', values: { "available-KC-ETH-200": "6" } };

  test("reads every step's time and checks, the pages seen and the result of a passing script", () => {
    const stdout = [
      started("sign-in"),
      "Filled element (selector: input) with \"$LP_STORE_USERNAME\".\n",
      done("sign-in", 41, 5),
      started("compare"),
      marker(page),
      marker({ clientops: "table", stepId: "compare", into: "store", columns: ["SKU"], rows: [["KC-ETH-200"]], pages: 1 }),
      done("compare", 12, 2),
      marker({ clientops: "result", said: {} }),
    ].join("");

    const result = readScriptRun({ exitCode: 0, stdout, stderr: "", tookMs: 230 });

    expect(result).toEqual({
      ok: true,
      command: "script",
      outcome: "passed",
      tookMs: 230,
      steps: [
        { stepId: "sign-in", status: "done", tookMs: 41, checks: 5, pages: [] },
        {
          stepId: "compare",
          status: "done",
          tookMs: 12,
          checks: 2,
          pages: [
            {
              url: page.url,
              host: "127.0.0.1:3002",
              title: "Inventory",
              html: '<html><body><input name="available-KC-ETH-200" value="6"></body></html>',
              bytes: 71,
              truncated: false,
            },
          ],
        },
      ],
      tables: { store: { columns: ["SKU"], rows: [["KC-ETH-200"]], pages: 1 } },
      result: { said: {} },
    });
  });

  test("names the step whose check failed and the kind of check, keeping the tables read before it", () => {
    const table = { columns: ["SKU", "Available"], rows: [["KC-ETH-200", "2"]], pages: 3 };
    const stdout = [started("read"), marker({ clientops: "table", stepId: "read", into: "supplier", ...table }), done("read", 30, 4), started("check-save")].join("");
    const stderr = red('Error: check failed: wrong-name: form[action="/inventory"] button says "Update inventory", expected "Save changes"');

    const result = readScriptRun({ exitCode: 1, stdout, stderr, tookMs: 120 });

    expect(result).toMatchObject({
      ok: true,
      command: "script",
      outcome: "check-failed",
      steps: [
        { stepId: "read", status: "done" },
        { stepId: "check-save", status: "failed", tookMs: 0, checks: 0, pages: [] },
      ],
      tables: { supplier: table },
      failure: {
        stepId: "check-save",
        kind: "check",
        code: "wrong-name",
        message: 'form[action="/inventory"] button says "Update inventory", expected "Save changes"',
      },
    });
    expect(result).not.toHaveProperty("result");
  });

  test("tells a step that broke apart from a check, by the missing check failed prefix", () => {
    const stderr = red("Error: click failed: NodeNotFound: the selector or backendNodeId matched nothing on the current page.");

    const result = readScriptRun({ exitCode: 1, stdout: started("read"), stderr, tookMs: 80 });

    expect(result).toMatchObject({
      outcome: "step-failed",
      failure: { stepId: "read", kind: "step", code: null, message: "click failed: NodeNotFound: the selector or backendNodeId matched nothing on the current page." },
    });
  });

  test("a script that stops before any step, or ends without a result, is a failed step", () => {
    const syntax = readScriptRun({ exitCode: 1, stdout: "", stderr: red("Error: SyntaxError: Unexpected token ';'"), tookMs: 20 });
    expect(syntax).toMatchObject({ outcome: "step-failed", steps: [], failure: { stepId: null, kind: "step", message: "SyntaxError: Unexpected token ';'" } });

    const silent = readScriptRun({ exitCode: 0, stdout: [started("sign-in"), done("sign-in", 5, 1)].join(""), stderr: "", tookMs: 20 });
    expect(silent).toMatchObject({ outcome: "step-failed", failure: { stepId: null, kind: "step", message: "The script ended without a result" } });
  });
});

describe("runScript", () => {
  const command = { command: "script", source: signInSource } as const;

  test("runs lightpanda run with the script on standard input and the vault values in its environment", async () => {
    const { calls, spawn } = recordingSpawner({ exitCode: 0, stdout: marker({ clientops: "result", ok: 1 }) });

    const result = await runScript(command, vault, { lightpanda: "/opt/lightpanda", spawn, base, now: (() => { let tick = 0; return () => (tick += 150); })() });

    expect(calls).toEqual([
      {
        argv: ["/opt/lightpanda", "run", "-"],
        stdin: signInSource,
        timeoutMs: 60_000,
        env: { PATH: "/usr/bin", HOME: "/home/daytona", TMPDIR: "/tmp", LIGHTPANDA_DISABLE_TELEMETRY: "true", LP_STORE_USERNAME: "demo-user", LP_STORE_PASSWORD: "demo-pass" },
      },
    ]);
    expect(result).toEqual({ ok: true, command: "script", outcome: "passed", tookMs: 150, steps: [], tables: {}, result: { ok: 1 } });
  });

  test("does not start lightpanda when a placeholder is missing", async () => {
    const { calls, spawn } = recordingSpawner({ exitCode: 0 });

    const result = await runScript({ command: "script", source: '"$LP_SUPPLIER_PASSWORD"' }, vault, { lightpanda: "lightpanda", spawn, base });

    expect(calls).toEqual([]);
    expect(result).toMatchObject({ ok: false, command: "script", error: { code: "placeholder-unset", missing: ["LP_SUPPLIER_PASSWORD"] } });
  });

  test("stops a script that runs past its time limit", async () => {
    const { calls, spawn } = recordingSpawner({ exitCode: null, timedOut: true });

    const result = await runScript({ ...command, timeoutMs: 5_000 }, vault, { lightpanda: "lightpanda", spawn, base });

    expect(calls[0].timeoutMs).toBe(5_000);
    expect(result).toEqual({ ok: false, command: "script", error: { code: "script-timeout", message: "The script did not finish within 5 seconds" } });
  });

  test("says so when lightpanda cannot be started", async () => {
    const result = await runScript(command, vault, { lightpanda: "/missing/lightpanda", spawn: missingLightpanda, base });

    expect(result).toEqual({
      ok: false,
      command: "script",
      error: { code: "runner-error", message: "Could not start Lightpanda: ENOENT: no such file or directory, posix_spawn '/missing/lightpanda'" },
    });
  });
});
