import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { dataDirectory, readUseClaude, SettingsError, settingsPath, writeUseClaude } from "./settings";

const projectRoot = resolve(import.meta.dir, "..", "..");
let scratch = "";
let dataDir = "";
let env: Record<string, string | undefined> = {};

function mode(path: string) {
  return statSync(path).mode & 0o777;
}

function writeRaw(content: unknown) {
  mkdirSync(dataDir, { recursive: true });
  writeFileSync(join(dataDir, "settings.json"), typeof content === "string" ? content : JSON.stringify(content));
}

beforeEach(() => {
  scratch = realpathSync(mkdtempSync(join(tmpdir(), "clientops-settings-")));
  dataDir = join(scratch, "clientops-data");
  env = { CLIENTOPS_DATA_DIR: dataDir };
});

afterEach(() => {
  if (existsSync(join(dataDir, "settings.json"))) chmodSync(join(dataDir, "settings.json"), 0o600);
});

describe("where the settings live", () => {
  test("next to the audit log, in CLIENTOPS_DATA_DIR when it is set", () => {
    expect(settingsPath(env, projectRoot)).toBe(join(dataDirectory(env, projectRoot), "settings.json"));
    expect(dataDirectory(env, projectRoot)).toBe(resolve(dataDir));
  });

  test("otherwise under XDG_STATE_HOME, otherwise under ~/.local/state", () => {
    expect(dataDirectory({ XDG_STATE_HOME: join(scratch, "state") }, projectRoot, join(scratch, "home"))).toBe(join(scratch, "state", "clientops"));
    expect(dataDirectory({}, projectRoot, join(scratch, "home"))).toBe(join(scratch, "home", ".local", "state", "clientops"));
  });

  test("a directory inside the project is refused, so settings never land in a snapshot", () => {
    for (const inside of [projectRoot, join(projectRoot, "agent", "data"), join(projectRoot, "config", "..", "state")]) {
      expect(() => dataDirectory({ CLIENTOPS_DATA_DIR: inside }, projectRoot)).toThrow(SettingsError);
    }
    expect(() => dataDirectory({ XDG_STATE_HOME: projectRoot }, projectRoot)).toThrow(SettingsError);
  });

  test("a link that leads into the project is refused too", () => {
    const link = join(scratch, "link-into-project");
    Bun.spawnSync(["ln", "-s", projectRoot, link]);
    expect(() => dataDirectory({ CLIENTOPS_DATA_DIR: join(link, "state") }, projectRoot)).toThrow(SettingsError);
  });
});

describe("reading the Use Claude switch", () => {
  test("without a settings file Claude is used, and nothing is created", async () => {
    expect(await readUseClaude("kumo", env, projectRoot)).toBe(true);
    expect(existsSync(dataDir)).toBe(false);
  });

  test("a client missing from the file, or without the switch, uses Claude", async () => {
    writeRaw({ version: 1, clients: { hinata: { useClaude: false }, sora: {} } });
    expect(await readUseClaude("kumo", env, projectRoot)).toBe(true);
    expect(await readUseClaude("sora", env, projectRoot)).toBe(true);
    expect(await readUseClaude("hinata", env, projectRoot)).toBe(false);
  });

  test("a file without clients uses Claude for everyone", async () => {
    writeRaw({ version: 1 });
    expect(await readUseClaude("kumo", env, projectRoot)).toBe(true);
  });

  test("names that only exist on every object are not clients", async () => {
    writeRaw({ version: 1, clients: {} });
    expect(await readUseClaude("constructor", env, projectRoot)).toBe(true);
    expect(await readUseClaude("__proto__", env, projectRoot)).toBe(true);
  });

  const untrusted: unknown[] = [
    "{not json",
    "[]",
    { version: 2, clients: {} },
    { clients: { kumo: { useClaude: false } } },
    { version: 1, clients: [] },
    { version: 1, clients: { kumo: true } },
    { version: 1, clients: { kumo: { useClaude: "no" } } },
    { version: 1, clients: { kumo: { useClaude: 0 } } },
  ];

  for (const content of untrusted) {
    test(`a file that cannot be trusted is an error instead of a guess: ${JSON.stringify(content)}`, async () => {
      writeRaw(content);
      await expect(readUseClaude("kumo", env, projectRoot)).rejects.toBeInstanceOf(SettingsError);
    });
  }

  test("an unreadable file is an error", async () => {
    writeRaw({ version: 1 });
    chmodSync(join(dataDir, "settings.json"), 0);
    await expect(readUseClaude("kumo", env, projectRoot)).rejects.toBeInstanceOf(SettingsError);
  });
});

