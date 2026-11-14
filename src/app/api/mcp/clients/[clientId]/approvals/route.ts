import { handleMcpWaitingApprovals, liveMcpDeps } from "@/lib/mcpRoutes";

export async function GET(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  return handleMcpWaitingApprovals(request, clientId, liveMcpDeps());
}
