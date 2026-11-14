const unavailable = "The worker service is not available right now.";

async function start(path: string, body: Record<string, string>, fetcher: typeof fetch): Promise<{ jobId: string } | { error: string }> {
  const response = await fetcher(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => null);
  const parsed: unknown = response ? await response.json().catch(() => null) : null;
  const reply = typeof parsed === "object" && parsed !== null ? (parsed as { jobId?: unknown; error?: unknown }) : {};
  if (response?.ok && typeof reply.jobId === "string") return { jobId: reply.jobId };
  return { error: typeof reply.error === "string" ? reply.error : unavailable };
}

export function startTripSearch(clientId: string, to: string, date: string, fetcher: typeof fetch = fetch) {
  return start("/api/trips/search", { clientId, to, date }, fetcher);
}

export function startTripBooking(clientId: string, option: string, seat: string, fetcher: typeof fetch = fetch) {
  return start("/api/trips/book", { clientId, option, seat }, fetcher);
}
