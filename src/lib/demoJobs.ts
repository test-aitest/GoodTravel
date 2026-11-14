import { eventWriter, type JobEvent, type PlanStep, type StockChange } from "./events";

export type DemoJob = { events: readonly JobEvent[] };

const operator = "Aoi Tanaka";

const kumo = { clientId: "kumo", clientName: "Kumo Coffee Roasters" };
const hinata = { clientId: "hinata", clientName: "Hinata Skincare" };

export const kumoProductCount = 42;

export const kumoFirstRunChanges: StockChange[] = [
  { sku: "KC-ETH-200", name: "Ethiopia Guji 200g", store: 18, supplier: 6 },
  { sku: "KC-YIR-200", name: "Ethiopia Yirgacheffe 200g", store: 20, supplier: 11 },
  { sku: "KC-COL-200", name: "Colombia Huila 200g", store: 0, supplier: 24 },
  { sku: "KC-KEN-200", name: "Kenya Nyeri 200g", store: 9, supplier: 0 },
  { sku: "KC-GUA-500", name: "Guatemala Antigua 500g", store: 4, supplier: 15 },
  { sku: "KC-BRA-500", name: "Brazil Cerrado 500g", store: 7, supplier: 0 },
  { sku: "KC-SUM-500", name: "Sumatra Mandheling 500g", store: 3, supplier: 8 },
  { sku: "KC-PAN-200", name: "Panama Boquete 200g", store: 2, supplier: 5 },
  { sku: "KC-HSE-200", name: "House Blend 200g", store: 52, supplier: 40 },
  { sku: "KC-ESP-500", name: "Espresso Blend 500g", store: 25, supplier: 19 },
  { sku: "KC-DRP-KEN", name: "Drip Bags Kenya Nyeri x10", store: 14, supplier: 0 },
  { sku: "KC-GDS-FLT", name: "Paper Filters x100", store: 45, supplier: 60 },
];

export const kumoReplayChanges: StockChange[] = [
  { sku: "KC-ETH-200", name: "Ethiopia Guji 200g", store: 6, supplier: 2 },
  { sku: "KC-COL-200", name: "Colombia Huila 200g", store: 24, supplier: 18 },
  { sku: "KC-KEN-200", name: "Kenya Nyeri 200g", store: 0, supplier: 12 },
  { sku: "KC-HSE-200", name: "House Blend 200g", store: 40, supplier: 36 },
];

export const kumoRepairChanges: StockChange[] = [
  { sku: "KC-ETH-200", name: "Ethiopia Guji 200g", store: 2, supplier: 0 },
  { sku: "KC-YIR-200", name: "Ethiopia Yirgacheffe 200g", store: 11, supplier: 15 },
  { sku: "KC-GUA-500", name: "Guatemala Antigua 500g", store: 15, supplier: 9 },
  { sku: "KC-BRA-500", name: "Brazil Cerrado 500g", store: 0, supplier: 20 },
  { sku: "KC-ESP-200", name: "Espresso Blend 200g", store: 30, supplier: 26 },
  { sku: "KC-DRP-KEN", name: "Drip Bags Kenya Nyeri x10", store: 0, supplier: 10 },
  { sku: "KC-GDS-GRN", name: "Hand Grinder", store: 3, supplier: 6 },
];

const signIn: PlanStep = { stepId: "sign-in", title: "Sign in to Maruyama Supply" };
const readStock: PlanStep = { stepId: "read", title: "Read today's stock" };
const compare: PlanStep = { stepId: "compare", title: "Compare with the store admin" };
const checkSave: PlanStep = { stepId: "check-save", title: "Check the save button" };
const ask: PlanStep = { stepId: "ask", title: "Ask you before saving" };
const save: PlanStep = { stepId: "save", title: "Save and check every row" };

const stockSync = {
  title: "Supplier stock sync",
  procedureId: "kumo-stock-sync",
  request: "Sync today's stock from Maruyama Supply to our store admin. Ask me before you save anything.",
  scheduledRequest: "Daily at 18:00: sync supplier stock to the store admin.",
};

const stockQuestion = (changes: StockChange[]) => `Save ${changes.length} stock changes to the store admin?`;

