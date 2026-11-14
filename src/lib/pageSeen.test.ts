import { describe, expect, test } from "bun:test";
import { newEvents } from "./agentfieldRecords";
import { eventWriter, jobEventTypes, pageHtmlMaxLength, safeParseJobEvent, type JobEvent } from "./events";
import { applyEvent, buildJobState } from "./jobState";

const startAt = "2026-11-14T09:00:00.000Z";
const loginPage = `<!doctype html><html><head><title>Partner sign in</title></head><body><form action="/login"><input name="username"><input type="password" name="password"><button>Sign in</button></form></body></html>`;
const stockPage = `<!doctype html><html><head><title>Stock list</title></head><body><table><tr><td>KC-ETH-200</td><td>6</td></tr></table></body></html>`;

const base = { id: "evt-1", seq: 1, jobId: "job-1", clientId: "kumo", at: startAt };
const seen = { ...base, type: "page.seen", stepId: "sign-in", host: "portal.maruyama-supply.example", html: loginPage };

const note = (n: number, event: Record<string, unknown>) => ({
  message: JSON.stringify({ n, event }),
  tags: ["clientops.event"],
  timestamp: `2026-11-14T09:00:0${n}.000Z`,
});

function startedJob() {
  const write = eventWriter({ jobId: "job-1", clientId: "kumo", startAt });
  const events: JobEvent[] = [
    write("job.created", {
      clientName: "Kumo Coffee Roasters",
      title: "Supplier stock sync",
      request: "Sync today's stock from Maruyama Supply to our store admin.",
      run: { kind: "learn" },
      plan: [
        { stepId: "sign-in", title: "Sign in to Maruyama Supply" },
        { stepId: "read", title: "Read today's stock" },
      ],
    }, 0),
    write("step.started", { stepId: "sign-in", activity: "Signing in to Maruyama Supply" }, 100),
  ];
  return { write, events };
}

describe("page.seen event", () => {
  test("is one of the job events", () => {
    expect(jobEventTypes).toContain("page.seen");
  });

  test("accepts a snapshot of the page the worker saw, with the host it came from", () => {
    expect(safeParseJobEvent(seen).success).toBe(true);
    expect(safeParseJobEvent({ ...seen, host: "127.0.0.1:3002" }).success).toBe(true);
    expect(safeParseJobEvent({ ...seen, host: "[::1]:3002" }).success).toBe(true);
  });

  const malformed: Array<[string, unknown]> = [
    ["a host with a scheme", { ...seen, host: "https://portal.maruyama-supply.example" }],
    ["a host with a path", { ...seen, host: "portal.maruyama-supply.example/stock" }],
    ["an empty host", { ...seen, host: "" }],
    ["empty html", { ...seen, html: "" }],
    ["html over the cap", { ...seen, html: "x".repeat(pageHtmlMaxLength + 1) }],
    ["no step", { ...seen, stepId: undefined }],
    ["the full URL as an extra field", { ...seen, url: "https://portal.maruyama-supply.example/stock?page=2" }],
  ];

  test.each(malformed)("rejects %s", (_label, input) => {
    expect(safeParseJobEvent(input).success).toBe(false);
  });

  test("caps html at 256 KiB, the same as the runner's snapshot", () => {
    expect(pageHtmlMaxLength).toBe(262_144);
    expect(safeParseJobEvent({ ...seen, html: "x".repeat(pageHtmlMaxLength) }).success).toBe(true);
  });
});

describe("job state page", () => {
  test("a new job has no page yet", () => {
    expect(buildJobState(startedJob().events).page).toBeNull();
  });

  test("keeps the latest page the worker saw, replacing the one before", () => {
    const { write, events } = startedJob();
    events.push(write("page.seen", { stepId: "sign-in", host: "portal.maruyama-supply.example", html: loginPage }, 300));
    expect(buildJobState(events).page).toEqual({ stepId: "sign-in", host: "portal.maruyama-supply.example", html: loginPage });
    events.push(
      write("step.done", { stepId: "sign-in", summary: "Signed in to Maruyama Supply", tookMs: 900 }, 1000),
      write("step.started", { stepId: "read", activity: "Reading today's stock" }, 1100),
      write("page.seen", { stepId: "read", host: "portal.maruyama-supply.example", html: stockPage }, 1500),
    );
    expect(buildJobState(events).page).toEqual({ stepId: "read", host: "portal.maruyama-supply.example", html: stockPage });
  });

  test("a page changes nothing else about the job", () => {
    const { write, events } = startedJob();
    const before = buildJobState(events);
    const after = applyEvent(before, write("page.seen", { stepId: "sign-in", host: "portal.maruyama-supply.example", html: loginPage }, 300));
    const { page, lastSeq, updatedAt, elapsedMs, ...rest } = after;
    const { page: _page, lastSeq: _lastSeq, updatedAt: _updatedAt, elapsedMs: _elapsedMs, ...restBefore } = before;
    expect(rest).toEqual(restBefore);
    expect(page?.stepId).toBe("sign-in");
    expect([lastSeq, updatedAt, elapsedMs]).toEqual([3, "2026-11-14T09:00:00.300Z", 300]);
  });

  test("refuses a page for a step the job does not have", () => {
    const { write, events } = startedJob();
    const state = buildJobState(events);
    expect(() => applyEvent(state, write("page.seen", { stepId: "pay", host: "admin.kumo-coffee.example", html: stockPage }, 300))).toThrow("pay");
  });

  test("comes through from the worker's notes like any other event", () => {
    const converted = newEvents(
      {
        executionId: "exec-1",
        clientId: "kumo",
        status: "running",
        notes: [
          note(1, {
            type: "job.created",
            clientName: "Kumo Coffee Roasters",
            title: "Supplier stock sync",
            request: "Sync today's stock.",
            run: { kind: "learn" },
            plan: [{ stepId: "sign-in", title: "Sign in" }],
          }),
          note(2, { type: "page.seen", stepId: "sign-in", host: "127.0.0.1:3001", html: loginPage }),
        ],
      },
      0,
    );
    expect(converted.rejected).toEqual([]);
    expect(buildJobState(converted.events).page).toEqual({ stepId: "sign-in", host: "127.0.0.1:3001", html: loginPage });
  });
});