describe("writing the Use Claude switch", () => {
  test("writes version 1 with the client's switch, in a 700 folder and a 600 file", async () => {
    await writeUseClaude("kumo", false, env, projectRoot);

    expect(JSON.parse(readFileSync(join(dataDir, "settings.json"), "utf8"))).toEqual({ version: 1, clients: { kumo: { useClaude: false } } });
    expect(mode(dataDir)).toBe(0o700);
    expect(mode(join(dataDir, "settings.json"))).toBe(0o600);
    expect(await readUseClaude("kumo", env, projectRoot)).toBe(false);
  });

  test("keeps the other clients and any other settings a client has", async () => {
    writeRaw({ version: 1, clients: { hinata: { useClaude: false }, kumo: { useClaude: false, schedule: "18:00" } } });

    await writeUseClaude("kumo", true, env, projectRoot);

    expect(JSON.parse(readFileSync(join(dataDir, "settings.json"), "utf8"))).toEqual({
      version: 1,
      clients: { hinata: { useClaude: false }, kumo: { useClaude: true, schedule: "18:00" } },
    });
  });

  test("tightens an existing folder and file that others could read", async () => {
    writeRaw({ version: 1 });
    chmodSync(dataDir, 0o755);
    chmodSync(join(dataDir, "settings.json"), 0o644);

    await writeUseClaude("hinata", false, env, projectRoot);

    expect(mode(dataDir)).toBe(0o700);
    expect(mode(join(dataDir, "settings.json"))).toBe(0o600);
  });

  test("replaces the file in one step and leaves no temporary file behind", async () => {
    await writeUseClaude("kumo", false, env, projectRoot);
    const before = statSync(join(dataDir, "settings.json")).ino;
    await writeUseClaude("kumo", true, env, projectRoot);

    expect(readdirSync(dataDir)).toEqual(["settings.json"]);
    expect(statSync(join(dataDir, "settings.json")).ino).not.toBe(before);
  });

  test("never overwrites a file it cannot trust", async () => {
    writeRaw({ version: 2, clients: { kumo: { useClaude: false } } });

    await expect(writeUseClaude("kumo", true, env, projectRoot)).rejects.toBeInstanceOf(SettingsError);
    expect(JSON.parse(readFileSync(join(dataDir, "settings.json"), "utf8"))).toEqual({ version: 2, clients: { kumo: { useClaude: false } } });
  });

  test("the agent reads what the screen wrote, from the same place", async () => {
    await writeUseClaude("kumo", false, env, projectRoot);
    await writeUseClaude("hinata", true, env, projectRoot);

    const read = Bun.spawnSync(
      ["uv", "run", "--directory", "agent", "python", "-c", "from clientops.settings import settings_path, use_claude; print(settings_path(), use_claude('kumo'), use_claude('hinata'), use_claude('sora'))"],
      { cwd: projectRoot, env: { ...process.env, CLIENTOPS_DATA_DIR: dataDir, LITELLM_LOCAL_MODEL_COST_MAP: "True" } },
    );

    expect(read.exitCode).toBe(0);
    expect(read.stdout.toString().trim()).toBe(`${settingsPath(env, projectRoot)} False True True`);
  }, 60_000);

  test("refuses to write inside the project", async () => {
    await expect(writeUseClaude("kumo", false, { CLIENTOPS_DATA_DIR: join(projectRoot, "tmp-state") }, projectRoot)).rejects.toBeInstanceOf(SettingsError);
    expect(existsSync(join(projectRoot, "tmp-state"))).toBe(false);
  });
});
