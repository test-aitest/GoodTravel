import source from "../../config/clients.json";
import type { ConsoleClientId } from "./consoleState";

export type ConsoleClient = {
  id: ConsoleClientId;
  name: string;
  shortName: string;
  nickname: string;
  storeHost: string;
  starters: { text: string; note: string }[];
};

const consoleDetails: Record<ConsoleClientId, Pick<ConsoleClient, "storeHost" | "starters">> = {
  kumo: {
    storeHost: "admin.kumo-coffee.example",
    starters: [{ text: "Sync today's supplier stock to the store admin", note: "New job" }],
  },
  hinata: { storeHost: "admin.hinata-skincare.example", starters: [] },
  sora: { storeHost: "admin.sora-outdoor.example", starters: [] },
};

function isConsoleClientId(id: string): id is ConsoleClientId {
  return id in consoleDetails;
}

export const consoleClients: ConsoleClient[] = source.clients
  .filter((client) => isConsoleClientId(client.id))
  .map((client) => ({ ...client, id: client.id as ConsoleClientId, ...consoleDetails[client.id as ConsoleClientId] }));

export function clientById(id: ConsoleClientId) {
  const client = consoleClients.find((candidate) => candidate.id === id);
  if (!client) throw new Error(`Unknown client ${id}`);
  return client;
}
