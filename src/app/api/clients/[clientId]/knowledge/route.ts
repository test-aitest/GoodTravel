import { agentFieldConfig, readClientMemory, writeClientMemory } from "@/lib/agentfieldClient";
import { findClient } from "@/lib/clients";
import { createKnowledgeStore, handleReadKnowledge, handleRememberTrip } from "@/lib/tripKnowledgeRoutes";

type Params = { params: Promise<{ clientId: string }> };

function liveKnowledge() {
  const config = agentFieldConfig();
  return {
    findClient,
    store: createKnowledgeStore({
      get: (clientId, key) => readClientMemory(config, clientId, key),
      set: (clientId, key, data) => writeClientMemory(config, clientId, key, data),
    }),
  };
}

export async function GET(_request: Request, { params }: Params) {
  const { clientId } = await params;
  return handleReadKnowledge(clientId, liveKnowledge());
}

export async function POST(request: Request, { params }: Params) {
  const { clientId } = await params;
  return handleRememberTrip(request, clientId, liveKnowledge());
}
