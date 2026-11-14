import { expect, test, type Locator } from "@playwright/test";

type Box = { x: number; y: number; width: number; height: number };

function overlaps(a: Box, b: Box) {
  return a.x < b.x + b.width - 1 && b.x < a.x + a.width - 1 && a.y < b.y + b.height - 1 && b.y < a.y + a.height - 1;
}

async function boxOf(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  return box!;
}

test("the opening waits for the sky: tapping anywhere else does nothing", async ({ page }) => {
  await page.goto("/travel?mode=playback");
  const opening = page.getByRole("heading", { name: "Every trip begins long before you board." });
  await expect(opening).toBeVisible();
  for (const [x, y] of [
    [120, 120],
    [640, 320],
    [1100, 560],
  ] as const) {
    await page.mouse.click(x, y);
  }
  await page.waitForTimeout(800);
  await expect(opening).toBeVisible();
  await expect(page.getByRole("button", { name: /^Singapore,/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Talk to the worker" }).click();
  await expect(page.getByPlaceholder("or type it")).toBeVisible();
});

for (const viewport of [
  { width: 1280, height: 720 },
  { width: 1440, height: 810 },
  { width: 1920, height: 1080 },
]) {
  test(`on a city, its name, the guide, the guide's photos and the rides never cover each other at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/travel?mode=playback");
    await page.getByRole("button", { name: "Talk to the worker" }).click();
    const typed = page.getByPlaceholder("or type it");
    await typed.fill("Singapore");
    await typed.press("Enter");
    await expect(page.getByRole("button", { name: /JPY/ }).first()).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(1200);
    const name = await boxOf(page.locator("h1").filter({ hasText: "Singapore" }));
    const guide = await boxOf(page.getByRole("img", { name: "Your local guide in Singapore" }));
    const photos = await boxOf(page.getByRole("list", { name: "Highlights of Singapore" }));
    const rides = await Promise.all((await page.getByRole("button", { name: /JPY/ }).all()).map(boxOf));
    expect(overlaps(name, photos)).toBe(false);
    expect(overlaps(name, guide)).toBe(false);
    expect(overlaps(photos, guide)).toBe(false);
    for (const ride of rides) expect(overlaps(photos, ride)).toBe(false);
  });
}
