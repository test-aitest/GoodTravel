import { liveChatStore } from "@/lib/chatServer";
import { handleReadThread, handleSaveThread } from "@/lib/chatStore";

type Params = { params: Promise<{ clientId: string; threadId: string }> };

export async function GET(_request: Request, { params }: Params) {
  const { clientId, threadId } = await params;
  return handleReadThread(clientId, threadId, liveChatStore());
}

export async function PUT(request: Request, { params }: Params) {
  const { clientId, threadId } = await params;
  return handleSaveThread(request, clientId, threadId, liveChatStore());
}