function firstRun(): DemoJob {
  const write = eventWriter({ jobId: "job-kumo-first-run", clientId: kumo.clientId, startAt: "2026-11-14T08:40:00.000Z" });
  const changes = kumoFirstRunChanges;
  return {
    events: [
      write("job.created", { clientName: kumo.clientName, title: stockSync.title, request: stockSync.request, run: { kind: "learn" }, plan: [signIn, readStock, compare, checkSave, ask, save] }, 0),
      write("step.started", { stepId: "sign-in", activity: "Signing in to Maruyama Supply" }, 300),
      write("step.decided", { stepId: "sign-in", rung: "claude", tookMs: 2200 }, 2500),
      write("step.done", { stepId: "sign-in", summary: "Signed in to Maruyama Supply", tookMs: 4500 }, 4800),
      write("step.started", { stepId: "read", activity: "Reading today's stock" }, 5000),
      write("step.decided", { stepId: "read", rung: "claude", tookMs: 1100 }, 6100),
      write("step.done", { stepId: "read", summary: "Read today's stock", detail: `${kumoProductCount} products`, tookMs: 11800 }, 16800),
      write("step.started", { stepId: "compare", activity: "Comparing with the store admin", detail: "Matching by SKU" }, 17000),
      write("step.decided", { stepId: "compare", rung: "claude", tookMs: 3400 }, 20400),
      write("step.done", { stepId: "compare", summary: "Compared with the store admin", detail: `${changes.length} products differ`, tookMs: 29400 }, 46400),
      write("step.started", { stepId: "check-save", activity: "Checking the save button" }, 46500),
      write("step.decided", { stepId: "check-save", rung: "claude", tookMs: 1200 }, 47700),
      write("step.done", { stepId: "check-save", summary: "Save button found", tookMs: 1400 }, 47900),
      write("step.started", { stepId: "ask", activity: "Waiting for your OK" }, 48200),
      write("approval.requested", { approvalId: "apr-kumo-first-run", stepId: "ask", question: stockQuestion(changes), changes, undoable: true }, 48600),
      write("approval.answered", { approvalId: "apr-kumo-first-run", decision: "approved", by: operator }, 60000),
      write("step.decided", { stepId: "ask", rung: "human", tookMs: 11400 }, 60000),
      write("step.done", { stepId: "ask", summary: `You approved ${changes.length} changes`, tookMs: 11800 }, 60000),
      write("step.started", { stepId: "save", activity: `Saving ${changes.length} changes` }, 60200),
      write("step.decided", { stepId: "save", rung: "claude", tookMs: 1100 }, 61300),
      write("write.done", { stepId: "save", written: changes.length }, 66000),
      write("verify.done", { stepId: "save", checked: changes.length, matched: changes.length }, 72500),
      write("step.done", { stepId: "save", summary: "Saved and checked every row", detail: `${changes.length} products`, tookMs: 12400 }, 72600),
      write("procedure.saved", { procedureId: stockSync.procedureId, version: 1, stepCount: 6, expectedReplayMs: 6000 }, 73000),
    ],
  };
}

function replay(): DemoJob {
  const write = eventWriter({ jobId: "job-kumo-replay", clientId: kumo.clientId, startAt: "2026-11-14T09:00:00.000Z" });
  const changes = kumoReplayChanges;
  return {
    events: [
      write("job.created", { clientName: kumo.clientName, title: stockSync.title, request: stockSync.scheduledRequest, run: { kind: "replay", procedureVersion: 1 }, plan: [signIn, readStock, compare, checkSave, ask, save] }, 0),
      write("step.started", { stepId: "sign-in", activity: "Signing in to Maruyama Supply" }, 100),
      write("step.decided", { stepId: "sign-in", rung: "script", tookMs: 30 }, 150),
      write("step.done", { stepId: "sign-in", summary: "Signed in to Maruyama Supply", tookMs: 1300 }, 1400),
      write("step.started", { stepId: "read", activity: "Reading today's stock" }, 1500),
      write("step.decided", { stepId: "read", rung: "script", tookMs: 20 }, 1520),
      write("step.done", { stepId: "read", summary: "Read today's stock", detail: `${kumoProductCount} products`, tookMs: 2100 }, 3600),
      write("step.started", { stepId: "compare", activity: "Comparing with the store admin" }, 3700),
      write("step.decided", { stepId: "compare", rung: "script", tookMs: 20 }, 3720),
      write("step.done", { stepId: "compare", summary: "Compared with the store admin", detail: `${changes.length} products differ`, tookMs: 1600 }, 5300),
      write("step.started", { stepId: "check-save", activity: "Checking the save button" }, 5400),
      write("step.decided", { stepId: "check-save", rung: "script", tookMs: 20 }, 5420),
      write("step.done", { stepId: "check-save", summary: "Save button found", tookMs: 500 }, 5900),
      write("step.started", { stepId: "ask", activity: "Waiting for your OK" }, 6000),
      write("approval.requested", { approvalId: "apr-kumo-replay", stepId: "ask", question: stockQuestion(changes), changes, undoable: true }, 6200),
      write("approval.answered", { approvalId: "apr-kumo-replay", decision: "approved", by: operator }, 30000),
      write("step.decided", { stepId: "ask", rung: "human", tookMs: 23800 }, 30000),
      write("step.done", { stepId: "ask", summary: `You approved ${changes.length} changes`, tookMs: 24000 }, 30000),
      write("step.started", { stepId: "save", activity: `Saving ${changes.length} changes` }, 30100),
      write("step.decided", { stepId: "save", rung: "script", tookMs: 20 }, 30120),
      write("write.done", { stepId: "save", written: changes.length }, 31500),
      write("verify.done", { stepId: "save", checked: changes.length, matched: changes.length }, 33000),
      write("step.done", { stepId: "save", summary: "Saved and checked every row", detail: `${changes.length} products`, tookMs: 2900 }, 33100),
    ],
  };
}

