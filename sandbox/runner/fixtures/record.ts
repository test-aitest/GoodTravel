import { join } from "node:path";
import { accounts } from "../../sites/catalog";
import { BrowserTab } from "../cdp";

const cdpUrl = process.env.LIGHTPANDA_CDP_WS ?? "ws://127.0.0.1:9222/";
const supplier = "http://127.0.0.1:3001";
const store = "http://127.0.0.1:3002";
const allOff = { restyle: false, renameButton: false, decoyButton: false, hiddenInstruction: false };

async function control(site: string, path: string, body?: unknown) {
  const response = await fetch(`${site}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path} answered ${response.status}`);
}

async function lightpandaVersion() {
  const response = await fetch(`${cdpUrl.replace("ws://", "http://").replace(/\/$/, "")}/json/version`);
  return ((await response.json()) as { "Lightpanda-Version": string })["Lightpanda-Version"];
}

const version = await lightpandaVersion();

async function save(name: string, tab: BrowserTab, parts: { text?: boolean; interactiveText?: boolean; html?: boolean }) {
  const { url, title } = await tab.location();
  const record: Record<string, unknown> = {
    observed: { date: new Date().toLocaleDateString("en-CA"), lightpanda: version, url, title },
    jsonTree: ((await tab.send("LP.getSemanticTree", {})) as { semanticTree: unknown }).semanticTree,
  };
  if (parts.text) record.text = ((await tab.send("LP.getSemanticTree", { format: "text" })) as { semanticTree: string }).semanticTree;
  if (parts.interactiveText) {
    record.interactiveText = (
      (await tab.send("LP.getSemanticTree", { format: "text", interactiveOnly: true })) as { semanticTree: string }
    ).semanticTree;
  }
  if (parts.html) record.html = ((await tab.send("LP.dump", { format: "html", strip: { js: true } })) as { content: string }).content;
  await Bun.write(join(import.meta.dir, `${name}.json`), `${JSON.stringify(record, null, 1)}\n`);
  console.log(`saved ${name}.json`);
}

async function goTo(tab: BrowserTab, url: string) {
  await tab.send("Page.navigate", { url });
  await tab.send("LP.waitUntil", { until: "load" });
}

async function signIn(tab: BrowserTab, site: "supplier" | "store") {
  const account = accounts.find((candidate) => candidate.client === "kumo" && candidate.site === site);
  if (!account) throw new Error(`No kumo ${site} account`);
  await goTo(tab, `${site === "supplier" ? supplier : store}/login`);
  const { elements } = (await tab.send("LP.getInteractiveElements")) as {
    elements: { backendNodeId: number; role: string; name: string }[];
  };
  const byName = (name: string) => elements.find((element) => element.name === name)?.backendNodeId;
  const [user, password, submit] =
    site === "supplier" ? [byName("Account ID"), byName("Password"), byName("Sign in")] : [byName("Username"), byName("Password"), byName("Sign in")];
  await tab.send("LP.fillNode", { backendNodeId: user, text: account.username });
  await tab.send("LP.fillNode", { backendNodeId: password, text: account.password });
  await tab.send("LP.clickNode", { backendNodeId: submit });
  await tab.send("LP.waitForSelector", { selector: site === "supplier" ? "table" : 'form[action="/inventory"]' });
}

for (const site of [supplier, store]) {
  await control(site, "/__control/reset");
  await control(site, "/__control/switches", allOff);
}

const tab = await BrowserTab.open(cdpUrl);
try {
  await goTo(tab, `${store}/login`);
  await save("store-login", tab, { text: true, interactiveText: true, html: true });
  await signIn(tab, "store");
  await save("store-inventory", tab, { interactiveText: true, html: true });
  await control(store, "/__control/switches", { decoyButton: true });
  await goTo(tab, `${store}/inventory`);
  await save("store-inventory-decoy", tab, {});
  await control(store, "/__control/switches", allOff);

  await signIn(tab, "supplier");
  await save("supplier-stock-1", tab, { text: true, html: true });
  await goTo(tab, `${supplier}/stock?page=2`);
  await save("supplier-stock-2", tab, {});
  await goTo(tab, `${supplier}/stock?page=3`);
  await save("supplier-stock-3", tab, {});
  await control(supplier, "/__control/switches", { hiddenInstruction: true });
  await goTo(tab, `${supplier}/stock`);
  await save("supplier-stock-hidden-note", tab, { html: true });
} finally {
  await tab.close();
  for (const site of [supplier, store]) {
    await control(site, "/__control/switches", allOff);
    await control(site, "/__control/reset");
  }
}
