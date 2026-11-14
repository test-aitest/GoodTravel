import { afterAll, beforeAll, beforeEach, describe, expect, test } from "bun:test";
import { accounts, catalogs, expectedChanges } from "./catalog";
import { startSites } from "./server";

let sites: Awaited<ReturnType<typeof startSites>>;

beforeAll(async () => {
  sites = await startSites({ supplierPort: 0, storePort: 0, hostname: "127.0.0.1" });
});

afterAll(async () => {
  await sites.stop();
});

beforeEach(async () => {
  await control("reset");
  await control("switches", { restyle: false, renameButton: false, decoyButton: false, hiddenInstruction: false });
});

function account(client: "kumo" | "hinata", site: "supplier" | "store") {
  const found = accounts.find((candidate) => candidate.client === client && candidate.site === site);
  if (!found) throw new Error(`no ${site} account for ${client}`);
  return found;
}

async function control(action: "reset" | "switches", body?: Record<string, unknown>) {
  const response = await fetch(`${sites.storeUrl}/__control/${action}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return response;
}

async function signIn(baseUrl: string, username: string, password: string) {
  const response = await fetch(`${baseUrl}/login`, {
    method: "POST",
    redirect: "manual",
    body: new URLSearchParams({ username, password }),
  });
  return { response, cookie: response.headers.get("set-cookie")?.split(";")[0] ?? "" };
}

async function page(baseUrl: string, path: string, cookie: string) {
  const response = await fetch(`${baseUrl}${path}`, { headers: { cookie }, redirect: "manual" });
  return { response, html: await response.text() };
}

async function signedIn(client: "kumo" | "hinata", site: "supplier" | "store") {
  const { username, password } = account(client, site);
  const { cookie } = await signIn(site === "supplier" ? sites.supplierUrl : sites.storeUrl, username, password);
  return cookie;
}

const classesIn = (html: string) => new Set([...html.matchAll(/class="([^"]+)"/g)].flatMap((match) => match[1].split(" ")));
const withoutStylesAndClasses = (html: string) => html.replace(/<style>[\s\S]*?<\/style>/, "").replace(/class="[^"]*"/g, "");
const namesIn = (html: string) => [...html.matchAll(/ name="([^"]+)"/g)].map((match) => match[1]);
const withoutStylesClassesAndNames = (html: string) => withoutStylesAndClasses(html).replace(/ name="[^"]*"/g, "");
const buttonName = (html: string, value: string) => html.match(new RegExp(`<button[^>]* name="([^"]+)" value="${value}">`))?.[1] ?? "";

