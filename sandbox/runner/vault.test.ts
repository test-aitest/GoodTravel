import { afterAll, describe, expect, test } from "bun:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defaultVaultPath, readVault } from "./vault";

const directory = mkdtempSync(join(tmpdir(), "cr-vault-"));

function vaultFile(name: string, content: unknown, mode = 0o600) {
  const path = join(directory, name);
  writeFileSync(path, typeof content === "string" ? content : JSON.stringify(content));
  chmodSync(path, mode);
  return path;
}

afterAll(() => {
  rmSync(directory, { recursive: true, force: true });
});

describe("readVault", () => {
  test("lives in the computer user's home by default", () => {
    expect(defaultVaultPath).toBe("/home/daytona/.clientops/vault.json");
  });

  test("reads the LP_ values of a version 1 vault", () => {
    const path = vaultFile("good.json", { version: 1, values: { LP_STORE_USERNAME: "demo-user", LP_STORE_PASSWORD: "demo-pass" } });
    expect(readVault(path)).toEqual({ ok: true, values: { LP_STORE_USERNAME: "demo-user", LP_STORE_PASSWORD: "demo-pass" } });
  });

  test("says when there is no vault", () => {
    expect(readVault(join(directory, "absent.json"))).toEqual({ ok: false, problem: "missing", message: `No vault at ${join(directory, "absent.json")}` });
  });

  test("refuses a vault other users could read", () => {
    const path = vaultFile("open.json", { version: 1, values: { LP_A: "x" } }, 0o644);
    expect(readVault(path)).toEqual({ ok: false, problem: "unsafe", message: `The vault at ${path} can be read by other users; it must be mode 600` });
  });

  const broken: Array<[string, unknown]> = [
    ["text that is not JSON", "{version"],
    ["another version", { version: 2, values: {} }],
    ["values that are not an object", { version: 1, values: ["LP_A"] }],
    ["a name that is not LP_", { version: 1, values: { PASSWORD: "x" } }],
    ["a value that is not text", { version: 1, values: { LP_A: 7 } }],
  ];

  test.each(broken)("refuses %s without echoing what it read", (label, content) => {
    const path = vaultFile(`${label.replaceAll(" ", "-")}.json`, content);
    const read = readVault(path);
    expect(read).toMatchObject({ ok: false, problem: "unreadable" });
    expect(JSON.stringify(read)).not.toContain('"x"');
  });
});
