import { afterEach, beforeAll, describe, expect, spyOn, test } from "bun:test";
import { mkdtempSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import contract from "../../agent/tests/contract/setup.json";
import { LoginBridgeError, loginBridge, putLoginsThroughBridge, type LoginBridge } from "./loginBridge";

const projectRoot = resolve(import.meta.dir, "..", "..");
const values: Record<string, string> = contract.logins.stdin;
const secretValues = Object.values(values);
let scratch = "";
let standIn = "";

const standInSource = `
const stdin = await Bun.stdin.text();
await Bun.write(process.env.RECORD_FILE, JSON.stringify({ argv: process.argv.slice(2), env: process.env, stdin }));
const names = Object.keys(JSON.parse(stdin || "{}"));
const mode = process.env.MODE ?? "ok";
if (mode === "ok") console.log(JSON.stringify({ ok: true, present: names, missing: [] }));
if (mode === "extra") console.log(JSON.stringify({ ok: true, present: names, missing: [], values: JSON.parse(stdin) }));
if (mode === "stranger") console.log(JSON.stringify({ ok: true, present: ["LP_SUPPLIER_USERNAME", "AWS_SECRET"], missing: [] }));
if (mode === "echo") { console.log(JSON.stringify({ ok: false, error: "Refused " + stdin })); process.exit(1); }
if (mode === "stderr") { console.error("traceback with " + stdin); console.log(JSON.stringify({ ok: false, error: "Could not put the logins into Kumo's computer." })); process.exit(1); }
if (mode === "garbage") { console.log("Traceback (most recent call last): " + stdin); process.exit(1); }
if (mode === "silent") process.exit(0);
if (mode === "hang") await new Promise(() => {});
`;

function bridge(mode: string, extra: Partial<LoginBridge> = {}): LoginBridge & { record: string } {
  const record = join(scratch, `record-${mode}-${crypto.randomUUID()}.json`);
  return {
    command: [process.execPath, standIn],
    cwd: scratch,
    env: { PATH: process.env.PATH, MODE: mode, RECORD_FILE: record },
    timeoutMs: 10_000,
    ...extra,
    record,
  };
}

function recorded(path: string) {
  return JSON.parse(readFileSync(path, "utf8")) as { argv: string[]; env: Record<string, string>; stdin: string };
}

function leaked(text: string) {
  return secretValues.filter((value) => text.includes(value));
}

const consoles = () => [spyOn(console, "log"), spyOn(console, "info"), spyOn(console, "warn"), spyOn(console, "error"), spyOn(console, "debug")];

beforeAll(() => {
  scratch = realpathSync(mkdtempSync(join(tmpdir(), "clientops-bridge-")));
  standIn = join(scratch, "stand-in.ts");
  writeFileSync(standIn, standInSource);
});

afterEach(() => {
  for (const method of ["log", "info", "warn", "error", "debug"] as const) (console[method] as unknown as { mockRestore?: () => void }).mockRestore?.();
});

describe("the login bridge to the agent", () => {
  test("by default runs the agent's setup_logins module with uv from the project", () => {
    const configured = loginBridge({ PATH: "/usr/bin", CLIENTOPS_EXTERNALS: "fake" }, projectRoot);
    expect(configured.command).toEqual(["uv", "run", "--directory", "agent", "python", "-m", "clientops.setup_logins"]);
    expect(configured.cwd).toBe(projectRoot);
    expect(configured.env).toMatchObject({ PATH: "/usr/bin", CLIENTOPS_EXTERNALS: "fake" });
  });

  test("sends the values only on stdin: never in the arguments or the environment", async () => {
    const used = bridge("ok");

    await putLoginsThroughBridge(used, "kumo", values);

    const seen = recorded(used.record);
    expect(seen.argv).toEqual(["kumo"]);
    expect(JSON.parse(seen.stdin)).toEqual(values);
    expect(leaked(JSON.stringify(seen.argv) + JSON.stringify(seen.env))).toEqual([]);
  });

  test("gives back the placeholder names only", async () => {
    const plain: unknown = await putLoginsThroughBridge(bridge("ok"), "kumo", values);
    expect(plain).toEqual({ present: contract.logins.added.present, missing: [] });
    const answer: unknown = await putLoginsThroughBridge(bridge("extra"), "kumo", values);
    expect(answer).toEqual({ present: contract.logins.added.present, missing: [] });
    expect(leaked(JSON.stringify(answer))).toEqual([]);
  });

  test("refuses an answer that names something other than a login placeholder", async () => {
    await expect(putLoginsThroughBridge(bridge("stranger"), "kumo", values)).rejects.toBeInstanceOf(LoginBridgeError);
  });

  test("never passes on an error that carries a value, and logs nothing at all", async () => {
    const spies = consoles();
    for (const mode of ["echo", "stderr", "garbage", "silent"]) {
      const error = await putLoginsThroughBridge(bridge(mode), "kumo", values).catch((caught) => caught);
      expect(error).toBeInstanceOf(LoginBridgeError);
      expect(leaked(`${error.message} ${String(error.stack)} ${JSON.stringify(error)}`)).toEqual([]);
    }
    expect(spies.flatMap((spy) => spy.mock.calls)).toEqual([]);
  });

  test("passes on the agent's own refusal, which names no value", async () => {
    const error = await putLoginsThroughBridge(bridge("stderr"), "kumo", values).catch((caught) => caught);
    expect(error.message).toBe("Could not put the logins into Kumo's computer.");
  });

  test("stops the program when it takes too long", async () => {
    const started = Date.now();
    const error = await putLoginsThroughBridge(bridge("hang", { timeoutMs: 500 }), "kumo", values).catch((caught) => caught);
    expect(error).toBeInstanceOf(LoginBridgeError);
    expect(Date.now() - started).toBeLessThan(5_000);
  });

  test("checks the client id before starting anything", async () => {
    for (const clientId of ["", "Kumo", "kumo; rm", "--help", "../kumo"]) {
      const used = bridge("ok");
      await expect(putLoginsThroughBridge(used, clientId, values)).rejects.toBeInstanceOf(LoginBridgeError);
      expect(() => readFileSync(used.record)).toThrow();
    }
  });

  test("runs the real agent module: with the Daytona fake the vault cannot be confirmed, and nothing leaks", async () => {
    const real = loginBridge({ ...process.env, CLIENTOPS_EXTERNALS: "fake", CLIENTOPS_DATA_DIR: join(scratch, "data") }, projectRoot);

    const refused = await putLoginsThroughBridge(real, "acme", values).catch((caught) => caught);
    expect(refused.message).toBe(contract.logins.refused.error);

    const unconfirmed = await putLoginsThroughBridge(real, "kumo", values).catch((caught) => caught);
    expect(unconfirmed).toBeInstanceOf(LoginBridgeError);
    expect(unconfirmed.message).toContain("Kumo's computer");
    expect(leaked(unconfirmed.message)).toEqual([]);
  }, 60_000);

  test("runs the real agent module on this Mac: the values land in the client's own vault and only names come back", async () => {
    const dataDir = join(scratch, "local-data");
    const real = loginBridge({ ...process.env, CLIENTOPS_EXTERNALS: "fake", CLIENTOPS_COMPUTERS: "local", CLIENTOPS_DATA_DIR: dataDir }, projectRoot);

    const answer: unknown = await putLoginsThroughBridge(real, "kumo", values);

    const vault = join(dataDir, "computers", "kumo", "vault.json");
    expect(answer).toEqual({ present: contract.logins.added.present, missing: [] });
    expect(JSON.parse(readFileSync(vault, "utf8"))).toEqual({ version: 1, values });
    expect(statSync(vault).mode & 0o777).toBe(0o600);
    expect(statSync(join(dataDir, "computers", "kumo")).mode & 0o777).toBe(0o700);
  }, 60_000);
});
