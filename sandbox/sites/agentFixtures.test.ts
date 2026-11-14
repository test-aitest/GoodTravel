import { describe, expect, test } from "bun:test";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { expectedChanges } from "./catalog";
import { agentFixture, fixtureCases, fixtureDirectory, fixtureFileName, fixtureJson } from "./agentFixtures";

describe("agent fixtures", () => {
  test("cover Kumo days 1 to 3 and Hinata day 1", () => {
    expect(fixtureCases).toEqual([
      { client: "kumo", day: 1 },
      { client: "kumo", day: 2 },
      { client: "kumo", day: 3 },
      { client: "hinata", day: 1 },
    ]);
  });

  test("carry every product row from both sites and the catalog's expected changes", () => {
    const kumoDay1 = agentFixture("kumo", 1);
    expect(kumoDay1.supplier).toHaveLength(42);
    expect(kumoDay1.store).toHaveLength(42);
    expect(kumoDay1.supplier[0]).toEqual({ sku: "KC-ETH-200", name: "Ethiopia Guji 200g", available: 6 });
    expect(kumoDay1.store[0]).toEqual({ sku: "KC-ETH-200", name: "Ethiopia Guji 200g", available: 18 });
    expect(kumoDay1.changes).toEqual(expectedChanges.kumo[1]);
    expect(agentFixture("kumo", 2).changes).toHaveLength(4);
    expect(agentFixture("kumo", 3).changes).toHaveLength(7);
  });

  for (const { client, day } of fixtureCases) {
    test(`the JSON for ${client} day ${day} is exactly what the catalog produces`, async () => {
      const onDisk = await readFile(join(fixtureDirectory, fixtureFileName(client, day)), "utf8");
      expect(onDisk).toBe(fixtureJson(client, day));
    });
  }
});
