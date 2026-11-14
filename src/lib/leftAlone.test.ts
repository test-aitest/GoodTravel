import { describe, expect, test } from "bun:test";
import { eventWriter, safeParseJobEvent, type JobEvent } from "./events";
import { buildJobState } from "./jobState";

const base = { id: "evt-1", seq: 2, jobId: "job-1", clientId: "kumo", at: "2026-11-14T09:00:05.000Z" };
const approval = {
  ...base,
  type: "approval.requested",
  approvalId: "apr-1",
  stepId: "ask",
  question: "Save 1 stock change to the store admin?",
  changes: [{ sku: "KC-ETH-200", name: "Ethiopia Guji 200g", store: 18, supplier: 6 }],
  undoable: true,
};

describe("approval.requested leftAlone", () => {
  test("is optional", () => {
    expect(safeParseJobEvent(approval).success).toBe(true);
  });

  test("carries how many products were only in the store and only at the supplier", () => {
    const parsed = safeParseJobEvent({ ...approval, leftAlone: { onlyInStore: 2, onlyAtSupplier: 0 } });
    expect(parsed.success).toBe(true);
    expect(parsed.data).toMatchObject({ leftAlone: { onlyInStore: 2, onlyAtSupplier: 0 } });
  });

  const malformed: Array<[string, unknown]> = [
    ["a negative count", { onlyInStore: -1, onlyAtSupplier: 0 }],
    ["a fractional count", { onlyInStore: 1.5, onlyAtSupplier: 0 }],
    ["a count as text", { onlyInStore: "2", onlyAtSupplier: 0 }],
    ["a missing count", { onlyInStore: 2 }],
    ["product names", { onlyInStore: 1, onlyAtSupplier: 0, names: ["Ethiopia Guji 200g"] }],
    ["SKUs", { onlyInStore: 1, onlyAtSupplier: 0, skus: ["KC-ETH-200"] }],
    ["a list instead of counts", ["KC-ETH-200"]],
  ];

  test.each(malformed)("rejects %s", (_label, leftAlone) => {
    expect(safeParseJobEvent({ ...approval, leftAlone }).success).toBe(false);
  });

  test("reaches the pending approval the card reads", () => {
    const write = eventWriter({ jobId: "job-1", clientId: "kumo", startAt: "2026-11-14T09:00:00.000Z" });
    const events: JobEvent[] = [
      write("job.created", {
        clientName: "Kumo Coffee Roasters",
        title: "Supplier stock sync",
        request: "Sync today's stock.",
        run: { kind: "learn" },
        plan: [{ stepId: "ask", title: "Ask you before saving" }],
      }, 0),
      write("approval.requested", {
        approvalId: "apr-1",
        stepId: "ask",
        question: "Save 1 stock change to the store admin?",
        changes: approval.changes,
        undoable: true,
        leftAlone: { onlyInStore: 1, onlyAtSupplier: 3 },
      }, 500),
    ];
    expect(buildJobState(events).pendingApproval?.leftAlone).toEqual({ onlyInStore: 1, onlyAtSupplier: 3 });
  });
});
