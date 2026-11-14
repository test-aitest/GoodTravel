import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { catalogs, expectedChanges, stockOnDay, type ClientId, type DemoDay } from "./catalog";

export const fixtureDirectory = join(import.meta.dirname, "..", "..", "agent", "tests", "fixtures");

export const fixtureCases: { client: ClientId; day: DemoDay }[] = [
  { client: "kumo", day: 1 },
  { client: "kumo", day: 2 },
  { client: "kumo", day: 3 },
  { client: "hinata", day: 1 },
];

export function fixtureFileName(client: ClientId, day: DemoDay) {
  return `${client}-day${day}.json`;
}

export function agentFixture(client: ClientId, day: DemoDay) {
  const { supplierAvailable, storeAvailable } = stockOnDay(client, day);
  const rows = (available: Record<string, number>) =>
    catalogs[client].products.map(({ sku, name }) => ({ sku, name, available: available[sku] }));
  return {
    client,
    day,
    supplier: rows(supplierAvailable),
    store: rows(storeAvailable),
    changes: expectedChanges[client][day],
  };
}

export function fixtureJson(client: ClientId, day: DemoDay) {
  return `${JSON.stringify(agentFixture(client, day), null, 2)}\n`;
}

if (import.meta.main) {
  await mkdir(fixtureDirectory, { recursive: true });
  for (const { client, day } of fixtureCases) {
    await writeFile(join(fixtureDirectory, fixtureFileName(client, day)), fixtureJson(client, day));
  }
  console.log(`Wrote ${fixtureCases.length} fixtures to ${fixtureDirectory}`);
}
