import { expect, test, type Page } from "@playwright/test";
import { setUpKumo, slow, startStockSync } from "./helpers";

type Drawn = { motion: string | null; text: string; opacity: number; width: number };

async function watchMotion(page: Page) {
  await page.addInitScript(() => {
    const log: Drawn[] = [];
    Object.assign(window, { motionLog: log });
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (!(node instanceof Element)) continue;
          const found = [...(node.matches("[data-motion]") ? [node] : []), ...node.querySelectorAll("[data-motion]")];
          for (const element of found) {
            requestAnimationFrame(() =>
              log.push({
                motion: element.getAttribute("data-motion"),
                text: (element.textContent ?? "").trim().slice(0, 80),
                opacity: Number(getComputedStyle(element).opacity),
                width: Math.round(element.getBoundingClientRect().width),
              }),
            );
          }
        }
      }
    }).observe(document, { childList: true, subtree: true });
  });
}

const drawn = (page: Page) => page.evaluate(() => (window as unknown as { motionLog: Drawn[] }).motionLog.slice());
const forget = (page: Page) => page.evaluate(() => (window as unknown as { motionLog: Drawn[] }).motionLog.splice(0));

function widthsAfterClicking(page: Page, button: string, target: string) {
  return page.evaluate(
    async (asked) => {
      const element = document.querySelector(asked.target);
      document.querySelector<HTMLButtonElement>(`button[aria-label="${asked.button}"]`)?.click();
      const widths: number[] = [];
      for (let frame = 0; frame < 12; frame += 1) {
        await new Promise((done) => requestAnimationFrame(done));
        widths.push(element?.isConnected ? Math.round(element.getBoundingClientRect().width) : -1);
      }
      return widths;
    },
    { button, target },
  );
}

const between = (widths: number[], low: number, high: number) => widths.some((width) => width > low && width < high);

test.describe("motion", () => {
  test("a message that arrives rises into the conversation, and nothing moves while the page first draws", async ({ page }) => {
    test.setTimeout(120_000);
    await watchMotion(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "Set up Kumo Coffee's worker" })).toBeVisible();
    expect((await drawn(page)).filter((entry) => entry.opacity < 1)).toEqual([]);

    await setUpKumo(page);
    await startStockSync(page);
    await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible(slow);

    const arrived = (await drawn(page)).filter((entry) => entry.motion === "enter" && entry.text.includes("Save 12 stock changes to the store admin?"));
    expect(arrived.some((entry) => entry.opacity < 1)).toBe(true);
  });

  test("switching to a new job and back fades the whole view and does not replay each message", async ({ page }) => {
    test.setTimeout(120_000);
    await watchMotion(page);
    await setUpKumo(page);
    await startStockSync(page);
    await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible(slow);

    await forget(page);
    await page.getByRole("link", { name: "New job" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "What should Kumo Coffee's worker do?" })).toBeVisible();
    await page.getByRole("link", { name: "Supplier stock sync" }).click();
    await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible();

    const switched = await drawn(page);
    expect(switched.filter((entry) => entry.motion === "view" && entry.opacity < 1).length).toBeGreaterThanOrEqual(2);
    expect(switched.filter((entry) => entry.motion === "enter" && entry.opacity < 1)).toEqual([]);
  });

  test("the side panel slides open from the edge and slides closed before it goes", async ({ page }) => {
    test.setTimeout(120_000);
    await watchMotion(page);
    await setUpKumo(page);
    await startStockSync(page);
    const activity = page.getByRole("complementary", { name: "Activity" });
    await expect(activity).toBeVisible();
    expect((await drawn(page)).some((entry) => entry.motion === "panel" && entry.width < 400)).toBe(true);
    await expect.poll(async () => Math.round((await activity.boundingBox())?.width ?? 0)).toBe(400);

    const closing = await widthsAfterClicking(page, "Close activity", "[data-motion=panel]");
    expect(between(closing, 0, 400)).toBe(true);
    await expect(activity).toHaveCount(0);
  });

  test("the sidebar slides closed and open again", async ({ page }) => {
    await watchMotion(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "Set up Kumo Coffee's worker" })).toBeVisible();

    const closing = await widthsAfterClicking(page, "Close sidebar", "[data-motion=sidebar]");
    expect(between(closing, 0, 272)).toBe(true);
    await expect(page.getByRole("button", { name: "Open sidebar" })).toBeVisible();
    await expect.poll(async () => Math.round((await page.locator("[data-motion=sidebar]").boundingBox())?.width ?? -1)).toBe(0);

    const opening = await widthsAfterClicking(page, "Open sidebar", "[data-motion=sidebar]");
    expect(between(opening, 0, 272)).toBe(true);
    await expect(page.getByRole("button", { name: "Close sidebar" })).toBeVisible();
  });

  test("with Reduce Motion on, everything changes at once", async ({ page }) => {
    test.setTimeout(120_000);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await watchMotion(page);
    await setUpKumo(page);
    await startStockSync(page);
    await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible(slow);

    const entries = await drawn(page);
    expect(entries.filter((entry) => entry.opacity < 1)).toEqual([]);
    expect(entries.filter((entry) => entry.motion === "panel" && entry.width < 400)).toEqual([]);
    const closing = await widthsAfterClicking(page, "Close sidebar", "[data-motion=sidebar]");
    expect(closing[0]).toBe(0);
  });
});
