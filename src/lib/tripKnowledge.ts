import { z } from "zod";
import type { DestinationId, RideKind, SeatPosition } from "@/travel/travelData";

export type NodeKind = "traveller" | "month" | "purpose" | "wish" | "destination" | "ride" | "seat-position";
export type KnowledgeNode = { id: string; kind: NodeKind };
export type KnowledgeEdge = { from: string; to: string; weight: number };
export type KnowledgeGraph = { nodes: readonly KnowledgeNode[]; edges: readonly KnowledgeEdge[]; trips: number };

export const monthNames = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"] as const;
export type MonthName = (typeof monthNames)[number];
export type Season = "winter-north" | "spring-north" | "summer-north" | "autumn-north";
export const purposes = ["business", "trade-show", "client-meeting", "conference", "offsite", "leisure"] as const;
export type Purpose = (typeof purposes)[number];
export const wishes = ["warm", "short-trip", "culture", "food"] as const;
export type Wish = (typeof wishes)[number];
export const groups = ["family", "team", "alone"] as const;
export type Group = (typeof groups)[number];

export type Features = { month: MonthName; season: Season; purposes: Purpose[]; wishes: Wish[]; group: Group | null; mentioned: DestinationId[] };
export type BookedTrip = { traveller: string; date: string; said: string; destination: DestinationId; ride: RideKind; seat: SeatPosition; exitRow: boolean };
export type Reason = { kind: NodeKind; label: string };
export type RankedDestination = { destination: DestinationId; score: number; reasons: Reason[] };
export type SeatPreference = { position: SeatPosition | null; confidence: number; exitRow: { likes: boolean; confidence: number }; basis: "traveller" | "everyone" | "none" };
export type KnowledgeSummary = { trips: number; nodes: number; edges: number; strongest: KnowledgeEdge[] };

const destinationIds: readonly DestinationId[] = ["kyoto", "seoul", "singapore"];
const destinationNames: Record<DestinationId, string> = { kyoto: "kyoto", seoul: "seoul", singapore: "singapore" };
const rideKinds: readonly RideKind[] = ["rail", "flight"];
const seatPositions: readonly SeatPosition[] = ["window", "aisle", "middle"];
const exitRowNode = "seat-position:exit-row";

const seasons: Record<MonthName, Season> = {
  december: "winter-north",
  january: "winter-north",
  february: "winter-north",
  march: "spring-north",
  april: "spring-north",
  may: "spring-north",
  june: "summer-north",
  july: "summer-north",
  august: "summer-north",
  september: "autumn-north",
  october: "autumn-north",
  november: "autumn-north",
};

const purposeWords: Record<Exclude<Purpose, "business">, RegExp> = {
  "trade-show": /\b(trade[ -]?shows?|expo|exhibition|cosmoprof)\b/,
  "client-meeting": /\b(meetings?|client visits?|visit(ing)? (a |the )?clients?|distributors?|retailers?|pitch)\b/,
  conference: /\b(conferences?|summit|seminar|forum)\b/,
  offsite: /\b(off-?site|retreat|team building)\b/,
  leisure: /\b(leisure|beach|relax\w*|holiday|vacation)\b/,
};
const businessPurposes: readonly Purpose[] = ["trade-show", "client-meeting", "conference"];

const wishWords: Record<Wish, RegExp> = {
  warm: /\b(warm\w*|sunny|sun|hot|tropical|beach)\b/,
  "short-trip": /\b(quick|short|nearby|near|close by|day trip)\b/,
  culture: /\b(culture|cultural|temples?|palaces?|shrines?|museums?|history|historic|gardens?)\b/,
  food: /\b(food|foodie|eat|eating|dinner|lunch|cuisine|restaurants?)\b/,
};

const groupWords: Record<Group, RegExp> = {
  family: /\b(family|kids|children|partner)\b/,
  team: /\b(team|colleagues|coworkers|group|all of us)\b/,
  alone: /\b(alone|solo|just me|by myself|on my own)\b/,
};

const labels: Record<string, string> = {
  business: "Business",
  "trade-show": "Trade show",
  "client-meeting": "Client meeting",
  conference: "Conference",
  offsite: "Offsite",
  leisure: "Leisure",
  warm: "Somewhere warm",
  "short-trip": "A short trip",
  culture: "Culture",
  food: "Food",
  family: "With family",
  team: "With the team",
  alone: "Travelling alone",
};

