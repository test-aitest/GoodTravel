import { describe, expect, test } from "bun:test";
import { AgentFieldError, waitingJobIds } from "./agentfieldClient";

function answering(body: unknown) {
  const fetcher = async () => Response.json(body);
  return { baseUrl: "http://agentfield.test", fetch: fetcher as unknown as typeof fetch };
}

describe("waitingJobIds against AgentField's real answers", () => {
  test("is empty when AgentField answers results: null, as v0.1.145 does when nothing waits", async () => {
    const body = { ok: true, data: { limit: 100, offset: 0, resource: "executions", results: null, total: 0 } };
    expect(await waitingJobIds(answering(body), "kumo")).toEqual([]);
  });

  test("throws AgentFieldError when the answer has no list at all", async () => {
    await expect(waitingJobIds(answering({ ok: false, error: "bad filter" }), "kumo")).rejects.toBeInstanceOf(AgentFieldError);
    await expect(waitingJobIds(answering({ ok: true, data: { results: "none" } }), "kumo")).rejects.toBeInstanceOf(AgentFieldError);
  });
});
