import { describe, expect, test } from "bun:test";
import { buildGraph, cueText, emptyGraph, featuresOf, knowledgeFor, learn, rank, rideOrder, seatPreference, seedTrips, summarize, type BookedTrip } from "./tripKnowledge";

const tradeShow = featuresOf("Singapore next Saturday, for the trade show", "2026-11-21");

describe("featuresOf", () => {
  test("reads the month, the season and the purpose of a trade show trip", () => {
    expect(tradeShow).toEqual({ month: "november", season: "autumn-north", purposes: ["business", "trade-show"], wishes: [], group: null, mentioned: ["singapore"] });
  });

  test("reads wishes and the group from plain words", () => {
    const features = featuresOf("Somewhere warm and sunny with the team, good food, not a long trip, quick please", "2026-02-03");
    expect(features.month).toBe("february");
    expect(features.season).toBe("winter-north");
    expect(features.wishes).toEqual(["warm", "short-trip", "food"]);
    expect(features.group).toBe("team");
  });

  test("reads leisure, culture and family, and conferences and meetings as business", () => {
    expect(featuresOf("A relaxing beach break with the family", "2026-07-01")).toMatchObject({ purposes: ["leisure"], wishes: ["warm"], group: "family", season: "summer-north" });
    expect(featuresOf("Temples and a palace, just me", "2026-04-10")).toMatchObject({ wishes: ["culture"], group: "alone", season: "spring-north" });
    expect(featuresOf("A client meeting, then the conference", "2026-03-10").purposes).toEqual(["business", "client-meeting", "conference"]);
  });

  test("cue words read back to the same features", () => {
    const features = featuresOf("Cosmoprof trade show with the team, somewhere warm, good food", "2026-11-21");
    expect(featuresOf(cueText(features), "2026-11-21")).toEqual({ ...features, mentioned: [] });
  });
});

describe("learning from bookings", () => {
  const trip: BookedTrip = { traveller: "sales-lead", date: "2026-11-21", said: "trade show", destination: "singapore", ride: "flight", seat: "aisle", exitRow: true };

  test("a booking adds nodes and strengthens edges without changing the old graph", () => {
    const once = learn(emptyGraph, trip);
    const twice = learn(once, trip);

    expect(emptyGraph.nodes).toEqual([]);
    expect(once.nodes.map((node) => node.id)).toContain("month:november");
    expect(once.nodes.find((node) => node.id === "destination:singapore")?.kind).toBe("destination");
    const weight = (graph: typeof once) => graph.edges.find((edge) => edge.from === "purpose:trade-show" && edge.to === "destination:singapore")?.weight;
    expect(weight(once)).toBe(1);
    expect(weight(twice)).toBe(2);
    expect(twice.trips).toBe(2);
  });

  test("the seed is believable Hinata trips across months and purposes", () => {
    expect(seedTrips.length).toBeGreaterThanOrEqual(8);
    expect(seedTrips.length).toBeLessThanOrEqual(12);
    expect(new Set(seedTrips.map((seed) => seed.destination))).toEqual(new Set(["kyoto", "seoul", "singapore"]));
    expect(new Set(seedTrips.map((seed) => seed.date.slice(5, 7))).size).toBeGreaterThanOrEqual(6);
    expect(seedTrips.every((seed) => /^[a-z][a-z0-9-]+$/.test(seed.traveller))).toBe(true);
  });

  test("only Hinata starts from the seed", () => {
    expect(knowledgeFor("hinata", []).trips).toBe(seedTrips.length);
    expect(knowledgeFor("kumo", []).trips).toBe(0);
    expect(knowledgeFor("kumo", [trip]).trips).toBe(1);
  });
});

