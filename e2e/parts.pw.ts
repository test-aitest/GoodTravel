import { expect, test } from "@playwright/test";

const parts = [
  "1 · Composer",
  "2 · Your message",
  "3 · Worker message",
  "4 · Progress card",
  "5 · Steps",
  "6 · Approval card",
  "7 · Result line",
  "The worker mark",
];

test("the parts page shows all seven parts and the worker mark without errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto("/dev/parts");

  for (const name of parts) {
    await expect(page.getByRole("region", { name })).toBeVisible();
  }
  await expect(page.getByRole("textbox", { name: "Describe a job" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Job progress" })).toHaveAttribute("aria-valuenow", "2");
  await expect(page.getByRole("list", { name: "Steps" }).getByRole("listitem")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "Save changes" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Not allowed" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("the frame shows the sidebar, the conversation and the Activity panel", async ({ page }) => {
  await page.goto("/dev/frame");

  await expect(page.getByRole("link", { name: /New job/ })).toBeVisible();
  await expect(page.getByLabel("1 waiting for your approval")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Supplier stock sync" })).toBeVisible();
  const activity = page.getByRole("complementary", { name: "Activity" });
  await expect(activity).toBeVisible();
  await activity.getByRole("button", { name: "Close activity" }).click();
  await expect(activity).toBeHidden();
  await page.getByRole("button", { name: "Show activity" }).click();
  await expect(activity).toBeVisible();
});
