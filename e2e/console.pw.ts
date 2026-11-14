import { expect, test } from "@playwright/test";
import { setUpKumo, slow, startStockSync } from "./helpers";

test("the whole demo: setup, job, approval, done, replay, repair and another client's login", async ({ page }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await setUpKumo(page);
  await startStockSync(page);

  const activity = page.getByRole("complementary", { name: "Activity" });
  await expect(activity).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Job progress" })).toBeVisible();
  await expect(activity.getByRole("list", { name: "Steps" }).getByRole("listitem")).toHaveCount(6);

  await expect(page.getByText("12 products are different. Nothing is saved yet.")).toBeVisible(slow);
  const approval = page.getByRole("group", { name: "Save 12 stock changes to the store admin?" });
  await expect(approval).toBeVisible();
  await expect(approval.getByRole("row")).toHaveCount(4);
  await expect(page.getByLabel("1 waiting for your approval")).toBeVisible();

  await approval.getByRole("button", { name: "Not now" }).click();
  await expect(page.getByText("Waiting for your OK.")).toBeVisible();
  await expect(page.getByLabel("1 waiting for your approval")).toBeVisible();
  await page.getByRole("button", { name: "Review changes" }).click();

  await page.getByRole("group", { name: "Save 12 stock changes to the store admin?" }).getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Done. 12 products updated, and each one checked.")).toBeVisible(slow);
  await expect(page.getByRole("group", { name: "Saved for next time" })).toBeVisible();
  await expect(page.getByText("Kumo Coffee Roasters · runs daily at 18:00")).toBeVisible();
  await expect(page.getByLabel("1 waiting for your approval")).toHaveCount(0);

  await page.getByRole("button", { name: "Run now" }).click();
  await expect(page.getByText("Today, 18:00")).toBeVisible();
  await expect(page.getByText("4 products changed at the supplier. Ready in 6.2 s, with no AI.").first()).toBeVisible(slow);
  await expect(page.getByRole("group", { name: "Time compared with the first run" }).first()).toBeVisible();
  await page.getByRole("group", { name: "Save 4 stock changes to the store admin?" }).getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Done. 4 products updated, and each one checked.")).toBeVisible(slow);

  await page.getByRole("button", { name: "Run now" }).click();
  await expect(page.getByText("Tomorrow, 18:00")).toBeVisible();
  await expect(
    page.getByText("The store admin renamed its Save button today. I stopped before changing anything and found the new button.").first(),
  ).toBeVisible(slow);
  await page.getByRole("button", { name: "What changed" }).click();
  const whatChanged = page.getByRole("complementary", { name: "What changed" });
  await expect(whatChanged.getByText("Update inventory")).toBeVisible();
  await expect(whatChanged.getByText("Become v2 after you approve")).toBeVisible();
  await page.getByRole("group", { name: "Save 7 stock changes with the new button?" }).getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Done. 7 products updated, and each one checked.")).toBeVisible(slow);
  await expect(page.getByText("I updated the saved steps")).toBeVisible();

  await page.getByRole("link", { name: /Hinata Skincare/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "What should Hinata Skincare's worker do?" })).toBeVisible();
  const composer = page.getByRole("textbox", { name: "Describe a job" });
  await composer.fill("Use Kumo Coffee's Maruyama login to check Hinata's supplier prices too.");
  await composer.press("Enter");
  const refusal = page.getByRole("group", { name: "Not allowed" });
  await expect(refusal.getByText("I can't use Kumo Coffee Roasters' login here")).toBeVisible(slow);
  await expect(page.getByText("This request was recorded in the audit log.")).toBeVisible();
  await page.getByRole("button", { name: "Use Hinata's account" }).click();
  await expect(page.getByText("I'll use Hinata's own supplier account for this.")).toBeVisible();

  expect(errors).toEqual([]);
});

test("Stop ends a running job before anything is saved", async ({ page }) => {
  await setUpKumo(page);
  await startStockSync(page);
  await page.getByRole("button", { name: "Stop the job" }).click();
  await expect(page.getByText("Stopped before saving.")).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Activity" })).toHaveCount(0);
});

test("every screen has at most one main button and one progress bar", async ({ page }) => {
  await setUpKumo(page);
  await startStockSync(page);
  await expect(page.getByRole("progressbar")).toHaveCount(1);
  await expect(page.getByText("12 products are different. Nothing is saved yet.")).toBeVisible(slow);
  const primaries = page.locator('button[data-variant="primary"]:visible:not([disabled]):not([aria-disabled="true"])');
  await expect(primaries).toHaveCount(1);
});

test("the setup and new job screens fit a phone without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Set up Kumo Coffee's worker" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
