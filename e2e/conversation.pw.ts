import { expect, test } from "@playwright/test";
import { setUpKumo, slow, startStockSync } from "./helpers";

test("a conversation: questions get answers with the job's values, a request to run starts the job in the same conversation, and nothing earlier disappears", async ({ page }) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await setUpKumo(page);
  await startStockSync(page);
  await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible(slow);

  const message = page.getByRole("textbox", { name: "Message the worker" });
  await message.fill("How many products changed?");
  await message.press("Enter");
  await expect(page.getByText("12 products changed:")).toBeVisible();
  await expect(page.getByText("Ethiopia Guji 200g: 18 → 6")).toBeVisible();
  await expect(page.getByText("Sync today's supplier stock to the store admin")).toBeVisible();
  await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible();

  await message.fill("Write a poem about coffee");
  await message.press("Enter");
  await expect(page.getByText(/^I can't do that here\./)).toBeVisible();

  await page.getByRole("group", { name: "Save 12 stock changes to the store admin?" }).getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Done. 12 products updated, and each one checked.")).toBeVisible(slow);

  await message.fill("Run it again");
  await message.press("Enter");
  await expect(page.getByText("Run it again", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible(slow);
  await expect(page.getByText("12 products changed:")).toBeVisible();
  await expect(page.getByText("Done. 12 products updated, and each one checked.")).toBeVisible();

  const recent = page.getByRole("navigation", { name: "Side navigation" }).getByRole("link", { name: "Supplier stock sync" });
  await expect(recent).toHaveCount(1);
  expect(errors).toEqual([]);
});
