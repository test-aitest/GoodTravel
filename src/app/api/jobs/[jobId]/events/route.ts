import { agentFieldConfig, readExecution } from "@/lib/agentfieldClient";
import { handleJobEvents } from "@/lib/jobRoutes";

export async function GET(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  const config = agentFieldConfig();
  return handleJobEvents(request, jobId, { read: (executionId) => readExecution(config, executionId) });
}
