import type { FactId } from "@/lib/chatFacts";
import { chatThreadSchema, type ChatDecision, type ChatThread, type ChatThreadEntry } from "@/lib/chatStore";

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

const unavailable = "The worker service is not available right now.";

async function send(fetcher: Fetcher, url: string, init?: RequestInit) {
  const response = await fetcher(url, init).catch(() => null);
  if (!response) return null;
  const body: unknown = await response.json().catch(() => null);
  return { ok: response.ok, body };
}

function errorOf(body: unknown) {
  return body && typeof body === "object" && "error" in body && typeof body.error === "string" ? body.error : unavailable;
}

const path = (clientId: string) => `/api/clients/${encodeURIComponent(clientId)}`;

export function createChatApi(fetcher: Fetcher = (input, init) => fetch(input, init)) {
  return {
    async ask(clientId: string, message: string, facts: FactId[]): Promise<ChatDecision | { error: string }> {
      const reply = await send(fetcher, `${path(clientId)}/chat`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ message, facts }) });
      if (!reply?.ok || !reply.body || typeof reply.body !== "object" || !("action" in reply.body)) return { error: errorOf(reply?.body) };
      return reply.body as ChatDecision;
    },
    async list(clientId: string): Promise<ChatThreadEntry[]> {
      const reply = await send(fetcher, `${path(clientId)}/threads`);
      const threads = reply?.ok && reply.body && typeof reply.body === "object" && "threads" in reply.body ? reply.body.threads : null;
      return Array.isArray(threads) ? (threads as ChatThreadEntry[]) : [];
    },
    async read(clientId: string, id: string): Promise<ChatThread | null> {
      const reply = await send(fetcher, `${path(clientId)}/threads/${encodeURIComponent(id)}`);
      const parsed = chatThreadSchema.safeParse(reply?.ok ? reply.body : null);
      return parsed.success ? parsed.data : null;
    },
    async save(thread: ChatThread): Promise<boolean> {
      const reply = await send(fetcher, `${path(thread.clientId)}/threads/${encodeURIComponent(thread.id)}`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(thread) });
      return Boolean(reply?.ok);
    },
  };
}

export const chatApi = createChatApi();
export type ChatApi = ReturnType<typeof createChatApi>;
