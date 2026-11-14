import { describe, expect, test } from "bun:test";
import { loadWaitingJobs } from "./waitingApi";

function browser(reply: { status: number; body: unknown } | Error) {
  const sent: string[] = [];
  const fetcher = async (input: string | URL | Request) => {
    sent.push(String(input));
    if (reply instanceof Error) throw reply;
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { sent, fetcher: fetcher as typeof fetch };
}

describe("loadWaitingJobs", () => {
  test("reads the job ids waiting for the client", async () => {
    const page = browser({ status: 200, body: { clientId: "kumo", jobIds: ["exec_2", "exec_1"] } });
    expect(await loadWaitingJobs("kumo", page.fetcher)).toEqual(["exec_2", "exec_1"]);
    expect(page.sent).toEqual(["/api/clients/kumo/waiting"]);
  });

  test("gives nothing when the server fails or answers something else", async () => {
    expect(await loadWaitingJobs("kumo", browser({ status: 502, body: { error: "down" } }).fetcher)).toBeNull();
    expect(await loadWaitingJobs("kumo", browser({ status: 200, body: { jobIds: [1] } }).fetcher)).toBeNull();
    expect(await loadWaitingJobs("kumo", browser(new TypeError("Failed to fetch")).fetcher)).toBeNull();
  });
});
