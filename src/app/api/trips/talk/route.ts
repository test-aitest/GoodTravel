import { agentFieldConfig, runSkill } from "@/lib/agentfieldClient";
import { findClient } from "@/lib/clients";
import { handleTripTalk } from "@/lib/tripTalkRoutes";

const TALK_WAIT_MS = 30_000;

export async function POST(request: Request) {
  const config = agentFieldConfig();
  return handleTripTalk(request, {
    findClient,
    talk: ({ clientId, said, context }) => runSkill(config, "trip_talk", { client_id: clientId, said, context }, { timeoutMs: TALK_WAIT_MS }),
  });
}
