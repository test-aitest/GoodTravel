export const clientMemoryScope = "actor";

export const procedureMemoryKey = "stock-sync/procedure";

export function clientMemoryActor(clientId: string) {
  return `clientops-client:${clientId}`;
}

export function procedureIdFor(clientId: string) {
  return `${clientId}-stock-sync`;
}
