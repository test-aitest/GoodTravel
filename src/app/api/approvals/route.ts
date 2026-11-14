import { agentFieldConfig, sendApprovalResponse } from "@/lib/agentfieldClient";
import { handleApprove, readJob } from "@/lib/approvalRoutes";

export async function POST(request: Request) {
  const config = agentFieldConfig();
  return handleApprove(request, {
    secret: process.env.AGENTFIELD_APPROVAL_WEBHOOK_SECRET || undefined,
    readJob: (jobId) => readJob(config, jobId),
    sendApproval: (rawBody, signature) => sendApprovalResponse(config, rawBody, signature),
  });
}
