import { agentFieldConfig, cancelExecution } from "@/lib/agentfieldClient";
import { handleStop, readJob, stopReason } from "@/lib/approvalRoutes";

export async function POST(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  const config = agentFieldConfig();
  return handleStop(request, jobId, {
    readJob: (id) => readJob(config, id),
    cancel: (id) => cancelExecution(config, id, stopReason),
  });
}
