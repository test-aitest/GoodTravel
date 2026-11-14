import { z } from "zod";
import { AgentFieldError } from "./agentfieldClient";
import type { ClientMemoryAccess } from "./chatStore";
import type { KnownClient } from "./jobs";
import { isSameOrigin, type RequestLike } from "./setupRoutes";
import { bookedTripSchema, cueText, featuresOf, knowledgeFor, summarize, type BookedTrip } from "./tripKnowledge";

export const knowledgeMemoryKey = "travel/trips";
const KEPT_TRIPS = 200;
const storedSchema = z.strictObject({ version: z.literal(1), trips: z.array(bookedTripSchema).max(KEPT_TRIPS) });
const rememberSchema = z.strictObject({ trip: bookedTripSchema });

export function createKnowledgeStore(memory: ClientMemoryAccess) {
  const read = async (clientId: string): Promise<BookedTrip[]> => {
    const parsed = storedSchema.safeParse(await memory.get(clientId, knowledgeMemoryKey));
    return parsed.success ? parsed.data.trips : [];
  };
  return {
    read,
    async append(clientId: string, trip: BookedTrip) {
      const trips = [...(await read(clientId)), trip].slice(-KEPT_TRIPS);
      await memory.set(clientId, knowledgeMemoryKey, { version: 1, trips });
      return trips;
    },
  };
}

export type KnowledgeStore = ReturnType<typeof createKnowledgeStore>;
export type KnowledgeDeps = { findClient: (clientId: string) => KnownClient | null; store: KnowledgeStore };

const copy = {
  notThisPage: "Only the ClientOps page can do this.",
  noClient: "There is no such client.",
  badTrip: "Send one booked trip: a role, a day, the cue words, the place, the ride and the seat.",
  unavailable: "The worker service is not available right now.",
};

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

function guarded(error: unknown) {
  if (error instanceof AgentFieldError) return reply({ error: copy.unavailable }, 502);
  throw error;
}

async function bodyOf(request: RequestLike) {
  const text = await request.text().catch(() => "");
  if (text.length > 16 * 1024) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

export async function handleReadKnowledge(clientId: string, deps: KnowledgeDeps) {
  const client = deps.findClient(clientId);
  if (!client) return reply({ error: copy.noClient }, 404);
  try {
    const trips = await deps.store.read(client.id);
    return reply({ clientId: client.id, trips, graph: summarize(knowledgeFor(client.id, trips)) });
  } catch (error) {
    return guarded(error);
  }
}

export async function handleRememberTrip(request: RequestLike, clientId: string, deps: KnowledgeDeps) {
  if (!isSameOrigin(request)) return reply({ error: copy.notThisPage }, 403);
  const client = deps.findClient(clientId);
  if (!client) return reply({ error: copy.noClient }, 404);
  const parsed = rememberSchema.safeParse(await bodyOf(request));
  if (!parsed.success) return reply({ error: copy.badTrip }, 400);
  const trip = { ...parsed.data.trip, said: cueText(featuresOf(parsed.data.trip.said, parsed.data.trip.date)) };
  try {
    const trips = await deps.store.append(client.id, trip);
    return reply({ clientId: client.id, saved: true, graph: summarize(knowledgeFor(client.id, trips)) }, 201);
  } catch (error) {
    return guarded(error);
  }
}
