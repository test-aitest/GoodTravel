import { beforeEach, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { loginNamesPath, rememberedLogins, rememberLogins } from "./loginMemory";
import { SettingsError } from "./settings";
import { allLoginNames, loginNames } from "./setup";

const projectRoot = resolve(import.meta.dir, "..", "..");
const supplierNames = [loginNames.supplier.username, loginNames.supplier.password];
const at = new Date("2026-11-14T04:30:00.000Z");
let dataDir = "";
let env: Record<string, string | undefined> = {};

beforeEach(() => {
  dataDir = join(realpathSync(mkdtempSync(join(tmpdir(), "clientops-memory-"))), "clientops-data");
  env = { CLIENTOPS_DATA_DIR: dataDir };
});

function stored() {
  return JSON.parse(readFileSync(join(dataDir, "login-names.json"), "utf8"));
}

describe("the last confirmed login names", () => {
  test("live in the data directory next to the settings", () => {
    expect(loginNamesPath(env, projectRoot)).toBe(join(dataDir, "login-names.json"));
  });

  test("are unknown until a computer has been checked", async () => {
    expect(await rememberedLogins("kumo", env, projectRoot)).toBeNull();
  });

  test("keep names only, with when they were checked, in a 600 file in a 700 folder", async () => {
    await rememberLogins("kumo", supplierNames, env, projectRoot, at);

    expect(await rememberedLogins("kumo", env, projectRoot)).toEqual(supplierNames);
    expect(stored()).toEqual({ version: 1, clients: { kumo: { present: supplierNames, checkedAt: "2026-11-14T04:30:00.000Z" } } });
    expect(statSync(join(dataDir, "login-names.json")).mode & 0o777).toBe(0o600);
    expect(statSync(dataDir).mode & 0o777).toBe(0o700);
    expect(readdirSync(dataDir)).toEqual(["login-names.json"]);
  });

  test("a newer check replaces that client's names and leaves the others", async () => {
    await rememberLogins("hinata", allLoginNames, env, projectRoot, at);
    await rememberLogins("kumo", supplierNames, env, projectRoot, at);
    await rememberLogins("kumo", allLoginNames, env, projectRoot, at);

    expect(await rememberedLogins("kumo", env, projectRoot)).toEqual([...allLoginNames]);
    expect(await rememberedLogins("hinata", env, projectRoot)).toEqual([...allLoginNames]);
  });

  test("anything that is not a placeholder name is never written", async () => {
    await rememberLogins("kumo", [...supplierNames, "kumo-supply-pass-3Rt!"], env, projectRoot, at);
    expect(JSON.stringify(stored())).not.toContain("kumo-supply-pass-3Rt!");
  });

  test("a file that holds anything else is not trusted, and the next check replaces it", async () => {
    mkdirSync(dataDir, { recursive: true });
    writeFileSync(join(dataDir, "login-names.json"), JSON.stringify({ version: 1, clients: { kumo: { present: ["kumo-supply-pass-3Rt!"], checkedAt: "x" } } }));

    expect(await rememberedLogins("kumo", env, projectRoot)).toBeNull();
    await rememberLogins("kumo", supplierNames, env, projectRoot, at);
    expect(stored().clients.kumo.present).toEqual(supplierNames);
  });

  test("a data directory inside the project is refused", async () => {
    await expect(rememberLogins("kumo", supplierNames, { CLIENTOPS_DATA_DIR: join(projectRoot, "state") }, projectRoot, at)).rejects.toBeInstanceOf(SettingsError);
  });
});
