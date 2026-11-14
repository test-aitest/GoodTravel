import { describe, expect, mock, test } from "bun:test";
import { AgentFieldError } from "./agentfieldClient";
import { createJob } from "./jobs";

const clients = { kumo: "Kumo Coffee Roasters" } as Record<string, string>;
const findClient = (id: string) => (clients[id] ? { id, name: clients[id] } : null);

describe("createJob", () => {
  test("starts the job for a known client with the name the server knows, not one sent by the browser", async () => {
    const start = mock(async () => ({ executionId: "exec_1", runId: "run_1" }));

    const reply = await createJob({ clientId: "kumo", request: "  Check the sites.  ", clientName: "Someone else" }, { start, findClient });

    expect(reply).toEqual({ status: 201, body: { jobId: "exec_1", runId: "run_1" } });
    expect(start).toHaveBeenCalledWith({ clientId: "kumo", clientName: "Kumo Coffee Roasters", request: "Check the sites." });
  });

  test("refuses a missing request or client before calling AgentField", async () => {
    const start = mock(async () => ({ executionId: "exec_1", runId: "run_1" }));
    expect((await createJob({ clientId: "kumo", request: "   " }, { start, findClient })).status).toBe(400);
    expect((await createJob({ request: "Go." }, { start, findClient })).status).toBe(400);
    expect((await createJob("not an object", { start, findClient })).status).toBe(400);
    expect(start).not.toHaveBeenCalled();
  });

  test("answers 404 for a client the server does not know", async () => {
    const start = mock(async () => ({ executionId: "exec_1", runId: "run_1" }));
    const reply = await createJob({ clientId: "acme", request: "Go." }, { start, findClient });
    expect(reply.status).toBe(404);
    expect(start).not.toHaveBeenCalled();
  });

  test("answers 502 with plain words when AgentField fails", async () => {
    const start = mock(async () => {
      throw new AgentFieldError("AgentField is not reachable");
    });
    const reply = await createJob({ clientId: "kumo", request: "Go." }, { start, findClient });
    expect(reply).toEqual({ status: 502, body: { error: "The worker service is not available right now." } });
  });
});
