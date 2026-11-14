import { z } from "zod";
import { AgentFieldError } from "./agentfieldClient";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

export type AgentReset =
  | { clientId: string; ok: true; sites: "reset" | "skipped"; forgotten: number }
  | { clientId: string; ok: false; problem: string };

export type DemoResetDeps = {
  clients: readonly { id: string }[];
  resetAgent: (clientId: string) => Promise<AgentReset>;
  waitingJobIds: (clientId: string) => Promise<string[]>;
  stop: (jobId: string) => Promise<unknown>;
  scheduleOff: (clientId: string) => Promise<void>;
};

const agentResetSchema = z.discriminatedUnion("ok", [
  z.object({ clientId: z.string(), ok: z.literal(true), sites: z.enum(["reset", "skipped"]), forgotten: z.number().int().nonnegative() }),
  z.object({ clientId: z.string(), ok: z.literal(false), problem: z.string().min(1) }),
]);

export function readAgentReset(clientId: string, raw: unknown): AgentReset {
  const parsed = agentResetSchema.safeParse(raw);
  if (!parsed.success || parsed.data.clientId !== clientId) return { clientId, ok: false, problem: "unreadable" };
  return parsed.data;
}

const copy = {
  notThisPage: "Only the ClientOps page can do this.",
  notEnabled: "Demo reset is off. Start the worker with CLIENTOPS_DEMO_RESET=on to use it.",
  failed: "The worker couldn't reset the demo. Nothing else was changed.",
  unavailable: "The worker service is not available right now.",
};

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

export async function handleDemoReset(request: RequestLike, deps: DemoResetDeps) {
  if (!isSameOrigin(request)) return reply({ error: copy.notThisPage }, 403);
  const clients = [];
  try {
    for (const client of deps.clients) {
      const agent = await deps.resetAgent(client.id);
      if (!agent.ok) return reply({ error: agent.problem === "not-enabled" ? copy.notEnabled : copy.failed }, 409);
      const waiting = await deps.waitingJobIds(client.id);
      for (const jobId of waiting) await deps.stop(jobId);
      await deps.scheduleOff(client.id);
      clients.push({ clientId: client.id, stopped: waiting.length, sites: agent.sites, forgotten: agent.forgotten, dailyRun: "off" });
    }
  } catch (error) {
    if (error instanceof AgentFieldError) return reply({ error: copy.unavailable }, 502);
    throw error;
  }
  return reply({ clients });
}
