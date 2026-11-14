import { describe, expect, test } from "bun:test";
import { claudeSpend, computerTime, dollars } from "./costView";
import type { JobCost } from "./jobState";

const cost = (change: Partial<JobCost> = {}): JobCost => ({
  claudeCalls: 8,
  inputTokens: 41600,
  outputTokens: 5120,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  claudeUsd: 0.42,
  computerSeconds: 61,
  estimated: false,
  ...change,
});

describe("dollars", () => {
  test.each([
    [0, "$0"],
    [0.0336, "$0.03"],
    [0.2688, "$0.27"],
    [0.42, "$0.42"],
    [12.5, "$12.50"],
    [0.004, "under $0.01"],
    [0.000001, "under $0.01"],
  ])("shows %p as %p", (usd, text) => {
    expect(dollars(usd)).toBe(text);
  });
});

describe("claudeSpend", () => {
  test("counts the calls and the dollars", () => {
    expect(claudeSpend(cost())).toBe("8 calls · $0.42");
    expect(claudeSpend(cost({ claudeCalls: 1, claudeUsd: 0.0336 }))).toBe("1 call · $0.03");
  });

  test("says Claude was not used when there were no calls", () => {
    expect(claudeSpend(cost({ claudeCalls: 0, inputTokens: 0, outputTokens: 0, claudeUsd: 0 }))).toBe("Not used");
  });

  test("shows a call Claude did not bill as costing nothing", () => {
    expect(claudeSpend(cost({ claudeCalls: 1, inputTokens: 0, outputTokens: 0, claudeUsd: 0 }))).toBe("1 call · $0");
  });
});

describe("computerTime", () => {
  test.each([
    [0, "Not used"],
    [1, "1 s"],
    [45, "45 s"],
    [59, "59 s"],
    [60, "1 min"],
    [61, "1 min"],
    [89, "1 min"],
    [90, "2 min"],
    [3600, "60 min"],
  ])("shows %p seconds as %p", (seconds, text) => {
    expect(computerTime(cost({ computerSeconds: seconds }))).toBe(text);
  });

  test("labels the local computer's time as an estimate", () => {
    expect(computerTime(cost({ computerSeconds: 61, estimated: true }))).toBe("1 min (estimated)");
    expect(computerTime(cost({ computerSeconds: 6, estimated: true }))).toBe("6 s (estimated)");
  });
});
