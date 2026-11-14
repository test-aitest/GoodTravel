import { handleMcpJob, liveMcpDeps } from "@/lib/mcpRoutes";

export async function GET(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  return handleMcpJob(request, jobId, liveMcpDeps());
}
