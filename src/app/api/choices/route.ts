import { agentFieldConfig, sendApprovalResponse } from "@/lib/agentfieldClient";
import { readJob } from "@/lib/approvalRoutes";
import { handleChoose } from "@/lib/choiceRoutes";

export async function POST(request: Request) {
  const config = agentFieldConfig();
  return handleChoose(request, {
    secret: process.env.AGENTFIELD_APPROVAL_WEBHOOK_SECRET || undefined,
    readJob: (jobId) => readJob(config, jobId),
    sendApproval: (rawBody, signature) => sendApprovalResponse(config, rawBody, signature),
  });
}
