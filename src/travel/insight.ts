export type Insight = { line: string; source: { title: string; url: string } };

export async function loadInsight(destination: string, month: string, fetcher: typeof fetch = fetch): Promise<Insight | null> {
  const reply = await fetcher("/api/trips/insight", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ destination, month }) }).catch(() => null);
  if (!reply?.ok) return null;
  const body: unknown = await reply.json().catch(() => null);
  if (typeof body !== "object" || body === null) return null;
  const { line, source } = body as { line?: unknown; source?: { title?: unknown; url?: unknown } };
  if (typeof line !== "string" || typeof source?.title !== "string" || typeof source.url !== "string") return null;
  return { line, source: { title: source.title, url: source.url } };
}
