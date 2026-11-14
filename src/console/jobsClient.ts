"use client";

import { readFrame, type LiveFrame } from "./liveJob";

const frameTypes = ["job", "end", "missing", "unavailable"] as const;
const unavailable = "The worker service is not available right now.";

async function readReply(response: Response | null) {
  const body: unknown = response ? await response.json().catch(() => null) : null;
  return typeof body === "object" && body !== null ? (body as { jobId?: unknown; error?: unknown }) : {};
}

export async function createJob(clientId: string, request: string): Promise<{ jobId: string } | { error: string }> {
  const response = await fetch("/api/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ clientId, request }),
  }).catch(() => null);
  const reply = await readReply(response);
  if (response?.ok && typeof reply.jobId === "string") return { jobId: reply.jobId };
  return { error: typeof reply.error === "string" ? reply.error : unavailable };
}

export async function recheckLogins(clientId: string) {
  await fetch(`/api/clients/${encodeURIComponent(clientId)}/logins/check`, { method: "POST" }).catch(() => null);
}

export function followJob(jobId: string, onFrame: (frame: LiveFrame) => void) {
  const events = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
  for (const type of frameTypes) {
    events.addEventListener(type, (message) => {
      const frame = readFrame(type, (message as MessageEvent<string>).data);
      onFrame(frame);
      if (frame.kind === "end" || frame.kind === "missing" || frame.kind === "unavailable") events.close();
    });
  }
  return () => events.close();
}
