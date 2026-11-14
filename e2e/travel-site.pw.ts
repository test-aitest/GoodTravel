import { expect, test, type Page } from "@playwright/test";
import { accounts } from "../sandbox/sites/catalog";

const travelUrl = "http://127.0.0.1:3003";

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ request }) => {
  expect((await request.post(`${travelUrl}/reset`)).ok()).toBe(true);
});

function watchOutsideRequests(page: Page) {
  const outside: string[] = [];
  page.on("request", (request) => {
    const { hostname } = new URL(request.url());
    if (hostname !== "127.0.0.1" && hostname !== "localhost") outside.push(request.url());
  });
  return outside;
}

async function signIn(page: Page) {
  const account = accounts.find((candidate) => candidate.client === "hinata" && candidate.site === "travel");
  if (!account) throw new Error("no Hinata travel account");
  await page.goto(`${travelUrl}/login`);
  await page.getByRole("textbox", { name: "Username" }).fill(account.username);
  await page.getByRole("textbox", { name: "Password" }).fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Find a trip" })).toBeVisible();
}

test("search each destination and read the options by their names", async ({ page }) => {
  const outside = watchOutsideRequests(page);
  await signIn(page);
  const expected = {
    Kyoto: ["Nozomi 21", "Nozomi 27"],
    Seoul: ["Kestrel Air KA 712", "Northstar NS 104"],
    Singapore: ["Kestrel Air KA 631", "Meridian MR 12"],
  };
  for (const [to, names] of Object.entries(expected)) {
    await page.goto(`${travelUrl}/search?from=Tokyo&to=${to}&date=2026-11-21`);
    await expect(page.getByRole("heading", { level: 1, name: `Trips to ${to}` })).toBeVisible();
    const options = page.getByRole("list", { name: "Options" });
    await expect(options.getByRole("listitem")).toHaveCount(names.length);
    for (const name of names) {
      await expect(options.getByRole("heading", { name, exact: true })).toBeVisible();
      await expect(options.getByRole("link", { name: `Choose seats for ${name}` })).toBeVisible();
    }
  }
  await expect(page.getByRole("list", { name: "Options" }).getByText("Flight · 08:05 → 14:35 · JPY 72,800")).toBeVisible();
  expect(outside).toEqual([]);
});

test("the KA 631 seat map names every seat, and taken seats are disabled", async ({ page }) => {
  await signIn(page);
  await page.goto(`${travelUrl}/search?from=Tokyo&to=Singapore&date=2026-11-21`);
  await page.getByRole("link", { name: "Choose seats for Kestrel Air KA 631" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Seats on Kestrel Air KA 631" })).toBeVisible();

  const map = page.getByRole("form", { name: "Seat map" });
  await expect(map.getByRole("button", { name: /^Seat \d+[A-F], / })).toHaveCount(180);
  await expect(map.getByRole("button", { name: "Seat 12A, window, exit row, taken" })).toBeDisabled();
  await expect(map.getByRole("button", { name: "Seat 12C, aisle, exit row, available" })).toBeEnabled();
  await expect(map.getByRole("button", { name: "Seat 2B, middle, available" })).toBeEnabled();
  await expect(map.getByRole("button", { name: /, taken$/ })).toHaveCount(60);
});

test("hold, review and confirm a seat on KA 631, then see the reference", async ({ page }) => {
  const outside = watchOutsideRequests(page);
  await signIn(page);
  await page.goto(`${travelUrl}/seats?option=ka-631`);
  await page.getByRole("button", { name: "Seat 12C, aisle, exit row, available" }).click();

  await expect(page.getByRole("button", { name: "Seat 12C, aisle, exit row, available" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("textbox", { name: "Full name" }).fill("Aoi Tanaka");
  await page.getByRole("textbox", { name: "Email" }).fill("aoi@hinata-skincare.example");
  await page.getByRole("button", { name: "Hold seat" }).click();

  await expect(page).toHaveURL(/\/review\?hold=[a-z0-9]+$/);
  await expect(page.getByRole("heading", { level: 1, name: "Review your booking" })).toBeVisible();
  const summary = page.getByRole("region", { name: "Booking summary" });
  await expect(summary).toContainText("Kestrel Air KA 631 · Flight · Sat, 21 Nov 2026");
  await expect(summary).toContainText("Seat 12C, aisle, exit row");
  await expect(summary).toContainText("Total JPY 72,800");
  await expect(page.getByText("Aoi Tanaka")).toHaveCount(0);
  await page.getByRole("button", { name: "Confirm booking" }).click();

  await expect(page).toHaveURL(/\/booked\?ref=[A-Z]{6}$/);
  await expect(page.getByRole("heading", { level: 1, name: "Booking confirmed" })).toBeVisible();
  const reference = new URL(page.url()).searchParams.get("ref");
  await expect(page.getByText(`Reference ${reference}`)).toBeVisible();

  await page.goto(`${travelUrl}/seats?option=ka-631`);
  await expect(page.getByRole("button", { name: "Seat 12C, aisle, exit row, taken" })).toBeDisabled();
  expect(outside).toEqual([]);
});

test("reset frees the booked seat again", async ({ page, request }) => {
  await signIn(page);
  await page.goto(`${travelUrl}/seats?option=ka-631&seat=12D`);
  await page.getByRole("textbox", { name: "Full name" }).fill("Aoi Tanaka");
  await page.getByRole("textbox", { name: "Email" }).fill("aoi@hinata-skincare.example");
  await page.getByRole("button", { name: "Hold seat" }).click();
  await page.getByRole("button", { name: "Confirm booking" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Booking confirmed" })).toBeVisible();

  expect((await request.post(`${travelUrl}/reset`)).ok()).toBe(true);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
  await page.goto(`${travelUrl}/seats?option=ka-631`);
  await expect(page.getByRole("button", { name: "Seat 12D, aisle, exit row, available" })).toBeEnabled();
});
