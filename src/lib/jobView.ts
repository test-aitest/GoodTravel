import type { StepItem } from "@/components/Steps";
import type { LeftAlone, Rung } from "./events";
import type { JobState, StepState } from "./jobState";

const deciders: Record<Rung, string> = {
  script: "Saved steps, no AI",
  tree: "Saved steps, found again by name",
  claude: "Decided by Claude",
  human: "Decided by you",
};

export function decidedBy(rung: Rung) {
  return deciders[rung];
}

export function seconds(ms: number) {
  return ms < 100 ? "< 0.1 s" : `${(ms / 1000).toFixed(1)} s`;
}

export const ASK_STEP = "ask";

const joined = (...parts: (string | undefined)[]) => parts.filter(Boolean).join(" · ");

function deciding(state: JobState, step: StepState) {
  if (step.stepId === state.pendingApproval?.stepId || step.stepId === state.pendingChoice?.stepId) return "You decide";
  if (step.decidedBy) return decidedBy(step.decidedBy);
  return state.run.kind === "learn" ? "Claude is deciding" : "Running saved steps";
}

function stepItem(state: JobState, step: StepState): StepItem {
  if (step.status === "done") {
    return {
      label: step.detail ? `${step.summary}, ${step.detail}` : (step.summary ?? step.title),
      detail: step.decidedBy ? joined(decidedBy(step.decidedBy), step.decidedInMs === undefined ? undefined : seconds(step.decidedInMs)) : undefined,
      state: "done",
    };
  }
  if (step.status === "active") {
    return { label: step.activity ?? step.title, detail: joined(deciding(state, step), step.detail), state: "now" };
  }
  return step.stepId === ASK_STEP ? { label: step.title, detail: "You decide", state: "next" } : { label: step.title, state: "next" };
}

export function stepItems(state: JobState) {
  return state.steps.map((step) => stepItem(state, step));
}

export function progressOf(state: JobState) {
  const active = state.steps.find((step) => step.stepId === state.activeStepId);
  return {
    title: active?.activity ?? active?.title ?? state.steps.at(-1)?.summary ?? state.title,
    done: state.doneCount,
    total: state.steps.length,
  };
}

function supplierOf(state: JobState) {
  return state.steps[0]?.title.replace(/^Sign in to /, "") ?? "the supplier";
}

export function workerSays(state: JobState) {
  const approval = state.pendingApproval;
  const result = state.result;
  if (result?.kind === "blocked") return `I can't use ${result.ownerClientName}' login here`;
  if (result?.kind === "stopped") return "Stopped before saving.";
  if (result?.kind === "failed") return "I stopped because something did not match. Nothing else was changed.";
  if (result?.kind === "done" && state.run.kind === "undo") return `Put back ${result.written ?? 0} products, and checked each one.`;
  if (result?.kind === "done" && !result.written) return "Nothing to save. The store already matches the supplier.";
  if (result?.kind === "done") return `Done. ${result.written} products updated, and each one checked.`;
  if (state.pendingChoice) {
    const role = state.pendingChoice.candidates[0]?.role;
    const noun = role === "button" || role === "link" ? role : "field";
    if (state.pendingChoice.candidates.length === 1) return `The page changed today, and I'm not sure this ${noun} is the one I used before. Should I use it?`;
    return `The page changed today, and more than one ${noun} could be the one I used before. Which one should I use?`;
  }
  if (approval?.procedureChange && state.lastChoice?.ref) {
    return `The store admin renamed its ${approval.procedureChange.before.split(" ")[0]} button today. I'll use ${approval.procedureChange.after}, the one you picked.`;
  }
  if (approval?.procedureChange) {
    return `The store admin renamed its ${approval.procedureChange.before.split(" ")[0]} button today. I stopped before changing anything and found the new button.`;
  }
  if (approval?.booking) return `Seat ${approval.booking.seat} is held. Nothing is booked until you confirm.`;
  if (approval && state.run.kind === "replay") {
    const ready = state.readyInMs === null ? "" : ` Ready in ${seconds(state.readyInMs)}${state.claudeDecisions === 0 ? ", with no AI" : ""}.`;
    return `${approval.changes.length} products changed at the supplier.${ready}`;
  }
  if (approval) return `${approval.changes.length} products are different. Nothing is saved yet.`;
  if (state.lastAnswer) {
    const saving = state.steps.find((step) => step.stepId === state.activeStepId)?.activity;
    return `${saving ?? "Saving the changes"}. I'll check every row after.`;
  }
  if (state.run.kind === "replay") return `Running the saved steps for ${state.title.toLowerCase()}.`;
  if (state.run.kind === "undo") return "Putting back the stock from before the sync. I'll check every row after.";
  return `I'll read ${supplierOf(state)}'s stock and compare it with your store admin. I'll ask before saving anything.`;
}

const products = (count: number) => `${count} ${count === 1 ? "product" : "products"}`;

export function leftAloneText(leftAlone: LeftAlone | undefined) {
  if (!leftAlone) return null;
  const { onlyInStore, onlyAtSupplier } = leftAlone;
  if (onlyInStore > 0 && onlyAtSupplier > 0) {
    return `${products(onlyInStore)} ${onlyInStore === 1 ? "wasn't" : "weren't"} on the supplier's list. ${products(onlyAtSupplier)} ${onlyAtSupplier === 1 ? "is" : "are"} only at the supplier. Both stay as they are.`;
  }
  if (onlyInStore > 0) {
    return `${products(onlyInStore)} ${onlyInStore === 1 ? "wasn't" : "weren't"} on the supplier's list. ${onlyInStore === 1 ? "It stays as it is" : "They stay as they are"}.`;
  }
  if (onlyAtSupplier > 0) {
    return `${products(onlyAtSupplier)} ${onlyAtSupplier === 1 ? "is" : "are"} only at the supplier. ${onlyAtSupplier === 1 ? "It wasn't" : "They weren't"} added to the store.`;
  }
  return null;
}

export function replayEstimate(ms: number) {
  if (ms < 1000) return "under a second";
  const rounded = Math.round(ms / 1000);
  return `about ${rounded} ${rounded === 1 ? "second" : "seconds"}`;
}
