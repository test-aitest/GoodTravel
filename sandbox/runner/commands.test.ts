import { describe, expect, test } from "bun:test";
import { BrowserGone } from "./cdp";
import { parseCommand, prepareCommand, Runner, type Command } from "./commands";
import { FakeTab, type FakeTabOptions } from "./fixtures/fakeTab";
import { recorded } from "./fixtures/load";
import { hidden } from "./placeholders";
import { elementsOf } from "./tree";

const store = "http://127.0.0.1:3002";
const supplier = "http://127.0.0.1:3001";

function runnerWith(options: FakeTabOptions = {}) {
  const tab = new FakeTab(options);
  const runner = new Runner(async () => tab);
  return { tab, runner };
}

async function openedOn(url: string, options: FakeTabOptions = {}) {
  const made = runnerWith(options);
  const opened = await made.runner.run({ command: "open", url });
  expect(opened.ok).toBe(true);
  return made;
}

const refOf = (page: Parameters<typeof recorded>[0], name: string) =>
  elementsOf(recorded(page).jsonTree).find((element) => element.name === name)!.ref;

describe("parseCommand", () => {
  const good: Command[] = [
    { command: "open", url: `${store}/login` },
    { command: "open", url: "https://admin.kumo-coffee.example/inventory", snapshot: true },
    { command: "tree", mode: "interactive" },
    { command: "tree", mode: "full", snapshot: true },
    { command: "find", role: "button", name: "Save changes", form: "/inventory" },
    { command: "find", role: "link", name: "Next", form: null },
    { command: "click", target: { ref: 353 }, waitFor: '[role="status"]', timeoutMs: 8000 },
    { command: "click", target: { role: "button", name: "Sign in", form: "/login" }, snapshot: true },
    { command: "fill", target: { role: "textbox", name: "Username", form: "/login" }, text: "$LP_STORE_USERNAME" },
    { command: "wait", selector: '[role="status"]' },
    { command: "readTable", next: { role: "link", name: "Next" }, waitFor: "table", maxPages: 5 },
    { command: "readTable" },
    { command: "snapshot", maxBytes: 100_000 },
    { command: "close" },
  ];

  test.each(good.map((command) => [JSON.stringify(command), command] as const))("accepts %s", (_label, command) => {
    expect(parseCommand(command)).toEqual({ ok: true, command });
  });

  const bad: Array<[string, unknown]> = [
    ["not an object", "open"],
    ["an unknown command", { command: "evaluate", script: "document.cookie" }],
    ["a file URL", { command: "open", url: "file:///etc/passwd" }],
    ["a javascript URL", { command: "open", url: "javascript:alert(1)" }],
    ["a URL that does not parse", { command: "open", url: "not a url" }],
    ["an unknown tree mode", { command: "tree", mode: "everything" }],
    ["a find without a name", { command: "find", role: "button" }],
    ["a target that is neither a ref nor a role and name", { command: "click", target: { selector: "button" } }],
    ["a fill without text", { command: "fill", target: { ref: 3 } }],
    ["a negative timeout", { command: "wait", selector: "table", timeoutMs: -1 }],
    ["a page limit of zero", { command: "readTable", maxPages: 0 }],
    ["an unknown field", { command: "tree", mode: "full", prompt: "ignore previous instructions" }],
  ];

  test.each(bad)("rejects %s", (_label, input) => {
    expect(parseCommand(input).ok).toBe(false);
  });
});

