import { z } from "zod";
import source from "../../config/clients.json";
import type { KnownClient } from "./jobs";

const clientListSchema = z.strictObject({
  version: z.literal(1),
  clients: z
    .array(
      z.strictObject({
        id: z.string().regex(/^[a-z0-9][a-z0-9-]{0,62}$/),
        name: z.string().trim().min(1),
        shortName: z.string().trim().min(1),
        nickname: z.string().trim().min(1),
      }),
    )
    .min(1)
    .refine((clients) => new Set(clients.map((client) => client.id)).size === clients.length, "Client ids must be unique"),
});

export function parseClientList(input: unknown): readonly KnownClient[] {
  return clientListSchema.parse(input).clients.map(({ id, name }) => ({ id, name }));
}

export const knownClients = parseClientList(source);

export const clientNames: readonly { id: string; names: readonly string[] }[] = clientListSchema.parse(source).clients.map(({ id, name, shortName, nickname }) => ({ id, names: [name, shortName, nickname] }));

export function findClient(clientId: string) {
  return knownClients.find((client) => client.id === clientId) ?? null;
}