const weights: Record<"traveller" | "month" | "business" | "purpose" | "wish" | "group" | "destination", number> = {
  traveller: 0.75,
  month: 1,
  business: 0.75,
  purpose: 1.5,
  wish: 1,
  group: 0.5,
  destination: 1,
};
const SMOOTHING = 0.5;
const SAME_SEASON = 0.5;
const MENTIONED = 1;
const MEANINGFUL = 0.05;

function plain(text: string) {
  return text.toLowerCase().replaceAll("’", "'").replaceAll(/\s+/g, " ").trim();
}

function monthOf(date: string): MonthName {
  return monthNames[(Number(date.slice(5, 7)) || 1) - 1] ?? "january";
}

export function featuresOf(text: string, date: string): Features {
  const words = plain(text);
  const month = monthOf(date);
  const specific = (Object.keys(purposeWords) as Exclude<Purpose, "business">[]).filter((purpose) => purposeWords[purpose].test(words));
  const isBusiness = /\bbusiness\b/.test(words) || specific.some((purpose) => businessPurposes.includes(purpose));
  return {
    month,
    season: seasons[month],
    purposes: purposes.filter((purpose) => (purpose === "business" ? isBusiness : specific.includes(purpose))),
    wishes: wishes.filter((wish) => wishWords[wish].test(words)),
    group: groups.find((group) => groupWords[group].test(words)) ?? null,
    mentioned: destinationIds.filter((destination) => words.includes(destinationNames[destination])),
  };
}

export function cueText(features: Features) {
  return [...features.purposes, ...features.wishes, ...(features.group ? [features.group] : [])].join(" ");
}

export const emptyGraph: KnowledgeGraph = { nodes: [], edges: [], trips: 0 };

type Context = { id: string; kind: NodeKind; weight: number; label: string };

