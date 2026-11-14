import { accounts, catalogs, demoDays, stockOnDay, type ClientId, type DemoDay, type SiteId } from "./catalog";
import { TravelBookings } from "./travel";

export const switchNames = ["restyle", "renameButton", "decoyButton", "hiddenInstruction"] as const;
export type Switches = Record<(typeof switchNames)[number], boolean>;
export type SaveScope = "this-location" | "all-locations";

const allOff = (): Switches => ({ restyle: false, renameButton: false, decoyButton: false, hiddenInstruction: false });

export type SaveResult = { ok: true; changed: number } | { ok: false; error: string };

type StoreLocations = { warehouse: Record<string, number>; shop: Record<string, number> };

export class SitesState {
  switches = allOff();
  day: DemoDay = 1;
  readonly travel = new TravelBookings();
  private supplierAvailable = new Map<ClientId, Record<string, number>>();
  private storeLocations = new Map<ClientId, StoreLocations>();
  private sessions = new Map<string, { site: SiteId; client: ClientId }>();

  constructor(readonly clients: ClientId[]) {
    this.reset();
  }

  reset() {
    this.setDay(1);
  }

  setDay(day: unknown): DemoDay | undefined {
    const found = demoDays.find((candidate) => candidate === day);
    if (found === undefined) return undefined;
    this.day = found;
    for (const client of this.clients) {
      const stock = stockOnDay(client, found);
      this.supplierAvailable.set(client, stock.supplierAvailable);
      this.storeLocations.set(client, {
        warehouse: stock.storeAvailable,
        shop: Object.fromEntries(Object.keys(stock.storeAvailable).map((sku) => [sku, 0])),
      });
    }
    return found;
  }

  setSwitches(update: unknown): Switches | undefined {
    if (typeof update !== "object" || update === null || Array.isArray(update)) return undefined;
    const entries = Object.entries(update);
    const valid = entries.every(
      ([name, value]) => (switchNames as readonly string[]).includes(name) && typeof value === "boolean",
    );
    if (!valid) return undefined;
    this.switches = { ...this.switches, ...Object.fromEntries(entries) };
    return this.switches;
  }

  signIn(site: SiteId, username: string, password: string) {
    const account = accounts.find(
      (candidate) =>
        candidate.site === site &&
        candidate.username === username &&
        candidate.password === password &&
        this.clients.includes(candidate.client),
    );
    if (!account) return undefined;
    const token = crypto.randomUUID();
    this.sessions.set(token, { site, client: account.client });
    return token;
  }

  clientFor(site: SiteId, token: string | undefined) {
    const session = token ? this.sessions.get(token) : undefined;
    return session?.site === site ? session.client : undefined;
  }

  signOut(token: string | undefined) {
    if (token) this.sessions.delete(token);
  }

  supplierStock(client: ClientId) {
    return { ...this.supplierAvailable.get(client) };
  }

  storeStock(client: ClientId) {
    const locations = this.storeLocations.get(client);
    if (!locations) return {};
    return Object.fromEntries(
      Object.entries(locations.warehouse).map(([sku, warehouse]) => [sku, warehouse + (locations.shop[sku] ?? 0)]),
    );
  }

  saveStock(client: ClientId, submitted: Map<string, string>, scope: SaveScope): SaveResult {
    const updates = new Map<string, number>();
    for (const product of catalogs[client].products) {
      const value = submitted.get(product.sku);
      if (value === undefined) continue;
      if (!/^\d+$/.test(value.trim())) return { ok: false, error: "Available must be a whole number of 0 or more." };
      updates.set(product.sku, Number(value.trim()));
    }

    const before = this.storeStock(client);
    const locations = this.storeLocations.get(client);
    if (!locations) return { ok: true, changed: 0 };
    const warehouse = { ...locations.warehouse };
    const shop = { ...locations.shop };
    for (const [sku, value] of updates) {
      if (scope === "all-locations") {
        warehouse[sku] = value;
        shop[sku] = value;
      } else {
        shop[sku] = Math.min(shop[sku] ?? 0, value);
        warehouse[sku] = value - shop[sku];
      }
    }
    this.storeLocations.set(client, { warehouse, shop });

    const after = this.storeStock(client);
    return { ok: true, changed: [...updates.keys()].filter((sku) => before[sku] !== after[sku]).length };
  }
}
