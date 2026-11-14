import { handleSetUseClaude } from "@/lib/setupRoutes";
import { setupContext } from "@/lib/setupServer";

export async function PUT(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  const context = setupContext(request);
  return context.respond(await handleSetUseClaude(request, clientId, context.deps));
}
