import { agentFieldConfig, startRunJob } from "@/lib/agentfieldClient";
import { handleCreateJob } from "@/lib/jobRoutes";
import { findClient } from "@/lib/clients";

export async function POST(request: Request) {
  const config = agentFieldConfig();
  return handleCreateJob(request, { start: (job) => startRunJob(config, job), findClient });
}
