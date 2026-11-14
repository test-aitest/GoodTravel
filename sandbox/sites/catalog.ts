export type ClientId = "kumo" | "hinata";
export type SiteId = "supplier" | "store" | "travel";
export type DemoDay = 1 | 2 | 3;

export const clientIds: ClientId[] = ["kumo", "hinata"];
export const demoDays: DemoDay[] = [1, 2, 3];

export type Product = { sku: string; name: string; pack: string };

export type Catalog = {
  clientName: string;
  storeName: string;
  products: Product[];
  supplierAvailable: Record<string, number>;
  storeAvailable: Record<string, number>;
  supplierUpdatesAfterDay1: Record<string, number>[];
};

export type Account = { client: ClientId; site: SiteId; username: string; password: string };

export type StockDifference = { sku: string; name: string; store: number; supplier: number };

type Row = [sku: string, name: string, pack: string, supplier: number, store: number];

function catalogFrom(
  clientName: string,
  storeName: string,
  rows: Row[],
  supplierUpdatesAfterDay1: Record<string, number>[] = [],
): Catalog {
  return {
    clientName,
    storeName,
    products: rows.map(([sku, name, pack]) => ({ sku, name, pack })),
    supplierAvailable: Object.fromEntries(rows.map(([sku, , , supplier]) => [sku, supplier])),
    storeAvailable: Object.fromEntries(rows.map(([sku, , , , store]) => [sku, store])),
    supplierUpdatesAfterDay1,
  };
}

const kumoRows: Row[] = [
  ["KC-ETH-200", "Ethiopia Guji 200g", "200 g bag", 6, 18],
  ["KC-ETH-500", "Ethiopia Guji 500g", "500 g bag", 12, 12],
  ["KC-YIR-200", "Ethiopia Yirgacheffe 200g", "200 g bag", 11, 20],
  ["KC-YIR-500", "Ethiopia Yirgacheffe 500g", "500 g bag", 7, 7],
  ["KC-COL-200", "Colombia Huila 200g", "200 g bag", 24, 0],
  ["KC-COL-500", "Colombia Huila 500g", "500 g bag", 10, 10],
  ["KC-KEN-200", "Kenya Nyeri 200g", "200 g bag", 0, 9],
  ["KC-KEN-500", "Kenya Nyeri 500g", "500 g bag", 5, 5],
  ["KC-GUA-200", "Guatemala Antigua 200g", "200 g bag", 22, 22],
  ["KC-GUA-500", "Guatemala Antigua 500g", "500 g bag", 15, 4],
  ["KC-BRA-200", "Brazil Cerrado 200g", "200 g bag", 35, 35],
  ["KC-BRA-500", "Brazil Cerrado 500g", "500 g bag", 0, 7],
  ["KC-CRI-200", "Costa Rica Tarrazu 200g", "200 g bag", 16, 16],
  ["KC-CRI-500", "Costa Rica Tarrazu 500g", "500 g bag", 6, 6],
  ["KC-PER-200", "Peru Cajamarca 200g", "200 g bag", 19, 19],
  ["KC-PER-500", "Peru Cajamarca 500g", "500 g bag", 8, 8],
  ["KC-SUM-200", "Sumatra Mandheling 200g", "200 g bag", 14, 14],
  ["KC-SUM-500", "Sumatra Mandheling 500g", "500 g bag", 8, 3],
  ["KC-RWA-200", "Rwanda Nyamasheke 200g", "200 g bag", 9, 9],
  ["KC-RWA-500", "Rwanda Nyamasheke 500g", "500 g bag", 4, 4],
  ["KC-HON-200", "Honduras Marcala 200g", "200 g bag", 13, 13],
  ["KC-HON-500", "Honduras Marcala 500g", "500 g bag", 6, 6],
  ["KC-PAN-200", "Panama Boquete 200g", "200 g bag", 5, 2],
  ["KC-PAN-500", "Panama Boquete 500g", "500 g bag", 2, 2],
  ["KC-HSE-200", "House Blend 200g", "200 g bag", 40, 52],
  ["KC-HSE-500", "House Blend 500g", "500 g bag", 18, 18],
  ["KC-ESP-200", "Espresso Blend 200g", "200 g bag", 30, 30],
  ["KC-ESP-500", "Espresso Blend 500g", "500 g bag", 19, 25],
  ["KC-DEC-200", "Decaf Colombia 200g", "200 g bag", 12, 12],
  ["KC-DEC-500", "Decaf Colombia 500g", "500 g bag", 5, 5],
  ["KC-DRP-ETH", "Drip Bags Ethiopia Guji x10", "10 bags", 25, 25],
  ["KC-DRP-COL", "Drip Bags Colombia Huila x10", "10 bags", 20, 20],
  ["KC-DRP-KEN", "Drip Bags Kenya Nyeri x10", "10 bags", 0, 14],
  ["KC-DRP-GUA", "Drip Bags Guatemala Antigua x10", "10 bags", 15, 15],
  ["KC-DRP-HSE", "Drip Bags House Blend x10", "10 bags", 48, 48],
  ["KC-DRP-DEC", "Drip Bags Decaf Colombia x10", "10 bags", 10, 10],
  ["KC-GDS-FLT", "Paper Filters x100", "100 pcs", 60, 45],
  ["KC-GDS-DRP", "Ceramic Dripper", "1 pc", 9, 9],
  ["KC-GDS-SRV", "Glass Server 600ml", "1 pc", 11, 11],
  ["KC-GDS-GRN", "Hand Grinder", "1 pc", 3, 3],
  ["KC-GDS-CAN", "Storage Canister", "1 pc", 17, 17],
  ["KC-GDS-GFT", "Gift Box 2 x 200g", "1 box", 8, 8],
];

