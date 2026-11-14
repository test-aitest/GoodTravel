import { liveChat } from "@/lib/chatServer";
import { handleChat } from "@/lib/chatStore";

export async function POST(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  return handleChat(request, clientId, liveChat());
}
