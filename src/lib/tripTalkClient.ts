import type { TripTalkContext, TripTalkDecision } from "./tripTalkRoutes";

export type { TripTalkContext, TripTalkDecision };

const unavailable = "The worker service is not available right now.";

export async function talkAboutTrip(clientId: string, said: string, context: TripTalkContext, fetcher: typeof fetch = fetch): Promise<TripTalkDecision | { error: string }> {
  const response = await fetcher("/api/trips/talk", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ clientId, said, context }),
  }).catch(() => null);
  const parsed: unknown = response ? await response.json().catch(() => null) : null;
  const body = typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : {};
  if (response?.ok && typeof body.reply === "string" && typeof body.action === "string") return body as TripTalkDecision;
  return { error: typeof body.error === "string" ? body.error : unavailable };
}