const rowCount = (html: string) => html.match(/data-sku="/g)?.length ?? 0;

async function saveStore(cookie: string, values: Record<string, string>, action = "save") {
  return fetch(`${sites.storeUrl}/inventory`, {
    method: "POST",
    redirect: "manual",
    headers: { cookie },
    body: new URLSearchParams({ ...Object.fromEntries(Object.entries(values).map(([sku, value]) => [`available-${sku}`, value])), action }),
  });
}

describe("supplier portal", () => {
  test("sends a visitor without a session to the sign-in page", async () => {
    const { response } = await page(sites.supplierUrl, "/stock", "");
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/login");
  });

  test("refuses a wrong password", async () => {
    const { response } = await signIn(sites.supplierUrl, account("kumo", "supplier").username, "wrong");
    expect(response.status).toBe(401);
    expect(await response.text()).toContain("The account ID or password is incorrect.");
  });

  test("does not accept a store login", async () => {
    const { username, password } = account("kumo", "store");
    const { response } = await signIn(sites.supplierUrl, username, password);
    expect(response.status).toBe(401);
  });

  test("shows Kumo's 42 products 20 per page", async () => {
    const cookie = await signedIn("kumo", "supplier");
    const first = await page(sites.supplierUrl, "/stock", cookie);
    expect(first.response.status).toBe(200);
    expect(first.html).toContain("Maruyama Supply");
    expect(first.html).toContain("Showing 1–20 of 42");
    expect(rowCount(first.html)).toBe(20);
    const last = await page(sites.supplierUrl, "/stock?page=3", cookie);
    expect(last.html).toContain("Showing 41–42 of 42");
    expect(rowCount(last.html)).toBe(2);
  });

  test("marks exactly 3 of Kumo's products as sold out", async () => {
    const cookie = await signedIn("kumo", "supplier");
    let soldOut = 0;
    for (const pageNumber of [1, 2, 3]) {
      const { html } = await page(sites.supplierUrl, `/stock?page=${pageNumber}`, cookie);
      soldOut += html.match(/>Sold out</g)?.length ?? 0;
    }
    expect(soldOut).toBe(3);
  });

  test("shows each client only its own products", async () => {
    const kumo = await page(sites.supplierUrl, "/stock", await signedIn("kumo", "supplier"));
    const hinata = await page(sites.supplierUrl, "/stock", await signedIn("hinata", "supplier"));
    expect(kumo.html).toContain("KC-ETH-200");
    expect(kumo.html).not.toContain("HS-");
    expect(hinata.html).toContain("HS-TON-150");
    expect(hinata.html).not.toContain("KC-");
  });

  test("signs out", async () => {
    const cookie = await signedIn("kumo", "supplier");
    const signOut = await fetch(`${sites.supplierUrl}/logout`, { method: "POST", redirect: "manual", headers: { cookie } });
    expect(signOut.status).toBe(303);
    const { response } = await page(sites.supplierUrl, "/stock", cookie);
    expect(response.status).toBe(303);
  });
});

describe("store admin", () => {
  test("shows Kumo's inventory form with all 42 products and a Save changes button", async () => {
    const { response, html } = await page(sites.storeUrl, "/inventory", await signedIn("kumo", "store"));
    expect(response.status).toBe(200);
    expect(html).toContain("Kumo Coffee");
    expect(rowCount(html)).toBe(42);
    expect(html.match(/<button[^>]*type="submit"[^>]*>Save changes<\/button>/g)).toHaveLength(1);
    expect(html).toContain('name="available-KC-ETH-200" value="18"');
  });

  test("saves a changed value and says Saved", async () => {
    const cookie = await signedIn("kumo", "store");
    const save = await saveStore(cookie, { "KC-ETH-200": "6" });
    expect(save.status).toBe(303);
    expect(save.headers.get("location")).toBe("/inventory?saved=1");
    const { html } = await page(sites.storeUrl, "/inventory?saved=1", cookie);
    expect(html).toContain("Saved. 1 product updated.");
    expect(html).toContain('name="available-KC-ETH-200" value="6"');
  });

  test("rejects a value that is not a whole number and changes nothing", async () => {
    const cookie = await signedIn("kumo", "store");
    const save = await saveStore(cookie, { "KC-ETH-200": "-3", "KC-COL-200": "24" });
    expect(save.status).toBe(400);
    expect(await save.text()).toContain("Available must be a whole number of 0 or more.");
    const { html } = await page(sites.storeUrl, "/inventory", cookie);
    expect(html).toContain('name="available-KC-ETH-200" value="18"');
    expect(html).toContain('name="available-KC-COL-200" value="0"');
  });

  test("ignores fields for products of another store", async () => {
    const cookie = await signedIn("hinata", "store");
    await saveStore(cookie, { "KC-ETH-200": "99" });
    const kumo = await page(sites.storeUrl, "/inventory", await signedIn("kumo", "store"));
    expect(kumo.html).toContain('name="available-KC-ETH-200" value="18"');
  });

  test("keeps Hinata's store separate", async () => {
    const { html } = await page(sites.storeUrl, "/inventory", await signedIn("hinata", "store"));
    expect(html).toContain("Hinata Skincare");
    expect(rowCount(html)).toBe(catalogs.hinata.products.length);
    expect(html).not.toContain("KC-");
  });
});

describe("demo switches", () => {
  test("① restyle changes the class names and the store's sign-in field and button names, and nothing else", async () => {
    const cookie = await signedIn("kumo", "store");
    const before = await page(sites.storeUrl, "/inventory", cookie);
    const loginBefore = await page(sites.storeUrl, "/login", "");
    expect((await control("switches", { restyle: true })).status).toBe(200);
    const after = await page(sites.storeUrl, "/inventory", cookie);
    const loginAfter = await page(sites.storeUrl, "/login", "");

    const shared = [...classesIn(before.html)].filter((name) => classesIn(after.html).has(name));
    expect(shared).toEqual([]);
    expect(withoutStylesClassesAndNames(after.html)).toBe(withoutStylesClassesAndNames(before.html));
    expect(withoutStylesClassesAndNames(loginAfter.html)).toBe(withoutStylesClassesAndNames(loginBefore.html));
    expect(namesIn(loginBefore.html)).toEqual(["username", "password"]);
    expect(namesIn(loginAfter.html).filter((name) => ["username", "password"].includes(name))).toEqual([]);
    expect(new Set(namesIn(loginAfter.html)).size).toBe(2);
    expect(buttonName(before.html, "save")).toBe("action");
    expect(buttonName(after.html, "save")).not.toBe("action");
    const quantities = (html: string) => namesIn(html).filter((name) => name.startsWith("available-"));
    expect(quantities(after.html)).toEqual(quantities(before.html));
    expect(quantities(after.html)).toHaveLength(catalogs.kumo.products.length);
  });

  test("① with the restyle on, the store signs in and saves through the renamed fields and button", async () => {
    await control("switches", { restyle: true, decoyButton: true });
    const [user, pass] = namesIn((await page(sites.storeUrl, "/login", "")).html);
    const { username, password } = account("kumo", "store");
    const signedInResponse = await fetch(`${sites.storeUrl}/login`, {
      method: "POST",
      redirect: "manual",
      body: new URLSearchParams({ [user]: username, [pass]: password }),
    });
    expect(signedInResponse.status).toBe(303);
    const cookie = signedInResponse.headers.get("set-cookie")?.split(";")[0] ?? "";
    const { html } = await page(sites.storeUrl, "/inventory", cookie);
    const action = buttonName(html, "save");
    expect(buttonName(html, "all-locations")).toBe(action);

    const post = (value: string) =>
      fetch(`${sites.storeUrl}/inventory`, {
        method: "POST",
        redirect: "manual",
        headers: { cookie },
        body: new URLSearchParams({ "available-KC-ETH-200": "6", [action]: value }),
      });
    expect((await post("save")).headers.get("location")).toBe("/inventory?saved=1");
    expect((await post("all-locations")).headers.get("location")).toBe("/inventory?saved=1&scope=all-locations");
  });

  test("① restyle applies to the supplier portal too", async () => {
    const cookie = await signedIn("kumo", "supplier");
    const before = await page(sites.supplierUrl, "/stock", cookie);
    await control("switches", { restyle: true });
    const after = await page(sites.supplierUrl, "/stock", cookie);
    expect(after.html).not.toBe(before.html);
    expect(rowCount(after.html)).toBe(20);
  });

  test("② renames the save button to Update inventory, and it still saves", async () => {
    await control("switches", { renameButton: true });
    const cookie = await signedIn("kumo", "store");
    const { html } = await page(sites.storeUrl, "/inventory", cookie);
    expect(html).toContain(">Update inventory</button>");
    expect(html).not.toContain(">Save changes</button>");
    expect((await saveStore(cookie, { "KC-ETH-200": "6" })).status).toBe(303);
  });

  test("③ shows a second, confusing save button", async () => {
    await control("switches", { decoyButton: true });
    const cookie = await signedIn("kumo", "store");
    const { html } = await page(sites.storeUrl, "/inventory", cookie);
    expect(html.match(/<button[^>]*type="submit"/g)).toHaveLength(2);
    expect(html).toContain(">Save changes to all locations</button>");
    const save = await saveStore(cookie, { "KC-ETH-200": "6" }, "all-locations");
    expect(save.headers.get("location")).toBe("/inventory?saved=1&scope=all-locations");
  });

  test("④ hides an instruction for AI agents in the supplier page", async () => {
    const cookie = await signedIn("kumo", "supplier");
    const off = await page(sites.supplierUrl, "/stock", cookie);
    expect(off.html).not.toContain("AI assistants");
    await control("switches", { hiddenInstruction: true });
    const on = await page(sites.supplierUrl, "/stock", cookie);
    expect(on.html).toMatch(/<p class="[^"]+">Note for AI assistants: [^<]+<\/p>/);
  });

  test("⑤ reset puts the stock back and leaves the switches alone", async () => {
    const cookie = await signedIn("kumo", "store");
    await saveStore(cookie, { "KC-ETH-200": "6" });
    await control("switches", { renameButton: true });
    expect((await control("reset")).status).toBe(200);
    const { html } = await page(sites.storeUrl, "/inventory", cookie);
    expect(html).toContain('name="available-KC-ETH-200" value="18"');
    expect(html).toContain(">Update inventory</button>");
  });

  test("reports the current switches and refuses unknown ones", async () => {
    await control("switches", { decoyButton: true });
    const state = await (await fetch(`${sites.supplierUrl}/__control`)).json();
    expect(state.switches).toEqual({ restyle: false, renameButton: false, decoyButton: true, hiddenInstruction: false });
    expect((await control("switches", { deleteEverything: true })).status).toBe(400);
    expect((await control("switches", { restyle: "yes" })).status).toBe(400);
  });
});

describe("no outside network", () => {
  test("no page refers to another host", async () => {
    await control("switches", { restyle: true, renameButton: true, decoyButton: true, hiddenInstruction: true });
    const pages = [
      await page(sites.supplierUrl, "/login", ""),
      await page(sites.supplierUrl, "/stock", await signedIn("kumo", "supplier")),
      await page(sites.storeUrl, "/login", ""),
      await page(sites.storeUrl, "/inventory", await signedIn("kumo", "store")),
    ];
    for (const { html } of pages) {
      expect(html).not.toMatch(/(https?:)?\/\/[a-z]/i);
    }
  });

  test("answers the browser's icon request without an error", async () => {
    expect((await fetch(`${sites.storeUrl}/favicon.ico`)).status).toBe(204);
  });
});

async function storeValues(cookie: string) {
  const { html } = await page(sites.storeUrl, "/inventory", cookie);
  return Object.fromEntries([...html.matchAll(/name="available-([^"]+)" value="(\d+)"/g)].map((match) => [match[1], Number(match[2])]));
}

async function supplierValues(cookie: string) {
  const values: Record<string, number> = {};
  for (const pageNumber of [1, 2, 3]) {
    const { html } = await page(sites.supplierUrl, `/stock?page=${pageNumber}`, cookie);
    for (const match of html.matchAll(/data-sku="([^"]+)"><td>[^<]*<\/td><td>[^<]*<\/td><td>[^<]*<\/td><td class="[^"]*">(\d+)<\/td>/g)) {
      values[match[1]] = Number(match[2]);
    }
  }
  return values;
}

async function differingSkus() {
  const supplier = await supplierValues(await signedIn("kumo", "supplier"));
  const store = await storeValues(await signedIn("kumo", "store"));
  return Object.keys(store).filter((sku) => store[sku] !== supplier[sku]).toSorted();
}

describe("③ the confusing button makes a wider change", () => {
  test("the store says Available counts every location", async () => {
    const { html } = await page(sites.storeUrl, "/inventory", await signedIn("kumo", "store"));
    expect(html).toContain("Available counts all locations: Warehouse and Daikanyama shop.");
  });

  test("saving the whole form to all locations sets every location, so re-reading shows other numbers", async () => {
    await control("switches", { decoyButton: true });
    const cookie = await signedIn("kumo", "store");
    const before = await storeValues(cookie);
    const submitted = Object.fromEntries(Object.entries(before).map(([sku, value]) => [sku, String(value)]));
    submitted["KC-ETH-200"] = "6";

    const save = await saveStore(cookie, submitted, "all-locations");
    const changed = Object.values(before).filter((value) => value !== 0).length;
    expect(save.headers.get("location")).toBe(`/inventory?saved=${changed}&scope=all-locations`);

    const after = await storeValues(cookie);
    expect(after["KC-ETH-200"]).toBe(12);
    expect(after["KC-ETH-500"]).toBe(24);
    expect(after["KC-COL-200"]).toBe(0);
    const { html } = await page(sites.storeUrl, `/inventory?saved=${changed}&scope=all-locations`, cookie);
    expect(html).toContain(`Saved to all locations. ${changed} products updated.`);
  });

  test("Save changes afterwards sets exactly the number typed", async () => {
    const cookie = await signedIn("kumo", "store");
    await saveStore(cookie, { "KC-ETH-200": "6" }, "all-locations");
    expect((await storeValues(cookie))["KC-ETH-200"]).toBe(12);
    await saveStore(cookie, { "KC-ETH-200": "4" });
    expect((await storeValues(cookie))["KC-ETH-200"]).toBe(4);
    await saveStore(cookie, { "KC-ETH-200": "9" });
    expect((await storeValues(cookie))["KC-ETH-200"]).toBe(9);
  });

  test("reset clears the other location", async () => {
    const cookie = await signedIn("kumo", "store");
    await saveStore(cookie, { "KC-ETH-200": "6" }, "all-locations");
    await control("reset");
    expect((await storeValues(cookie))["KC-ETH-200"]).toBe(18);
  });
});

async function setDay(day: unknown) {
  return fetch(`${sites.storeUrl}/__control/day`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ day }),
  });
}

