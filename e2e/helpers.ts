import { expect, type Page } from "@playwright/test";

export const slow = { timeout: 30_000 };

export async function setUpKumo(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Set up Kumo Coffee's worker" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Side navigation" }).getByRole("link", { name: /Finish setup/ })).toContainText("0 of 3");
  await page.getByRole("button", { name: "Create its computer" }).click();
  for (const site of ["Maruyama Supply portal", "Kumo store admin"]) {
    await page.getByRole("button", { name: `Add login for ${site}` }).click();
    const login = page.getByRole("dialog", { name: `Add the ${site} login` });
    await login.getByLabel("Username").fill("demo-user");
    await login.getByLabel("Password").fill("demo-pass");
    await login.getByRole("button", { name: "Add login" }).click();
    await expect(login).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Write the first job" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "What should Kumo Coffee's worker do?" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Side navigation" }).getByRole("link", { name: /Finish setup/ })).toHaveCount(0);
}

export async function startStockSync(page: Page) {
  await page.getByText("Sync today's supplier stock to the store admin").click();
  await expect(page.getByRole("heading", { level: 1, name: "Supplier stock sync" })).toBeVisible();
}
