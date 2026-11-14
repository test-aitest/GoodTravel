import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { accounts } from "../../sites/catalog";

const supplier = "http://127.0.0.1:3001";
const store = "http://127.0.0.1:3002";
const main = join(import.meta.dir, "..", "main.ts");
const target = join(import.meta.dir, "..", "..", "..", "agent", "tests", "contract", "runner");
const allOff = { restyle: false, renameButton: false, decoyButton: false, hiddenInstruction: false };
const login = (site: "supplier" | "store") => accounts.find((account) => account.client === "kumo" && account.site === site)!;
const vaultValues = {
  LP_SUPPLIER_USERNAME: login("supplier").username,
  LP_SUPPLIER_PASSWORD: login("supplier").password,
  LP_STORE_USERNAME: login("store").username,
  LP_STORE_PASSWORD: login("store").password,
};
const vaultDirectory = mkdtempSync(join(tmpdir(), "cr-record-vault-"));
const vault = join(vaultDirectory, "vault.json");
writeFileSync(vault, JSON.stringify({ version: 1, values: vaultValues }));
chmodSync(vault, 0o600);
const env = {
  PATH: process.env.PATH ?? "",
  HOME: process.env.HOME ?? "",
  CLIENTOPS_RUNNER_SOCKET: join(tmpdir(), `cr-record-${process.pid}.sock`),
  CLIENTOPS_VAULT: vault,
};

const commands: Record<string, unknown>[] = [
  { command: "open", url: `${supplier}/login`, snapshot: true },
  { command: "tree", mode: "interactive" },
  { command: "fill", target: { role: "textbox", name: "Account ID", form: "/login" }, text: "$LP_SUPPLIER_USERNAME" },
  { command: "fill", target: { role: "textbox", name: "Password", form: "/login" }, text: "$LP_SUPPLIER_PASSWORD" },
  { command: "click", target: { role: "button", name: "Sign in", form: "/login" }, waitFor: "table", snapshot: true },
  { command: "tree", mode: "full" },
  { command: "readTable", next: { role: "link", name: "Next", form: null } },
  { command: "open", url: `${store}/login` },
  { command: "fill", target: { role: "textbox", name: "Username", form: "/login" }, text: "$LP_NOT_SET" },
  { command: "fill", target: { role: "textbox", name: "Username", form: "/login" }, text: "$LP_STORE_USERNAME" },
  { command: "fill", target: { role: "textbox", name: "Password", form: "/login" }, text: "$LP_STORE_PASSWORD" },
  { command: "click", target: { role: "button", name: "Sign in", form: "/login" }, waitFor: 'form[action="/inventory"]' },
  { command: "tree", mode: "interactive" },
  { command: "readTable" },
  { command: "find", role: "button", name: "Save changes", form: "/inventory" },
  { command: "find", role: "button", name: "Update inventory", form: "/inventory" },
  { command: "fill", target: { role: "spinbutton", name: "Available for Ethiopia Guji 200g", form: "/inventory" }, text: "6", snapshot: true },
  { command: "click", target: { role: "button", name: "Save changes", form: "/inventory" }, waitFor: '[role="status"]' },
  { command: "wait", selector: '[role="status"]' },
  { command: "snapshot" },
  { command: "close" },
];

async function control(site: string, path: string, body?: unknown) {
  await fetch(`${site}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

for (const site of [supplier, store]) {
  await control(site, "/__control/switches", allOff);
  await control(site, "/__control/reset");
}

const exchanges: { command: Record<string, unknown>; exitCode: number; output: string }[] = [];
try {
  for (const command of commands) {
    const child = Bun.spawn(["bun", main, JSON.stringify(command)], { env, stdout: "pipe", stderr: "pipe" });
    const [output, , exitCode] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
    exchanges.push({ command, exitCode, output });
    console.log(`${exitCode} ${JSON.stringify(command).slice(0, 90)}`);
  }
} finally {
  for (const site of [supplier, store]) {
    await control(site, "/__control/switches", allOff);
    await control(site, "/__control/reset");
  }
  rmSync(vaultDirectory, { recursive: true, force: true });
}

const everything = exchanges.map((exchange) => exchange.output).join("\n");
for (const [name, value] of Object.entries(vaultValues)) {
  if (everything.includes(value)) throw new Error(`${name} leaked into the recording; nothing saved`);
}

const version = (await (await fetch("http://127.0.0.1:9222/json/version")).json()) as { "Lightpanda-Version": string };
mkdirSync(target, { recursive: true });
await Bun.write(
  join(target, "kumo-session.json"),
  `${JSON.stringify(
    {
      description:
        "One real runner session on this Mac: bun sandbox/runner/main.ts per command against Lightpanda serve and the demo sites (kumo, day 1, all switches off). Logins came from $LP_* placeholders resolved from a vault file (mode 600) given by CLIENTOPS_VAULT; the outputs are exactly what the runner printed.",
      observed: { date: new Date().toLocaleDateString("en-CA"), lightpanda: version["Lightpanda-Version"], recordedBy: "bun sandbox/runner/fixtures/recordSession.ts" },
      exchanges,
    },
    null,
    1,
  )}\n`,
);
console.log(`saved ${exchanges.length} exchanges`);
