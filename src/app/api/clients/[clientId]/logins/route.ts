import { handleAddLogins } from "@/lib/setupRoutes";
import { setupContext } from "@/lib/setupServer";

export async function POST(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  const context = setupContext(request);
  return context.respond(await handleAddLogins(request, clientId, context.deps));
}