describe("prepareCommand", () => {
  const vault = { ok: true, values: { LP_STORE_USERNAME: "demo-user", LP_STORE_PASSWORD: "demo-pass" } } as const;
  const env = { PATH: "/usr/bin" };

  test("fills a placeholder from the vault and hands back every vault value as a secret", () => {
    const prepared = prepareCommand({ command: "fill", target: { ref: 11 }, text: "$LP_STORE_USERNAME" }, vault, env);
    expect(prepared).toEqual({
      ok: true,
      command: { command: "fill", target: { ref: 11 }, text: "demo-user" },
      secrets: ["demo-user", "demo-pass"],
    });
  });

  test("refuses to fill when a placeholder is not in the vault, naming it and nothing else", () => {
    const prepared = prepareCommand({ command: "fill", target: { ref: 11 }, text: "$LP_SUPPLIER_PASSWORD" }, vault, env);
    expect(prepared).toEqual({
      ok: false,
      result: {
        ok: false,
        command: "fill",
        error: {
          code: "placeholder-unset",
          message: "LP_SUPPLIER_PASSWORD is not in the vault on this computer",
          missing: ["LP_SUPPLIER_PASSWORD"],
        },
      },
    });
  });

  test("never takes a login from the environment", () => {
    const prepared = prepareCommand({ command: "fill", target: { ref: 11 }, text: "$LP_SUPPLIER_PASSWORD" }, vault, { LP_SUPPLIER_PASSWORD: "from-env" });
    expect(prepared.ok).toBe(false);
    expect(JSON.stringify(prepared)).not.toContain("from-env");
  });

  test("refuses to fill a placeholder when there is no vault", () => {
    const prepared = prepareCommand(
      { command: "fill", target: { ref: 11 }, text: "$LP_STORE_USERNAME" },
      { ok: false, problem: "missing", message: "No vault at /home/daytona/.clientops/vault.json" },
      env,
    );
    expect(prepared).toEqual({
      ok: false,
      result: {
        ok: false,
        command: "fill",
        error: {
          code: "placeholder-unset",
          message: "LP_STORE_USERNAME is not in the vault on this computer (No vault at /home/daytona/.clientops/vault.json)",
          missing: ["LP_STORE_USERNAME"],
        },
      },
    });
  });

  test("refuses to fill a placeholder from a vault that is unsafe or broken", () => {
    const message = "The vault at /v.json can be read by other users; it must be mode 600";
    const prepared = prepareCommand({ command: "fill", target: { ref: 11 }, text: "$LP_STORE_USERNAME" }, { ok: false, problem: "unsafe", message }, env);
    expect(prepared).toEqual({ ok: false, result: { ok: false, command: "fill", error: { code: "vault-unreadable", message } } });
  });

  test("fills plain text without needing a vault", () => {
    const prepared = prepareCommand({ command: "fill", target: { ref: 43 }, text: "6" }, { ok: false, problem: "missing", message: "No vault" }, env);
    expect(prepared).toEqual({ ok: true, command: { command: "fill", target: { ref: 43 }, text: "6" }, secrets: [] });
  });

  test("passes other commands through, still listing the vault values and any LP_ variable so they can be hidden", () => {
    expect(prepareCommand({ command: "tree", mode: "interactive" }, vault, { LP_OLD: "stale-value" })).toEqual({
      ok: true,
      command: { command: "tree", mode: "interactive" },
      secrets: ["demo-user", "demo-pass", "stale-value"],
    });
  });
});

