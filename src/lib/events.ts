import { z } from "zod";

export const rungs = ["script", "tree", "claude", "human"] as const;
export const rungSchema = z.enum(rungs);
export type Rung = z.infer<typeof rungSchema>;

const identifier = z.string().min(1);
const label = z.string().trim().min(1);
const count = z.number().int().nonnegative();
const milliseconds = z.number().nonnegative();
const version = z.number().int().positive();

const envelope = {
  id: identifier,
  seq: z.number().int().positive(),
  jobId: identifier,
  clientId: identifier,
  at: z.iso.datetime(),
};

export const planStepSchema = z.strictObject({ stepId: identifier, title: label });
export type PlanStep = z.infer<typeof planStepSchema>;

export const stockChangeSchema = z.strictObject({ sku: identifier, name: label, store: count, supplier: count });
export type StockChange = z.infer<typeof stockChangeSchema>;

export const leftAloneSchema = z.strictObject({ onlyInStore: count, onlyAtSupplier: count });
export type LeftAlone = z.infer<typeof leftAloneSchema>;

export const procedureChangeSchema = z
  .strictObject({ stepId: identifier, fromVersion: version, toVersion: version, before: label, after: label })
  .refine((change) => change.toVersion > change.fromVersion, "toVersion must be newer than fromVersion");
export type ProcedureChange = z.infer<typeof procedureChangeSchema>;

export const choiceCandidateSchema = z.strictObject({ ref: identifier, role: label, name: label });
export type ChoiceCandidate = z.infer<typeof choiceCandidateSchema>;

export const runSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("learn") }),
  z.strictObject({ kind: z.literal("replay"), procedureVersion: version }),
  z.strictObject({ kind: z.literal("undo") }),
]);
export type Run = z.infer<typeof runSchema>;

export const tripKinds = ["rail", "flight"] as const;
export const seatPositions = ["window", "middle", "aisle"] as const;
export const seatReasons = ["window", "aisle", "quiet-front", "exit-legroom", "together"] as const;

const optionId = z.string().regex(/^[a-z0-9][a-z0-9-]{0,39}$/, "an option id like nozomi-21");
const seatId = z.string().regex(/^\d{1,2}[A-F]$/, "a seat like 12C");
const clock = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "a time like 08:05");
const currency = z.string().regex(/^[A-Z]{3}$/, "a currency code like JPY");
const price = z.number().nonnegative();

export const bookingSchema = z.strictObject({ option: optionId, seat: seatId });
export type Booking = z.infer<typeof bookingSchema>;

export const tripSeatSchema = z.strictObject({
  id: seatId,
  position: z.enum(seatPositions),
  exitRow: z.boolean(),
  available: z.boolean(),
});
export type TripSeat = z.infer<typeof tripSeatSchema>;

export const seatSuggestionSchema = z.strictObject({
  seat: seatId,
  reason: z.enum(seatReasons),
  by: z.enum(["claude", "rule"]),
});
export type SeatSuggestion = z.infer<typeof seatSuggestionSchema>;

export const tripOptionSchema = z
  .strictObject({
    id: optionId,
    name: label,
    kind: z.enum(tripKinds),
    depart: clock,
    arrive: clock,
    currency,
    price,
    seats: z.array(tripSeatSchema).refine((seats) => new Set(seats.map((seat) => seat.id)).size === seats.length, "Seat ids must be unique"),
    suggestion: seatSuggestionSchema.nullable(),
  })
  .refine((option) => option.suggestion === null || option.seats.some((seat) => seat.id === option.suggestion?.seat), "The suggested seat must be one of the ride's seats");
export type TripOption = z.infer<typeof tripOptionSchema>;

export const failureReasons = ["stopped", "error", "verify-mismatch"] as const;

export const pageHtmlMaxLength = 262_144;
const host = z.string().regex(/^(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:\d{1,5})?$/i, "host is a host name with an optional port");

