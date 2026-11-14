import { handleMcpStartJob, liveMcpDeps } from "@/lib/mcpRoutes";

export async function POST(request: Request) {
  return handleMcpStartJob(request, liveMcpDeps());
}
