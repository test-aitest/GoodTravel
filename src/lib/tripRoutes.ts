import { z } from "zod";
import { AgentFieldError } from "./agentfieldClient";
import { problem, readBody, reply, routeCopy } from "./approvalRoutes";
import type { KnownClient } from "./jobs";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

type Started = { executionId: string; runId: string };

export type TripSearch = { clientId: string; to: string; date: string };
export type TripBooking = { clientId: string; option: string; seat: string };

export type TripDeps = {
  findClient: (clientId: string) => KnownClient | null;
  startSearch: (search: TripSearch) => Promise<Started>;
  startBooking: (booking: TripBooking) => Promise<Started>;
};

const clientId = z.string().trim().min(1).max(200);

const searchSchema = z.strictObject({
  clientId,
  to: z.string().trim().regex(/^[A-Z][A-Za-z]{1,39}$/),
  date: z.iso.date(),
});

const bookingSchema = z.strictObject({
  clientId,
  option: z.string().regex(/^[a-z0-9][a-z0-9-]{0,39}$/),
  seat: z.string().regex(/^\d{1,2}[A-F]$/),
});

export const tripCopy = {
  sayWhere: "Say which client, where to, and which day.",
  sayWhich: "Say which client, which ride, and which seat.",
};

async function startFor<T extends { clientId: string }>(
  request: RequestLike,
  schema: z.ZodType<T>,
  unclear: string,
  deps: Pick<TripDeps, "findClient">,
  start: (input: T) => Promise<Started>,
) {
  if (!isSameOrigin(request)) return problem(403, routeCopy.notThisPage);
  const parsed = schema.safeParse(await readBody(request));
  if (!parsed.success) return problem(400, unclear);
  const client = deps.findClient(parsed.data.clientId);
  if (!client) return problem(404, routeCopy.noClient);
  try {
    const started = await start({ ...parsed.data, clientId: client.id });
    return reply({ jobId: started.executionId }, 201);
  } catch (error) {
    if (error instanceof AgentFieldError) return problem(502, routeCopy.unavailable);
    throw error;
  }
}

export function handleTripSearch(request: RequestLike, deps: Pick<TripDeps, "findClient" | "startSearch">) {
  return startFor(request, searchSchema, tripCopy.sayWhere, deps, deps.startSearch);
}

export function handleTripBooking(request: RequestLike, deps: Pick<TripDeps, "findClient" | "startBooking">) {
  return startFor(request, bookingSchema, tripCopy.sayWhich, deps, deps.startBooking);
}
