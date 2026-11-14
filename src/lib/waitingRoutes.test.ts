import { describe, expect, test } from "bun:test";
import { AgentFieldError } from "./agentfieldClient";
import { findClient } from "./clients";
import { handleWaitingJobs, type WaitingDeps } from "./waitingRoutes";

function deps(overrides: Partial<WaitingDeps> = {}): WaitingDeps {
  return { findClient, waitingJobIds: async (clientId) => (clientId === "kumo" ? ["exec_2", "exec_1"] : []), ...overrides };
}

async function answer(response: Response) {
  return { status: response.status, body: (await response.json()) as unknown, cache: response.headers.get("cache-control") };
}

describe("handleWaitingJobs", () => {
  test("lists the client's jobs that are waiting for you, newest first as AgentField gives them", async () => {
    expect(await answer(await handleWaitingJobs("kumo", deps()))).toEqual({ status: 200, body: { clientId: "kumo", jobIds: ["exec_2", "exec_1"] }, cache: "no-store" });
  });

  test("answers 404 for an unknown client and does not ask AgentField", async () => {
    let asked = 0;
    const reply = await answer(
      await handleWaitingJobs(
        "acme",
        deps({
          waitingJobIds: async () => {
            asked += 1;
            return [];
          },
        }),
      ),
    );
    expect(reply.status).toBe(404);
    expect(reply.body).toEqual({ error: "There is no such client." });
    expect(asked).toBe(0);
  });

  test("answers 502 when AgentField cannot be reached", async () => {
    const reply = await answer(
      await handleWaitingJobs(
        "kumo",
        deps({
          waitingJobIds: async () => {
            throw new AgentFieldError("AgentField is not reachable");
          },
        }),
      ),
    );
    expect(reply).toEqual({ status: 502, body: { error: "The worker service is not available right now." }, cache: "no-store" });
  });
});
