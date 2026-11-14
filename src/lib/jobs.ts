import { z } from "zod";
import { AgentFieldError } from "./agentfieldClient";

const newJobSchema = z.object({
  clientId: z.string().trim().min(1),
  request: z.string().trim().min(1).max(2000),
});

export type KnownClient = { id: string; name: string };

export type JobStarter = (job: { clientId: string; clientName: string; request: string }) => Promise<{ executionId: string; runId: string }>;

export type CreateJobReply =
  | { status: 201; body: { jobId: string; runId: string } }
  | { status: 400 | 404 | 502; body: { error: string } };

export async function createJob(
  raw: unknown,
  deps: { start: JobStarter; findClient: (clientId: string) => KnownClient | null },
): Promise<CreateJobReply> {
  const parsed = newJobSchema.safeParse(raw);
  if (!parsed.success) return { status: 400, body: { error: "Say which client and what the job is." } };

  const client = deps.findClient(parsed.data.clientId);
  if (!client) return { status: 404, body: { error: "This client is not set up." } };

  try {
    const started = await deps.start({ clientId: client.id, clientName: client.name, request: parsed.data.request });
    return { status: 201, body: { jobId: started.executionId, runId: started.runId } };
  } catch (error) {
    if (error instanceof AgentFieldError) return { status: 502, body: { error: "The worker service is not available right now." } };
    throw error;
  }
}
