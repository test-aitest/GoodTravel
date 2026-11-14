const unavailable = "The worker service is not available right now.";

type Fetcher = typeof fetch;
type Reply = { status: number; body: Record<string, unknown> };

async function send(fetcher: Fetcher, path: string, body?: unknown): Promise<Reply | null> {
  const init: RequestInit = { method: "POST" };
  if (body !== undefined) {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const response = await fetcher(path, init).catch(() => null);
  if (!response) return null;
  const parsed: unknown = await response.json().catch(() => null);
  return { status: response.status, body: typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : {} };
}

function failure(reply: Reply | null) {
  return { error: reply && typeof reply.body.error === "string" ? reply.body.error : unavailable };
}

export async function approve(jobId: string, approvalId: string, fetcher: Fetcher = fetch): Promise<{ ok: true } | { error: string }> {
  const reply = await send(fetcher, "/api/approvals", { jobId, approvalId });
  return reply && reply.status === 200 ? { ok: true } : failure(reply);
}

export async function choose(jobId: string, choiceId: string, ref: string | null, fetcher: Fetcher = fetch): Promise<{ ok: true } | { error: string }> {
  const reply = await send(fetcher, "/api/choices", { jobId, choiceId, ref });
  return reply && reply.status === 200 ? { ok: true } : failure(reply);
}

export async function undo(clientId: string, fetcher: Fetcher = fetch): Promise<{ jobId: string } | { error: string }> {
  const reply = await send(fetcher, `/api/clients/${encodeURIComponent(clientId)}/undo`);
  return reply && reply.status === 201 && typeof reply.body.jobId === "string" ? { jobId: reply.body.jobId } : failure(reply);
}

export async function stop(jobId: string, fetcher: Fetcher = fetch): Promise<{ ok: true } | { error: string }> {
  const reply = await send(fetcher, `/api/jobs/${encodeURIComponent(jobId)}/stop`);
  return reply && reply.status === 200 ? { ok: true } : failure(reply);
}
