import { expect, test, type Page } from "@playwright/test";
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const logins = {
  supplierUser: "kumo-supply-e2e-user",
  supplierPassword: "kumo-supply-e2e-Pass#41",
  storeUser: "kumo-store-e2e-user",
  storePassword: "kumo-store-e2e-Pass#73",
  newSupplierPassword: "kumo-supply-e2e-Pass#99",
};
const secretValues = Object.values(logins);

function mainButtons(page: Page) {
  return page.locator('button[data-variant="primary"]:visible');
}

function progress(page: Page) {
  return page.getByRole("navigation", { name: "Side navigation" }).getByRole("link", { name: /Finish setup/ });
}

async function addLogin(page: Page, site: string, username: string, password: string, isReplacing = false) {
  const verb = isReplacing ? "Replace" : "Add";
  await page.getByRole("button", { name: `${verb} login for ${site}` }).click();
  const dialog = page.getByRole("dialog", { name: `${verb} the ${site} login` });
  await expect(dialog.getByLabel("Password")).toHaveAttribute("type", "password");
  await expect(dialog.getByRole("button", { name: `${verb} login` })).toBeDisabled();
  await dialog.getByLabel("Username").fill(username);
  await dialog.getByLabel("Password").fill(password);
  const answered = page.waitForResponse((response) => response.url().endsWith("/api/clients/kumo/logins"));
  await dialog.getByRole("button", { name: `${verb} login` }).click();
  const response = await answered;
  expect(response.request().method()).toBe("POST");
  expect(response.headers()["cache-control"]).toBe("no-store");
  const answer = await response.text();
  expect(secretValues.filter((value) => answer.includes(value))).toEqual([]);
  await expect(dialog).toHaveCount(0);
}

async function openKumoSetup(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Set up Kumo Coffee's worker" })).toBeVisible();
}

test("Kumo's first-time setup: its computer, logins that are never shown again, the Claude switch, then a first job", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await openKumoSetup(page);
  await expect(progress(page)).toContainText("0 of 3");
  await expect(page.getByText("0 of 3")).toBeVisible();
  await expect(mainButtons(page)).toHaveText(["Create its computer"]);

  await page.getByRole("button", { name: "Create its computer" }).click();
  await expect(progress(page)).toContainText("1 of 3");
  await expect(page.getByText("1 of 3")).toBeVisible();
  await expect(page.getByText("Maruyama Supply portal")).toBeVisible();
  await expect(page.getByText("Kumo store admin")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add login for Maruyama Supply portal" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add login for Kumo store admin" })).toBeVisible();
  await expect(mainButtons(page)).toHaveText(["Add login"]);
  const useClaude = page.getByRole("switch", { name: /Use Claude for new steps/ });
  await expect(useClaude).toBeChecked();

  await addLogin(page, "Maruyama Supply portal", logins.supplierUser, logins.supplierPassword);
  await expect(page.getByText("Added", { exact: true })).toHaveCount(1);
  await expect(progress(page)).toContainText("1 of 3");
  await expect(mainButtons(page)).toHaveText(["Add login"]);

  await addLogin(page, "Kumo store admin", logins.storeUser, logins.storePassword);
  await expect(page.getByText("Added", { exact: true })).toHaveCount(2);
  await expect(progress(page)).toContainText("2 of 3");
  await expect(mainButtons(page)).toHaveText(["Write the first job"]);

  await addLogin(page, "Maruyama Supply portal", "kumo-supply-e2e-user-2", logins.newSupplierPassword, true);
  await expect(page.getByText("Added", { exact: true })).toHaveCount(2);
  const afterReplace = await page.request.get("/api/clients/kumo/setup");
  expect((await afterReplace.json()).logins).toEqual({ supplier: true, store: true });
  const shown = (await page.locator("body").innerText()) + (await page.content());
  expect(secretValues.filter((value) => shown.includes(value))).toEqual([]);

  const saved = page.waitForResponse((reply) => reply.url().endsWith("/api/clients/kumo/settings") && reply.ok());
  await useClaude.click();
  await saved;
  await expect(useClaude).not.toBeChecked();
  const dataDir = process.env.CLIENTOPS_E2E_DATA_DIR ?? "";
  expect(JSON.parse(readFileSync(join(dataDir, "settings.json"), "utf8"))).toEqual({ version: 1, clients: { kumo: { useClaude: false } } });
  expect(statSync(join(dataDir, "settings.json")).mode & 0o777).toBe(0o600);
  expect(statSync(dataDir).mode & 0o777).toBe(0o700);
  const fromServer = await page.request.get("/api/clients/kumo/setup");
  expect(await fromServer.json()).toEqual({ clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: false });

  await page.getByRole("button", { name: "Write the first job" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "What should Kumo Coffee's worker do?" })).toBeVisible();
  await expect(progress(page)).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "What should Kumo Coffee's worker do?" })).toBeVisible();
  await expect(progress(page)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("closing a login dialog keeps nothing that was typed", async ({ page }) => {
  await openKumoSetup(page);
  await page.getByRole("button", { name: "Create its computer" }).click();
  await page.getByRole("button", { name: "Add login for Maruyama Supply portal" }).click();
  const dialog = page.getByRole("dialog", { name: "Add the Maruyama Supply portal login" });
  await dialog.getByLabel("Password").fill(logins.supplierPassword);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);

  await page.getByRole("button", { name: "Add login for Maruyama Supply portal" }).click();
  await expect(page.getByRole("dialog", { name: "Add the Maruyama Supply portal login" }).getByLabel("Password")).toHaveValue("");
  await expect(page.getByText("Added", { exact: true })).toHaveCount(0);
});

test("Sora cannot have a computer, and the screen says so with nothing to press", async ({ page }) => {
  await openKumoSetup(page);
  await page.getByRole("link", { name: /Sora Outdoor/ }).click();
  await expect(page.getByText("Sora Outdoor can't have its own computer yet")).toBeVisible();
  await expect(page.getByText("Its supplier and store sites aren't connected to ClientOps.")).toBeVisible();
  await expect(progress(page)).toHaveCount(0);
  await expect(mainButtons(page)).toHaveCount(0);
});

test("the setup routes answer only the ClientOps page, never from a cache", async ({ page }) => {
  await openKumoSetup(page);
  const read = await page.request.get("/api/clients/kumo/setup");
  expect(read.headers()["cache-control"]).toBe("no-store");
  expect((await page.request.get("/api/clients/acme/setup")).status()).toBe(404);

  const elsewhere = { headers: { origin: "http://evil.example", "content-type": "application/json" } };
  expect((await page.request.post("/api/clients/kumo/computer", elsewhere)).status()).toBe(403);
  expect((await page.request.post("/api/clients/kumo/logins", { ...elsewhere, data: { supplier: {} } })).status()).toBe(403);
  expect((await page.request.post("/api/clients/kumo/logins/check", elsewhere)).status()).toBe(403);
  expect((await page.request.put("/api/clients/kumo/settings", { ...elsewhere, data: { useClaude: false } })).status()).toBe(403);
});
