import { z } from "zod";
import { AgentFieldError } from "./agentfieldClient";
import { problem, readBody, reply, routeCopy } from "./approvalRoutes";
import type { KnownClient } from "./jobs";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

const destination = z.enum(["kyoto", "seoul", "singapore"]);
const optionId = z.string().regex(/^[a-z0-9][a-z0-9-]{0,39}$/);
const seatId = z.string().regex(/^\d{1,2}[A-F]$/);
const clock = z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d$/);
const personal = /[^\s@]+@[^\s@]+|https?:\/\/|\bpass(?:word|code)\b|\busername\b|\blogin\b/i;
const impersonal = (text: string) => !personal.test(text);
const label = z
  .string()
  .regex(/^[A-Za-z][A-Za-z0-9 ,.'&/+()°%-]{0,59}$/)
  .refine(impersonal);
const rideName = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9 ,.'&/+()-]{0,79}$/)
  .refine(impersonal);
const spoken = (limit: number) => z.string().max(limit).refine(impersonal);

export const tripTalkActions = ["ask", "show-destinations", "search", "show-rides", "choose-ride", "show-seats", "choose-seat", "show-ticket", "none"] as const;

const option = z.strictObject({
  id: optionId,
  name: rideName,
  kind: z.enum(["rail", "flight"]),
  depart: clock,
  arrive: clock,
  price: z.number().finite().nonnegative(),
  currency: z.string().regex(/^[A-Z]{3}$/),
});

export const tripTalkContextSchema = z
  .strictObject({
    month: z.enum(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]),
    scene: z.enum(["where", "ride", "seat", "hold", "booked"]),
    destination: destination.nullable(),
    ranked: z
      .array(z.strictObject({ destination, score: z.number().finite().nonnegative(), reasons: z.array(label).max(6) }))
      .max(3)
      .refine((ranked) => new Set(ranked.map((entry) => entry.destination)).size === ranked.length),
    options: z
      .array(option)
      .max(12)
      .refine((options) => new Set(options.map((entry) => entry.id)).size === options.length)
      .nullable(),
    suggestion: z.strictObject({ option: optionId, seat: seatId, reason: z.enum(["window", "aisle", "quiet-front", "exit-legroom", "together"]) }).nullable(),
    seatPreference: z.strictObject({ position: z.enum(["window", "aisle", "middle"]), exitRow: z.boolean() }).nullable(),
    chosen: z.strictObject({ option: optionId, seat: seatId.nullable() }).nullable(),
    history: z.array(z.strictObject({ from: z.enum(["you", "worker"]), text: spoken(300) })).max(6),
  })
  .refine((context) => {
    const ids = new Set((context.options ?? []).map((entry) => entry.id));
    const listed = (id: string) => context.options === null || ids.has(id);
    return (!context.suggestion || listed(context.suggestion.option)) && (!context.chosen || listed(context.chosen.option));
  });

export type TripTalkContext = z.infer<typeof tripTalkContextSchema>;

export const tripTalkDecisionSchema = z.strictObject({
  reply: z.string().min(1).max(320),
  action: z.enum(tripTalkActions),
  destination: destination.nullable(),
  option: optionId.nullable(),
  seat: seatId.nullable(),
  speaker: z.enum(["worker", "kyoto", "seoul", "singapore"]),
  decidedBy: z.enum(["claude", "rules"]),
  claudeCalls: z.number().int().nonnegative(),
});

export type TripTalkDecision = z.infer<typeof tripTalkDecisionSchema>;

const talkSchema = z.strictObject({
  clientId: z.string().trim().min(1).max(200),
  said: spoken(500),
  context: tripTalkContextSchema,
});

export type TripTalk = { clientId: string; said: string; context: TripTalkContext };

export type TripTalkDeps = {
  findClient: (clientId: string) => KnownClient | null;
  talk: (talk: TripTalk) => Promise<unknown>;
};

export const tripTalkCopy = {
  sayWhat: "Say which client, what you said, and where the trip stands.",
};

function knownRide(decision: TripTalkDecision, context: TripTalkContext) {
  return decision.option === null || (context.options ?? []).some((entry) => entry.id === decision.option);
}

export async function handleTripTalk(request: RequestLike, deps: TripTalkDeps) {
  if (!isSameOrigin(request)) return problem(403, routeCopy.notThisPage);
  const parsed = talkSchema.safeParse(await readBody(request));
  if (!parsed.success) return problem(400, tripTalkCopy.sayWhat);
  const client = deps.findClient(parsed.data.clientId);
  if (!client) return problem(404, routeCopy.noClient);
  try {
    const answered = tripTalkDecisionSchema.safeParse(await deps.talk({ ...parsed.data, clientId: client.id }));
    if (!answered.success || !knownRide(answered.data, parsed.data.context)) return problem(502, routeCopy.unavailable);
    return reply(answered.data, 200);
  } catch (error) {
    if (error instanceof AgentFieldError) return problem(502, routeCopy.unavailable);
    throw error;
  }
}
