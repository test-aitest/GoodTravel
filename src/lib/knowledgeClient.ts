import { z } from "zod";
import { bookedTripSchema, knowledgeFor, type BookedTrip, type KnowledgeGraph } from "./tripKnowledge";

export type Knowledge = { trips: BookedTrip[]; graph: KnowledgeGraph; live: boolean };

const unavailable = "The worker service is not available right now.";
const readSchema = z.object({ trips: z.array(bookedTripSchema) });

function pathOf(clientId: string) {
  return `/api/clients/${encodeURIComponent(clientId)}/knowledge`;
}

export async function loadKnowledge(clientId: string, fetcher: typeof fetch = fetch): Promise<Knowledge> {
  const response = await fetcher(pathOf(clientId)).catch(() => null);
  const parsed = readSchema.safeParse(response?.ok ? await response.json().catch(() => null) : null);
  const trips = parsed.success ? parsed.data.trips : [];
  return { trips, graph: knowledgeFor(clientId, trips), live: parsed.success };
}

export async function rememberTrip(clientId: string, trip: BookedTrip, fetcher: typeof fetch = fetch): Promise<{ saved: true } | { error: string }> {
  const response = await fetcher(pathOf(clientId), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ trip }),
  }).catch(() => null);
  if (response?.ok) return { saved: true };
  const parsed: unknown = response ? await response.json().catch(() => null) : null;
  const error = typeof parsed === "object" && parsed !== null ? (parsed as { error?: unknown }).error : null;
  return { error: typeof error === "string" ? error : unavailable };
}