function titleCase(text: string) {
  const spaced = text.replaceAll("-", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function contextsOf(features: Features, traveller: string | null): Context[] {
  return [
    ...(traveller ? [{ id: `traveller:${traveller}`, kind: "traveller" as const, weight: weights.traveller, label: titleCase(traveller) }] : []),
    { id: `month:${features.month}`, kind: "month", weight: weights.month, label: titleCase(features.month) },
    ...features.purposes.map((purpose) => ({ id: `purpose:${purpose}`, kind: "purpose" as const, weight: purpose === "business" ? weights.business : weights.purpose, label: labels[purpose] ?? purpose })),
    ...features.wishes.map((wish) => ({ id: `wish:${wish}`, kind: "wish" as const, weight: weights.wish, label: labels[wish] ?? wish })),
    ...(features.group ? [{ id: `wish:${features.group}`, kind: "wish" as const, weight: weights.group, label: labels[features.group] ?? features.group }] : []),
  ];
}

export function learn(graph: KnowledgeGraph, trip: BookedTrip): KnowledgeGraph {
  const features = featuresOf(trip.said, trip.date);
  const contexts = contextsOf(features, trip.traveller);
  const destination = `destination:${trip.destination}`;
  const ride = `ride:${trip.ride}`;
  const links: [string, string][] = [
    ...contexts.flatMap((context): [string, string][] => [
      [context.id, destination],
      [context.id, ride],
    ]),
    [destination, ride],
    [`traveller:${trip.traveller}`, `seat-position:${trip.seat}`],
    ...(trip.exitRow ? [[`traveller:${trip.traveller}`, exitRowNode] as [string, string]] : []),
  ];
  const fresh: KnowledgeNode[] = [
    ...contexts.map((context) => ({ id: context.id, kind: context.kind })),
    { id: destination, kind: "destination" },
    { id: ride, kind: "ride" },
    { id: `seat-position:${trip.seat}`, kind: "seat-position" },
    ...(trip.exitRow ? [{ id: exitRowNode, kind: "seat-position" as const }] : []),
  ];
  const known = new Set(graph.nodes.map((node) => node.id));
  const nodes = [...graph.nodes, ...fresh.filter((node) => !known.has(node.id) && known.add(node.id))];
  const edges = new Map(graph.edges.map((edge) => [`${edge.from}>${edge.to}`, edge]));
  for (const [from, to] of links) {
    const key = `${from}>${to}`;
    edges.set(key, { from, to, weight: (edges.get(key)?.weight ?? 0) + 1 });
  }
  return { nodes, edges: [...edges.values()], trips: graph.trips + 1 };
}

export function buildGraph(trips: readonly BookedTrip[]) {
  return trips.reduce(learn, emptyGraph);
}

export const seedTrips: readonly BookedTrip[] = [
  { traveller: "sales-lead", date: "2024-11-13", said: "Cosmoprof Asia trade show, booth setup the day before", destination: "singapore", ride: "flight", seat: "aisle", exitRow: true },
  { traveller: "sales-lead", date: "2025-11-19", said: "Beauty trade show again, somewhere warm after Tokyo", destination: "singapore", ride: "flight", seat: "aisle", exitRow: true },
  { traveller: "brand-manager", date: "2025-11-12", said: "Trade show with the team, product launch at the booth", destination: "singapore", ride: "flight", seat: "window", exitRow: false },
  { traveller: "sales-lead", date: "2026-02-10", said: "Quick client meeting with a retailer in Myeongdong", destination: "seoul", ride: "flight", seat: "aisle", exitRow: true },
  { traveller: "sales-lead", date: "2026-06-03", said: "Short trip, meeting a distributor", destination: "seoul", ride: "flight", seat: "aisle", exitRow: false },
  { traveller: "brand-manager", date: "2026-09-08", said: "K-beauty conference, good food after", destination: "seoul", ride: "flight", seat: "window", exitRow: false },
  { traveller: "product-team", date: "2026-04-02", said: "Team offsite in spring, temples and culture", destination: "kyoto", ride: "rail", seat: "window", exitRow: false },
  { traveller: "founder", date: "2025-04-07", said: "Spring offsite with the team, temples and gardens", destination: "kyoto", ride: "rail", seat: "window", exitRow: false },
  { traveller: "founder", date: "2025-11-25", said: "Autumn leaves and a client dinner, good food", destination: "kyoto", ride: "rail", seat: "window", exitRow: false },
  { traveller: "brand-manager", date: "2026-07-15", said: "Relax after the launch, beach and warm weather", destination: "singapore", ride: "flight", seat: "window", exitRow: false },
  { traveller: "sales-lead", date: "2026-10-14", said: "Client meeting nearby, quick there and back", destination: "kyoto", ride: "rail", seat: "aisle", exitRow: false },
];

export const seededClientId = "hinata";

export function knowledgeFor(clientId: string, trips: readonly BookedTrip[]) {
  return buildGraph([...(clientId === seededClientId ? seedTrips : []), ...trips]);
}

type Weights = Map<string, Map<string, number>>;

function weightsOf(graph: KnowledgeGraph): Weights {
  const table: Weights = new Map();
  for (const edge of graph.edges) {
    const row = table.get(edge.from) ?? new Map<string, number>();
    row.set(edge.to, edge.weight);
    table.set(edge.from, row);
  }
  return table;
}

function evidenceFor(table: Weights, context: Context, features: Features, outcomes: readonly string[]) {
  const direct = outcomes.map((outcome) => table.get(context.id)?.get(outcome) ?? 0);
  if (context.kind !== "month") return direct;
  const neighbours = monthNames.filter((month) => month !== features.month && seasons[month] === features.season);
  return direct.map((count, index) => count + SAME_SEASON * neighbours.reduce((sum, month) => sum + (table.get(`month:${month}`)?.get(outcomes[index] ?? "") ?? 0), 0));
}

function scoresOf(table: Weights, contexts: readonly Context[], features: Features, outcomes: readonly string[]) {
  const uniform = 1 / outcomes.length;
  const totals = outcomes.map(() => 0);
  const reasons = outcomes.map((): { reason: Reason; strength: number }[] => []);
  for (const context of contexts) {
    const counts = evidenceFor(table, context, features, outcomes);
    const sum = counts.reduce((total, count) => total + count, 0);
    if (sum === 0) continue;
    counts.forEach((count, index) => {
      const share = (count + SMOOTHING) / (sum + SMOOTHING * outcomes.length);
      totals[index] = (totals[index] ?? 0) + context.weight * share;
      const strength = context.weight * (share - uniform);
      if (strength > MEANINGFUL) reasons[index]?.push({ reason: { kind: context.kind, label: context.label }, strength });
    });
  }
  return { totals, reasons };
}

function round(value: number) {
  return Math.round(value * 100) / 100;
}

export function rank(graph: KnowledgeGraph, features: Features, traveller: string): RankedDestination[] {
  const outcomes = destinationIds.map((destination) => `destination:${destination}`);
  const { totals, reasons } = scoresOf(weightsOf(graph), contextsOf(features, traveller), features, outcomes);
  const raw = destinationIds.map((destination, index) => (totals[index] ?? 0) + (features.mentioned.includes(destination) ? MENTIONED * weights.destination : 0));
  const sum = raw.reduce((total, value) => total + value, 0);
  return destinationIds
    .map((destination, index) => ({
      destination,
      score: round(sum === 0 ? 1 / destinationIds.length : (raw[index] ?? 0) / sum),
      reasons: (reasons[index] ?? [])
        .toSorted((a, b) => b.strength - a.strength)
        .slice(0, 2)
        .map((entry) => entry.reason),
      exact: sum === 0 ? 0 : (raw[index] ?? 0) / sum,
    }))
    .toSorted((a, b) => b.exact - a.exact)
    .map(({ destination, score, reasons: why }) => ({ destination, score, reasons: why }));
}

export function rideOrder<Option extends { kind: RideKind; arrive: string }>(graph: KnowledgeGraph, features: Features, options: readonly Option[]): Option[] {
  const outcomes = rideKinds.map((kind) => `ride:${kind}`);
  const contexts = [...contextsOf(features, null), ...features.mentioned.map((destination) => ({ id: `destination:${destination}`, kind: "destination" as const, weight: weights.destination, label: titleCase(destination) }))];
  const { totals } = scoresOf(weightsOf(graph), contexts, features, outcomes);
  const scoreOf = (kind: RideKind) => totals[rideKinds.indexOf(kind)] ?? 0;
  return options.toSorted((a, b) => scoreOf(b.kind) - scoreOf(a.kind) || a.arrive.localeCompare(b.arrive));
}

function confidenceOf(share: number, count: number) {
  return round(share * (count / (count + 2)));
}

export function seatPreference(graph: KnowledgeGraph, traveller: string): SeatPreference {
  const table = weightsOf(graph);
  const own = table.get(`traveller:${traveller}`);
  const ownSeats = seatPositions.reduce((sum, position) => sum + (own?.get(`seat-position:${position}`) ?? 0), 0);
  const rows = ownSeats > 0 ? [own] : [...table.entries()].filter(([id]) => id.startsWith("traveller:")).map(([, row]) => row);
  const basis = ownSeats > 0 ? "traveller" : "everyone";
  const count = (id: string) => rows.reduce((sum, row) => sum + (row?.get(id) ?? 0), 0);
  const seats = seatPositions.map((position) => ({ position, count: count(`seat-position:${position}`) }));
  const total = seats.reduce((sum, seat) => sum + seat.count, 0);
  if (total === 0) return { position: null, confidence: 0, exitRow: { likes: false, confidence: 0 }, basis: "none" };
  const best = seats.toSorted((a, b) => b.count - a.count)[0] ?? { position: "window" as const, count: 0 };
  const flights = count("ride:flight");
  const exits = count(exitRowNode);
  const likes = flights > 0 && exits / flights > 0.5;
  return {
    position: best.position,
    confidence: confidenceOf(best.count / total, total),
    exitRow: { likes, confidence: flights === 0 ? 0 : confidenceOf((likes ? exits : flights - exits) / flights, flights) },
    basis,
  };
}

export function summarize(graph: KnowledgeGraph): KnowledgeSummary {
  return {
    trips: graph.trips,
    nodes: graph.nodes.length,
    edges: graph.edges.length,
    strongest: graph.edges.toSorted((a, b) => b.weight - a.weight || a.from.localeCompare(b.from) || a.to.localeCompare(b.to)).slice(0, 5),
  };
}

const plainWords = z
  .string()
  .trim()
  .max(300)
  .refine((text) => !/[^\s@]+@[^\s@]+/.test(text), "No email addresses")
  .refine((text) => !/\d[\d\s-]{5,}\d/.test(text), "No phone numbers");

export const bookedTripSchema = z.strictObject({
  traveller: z.string().regex(/^[a-z][a-z0-9-]{1,39}$/),
  date: z.iso.date(),
  said: plainWords,
  destination: z.enum(["kyoto", "seoul", "singapore"]),
  ride: z.enum(["rail", "flight"]),
  seat: z.enum(["window", "middle", "aisle"]),
  exitRow: z.boolean(),
});
