import { agentFieldConfig, waitingJobIds } from "@/lib/agentfieldClient";
import { findClient } from "@/lib/clients";
import { handleWaitingJobs } from "@/lib/waitingRoutes";

export async function GET(_request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  const config = agentFieldConfig();
  return handleWaitingJobs(clientId, { findClient, waitingJobIds: (id) => waitingJobIds(config, id) });
}