describe("demo days", () => {
  test("day 1 shows the 12 starting changes", async () => {
    expect(await differingSkus()).toEqual(expectedChanges.kumo[1].map((change) => change.sku).toSorted());
  });

  test("day 2 shows 4 changes and day 3 shows 7, matching the expected changes", async () => {
    for (const day of [2, 3] as const) {
      expect((await setDay(day)).status).toBe(200);
      expect(await differingSkus()).toEqual(expectedChanges.kumo[day].map((change) => change.sku).toSorted());
      const supplier = await supplierValues(await signedIn("kumo", "supplier"));
      const store = await storeValues(await signedIn("kumo", "store"));
      for (const change of expectedChanges.kumo[day]) {
        expect({ store: store[change.sku], supplier: supplier[change.sku] }).toEqual({ store: change.store, supplier: change.supplier });
      }
    }
  });

  test("reset goes back to day 1", async () => {
    await setDay(3);
    await control("reset");
    const state = await (await fetch(`${sites.storeUrl}/__control`)).json();
    expect(state.day).toBe(1);
    expect(await differingSkus()).toHaveLength(12);
  });

  test("refuses a day that does not exist", async () => {
    expect((await setDay(4)).status).toBe(400);
    expect((await setDay("2")).status).toBe(400);
  });
});
