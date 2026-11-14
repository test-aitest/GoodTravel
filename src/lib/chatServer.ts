import { agentFieldConfig, readClientMemory, runSkill, writeClientMemory } from "./agentfieldClient";
import { createChatStore } from "./chatStore";
import { findClient } from "./clients";

const REPLY_WAIT_MS = 30_000;

export function liveChatStore() {
  const config = agentFieldConfig();
  return {
    findClient,
    store: createChatStore({
      get: (clientId, key) => readClientMemory(config, clientId, key),
      set: (clientId, key, data) => writeClientMemory(config, clientId, key, data),
    }),
  };
}

export function liveChat() {
  const config = agentFieldConfig();
  return {
    findClient,
    reply: (clientId: string, message: string, facts: { id: string; about: string }[]) => runSkill(config, "chat_reply", { client_id: clientId, message, facts }, { timeoutMs: REPLY_WAIT_MS }),
  };
}