describe("Runner", () => {
  test("asks for a page to be opened before anything else", async () => {
    const { runner } = runnerWith();
    expect(await runner.run({ command: "tree", mode: "interactive" })).toEqual({
      ok: false,
      command: "tree",
      error: { code: "no-page", message: "Open a page first" },
    });
  });

  test("opens a page and says where it landed", async () => {
    const { runner, tab } = runnerWith();
    expect(await runner.run({ command: "open", url: `${store}/login` })).toEqual({
      ok: true,
      command: "open",
      page: { url: `${store}/login`, title: "Sign in - Store Admin", status: 200 },
    });
    expect(tab.callsTo("LP.waitUntil")).toEqual([{ until: "load" }]);
  });

  test("says so when the page cannot be reached", async () => {
    const { runner } = runnerWith();
    expect(await runner.run({ command: "open", url: "http://127.0.0.1:3999/" })).toEqual({
      ok: false,
      command: "open",
      error: { code: "navigate-failed", message: "Could not open http://127.0.0.1:3999/: CouldntConnect" },
    });
  });

  test("says so when no browser is running", async () => {
    const runner = new Runner(async () => {
      throw new BrowserGone("No browser is serving CDP at ws://127.0.0.1:9222/");
    });
    expect(await runner.run({ command: "open", url: `${store}/login` })).toEqual({
      ok: false,
      command: "open",
      error: { code: "browser-unreachable", message: "No browser is serving CDP at ws://127.0.0.1:9222/" },
    });
  });

  test("reads the interactive tree as Lightpanda wrote it, with elements, their forms and the table cells", async () => {
    const { runner } = await openedOn(`${store}/inventory`);
    const result = await runner.run({ command: "tree", mode: "interactive" });
    if (!result.ok || result.command !== "tree") throw new Error("tree failed");
    expect(result.text).toBe(recorded("store-inventory").interactiveText!);
    expect(result.elements).toHaveLength(45);
    expect(result.elements.at(-1)).toEqual({ ref: 353, role: "button", name: "Save changes", form: "/inventory", tag: "button", fieldName: "action" });
    expect(result.cells).toContain("KC-ETH-200");
    expect(result.cells).toContain("Ethiopia Guji 200g");
    expect(result.cells).toContain("18");
  });

  test("reads the full tree when asked", async () => {
    const { runner, tab } = await openedOn(`${store}/login`);
    const result = await runner.run({ command: "tree", mode: "full" });
    expect(result.ok && result.command === "tree" && result.text).toBe(recorded("store-login").text!);
    expect(tab.callsTo("LP.getSemanticTree")[0]).toEqual({ format: "text" });
  });

  test("finds one element and gives its selector for the script", async () => {
    const { runner } = await openedOn(`${store}/inventory`);
    expect(await runner.run({ command: "find", role: "button", name: "Save changes", form: "/inventory" })).toEqual({
      ok: true,
      command: "find",
      page: { url: `${store}/inventory`, title: "Inventory - Kumo Coffee - Store Admin", status: 200 },
      element: { ref: 353, role: "button", name: "Save changes", form: "/inventory", tag: "button", fieldName: "action" },
      selector: 'button[name="action"]',
    });
  });

  test("clicks the one matching element, then waits for the next page", async () => {
    const { runner, tab } = await openedOn(`${store}/inventory`);
    const result = await runner.run({
      command: "click",
      target: { role: "button", name: "Save changes", form: "/inventory" },
      waitFor: '[role="status"]',
      timeoutMs: 8000,
    });
    expect(result).toMatchObject({ ok: true, command: "click", element: { ref: 353, role: "button", name: "Save changes", form: "/inventory" } });
    expect(tab.callsTo("LP.clickNode")).toEqual([{ backendNodeId: 353 }]);
    expect(tab.callsTo("LP.waitForSelector")).toEqual([{ selector: '[role="status"]', timeout: 8000 }]);
  });

  test("does not click when the target is missing or not unique, and says how many matched", async () => {
    const { runner, tab } = await openedOn(`${store}/inventory`, { duplicateRef: 353 });
    expect(await runner.run({ command: "click", target: { role: "button", name: "Save changes", form: "/logout" } })).toEqual({
      ok: false,
      command: "click",
      error: { code: "not-found", message: 'No button named "Save changes" in form /logout on this page', count: 0 },
    });
    expect(await runner.run({ command: "click", target: { role: "button", name: "Save changes", form: "/inventory" } })).toEqual({
      ok: false,
      command: "click",
      error: { code: "not-unique", message: '2 buttons named "Save changes" in form /inventory on this page', count: 2 },
    });
    expect(tab.callsTo("LP.clickNode")).toEqual([]);
  });

  test("refuses a ref that is not on the current page", async () => {
    const { runner, tab } = await openedOn(`${store}/inventory`);
    expect(await runner.run({ command: "click", target: { ref: 99999 } })).toEqual({
      ok: false,
      command: "click",
      error: { code: "not-found", message: "No element 99999 on this page", count: 0 },
    });
    expect(tab.callsTo("LP.clickNode")).toEqual([]);
  });

  test("reports a wait that ran out", async () => {
    const { runner } = await openedOn(`${store}/inventory`, { failWaitFor: true });
    const result = await runner.run({ command: "click", target: { ref: 353 }, waitFor: '[role="status"]' });
    expect(result).toEqual({
      ok: false,
      command: "click",
      error: { code: "wait-failed", message: 'Nothing matching [role="status"] appeared' },
    });
  });

  test("calls a click failed as soon as the page it led to has loaded and settled without the awaited element", async () => {
    const signIn = refOf("store-login", "Sign in");
    const { runner, tab } = await openedOn(`${store}/login`, { linkTargets: { [signIn]: "store-login" }, present: { "store-inventory": ["table"] } });
    const started = performance.now();
    const result = await runner.run({ command: "click", target: { ref: signIn }, waitFor: "table" });
    const tookMs = performance.now() - started;
    expect(result).toEqual({ ok: false, command: "click", error: { code: "wait-failed", message: "Nothing matching table appeared" } });
    expect(tookMs).toBeLessThan(1000);
    expect(tab.callsTo("LP.waitUntil").map((call) => call.until)).toContain("settled");
    for (const call of tab.callsTo("LP.waitForSelector")) expect(call.timeout).toBeLessThan(1000);
  });

  test("returns as soon as the page the click led to shows the awaited element", async () => {
    const signIn = refOf("store-login", "Sign in");
    const { runner } = await openedOn(`${store}/login`, { linkTargets: { [signIn]: "store-inventory" }, present: { "store-inventory": ["table"] } });
    const started = performance.now();
    const result = await runner.run({ command: "click", target: { ref: signIn }, waitFor: "table" });
    expect(performance.now() - started).toBeLessThan(1000);
    expect(result).toMatchObject({ ok: true, command: "click", page: { url: `${store}/inventory` }, element: { ref: signIn, role: "button", name: "Sign in" } });
  });

  test("waits for a slow next page to load instead of calling it failed early", async () => {
    const signIn = refOf("store-login", "Sign in");
    const { runner, tab } = await openedOn(`${store}/login`, {
      linkTargets: { [signIn]: "store-inventory" },
      present: { "store-inventory": ["table"] },
      loadMs: 1500,
    });
    const started = performance.now();
    const result = await runner.run({ command: "click", target: { ref: signIn }, waitFor: "table" });
    expect(performance.now() - started).toBeGreaterThanOrEqual(1450);
    expect(result).toMatchObject({ ok: true, command: "click", page: { url: `${store}/inventory` } });
    const loads = tab.callsTo("LP.waitUntil").filter((call) => call.until === "domcontentloaded");
    expect(loads.length).toBeGreaterThan(0);
    for (const call of loads) expect(call.timeout).toBeGreaterThanOrEqual(14_000);
  });

  test("still calls a slow next page failed once it has loaded without the awaited element", async () => {
    const signIn = refOf("store-login", "Sign in");
    const { runner } = await openedOn(`${store}/login`, {
      linkTargets: { [signIn]: "store-login" },
      present: { "store-inventory": ["table"] },
      loadMs: 1500,
    });
    const started = performance.now();
    const result = await runner.run({ command: "click", target: { ref: signIn }, waitFor: "table" });
    const tookMs = performance.now() - started;
    expect(result).toEqual({ ok: false, command: "click", error: { code: "wait-failed", message: "Nothing matching table appeared" } });
    expect(tookMs).toBeGreaterThanOrEqual(1450);
    expect(tookMs).toBeLessThan(2500);
  });

  test("keeps the whole wait it was given when the click leads to no new page", async () => {
    const { runner, tab } = await openedOn(`${store}/inventory`, { present: {} });
    const started = performance.now();
    const result = await runner.run({ command: "click", target: { ref: 353 }, waitFor: '[role="status"]', timeoutMs: 400 });
    expect(performance.now() - started).toBeGreaterThanOrEqual(390);
    expect(result).toEqual({ ok: false, command: "click", error: { code: "wait-failed", message: 'Nothing matching [role="status"] appeared' } });
    expect(tab.callsTo("LP.waitForSelector")).toEqual([{ selector: '[role="status"]', timeout: 400 }]);
  });

  test("fills a field, checks the value took, and never repeats a secret it typed", async () => {
    const { runner, tab } = await openedOn(`${store}/login`);
    const result = await runner.run({ command: "fill", target: { role: "textbox", name: "Username", form: "/login" }, text: "demo-user" }, ["demo-user"]);
    expect(result).toEqual({
      ok: true,
      command: "fill",
      page: { url: `${store}/login`, title: "Sign in - Store Admin", status: 200 },
      element: { ref: refOf("store-login", "Username"), role: "textbox", name: "Username", form: "/login" },
    });
    expect(tab.callsTo("LP.fillNode")).toEqual([{ backendNodeId: refOf("store-login", "Username"), text: "demo-user" }]);
    const tree = await runner.run({ command: "tree", mode: "interactive" });
    const shown = JSON.stringify(tree);
    expect(shown).not.toContain("demo-user");
    expect(shown).toContain(hidden);
  });

  test("checks a password fill by Lightpanda's mask", async () => {
    const { runner } = await openedOn(`${store}/login`);
    const result = await runner.run({ command: "fill", target: { role: "textbox", name: "Password", form: "/login" }, text: "demo-pass" }, ["demo-pass"]);
    expect(result.ok).toBe(true);
    expect(JSON.stringify(result)).not.toContain("demo-pass");
  });

  test("reports a fill that did not take", async () => {
    const { runner } = await openedOn(`${store}/inventory`, { ignoreFill: true });
    const result = await runner.run({ command: "fill", target: { role: "spinbutton", name: "Available for Ethiopia Guji 200g", form: "/inventory" }, text: "6" });
    expect(result).toEqual({
      ok: false,
      command: "fill",
      error: { code: "fill-not-applied", message: 'The spinbutton "Available for Ethiopia Guji 200g" did not take the new value' },
    });
  });

  test("reads the supplier stock across its three pages by clicking Next", async () => {
    const { runner, tab } = await openedOn(`${supplier}/stock`, {
      linkTargets: { [refOf("supplier-stock-1", "Next")]: "supplier-stock-2", [refOf("supplier-stock-2", "Next")]: "supplier-stock-3" },
    });
    const result = await runner.run({ command: "readTable", next: { role: "link", name: "Next" } });
    if (!result.ok || result.command !== "readTable") throw new Error(JSON.stringify(result));
    expect(result.columns).toEqual(["SKU", "Product", "Pack", "Available", "Status"]);
    expect(result.rows).toHaveLength(42);
    expect(result.pages).toBe(3);
    expect(result.page.url).toBe(`${supplier}/stock?page=3`);
    expect(tab.callsTo("LP.clickNode")).toHaveLength(2);
    expect(tab.callsTo("LP.waitForSelector")).toEqual([{ selector: "table" }, { selector: "table" }]);
  });

  test("stops with an error when Next does not change the page, instead of returning a short list", async () => {
    const { runner } = await openedOn(`${supplier}/stock`);
    expect(await runner.run({ command: "readTable", next: { role: "link", name: "Next" } })).toEqual({
      ok: false,
      command: "readTable",
      error: { code: "next-page-unchanged", message: "Page 2 showed the same rows as page 1" },
    });
  });

  test("stops with an error when there are more pages than allowed", async () => {
    const { runner } = await openedOn(`${supplier}/stock`, {
      linkTargets: { [refOf("supplier-stock-1", "Next")]: "supplier-stock-2" },
    });
    expect(await runner.run({ command: "readTable", next: { role: "link", name: "Next" }, maxPages: 2 })).toEqual({
      ok: false,
      command: "readTable",
      error: { code: "too-many-pages", message: "There are more than 2 pages" },
    });
  });

  test("reads the store inventory form as 42 rows", async () => {
    const { runner } = await openedOn(`${store}/inventory`);
    const result = await runner.run({ command: "readTable" });
    expect(result).toMatchObject({ ok: true, command: "readTable", columns: ["SKU", "Product", "Available"], pages: 1 });
    expect(result.ok && result.command === "readTable" && result.rows[0]).toEqual(["KC-ETH-200", "Ethiopia Guji 200g", "18"]);
  });

  test("reports a page without a table", async () => {
    const { runner } = await openedOn(`${store}/login`);
    expect(await runner.run({ command: "readTable" })).toEqual({
      ok: false,
      command: "readTable",
      error: { code: "not-found", message: "No table 1 on this page", count: 0 },
    });
  });

  test("waits for an element and reads its text", async () => {
    const { runner } = await openedOn(`${store}/inventory`, { statusText: "338 status\n 339 'Saved. 1 product updated.'\n" });
    expect(await runner.run({ command: "wait", selector: '[role="status"]' })).toMatchObject({
      ok: true,
      command: "wait",
      selector: '[role="status"]',
      text: "Saved. 1 product updated.",
    });
  });

  test("takes a safe snapshot of the page showing the values typed so far", async () => {
    const { runner } = await openedOn(`${store}/inventory`);
    await runner.run({ command: "fill", target: { role: "spinbutton", name: "Available for Ethiopia Guji 200g", form: "/inventory" }, text: "6" });
    const result = await runner.run({ command: "snapshot" });
    if (!result.ok || result.command !== "snapshot") throw new Error(JSON.stringify(result));
    expect(result.snapshot).toMatchObject({ url: `${store}/inventory`, host: "127.0.0.1:3002", title: "Inventory - Kumo Coffee - Store Admin", truncated: false });
    expect(result.snapshot.html).toContain(`name="available-KC-ETH-200" value="6"`);
    expect(result.snapshot.bytes).toBe(new TextEncoder().encode(result.snapshot.html).length);
  });

  test("hides a typed login in the snapshot too", async () => {
    const { runner } = await openedOn(`${store}/login`);
    await runner.run({ command: "fill", target: { role: "textbox", name: "Username", form: "/login" }, text: "demo-user" }, ["demo-user"]);
    const result = await runner.run({ command: "click", target: { role: "button", name: "Sign in", form: "/login" }, snapshot: true });
    expect(result.ok && result.command === "click" && result.snapshot?.html).toContain(`value="${hidden}"`);
    expect(JSON.stringify(result)).not.toContain("demo-user");
  });

  test("closing ends the page, and the next command has to open one again", async () => {
    const { runner, tab } = await openedOn(`${store}/login`);
    expect(await runner.run({ command: "close" })).toEqual({ ok: true, command: "close" });
    expect(tab.closed).toBe(true);
    expect((await runner.run({ command: "tree", mode: "full" })).ok).toBe(false);
  });
});
