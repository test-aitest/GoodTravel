import { AgentFieldError } from "./agentfieldClient";
import type { KnownClient } from "./jobs";

export type WaitingDeps = {
  findClient: (clientId: string) => KnownClient | null;
  waitingJobIds: (clientId: string) => Promise<string[]>;
};

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

export async function handleWaitingJobs(clientId: string, deps: WaitingDeps) {
  const client = deps.findClient(clientId);
  if (!client) return reply({ error: "There is no such client." }, 404);
  try {
    return reply({ clientId: client.id, jobIds: await deps.waitingJobIds(client.id) });
  } catch (error) {
    if (error instanceof AgentFieldError) return reply({ error: "The worker service is not available right now." }, 502);
    throw error;
  }
}
