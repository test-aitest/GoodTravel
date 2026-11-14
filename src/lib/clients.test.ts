import { describe, expect, test } from "bun:test";
import source from "../../config/clients.json";
import { findClient, knownClients, parseClientList } from "./clients";
import { handleCreateJob } from "./jobRoutes";

const start = async () => ({ executionId: "exec_1", runId: "run_1" });

function createJobFor(clientId: string) {
  return handleCreateJob(new Request("http://app.test/api/jobs", { method: "POST", body: JSON.stringify({ clientId, request: "Check the sites." }) }), {
    start,
    findClient,
  });
}

describe("the shared client list", () => {
  test("is config/clients.json, the same file the agent reads, with the clients the server knew before", () => {
    expect(knownClients).toEqual([
      { id: "kumo", name: "Kumo Coffee Roasters" },
      { id: "hinata", name: "Hinata Skincare" },
      { id: "sora", name: "Sora Outdoor" },
    ]);
    expect(knownClients.map((client) => client.id)).toEqual(source.clients.map((client) => client.id));
  });

  test("finds a client only by its exact id", () => {
    expect(findClient("kumo")).toEqual({ id: "kumo", name: "Kumo Coffee Roasters" });
    expect(findClient("sora")).toEqual({ id: "sora", name: "Sora Outdoor" });
    for (const id of ["acme", "Kumo", "kumo ", "", "__proto__", "constructor", "toString"]) expect(findClient(id)).toBeNull();
  });

  test("refuses a list with another version, a repeated id, a malformed id or a blank name", () => {
    const client = { id: "kumo", name: "Kumo Coffee Roasters", shortName: "Kumo Coffee", nickname: "Kumo" };
    expect(() => parseClientList({ version: 2, clients: [client] })).toThrow();
    expect(() => parseClientList({ version: 1, clients: [client, client] })).toThrow();
    expect(() => parseClientList({ version: 1, clients: [{ ...client, id: "Kumo Coffee" }] })).toThrow();
    expect(() => parseClientList({ version: 1, clients: [{ ...client, name: "  " }] })).toThrow();
    expect(() => parseClientList({ version: 1, clients: [] })).toThrow();
    expect(parseClientList({ version: 1, clients: [client] })).toEqual([{ id: "kumo", name: "Kumo Coffee Roasters" }]);
  });

  test("keeps the jobs API answering 201 for every listed client and 404 for anyone else", async () => {
    for (const id of ["kumo", "hinata", "sora"]) expect((await createJobFor(id)).status).toBe(201);
    expect((await createJobFor("acme")).status).toBe(404);
  });
});
