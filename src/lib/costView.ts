import type { JobCost } from "./jobState";

const SECONDS_PER_MINUTE = 60;

export function dollars(usd: number) {
  if (usd === 0) return "$0";
  if (usd < 0.005) return "under $0.01";
  return `$${usd.toFixed(2)}`;
}

export function claudeSpend(cost: JobCost) {
  if (cost.claudeCalls === 0) return "Not used";
  return `${cost.claudeCalls} ${cost.claudeCalls === 1 ? "call" : "calls"} · ${dollars(cost.claudeUsd)}`;
}

export function computerTime(cost: JobCost) {
  const seconds = cost.computerSeconds;
  if (seconds === 0) return "Not used";
  const time = seconds < SECONDS_PER_MINUTE ? `${seconds} s` : `${Math.round(seconds / SECONDS_PER_MINUTE)} min`;
  return cost.estimated ? `${time} (estimated)` : time;
}
