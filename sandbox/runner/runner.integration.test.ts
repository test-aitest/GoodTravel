import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { accounts, catalogs } from "../sites/catalog";

const cdp = "http://127.0.0.1:9222";
const supplier = "http://127.0.0.1:3001";
const store = "http://127.0.0.1:3002";
const main = join(import.meta.dir, "main.ts");
const socket = join(tmpdir(), `cr-it-${process.pid}.sock`);
const allOff = { restyle: false, renameButton: false, decoyButton: false, hiddenInstruction: false };

const login = (site: "supplier" | "store") => accounts.find((account) => account.client === "kumo" && account.site === site)!;
const vaultValues = {
  LP_SUPPLIER_USERNAME: login("supplier").username,
  LP_SUPPLIER_PASSWORD: login("supplier").password,
  LP_STORE_USERNAME: login("store").username,
  LP_STORE_PASSWORD: login("store").password,
};
const vaultDirectory = mkdtempSync(join(tmpdir(), "cr-it-vault-"));
afterAll(() => rmSync(vaultDirectory, { recursive: true, force: true }));
const vault = join(vaultDirectory, "vault.json");
writeFileSync(vault, JSON.stringify({ version: 1, values: vaultValues }));
chmodSync(vault, 0o600);
const env = { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "", CLIENTOPS_RUNNER_SOCKET: socket, CLIENTOPS_VAULT: vault };
const secretValues = Object.values(vaultValues);

async function answers(url: string) {
  try {
    return (await fetch(url, { signal: AbortSignal.timeout(1_000) })).ok;
  } catch {
    return false;
  }
}

async function missingService() {
  if (!(await answers(`${cdp}/json/version`))) return "Lightpanda is not serving CDP at 127.0.0.1:9222 (start it with `bun run lightpanda`)";
  if (!(await answers(`${supplier}/__control`)) || !(await answers(`${store}/__control`))) {
    return "the demo sites are not running on 127.0.0.1:3001 and :3002 (start them with `bun sandbox/sites/server.ts`)";
  }
  return null;
}

const missing = await missingService();
if (missing) console.warn(`runner integration skipped: ${missing}`);

const transcript: string[] = [];

type Result = { ok: boolean; command: string; [key: string]: unknown };

async function run(command: Record<string, unknown>): Promise<Result & Record<string, any>> {
  const child = Bun.spawn(["bun", main, JSON.stringify(command)], { env, stdout: "pipe", stderr: "pipe" });
  const [output, error] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
  transcript.push(output, error);
  return JSON.parse(output);
}

