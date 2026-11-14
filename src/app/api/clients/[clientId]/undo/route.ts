import { agentFieldConfig, startUndoSync } from "@/lib/agentfieldClient";
import { handleUndo } from "@/lib/approvalRoutes";
import { findClient } from "@/lib/clients";

export async function POST(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  const config = agentFieldConfig();
  return handleUndo(request, clientId, { findClient, startUndo: (id) => startUndoSync(config, id) });
}
