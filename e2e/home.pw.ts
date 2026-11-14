import { expect, test } from "@playwright/test";

test("the first screen is the client's setup, with the Astryx theme applied", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto("/");

  await expect(page).toHaveTitle("ClientOps");
  await expect(page.getByRole("heading", { level: 1, name: "Set up Kumo Coffee's worker" })).toBeVisible();
  await expect(page.locator(".astryx-heading").first()).toBeVisible();
  expect(errors).toEqual([]);
});