async function control(site: string, path: string, body?: unknown) {
  await fetch(`${site}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function resetSites() {
  for (const site of [supplier, store]) {
    await control(site, "/__control/switches", allOff);
    await control(site, "/__control/reset");
  }
}

const byNumber = (rows: string[][], skuColumn: number, numberColumn: number) =>
  Object.fromEntries(rows.map((row) => [row[skuColumn], Number(row[numberColumn])]));

describe.skipIf(missing !== null)(`runner on the real Lightpanda and demo sites${missing ? ` (skipped: ${missing})` : ""}`, () => {
  beforeAll(resetSites);

  afterAll(async () => {
    await run({ command: "close" });
    await resetSites();
  });

  test("signs in to the supplier portal from vault placeholders and reads all 42 rows across 3 pages", async () => {
    const opened = await run({ command: "open", url: `${supplier}/login`, snapshot: true });
    expect(opened).toMatchObject({ ok: true, page: { url: `${supplier}/login`, title: "Partner sign in - Maruyama Supply", status: 200 } });
    expect(opened.snapshot).toMatchObject({ host: "127.0.0.1:3001", truncated: false });
    expect(opened.snapshot.html).toContain("Partner sign in");

    expect(await run({ command: "fill", target: { role: "textbox", name: "Account ID", form: "/login" }, text: "$LP_SUPPLIER_USERNAME" })).toMatchObject({ ok: true });
    expect(await run({ command: "fill", target: { role: "textbox", name: "Password", form: "/login" }, text: "$LP_SUPPLIER_PASSWORD" })).toMatchObject({ ok: true });
    const signedIn = await run({ command: "click", target: { role: "button", name: "Sign in", form: "/login" }, waitFor: "table" });
    expect(signedIn).toMatchObject({ ok: true, page: { url: `${supplier}/stock`, status: 200 } });

    const stock = await run({ command: "readTable", next: { role: "link", name: "Next", form: null } });
    expect(stock).toMatchObject({ ok: true, columns: ["SKU", "Product", "Pack", "Available", "Status"], pages: 3, page: { url: `${supplier}/stock?page=3` } });
    expect(stock.rows).toHaveLength(42);
    expect(byNumber(stock.rows, 0, 3)).toEqual(catalogs.kumo.supplierAvailable);
  }, 30_000);

  test("refuses to type a placeholder that is not in the vault and leaves the field as it was", async () => {
    await run({ command: "open", url: `${supplier}/login` });
    expect(await run({ command: "fill", target: { role: "textbox", name: "Account ID", form: "/login" }, text: "$LP_NOT_SET" })).toEqual({
      ok: false,
      command: "fill",
      error: { code: "placeholder-unset", message: "LP_NOT_SET is not in the vault on this computer", missing: ["LP_NOT_SET"] },
    });
    const tree = await run({ command: "tree", mode: "interactive" });
    expect(tree.elements.find((element: { name: string }) => element.name === "Account ID")).toMatchObject({ value: "" });
  }, 30_000);

  test("signs in to the store and reads the 42-row inventory form", async () => {
    await run({ command: "open", url: `${store}/login` });
    await run({ command: "fill", target: { role: "textbox", name: "Username", form: "/login" }, text: "$LP_STORE_USERNAME" });
    await run({ command: "fill", target: { role: "textbox", name: "Password", form: "/login" }, text: "$LP_STORE_PASSWORD" });
    const signedIn = await run({ command: "click", target: { role: "button", name: "Sign in", form: "/login" }, waitFor: 'form[action="/inventory"]' });
    expect(signedIn).toMatchObject({ ok: true, page: { url: `${store}/inventory` } });

    const inventory = await run({ command: "readTable" });
    expect(inventory).toMatchObject({ ok: true, columns: ["SKU", "Product", "Available"], pages: 1 });
    expect(byNumber(inventory.rows, 0, 2)).toEqual(catalogs.kumo.storeAvailable);

    const tree = await run({ command: "tree", mode: "interactive" });
    expect(tree.elements.filter((element: { role: string; form: string }) => element.role === "spinbutton" && element.form === "/inventory")).toHaveLength(42);
  }, 30_000);

  test("finds the save button as exactly one element, also next to the look-alike", async () => {
    expect(await run({ command: "find", role: "button", name: "Save changes", form: "/inventory" })).toMatchObject({
      ok: true,
      element: { role: "button", name: "Save changes", form: "/inventory" },
      selector: 'button[name="action"]',
    });
    await control(store, "/__control/switches", { decoyButton: true });
    await run({ command: "open", url: `${store}/inventory` });
    const found = await run({ command: "find", role: "button", name: "Save changes", form: "/inventory" });
    expect(found).toMatchObject({ ok: true, element: { name: "Save changes" } });
    const tree = await run({ command: "tree", mode: "interactive" });
    expect(tree.elements.filter((element: { role: string; form: string }) => element.role === "button" && element.form === "/inventory")).toHaveLength(2);
    await control(store, "/__control/switches", allOff);
    expect(await run({ command: "find", role: "button", name: "Update inventory", form: "/inventory" })).toMatchObject({
      ok: false,
      error: { code: "not-found", count: 0 },
    });
  }, 30_000);

  test("types a number, shows it in the snapshot, saves, waits for the message and reads it back", async () => {
    await run({ command: "open", url: `${store}/inventory` });
    const filled = await run({
      command: "fill",
      target: { role: "spinbutton", name: "Available for Ethiopia Guji 200g", form: "/inventory" },
      text: "6",
      snapshot: true,
    });
    expect(filled).toMatchObject({ ok: true, element: { role: "spinbutton", form: "/inventory" } });
    expect(filled.snapshot.html).toContain(`name="available-KC-ETH-200" value="6"`);
    expect(filled.snapshot.html).not.toContain("<script");

    const saved = await run({ command: "click", target: { role: "button", name: "Save changes", form: "/inventory" }, waitFor: '[role="status"]' });
    expect(saved).toMatchObject({ ok: true, page: { url: `${store}/inventory?saved=1` } });
    expect(await run({ command: "wait", selector: '[role="status"]' })).toMatchObject({ ok: true, text: "Saved. 1 product updated." });

    const after = await run({ command: "readTable" });
    expect(byNumber(after.rows, 0, 2)).toEqual({ ...catalogs.kumo.storeAvailable, "KC-ETH-200": 6 });
    await resetSites();
  }, 30_000);

  test("printed no login value in anything it returned", () => {
    const everything = transcript.join("\n");
    expect(everything.length).toBeGreaterThan(0);
    for (const secret of secretValues) expect(everything).not.toContain(secret);
  });

  test("closing stops the runner on this computer", async () => {
    expect(await run({ command: "close" })).toEqual({ ok: true, command: "close" });
    for (let tries = 0; tries < 100 && existsSync(socket); tries += 1) await Bun.sleep(20);
    expect(existsSync(socket)).toBe(false);
  }, 10_000);
});