function repair(): DemoJob {
  const write = eventWriter({ jobId: "job-kumo-repair", clientId: kumo.clientId, startAt: "2026-11-15T09:00:00.000Z" });
  const changes = kumoRepairChanges;
  return {
    events: [
      write("job.created", { clientName: kumo.clientName, title: stockSync.title, request: stockSync.scheduledRequest, run: { kind: "replay", procedureVersion: 1 }, plan: [signIn, readStock, compare, checkSave, ask, save] }, 0),
      write("step.started", { stepId: "sign-in", activity: "Signing in to Maruyama Supply" }, 100),
      write("step.decided", { stepId: "sign-in", rung: "script", tookMs: 30 }, 150),
      write("step.done", { stepId: "sign-in", summary: "Signed in to Maruyama Supply", tookMs: 1300 }, 1400),
      write("step.started", { stepId: "read", activity: "Reading today's stock" }, 1500),
      write("step.decided", { stepId: "read", rung: "script", tookMs: 20 }, 1520),
      write("step.done", { stepId: "read", summary: "Read today's stock", detail: `${kumoProductCount} products`, tookMs: 2100 }, 3600),
      write("step.started", { stepId: "compare", activity: "Comparing with the store admin" }, 3700),
      write("step.decided", { stepId: "compare", rung: "script", tookMs: 20 }, 3720),
      write("step.done", { stepId: "compare", summary: "Compared with the store admin", detail: `${changes.length} products differ`, tookMs: 1600 }, 5300),
      write("step.started", { stepId: "check-save", activity: "Checking the save button", detail: "The saved button is missing" }, 5400),
      write("step.decided", { stepId: "check-save", rung: "claude", tookMs: 2600, escalatedFrom: ["script", "tree"] }, 8100),
      write("step.done", { stepId: "check-save", summary: "Found the new button", detail: "Save changes is now Update inventory", tookMs: 2900 }, 8300),
      write("step.started", { stepId: "ask", activity: "Waiting for your OK" }, 8400),
      write("approval.requested", {
        approvalId: "apr-kumo-repair",
        stepId: "ask",
        question: `Save ${changes.length} stock changes with the new button?`,
        changes,
        undoable: true,
        procedureChange: { stepId: "check-save", fromVersion: 1, toVersion: 2, before: "Save changes", after: "Update inventory" },
      }, 8600),
      write("approval.answered", { approvalId: "apr-kumo-repair", decision: "approved", by: operator }, 40000),
      write("step.decided", { stepId: "ask", rung: "human", tookMs: 31400 }, 40000),
      write("step.done", { stepId: "ask", summary: `You approved ${changes.length} changes and the new button`, tookMs: 31600 }, 40000),
      write("step.started", { stepId: "save", activity: `Saving ${changes.length} changes` }, 40100),
      write("step.decided", { stepId: "save", rung: "script", tookMs: 20 }, 40120),
      write("write.done", { stepId: "save", written: changes.length }, 41500),
      write("verify.done", { stepId: "save", checked: changes.length, matched: changes.length }, 43400),
      write("step.done", { stepId: "save", summary: "Saved and checked every row", detail: `${changes.length} products`, tookMs: 3300 }, 43500),
      write("procedure.saved", { procedureId: stockSync.procedureId, version: 2, stepCount: 6, expectedReplayMs: 6000 }, 43800),
    ],
  };
}

function boundary(): DemoJob {
  const write = eventWriter({ jobId: "job-hinata-boundary", clientId: hinata.clientId, startAt: "2026-11-14T08:45:00.000Z" });
  return {
    events: [
      write("job.created", {
        clientName: hinata.clientName,
        title: "Supplier prices",
        request: "Use Kumo Coffee's Maruyama login to check Hinata's supplier prices too.",
        run: { kind: "learn" },
        plan: [],
      }, 0),
      write("boundary.blocked", {
        resource: "Maruyama Supply login",
        ownerClientId: kumo.clientId,
        ownerClientName: kumo.clientName,
        auditId: "audit-hinata-20261114-0845",
        offersOwnAccount: true,
      }, 400),
    ],
  };
}

export const demoJobs = {
  firstRun: firstRun(),
  replay: replay(),
  repair: repair(),
  boundary: boundary(),
} satisfies Record<string, DemoJob>;
