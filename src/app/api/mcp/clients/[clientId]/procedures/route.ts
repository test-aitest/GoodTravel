import { handleMcpSavedProcedures, liveMcpDeps } from "@/lib/mcpRoutes";

export async function GET(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  return handleMcpSavedProcedures(request, clientId, liveMcpDeps());
}
