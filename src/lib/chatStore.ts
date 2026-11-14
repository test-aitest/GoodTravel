import { z } from "zod";
import { AgentFieldError } from "./agentfieldClient";
import { factAbout, factIds, type FactId } from "./chatFacts";
import type { KnownClient } from "./jobs";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

const identifier = z.string().regex(/^[A-Za-z0-9_-]{1,120}$/);
const instant = z.iso.datetime();
const factIdSchema = z.enum(factIds);
export const chatActions = ["answer", "run", "undo", "schedule", "clarify", "cannot"] as const;
export const clarifyKinds = ["which-job", "what-to-do"] as const;

const turnSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("you"), text: z.string().trim().min(1).max(2000), at: instant }),
  z.strictObject({ kind: z.literal("job"), jobId: identifier, trigger: z.enum(["request", "run-now", "undo", "reply"]), at: instant }),
  z.strictObject({
    kind: z.literal("reply"),
    action: z.enum(chatActions),
    facts: z.array(factIdSchema).max(factIds.length),
    clarify: z.enum(clarifyKinds).nullable(),
    lines: z.array(z.string().trim().min(1).max(500)).max(80).optional(),
    at: instant,
  }),
]);

export const chatThreadSchema = z.strictObject({
  version: z.literal(1),
  id: identifier,
  clientId: identifier,
  title: z.string().trim().min(1).max(200),
  createdAt: instant,
  updatedAt: instant,
  turns: z.array(turnSchema).max(500),
});

const indexEntrySchema = z.strictObject({ id: identifier, title: z.string(), updatedAt: instant });
const indexSchema = z.strictObject({ version: z.literal(1), threads: z.array(indexEntrySchema) });

export type ChatTurn = z.infer<typeof turnSchema>;
export type ChatThread = z.infer<typeof chatThreadSchema>;
export type ChatThreadEntry = z.infer<typeof indexEntrySchema>;

export type ClientMemoryAccess = {
  get: (clientId: string, key: string) => Promise<unknown>;
  set: (clientId: string, key: string, data: unknown) => Promise<void>;
};

const INDEX_KEY = "chat/threads";
const threadKey = (id: string) => `chat/thread/${id}`;

export function createChatStore(memory: ClientMemoryAccess) {
  const list = async (clientId: string): Promise<ChatThreadEntry[]> => {
    const parsed = indexSchema.safeParse(await memory.get(clientId, INDEX_KEY));
    return parsed.success ? parsed.data.threads.toSorted((a, b) => b.updatedAt.localeCompare(a.updatedAt)) : [];
  };
  return {
    list,
    async read(clientId: string, id: string): Promise<ChatThread | null> {
      const parsed = chatThreadSchema.safeParse(await memory.get(clientId, threadKey(id)));
      return parsed.success && parsed.data.clientId === clientId ? parsed.data : null;
    },
    async write(thread: ChatThread) {
      await memory.set(thread.clientId, threadKey(thread.id), thread);
      const others = (await list(thread.clientId)).filter((entry) => entry.id !== thread.id);
      await memory.set(thread.clientId, INDEX_KEY, { version: 1, threads: [{ id: thread.id, title: thread.title, updatedAt: thread.updatedAt }, ...others] });
    },
  };
}

export type ChatStore = ReturnType<typeof createChatStore>;
export type StoreDeps = { findClient: (clientId: string) => KnownClient | null; store: ChatStore };

const copy = {
  notThisPage: "Only the ClientOps page can do this.",
  noClient: "There is no such client.",
  noThread: "There is no such conversation.",
  badThread: "That conversation could not be saved.",
  badMessage: "Say what you want and which facts the conversation has.",
  unreadable: "The worker's answer could not be read.",
  unavailable: "The worker service is not available right now.",
};

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

async function bodyOf(request: RequestLike) {
  const text = await request.text().catch(() => "");
  if (text.length > 256 * 1024) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function guarded(error: unknown) {
  if (error instanceof AgentFieldError) return reply({ error: copy.unavailable }, 502);
  throw error;
}

export async function handleListThreads(clientId: string, deps: StoreDeps) {
  const client = deps.findClient(clientId);
  if (!client) return reply({ error: copy.noClient }, 404);
  try {
    return reply({ clientId: client.id, threads: await deps.store.list(client.id) });
  } catch (error) {
    return guarded(error);
  }
}

export async function handleReadThread(clientId: string, threadId: string, deps: StoreDeps) {
  const client = deps.findClient(clientId);
  if (!client) return reply({ error: copy.noClient }, 404);
  try {
    const thread = await deps.store.read(client.id, threadId);
    return thread ? reply(thread) : reply({ error: copy.noThread }, 404);
  } catch (error) {
    return guarded(error);
  }
}

export async function handleSaveThread(request: RequestLike, clientId: string, threadId: string, deps: StoreDeps) {
  if (!isSameOrigin(request)) return reply({ error: copy.notThisPage }, 403);
  const client = deps.findClient(clientId);
  if (!client) return reply({ error: copy.noClient }, 404);
  const parsed = chatThreadSchema.safeParse(await bodyOf(request));
  if (!parsed.success || parsed.data.clientId !== client.id || parsed.data.id !== threadId) return reply({ error: copy.badThread }, 400);
  try {
    await deps.store.write(parsed.data);
    return reply({ clientId: client.id, id: threadId, saved: true });
  } catch (error) {
    return guarded(error);
  }
}

const chatRequestSchema = z.strictObject({ message: z.string().trim().min(1).max(2000), facts: z.array(factIdSchema).max(factIds.length) });

const workerAnswerSchema = z.strictObject({
  clientId: z.string(),
  action: z.enum(chatActions),
  facts: z.array(factIdSchema),
  clarify: z.enum(clarifyKinds).nullable(),
  decidedBy: z.enum(["claude", "rules"]),
  claudeCalls: z.number().int().nonnegative(),
});

export type ChatDecision = { action: (typeof chatActions)[number]; facts: FactId[]; clarify: (typeof clarifyKinds)[number] | null };

export type ChatDeps = {
  findClient: (clientId: string) => KnownClient | null;
  reply: (clientId: string, message: string, facts: { id: FactId; about: string }[]) => Promise<unknown>;
};

export async function handleChat(request: RequestLike, clientId: string, deps: ChatDeps) {
  if (!isSameOrigin(request)) return reply({ error: copy.notThisPage }, 403);
  const client = deps.findClient(clientId);
  if (!client) return reply({ error: copy.noClient }, 404);
  const parsed = chatRequestSchema.safeParse(await bodyOf(request));
  if (!parsed.success) return reply({ error: copy.badMessage }, 400);
  const facts = parsed.data.facts.map((id) => ({ id, about: factAbout(id) }));
  try {
    const answer = workerAnswerSchema.safeParse(await deps.reply(client.id, parsed.data.message, facts));
    if (!answer.success || answer.data.clientId !== client.id || answer.data.facts.some((id) => !parsed.data.facts.includes(id))) return reply({ error: copy.unreadable }, 502);
    const decision: ChatDecision = { action: answer.data.action, facts: answer.data.facts, clarify: answer.data.clarify };
    return reply(decision);
  } catch (error) {
    return guarded(error);
  }
}

