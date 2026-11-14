import { claudeSpend, computerTime } from "./costView";
import type { JobState } from "./jobState";
import { leftAloneText, seconds, stepItems } from "./jobView";
import { scheduleText, type ClientSchedule } from "./schedule";

export const factIds = ["last.result", "last.changes", "last.leftAlone", "last.steps", "last.timing", "last.stepsChange", "schedule", "last.cost", "last.ignored"] as const;
export type FactId = (typeof factIds)[number];

export type FactSource = {
  job: JobState | null;
  waitingJob?: JobState;
  readyInMs: number | null;
  firstReadyMs: number | null;
  schedule: ClientSchedule | null;
};

const about: Record<FactId, string> = {
  "last.result": "how the last job ended",
  "last.changes": "the stock changes the last job found",
  "last.leftAlone": "products the last job left as they were",
  "last.steps": "each step of the last job and who decided it",
  "last.timing": "how long the last job took to get the changes ready",
  "last.stepsChange": "how the saved steps changed, such as a renamed button",
  schedule: "the daily run",
  "last.cost": "what the last job cost in Claude and computer time",
  "last.ignored": "a suspicious instruction the last job ignored",
};

export function factAbout(id: FactId) {
  return about[id];
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
const missing = ["I don't have that for this conversation yet."];

function approvalOf(source: FactSource) {
  return source.job?.pendingApproval ?? source.waitingJob?.pendingApproval ?? null;
}

function has(id: FactId, source: FactSource): boolean {
  const job = source.job;
  if (id === "schedule") return source.schedule !== null && job !== null;
  if (!job) return false;
  const approval = approvalOf(source);
  switch (id) {
    case "last.result":
    case "last.steps":
      return true;
    case "last.changes":
      return Boolean(approval?.changes.length);
    case "last.leftAlone":
      return Boolean(leftAloneText(approval?.leftAlone));
    case "last.timing":
      return source.readyInMs !== null;
    case "last.stepsChange":
      return Boolean(approval?.procedureChange);
    case "last.cost":
      return job.cost !== null;
    case "last.ignored":
      return job.ignoredInstructions.length > 0;
  }
}

export function factCatalog(source: FactSource) {
  return factIds.filter((id) => has(id, source)).map((id) => ({ id, about: about[id] }));
}

function resultLines(job: JobState): string[] {
  const result = job.result;
  if (result?.kind === "done" && result.written) return [`The last sync updated ${plural(result.written, "product")} and checked each one.`];
  if (result?.kind === "done") return ["The last sync found nothing to change."];
  if (result?.kind === "stopped") return ["The last job stopped before saving anything."];
  if (result?.kind === "failed") return [result.message ?? "The last job stopped because something did not match. Nothing else was changed."];
  if (result?.kind === "blocked") return [`The last job was stopped because it needed ${result.ownerClientName}'s login.`];
  if (job.pendingApproval) return [`The last job is waiting for your OK on ${plural(job.pendingApproval.changes.length, "change")}.`];
  return ["The last job is still running."];
}

export function factLines(id: FactId, source: FactSource): string[] {
  const job = source.job;
  if (!job || !has(id, source)) return missing;
  const approval = approvalOf(source);
  switch (id) {
    case "last.result":
      return resultLines(job);
    case "last.changes": {
      const changes = approval?.changes ?? [];
      return [`${plural(changes.length, "product")} changed:`, ...changes.map((change) => `${change.name}: ${change.store} → ${change.supplier === 0 ? "sold out" : change.supplier}`)];
    }
    case "last.leftAlone":
      return [leftAloneText(approval?.leftAlone) ?? missing[0]];
    case "last.steps":
      return stepItems(job).map((item) => (item.detail ? `${item.label} (${item.detail})` : item.label));
    case "last.timing": {
      const ready = source.readyInMs ?? 0;
      const first = source.firstReadyMs !== null && source.firstReadyMs !== ready ? ` The first time took ${seconds(source.firstReadyMs)}.` : "";
      return [`It took ${seconds(ready)} to get the changes ready.${first}`];
    }
    case "last.stepsChange": {
      const change = approval?.procedureChange;
      return change ? [`The button ${change.before} is now ${change.after}. The saved steps become version ${change.toVersion}.`] : missing;
    }
    case "schedule":
      return source.schedule ? [scheduleText(source.schedule)] : missing;
    case "last.cost":
      return job.cost ? [`Claude: ${claudeSpend(job.cost)}. Computer: ${computerTime(job.cost)}.`] : missing;
    case "last.ignored":
      return job.ignoredInstructions.map((ignored) => `I ignored a suspicious instruction on ${ignored.host}. Nothing it asked for was done.`);
  }
}
