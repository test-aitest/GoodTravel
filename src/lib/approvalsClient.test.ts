import { describe, expect, test } from "bun:test";
import { approve, choose, stop, undo } from "./approvalsClient";

type Sent = { url: string; method: string; body: unknown };

function browser(reply: { status: number; body: unknown } | Error) {
  const sent: Sent[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
    sent.push({ url: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (reply instanceof Error) throw reply;
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
  };
  return { sent, fetcher: fetcher as typeof fetch };
}

describe("approve", () => {
  test("sends only the job and approval ids", async () => {
    const page = browser({ status: 200, body: { jobId: "exec_1", approvalId: "apr-exec_1", approved: true } });

    expect(await approve("exec_1", "apr-exec_1", page.fetcher)).toEqual({ ok: true });
    expect(page.sent).toEqual([{ url: "/api/approvals", method: "POST", body: { jobId: "exec_1", approvalId: "apr-exec_1" } }]);
  });

  test("passes the server's sentence through on failure", async () => {
    const page = browser({ status: 409, body: { error: "This approval was already answered." } });
    expect(await approve("exec_1", "apr-exec_1", page.fetcher)).toEqual({ error: "This approval was already answered." });
  });

  test("says the service is unavailable when nothing answers", async () => {
    const page = browser(new TypeError("Failed to fetch"));
    expect(await approve("exec_1", "apr-exec_1", page.fetcher)).toEqual({ error: "The worker service is not available right now." });
  });
});

describe("undo", () => {
  test("starts the undo job for the client and returns its job id", async () => {
    const page = browser({ status: 201, body: { jobId: "exec_undo", runId: "run_undo" } });

    expect(await undo("kumo", page.fetcher)).toEqual({ jobId: "exec_undo" });
    expect(page.sent).toEqual([{ url: "/api/clients/kumo/undo", method: "POST", body: undefined }]);
  });

  test("keeps the client id inside its path segment", async () => {
    const page = browser({ status: 404, body: { error: "There is no such client." } });
    expect(await undo("a/b", page.fetcher)).toEqual({ error: "There is no such client." });
    expect(page.sent[0].url).toBe("/api/clients/a%2Fb/undo");
  });
});

describe("stop", () => {
  test("asks the server to stop the job", async () => {
    const page = browser({ status: 200, body: { jobId: "exec_1", stopped: true } });

    expect(await stop("exec_1", page.fetcher)).toEqual({ ok: true });
    expect(page.sent).toEqual([{ url: "/api/jobs/exec_1/stop", method: "POST", body: undefined }]);
  });

  test("passes the server's sentence through on failure", async () => {
    const page = browser({ status: 409, body: { error: "This job has already ended." } });
    expect(await stop("exec_1", page.fetcher)).toEqual({ error: "This job has already ended." });
  });
});

describe("choose", () => {
  test("sends the job, the question and the element you picked", async () => {
    const page = browser({ status: 200, body: { jobId: "exec_1", choiceId: "cho-exec_1-check-save", ref: "1016" } });

    expect(await choose("exec_1", "cho-exec_1-check-save", "1016", page.fetcher)).toEqual({ ok: true });
    expect(page.sent).toEqual([{ url: "/api/choices", method: "POST", body: { jobId: "exec_1", choiceId: "cho-exec_1-check-save", ref: "1016" } }]);
  });

  test("sends no element for None of these", async () => {
    const page = browser({ status: 200, body: { jobId: "exec_1", choiceId: "cho-exec_1-check-save", ref: null } });

    expect(await choose("exec_1", "cho-exec_1-check-save", null, page.fetcher)).toEqual({ ok: true });
    expect(page.sent[0]?.body).toEqual({ jobId: "exec_1", choiceId: "cho-exec_1-check-save", ref: null });
  });

  test("passes the server's sentence through on failure", async () => {
    const page = browser({ status: 409, body: { error: "This question was already answered." } });
    expect(await choose("exec_1", "cho-exec_1-check-save", "1016", page.fetcher)).toEqual({ error: "This question was already answered." });
  });
});
