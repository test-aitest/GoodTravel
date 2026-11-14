import { allLoginNames, SetupWorkerError, type SetupWorker, type WorkerAnswer } from "./setup";

type World = { computers: Set<string>; present: Map<string, readonly string[]> };

const clientsWithSites = new Set(["kumo", "hinata"]);
const MOST_WORLDS = 500;
const worlds = new Map<string, World>();

function newWorld(): World {
  return { computers: new Set(["hinata"]), present: new Map([["hinata", allLoginNames]]) };
}

function worldFor(worldId: string) {
  const known = worlds.get(worldId);
  if (known) return known;
  if (worlds.size >= MOST_WORLDS) worlds.delete(worlds.keys().next().value ?? "");
  const created = newWorld();
  worlds.set(worldId, created);
  return created;
}

export function fakeWorldNames(worldId: string) {
  const world = worldFor(worldId);
  return { computers: [...world.computers], present: Object.fromEntries(world.present) };
}

export function fakeSetupWorker(worldId: string): SetupWorker {
  const world = worldFor(worldId);
  const answer = (clientId: string): WorkerAnswer => {
    if (!clientsWithSites.has(clientId)) return { computer: "unavailable", present: [] };
    if (!world.computers.has(clientId)) return { computer: "missing", present: [] };
    return { computer: "ready", present: world.present.get(clientId) ?? [] };
  };
  return {
    status: async (clientId) => answer(clientId),
    prepare: async (clientId) => {
      if (clientsWithSites.has(clientId)) world.computers.add(clientId);
      return answer(clientId);
    },
    putLogins: async (clientId, values) => {
      if (!clientsWithSites.has(clientId)) throw new SetupWorkerError(`${clientId} cannot have a computer.`, "not_set_up");
      const kept = world.present.get(clientId) ?? [];
      const names = allLoginNames.filter((name) => kept.includes(name) || Object.hasOwn(values, name));
      world.computers.add(clientId);
      world.present.set(clientId, names);
      return { present: names };
    },
  };
}
