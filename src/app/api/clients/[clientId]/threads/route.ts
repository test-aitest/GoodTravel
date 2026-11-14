import { liveChatStore } from "@/lib/chatServer";
import { handleListThreads } from "@/lib/chatStore";

export async function GET(_request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  return handleListThreads(clientId, liveChatStore());
}