export const jobEventSchema = z.discriminatedUnion("type", [
  z.strictObject({
    ...envelope,
    type: z.literal("job.created"),
    clientName: label,
    title: label,
    request: label,
    run: runSchema,
    plan: z
      .array(planStepSchema)
      .refine((plan) => new Set(plan.map((step) => step.stepId)).size === plan.length, "Step ids must be unique"),
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("step.started"),
    stepId: identifier,
    activity: label,
    detail: label.optional(),
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("step.decided"),
    stepId: identifier,
    rung: rungSchema,
    tookMs: milliseconds,
    escalatedFrom: z.array(rungSchema).min(1).optional(),
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("step.done"),
    stepId: identifier,
    summary: label,
    detail: label.optional(),
    tookMs: milliseconds,
  }),
  z
    .strictObject({
      ...envelope,
      type: z.literal("approval.requested"),
      approvalId: identifier,
      stepId: identifier,
      question: label,
      changes: z.array(stockChangeSchema).min(1).optional(),
      booking: bookingSchema.optional(),
      undoable: z.boolean(),
      leftAlone: leftAloneSchema.optional(),
      procedureChange: procedureChangeSchema.optional(),
    })
    .refine((event) => event.changes !== undefined || event.booking !== undefined, "An approval asks about stock changes or a booking"),
  z.strictObject({
    ...envelope,
    type: z.literal("approval.answered"),
    approvalId: identifier,
    decision: z.literal("approved"),
    by: label,
    feedback: label.optional(),
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("choice.requested"),
    choiceId: identifier,
    stepId: identifier,
    question: label,
    before: label,
    candidates: z
      .array(choiceCandidateSchema)
      .min(1)
      .refine((candidates) => new Set(candidates.map((candidate) => candidate.ref)).size === candidates.length, "Candidate refs must be unique"),
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("choice.answered"),
    choiceId: identifier,
    ref: identifier.nullable(),
    by: label,
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("instruction.ignored"),
    stepId: identifier,
    host,
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("write.done"),
    stepId: identifier,
    written: count,
  }),
  z
    .strictObject({
      ...envelope,
      type: z.literal("verify.done"),
      stepId: identifier,
      checked: count,
      matched: count,
    })
    .refine((event) => event.matched <= event.checked, "matched cannot exceed checked"),
  z.strictObject({
    ...envelope,
    type: z.literal("procedure.saved"),
    procedureId: identifier,
    version,
    stepCount: z.number().int().positive(),
    expectedReplayMs: milliseconds,
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("job.failed"),
    reason: z.enum(failureReasons),
    message: label,
    stepId: identifier.optional(),
  }),
  z
    .strictObject({
      ...envelope,
      type: z.literal("boundary.blocked"),
      resource: label,
      ownerClientId: identifier,
      ownerClientName: label,
      auditId: identifier,
      offersOwnAccount: z.boolean(),
    })
    .refine((event) => event.ownerClientId !== event.clientId, "A client cannot be blocked from its own resources"),
  z.strictObject({
    ...envelope,
    type: z.literal("page.seen"),
    stepId: identifier,
    host,
    html: z.string().min(1).max(pageHtmlMaxLength),
  }),
  z
    .strictObject({
      ...envelope,
      type: z.literal("cost.recorded"),
      claudeCalls: count,
      inputTokens: count,
      outputTokens: count,
      cacheReadTokens: count,
      cacheWriteTokens: count,
      claudeUsd: z.number().nonnegative(),
      computerSeconds: count,
      estimated: z.boolean(),
    })
    .refine(
      (event) => event.claudeCalls > 0 || event.claudeUsd + event.inputTokens + event.outputTokens + event.cacheReadTokens + event.cacheWriteTokens === 0,
      "A job with no Claude calls has no Claude tokens or cost",
    ),
  z.strictObject({
    ...envelope,
    type: z.literal("trip.options"),
    to: label,
    date: z.iso.date(),
    options: z.array(tripOptionSchema).refine((options) => new Set(options.map((option) => option.id)).size === options.length, "Option ids must be unique"),
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("trip.review"),
    option: optionId,
    seat: seatId,
    name: label,
    depart: clock.optional(),
    arrive: clock.optional(),
    currency: currency.optional(),
    price: price.optional(),
    hold: z.string().regex(/^[A-Za-z0-9_-]{1,64}$/).optional(),
  }),
  z.strictObject({
    ...envelope,
    type: z.literal("trip.booked"),
    option: optionId,
    seat: seatId,
    reference: z.string().regex(/^[A-Z]{6}$/, "a six-letter reference"),
  }),
]);

export type JobEvent = z.infer<typeof jobEventSchema>;
export type JobEventType = JobEvent["type"];
export type JobEventOf<T extends JobEventType> = Extract<JobEvent, { type: T }>;
type EnvelopeKey = keyof typeof envelope | "type";
export type JobEventBody<T extends JobEventType> = Omit<JobEventOf<T>, EnvelopeKey>;

export const jobEventTypes: readonly JobEventType[] = jobEventSchema.options.map((option) => option.shape.type.value);

export function parseJobEvent(input: unknown): JobEvent {
  return jobEventSchema.parse(input);
}

export function safeParseJobEvent(input: unknown) {
  return jobEventSchema.safeParse(input);
}

export function eventWriter(job: { jobId: string; clientId: string; startAt: string }) {
  const start = Date.parse(job.startAt);
  let seq = 0;
  return <T extends JobEventType>(type: T, body: JobEventBody<T>, offsetMs: number): JobEventOf<T> => {
    seq += 1;
    return parseJobEvent({
      id: `${job.jobId}-${seq}`,
      seq,
      jobId: job.jobId,
      clientId: job.clientId,
      at: new Date(start + offsetMs).toISOString(),
      type,
      ...body,
    }) as JobEventOf<T>;
  };
}
