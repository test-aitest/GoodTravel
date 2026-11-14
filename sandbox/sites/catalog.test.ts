import { describe, expect, test } from "bun:test";
import { accounts, catalogs, demoDays, expectedChanges, stockDifferences, stockOnDay, type DemoDay } from "./catalog";

describe("Kumo Coffee Roasters catalog", () => {
  const kumo = catalogs.kumo;

  test("has 42 products with unique SKUs, the same set at the supplier and the store", () => {
    const skus = kumo.products.map((product) => product.sku);
    expect(skus).toHaveLength(42);
    expect(new Set(skus).size).toBe(42);
    expect(Object.keys(kumo.supplierAvailable).toSorted()).toEqual(skus.toSorted());
    expect(Object.keys(kumo.storeAvailable).toSorted()).toEqual(skus.toSorted());
  });

  test("starts with exactly 12 differences, 3 of them sold out at the supplier", () => {
    const differences = stockDifferences(kumo.products, kumo.supplierAvailable, kumo.storeAvailable);
    expect(differences).toHaveLength(12);
    expect(differences.filter((difference) => difference.supplier === 0)).toHaveLength(3);
  });

  test("matches the rows shown on the design canvas", () => {
    const differences = stockDifferences(kumo.products, kumo.supplierAvailable, kumo.storeAvailable);
    const bySku = new Map(differences.map((difference) => [difference.sku, difference]));
    expect(bySku.get("KC-ETH-200")).toMatchObject({ name: "Ethiopia Guji 200g", store: 18, supplier: 6 });
    expect(bySku.get("KC-COL-200")).toMatchObject({ name: "Colombia Huila 200g", store: 0, supplier: 24 });
    expect(bySku.get("KC-KEN-200")).toMatchObject({ name: "Kenya Nyeri 200g", store: 9, supplier: 0 });
    expect(bySku.get("KC-GUA-500")).toMatchObject({ name: "Guatemala Antigua 500g", store: 4, supplier: 15 });
  });

  test("only the 3 differing products are sold out at the supplier", () => {
    expect(Object.values(kumo.supplierAvailable).filter((available) => available === 0)).toHaveLength(3);
  });
});

describe("Hinata Skincare catalog", () => {
  test("has its own products that never overlap Kumo's", () => {
    const kumoSkus = new Set(catalogs.kumo.products.map((product) => product.sku));
    const hinata = catalogs.hinata;
    expect(hinata.products.length).toBeGreaterThan(0);
    expect(hinata.products.some((product) => kumoSkus.has(product.sku))).toBe(false);
  });

  test("has differences of its own, including a sold out product", () => {
    const hinata = catalogs.hinata;
    const differences = stockDifferences(hinata.products, hinata.supplierAvailable, hinata.storeAvailable);
    expect(differences.length).toBeGreaterThan(0);
    expect(differences.some((difference) => difference.supplier === 0)).toBe(true);
  });
});

describe("accounts", () => {
  test("each client has its own supplier login and store login", () => {
    for (const client of ["kumo", "hinata"] as const) {
      expect(accounts.filter((account) => account.client === client && account.site === "supplier")).toHaveLength(1);
      expect(accounts.filter((account) => account.client === client && account.site === "store")).toHaveLength(1);
    }
    const keys = accounts.map((account) => `${account.site}:${account.username}`);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe("later demo days", () => {
  test("Kumo has 12 changes on day 1 (3 sold out), 4 on day 2 and 7 on day 3", () => {
    expect(expectedChanges.kumo[1]).toHaveLength(12);
    expect(expectedChanges.kumo[1].filter((change) => change.supplier === 0)).toHaveLength(3);
    expect(expectedChanges.kumo[2]).toHaveLength(4);
    expect(expectedChanges.kumo[3]).toHaveLength(7);
  });

  test("day 1 changes are the starting differences", () => {
    const kumo = catalogs.kumo;
    expect(expectedChanges.kumo[1]).toEqual(stockDifferences(kumo.products, kumo.supplierAvailable, kumo.storeAvailable));
  });

  test("each later day starts from a store already synced to the previous day", () => {
    for (const day of [2, 3] as const) {
      expect(stockOnDay("kumo", day).storeAvailable).toEqual(stockOnDay("kumo", (day - 1) as DemoDay).supplierAvailable);
    }
  });

  test("each change names the SKU, the product and both values", () => {
    for (const day of demoDays) {
      const { supplierAvailable, storeAvailable } = stockOnDay("kumo", day);
      for (const change of expectedChanges.kumo[day]) {
        expect(catalogs.kumo.products.find((product) => product.sku === change.sku)?.name).toBe(change.name);
        expect(change.store).toBe(storeAvailable[change.sku]);
        expect(change.supplier).toBe(supplierAvailable[change.sku]);
        expect(change.store).not.toBe(change.supplier);
      }
    }
  });

  test("Hinata's stock does not move on later days", () => {
    expect(expectedChanges.hinata[2]).toEqual([]);
    expect(expectedChanges.hinata[3]).toEqual([]);
  });
});