const kumoSupplierUpdatesAfterDay1: Record<string, number>[] = [
  { "KC-ETH-200": 2, "KC-COL-200": 18, "KC-KEN-200": 12, "KC-HSE-200": 36 },
  {
    "KC-ETH-200": 0,
    "KC-YIR-200": 15,
    "KC-GUA-500": 9,
    "KC-BRA-500": 20,
    "KC-ESP-200": 26,
    "KC-DRP-KEN": 10,
    "KC-GDS-GRN": 6,
  },
];

const hinataRows: Row[] = [
  ["HS-TON-150", "Rice Toner 150ml", "1 bottle", 30, 22],
  ["HS-TON-300", "Rice Toner 300ml", "1 bottle", 14, 14],
  ["HS-MLK-120", "Yuzu Milk Lotion 120ml", "1 bottle", 18, 18],
  ["HS-SER-030", "Camellia Serum 30ml", "1 bottle", 0, 6],
  ["HS-CRM-050", "Green Tea Cream 50g", "1 jar", 21, 21],
  ["HS-CLN-150", "Gentle Cleansing Oil 150ml", "1 bottle", 16, 16],
  ["HS-FOM-100", "Soft Foam Cleanser 100g", "1 tube", 27, 27],
  ["HS-MSK-005", "Sheet Masks x5", "1 pack", 40, 40],
  ["HS-SUN-050", "Daily Sun Milk 50ml", "1 bottle", 48, 35],
  ["HS-LIP-010", "Honey Lip Balm 10g", "1 pc", 12, 20],
  ["HS-HND-060", "Hand Cream 60g", "1 tube", 33, 33],
  ["HS-EYE-015", "Eye Cream 15g", "1 jar", 9, 9],
  ["HS-MST-080", "Face Mist 80ml", "1 bottle", 19, 19],
  ["HS-PAD-060", "Toner Pads x60", "1 jar", 11, 11],
  ["HS-TRV-SET", "Travel Set", "1 set", 7, 7],
  ["HS-GFT-SET", "Gift Set", "1 set", 3, 8],
];

export const catalogs: Record<ClientId, Catalog> = {
  kumo: catalogFrom("Kumo Coffee Roasters", "Kumo Coffee", kumoRows, kumoSupplierUpdatesAfterDay1),
  hinata: catalogFrom("Hinata Skincare", "Hinata Skincare", hinataRows),
};

export const accounts: Account[] = [
  { client: "kumo", site: "supplier", username: "kumo-roasters", password: "kumo-supply-demo" },
  { client: "kumo", site: "store", username: "kumo-admin", password: "kumo-store-demo" },
  { client: "hinata", site: "supplier", username: "hinata-skincare", password: "hinata-supply-demo" },
  { client: "hinata", site: "store", username: "hinata-admin", password: "hinata-store-demo" },
  { client: "kumo", site: "travel", username: "kumo-trips", password: "kumo-travel-demo" },
  { client: "hinata", site: "travel", username: "hinata-trips", password: "hinata-travel-demo" },
];

export function stockDifferences(
  products: Product[],
  supplierAvailable: Record<string, number>,
  storeAvailable: Record<string, number>,
): StockDifference[] {
  return products
    .filter((product) => supplierAvailable[product.sku] !== storeAvailable[product.sku])
    .map((product) => ({
      sku: product.sku,
      name: product.name,
      store: storeAvailable[product.sku],
      supplier: supplierAvailable[product.sku],
    }));
}

export function stockOnDay(client: ClientId, day: DemoDay) {
  const catalog = catalogs[client];
  let storeAvailable = { ...catalog.storeAvailable };
  let supplierAvailable = { ...catalog.supplierAvailable };
  for (let laterDay = 2; laterDay <= day; laterDay += 1) {
    storeAvailable = { ...supplierAvailable };
    supplierAvailable = { ...supplierAvailable, ...catalog.supplierUpdatesAfterDay1[laterDay - 2] };
  }
  return { supplierAvailable, storeAvailable };
}

function changesByDay(client: ClientId): Record<DemoDay, StockDifference[]> {
  const changesOn = (day: DemoDay) => {
    const { supplierAvailable, storeAvailable } = stockOnDay(client, day);
    return stockDifferences(catalogs[client].products, supplierAvailable, storeAvailable);
  };
  return { 1: changesOn(1), 2: changesOn(2), 3: changesOn(3) };
}

export const expectedChanges: Record<ClientId, Record<DemoDay, StockDifference[]>> = {
  kumo: changesByDay("kumo"),
  hinata: changesByDay("hinata"),
};
