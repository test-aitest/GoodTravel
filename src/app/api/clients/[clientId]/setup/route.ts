import { handleReadSetup } from "@/lib/setupRoutes";
import { setupContext } from "@/lib/setupServer";

export async function GET(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  const context = setupContext(request);
  return context.respond(await handleReadSetup(request, clientId, context.deps));
}