describe("rank", () => {
  const graph = knowledgeFor("hinata", []);

  test("a November trade show puts Singapore first with month and purpose reasons", () => {
    const ranked = rank(graph, tradeShow, "sales-lead");
    expect(ranked.map((entry) => entry.destination)).toEqual(["singapore", "seoul", "kyoto"]);
    expect(ranked[0]?.reasons).toEqual([
      { kind: "purpose", label: "Trade show" },
      { kind: "month", label: "November" },
    ]);
    expect(ranked.reduce((sum, entry) => sum + entry.score, 0)).toBeCloseTo(1, 1);
    expect(ranked.every((entry) => entry.reasons.length <= 2)).toBe(true);
  });

  test("a spring culture offsite with the team puts Kyoto first", () => {
    const ranked = rank(graph, featuresOf("Team offsite, temples and culture", "2027-04-08"), "product-team");
    expect(ranked[0]?.destination).toBe("kyoto");
    expect(ranked[0]?.reasons.length).toBeGreaterThan(0);
  });

  test("a quick client meeting leans to Seoul", () => {
    expect(rank(graph, featuresOf("A quick client meeting", "2027-02-16"), "sales-lead")[0]?.destination).toBe("seoul");
  });

  test("with nothing learned every place scores the same and has no reasons", () => {
    const ranked = rank(emptyGraph, featuresOf("For the trade show", "2026-11-21"), "sales-lead");
    expect(new Set(ranked.map((entry) => entry.score)).size).toBe(1);
    expect(ranked.every((entry) => entry.reasons.length === 0)).toBe(true);
  });

  test("a place the person names comes first even before anything is learned", () => {
    expect(rank(emptyGraph, tradeShow, "sales-lead")[0]?.destination).toBe("singapore");
  });

  test("new bookings move the ranking", () => {
    const winter = featuresOf("A meeting", "2027-01-12");
    const before = rank(buildGraph([]), winter, "brand-manager");
    const learned = buildGraph(Array.from({ length: 3 }, () => ({ traveller: "brand-manager", date: "2026-01-14", said: "meeting", destination: "kyoto" as const, ride: "rail" as const, seat: "window" as const, exitRow: false })));
    expect(before[0]?.reasons).toEqual([]);
    expect(rank(learned, winter, "brand-manager")[0]).toMatchObject({ destination: "kyoto", reasons: [{ kind: "purpose", label: "Client meeting" }, { kind: "month", label: "January" }] });
  });
});

describe("seatPreference", () => {
  test("the sales lead likes aisle seats in exit rows", () => {
    const preference = seatPreference(knowledgeFor("hinata", []), "sales-lead");
    expect(preference.position).toBe("aisle");
    expect(preference.exitRow.likes).toBe(true);
    expect(preference.confidence).toBeGreaterThan(0.4);
    expect(preference.basis).toBe("traveller");
  });

  test("an unknown traveller falls back to everyone, and nothing at all says so", () => {
    expect(seatPreference(knowledgeFor("hinata", []), "new-hire").basis).toBe("everyone");
    expect(seatPreference(emptyGraph, "new-hire")).toEqual({ position: null, confidence: 0, exitRow: { likes: false, confidence: 0 }, basis: "none" });
  });
});

describe("rideOrder", () => {
  const options = [
    { id: "nozomi-27", kind: "rail" as const, arrive: "11:12" },
    { id: "ka-712", kind: "flight" as const, arrive: "11:05" },
    { id: "nozomi-21", kind: "rail" as const, arrive: "10:15" },
  ];

  test("a culture trip puts rail first, earlier arrival first within a kind", () => {
    const ordered = rideOrder(knowledgeFor("hinata", []), featuresOf("culture and temples", "2027-04-08"), options);
    expect(ordered.map((option) => option.id)).toEqual(["nozomi-21", "nozomi-27", "ka-712"]);
  });

  test("with nothing learned it keeps the earliest arrival first", () => {
    expect(rideOrder(emptyGraph, tradeShow, options).map((option) => option.id)).toEqual(["nozomi-21", "ka-712", "nozomi-27"]);
  });
});

describe("summarize", () => {
  test("counts what the graph holds and lists the strongest links", () => {
    const summary = summarize(knowledgeFor("hinata", []));
    expect(summary.trips).toBe(seedTrips.length);
    expect(summary.nodes).toBeGreaterThan(10);
    expect(summary.strongest.length).toBe(5);
    expect(summary.strongest[0]?.weight).toBeGreaterThanOrEqual(summary.strongest[4]?.weight ?? 0);
  });
});
