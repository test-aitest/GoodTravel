import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/chat_reply.json";
import { factIds, type FactId } from "./chatFacts";
import type { ChatDecision } from "./chatStore";
import { decideByRules } from "./chatRules";

const all: FactId[] = ["last.result", "last.changes", "last.leftAlone", "last.steps", "last.timing", "last.stepsChange", "schedule", "last.cost", "last.ignored"];

const table: [string, ChatDecision["action"], FactId[]][] = [
  ["Undo that, please", "undo", []],
  ["Run it again", "run", []],
  ["Sync today's supplier stock to the store admin", "run", []],
  ["Can you run this every day?", "schedule", ["schedule"]],
  ["How many products changed?", "answer", ["last.changes", "last.leftAlone"]],
  ["Why did it ask me?", "answer", ["last.stepsChange"]],
  ["How long did it take?", "answer", ["last.timing"]],
  ["What did Claude cost?", "answer", ["last.cost"]],
  ["Was there a suspicious instruction?", "answer", ["last.ignored"]],
  ["Did it work?", "answer", ["last.result"]],
];

describe("deciding what a message means without AI", () => {
  test.each(table)("%s", (message, action, facts) => {
    const decision = decideByRules(message, all, "kumo");
    expect(decision.action).toBe(action);
    expect(decision.facts).toEqual(facts);
  });

  test("only offers facts the conversation has", () => {
    expect(decideByRules("How many products changed?", ["last.result"], "kumo")).toEqual({ action: "clarify", facts: [], clarify: "which-job" });
  });

  test("asks what to do when the message says nothing it can act on", () => {
    expect(decideByRules("hmm", all, "kumo")).toEqual({ action: "clarify", facts: [], clarify: "what-to-do" });
    expect(decideByRules("Book me a flight to Osaka", all, "kumo")).toEqual({ action: "cannot", facts: [], clarify: null });
  });
});

describe("the same answers as the worker", () => {
  const known = new Set<string>(factIds);
  const examples = contract.examples.map((example) => ({
    name: example.name,
    clientId: example.input.client_id,
    message: example.input.message,
    facts: example.input.facts.map((fact) => fact.id).filter((id): id is FactId => known.has(id)),
    expected: { action: example.output.action, facts: example.output.facts, clarify: example.output.clarify },
  }));

  test("the worker's examples use only facts the screen knows", () => {
    expect(examples.flatMap((example) => contract.examples.find((entry) => entry.name === example.name)?.input.facts.map((fact) => fact.id) ?? []).every((id) => known.has(id))).toBe(true);
    expect(examples.length).toBeGreaterThanOrEqual(13);
  });

  test.each(examples.map((example) => [example.name, example] as const))("%s", (_name, example) => {
    expect(decideByRules(example.message, example.facts, example.clientId)).toEqual(example.expected as ChatDecision);
  });
});

describe("another client's things", () => {
  test("a request for another client's login or site goes on as a job, so the boundary refuses it and records it", () => {
    for (const [message, clientId] of [
      ["Use Hinata's login for the store.", "kumo"],
      ["Sign in with Hinata Skincare's account", "kumo"],
      ["Open admin.hinata-skincare.example and sync it", "kumo"],
      ["Use Kumo Coffee's Maruyama login to check Hinata's supplier prices too.", "hinata"],
    ] as const) {
      expect(decideByRules(message, all, clientId)).toEqual({ action: "run", facts: [], clarify: null });
    }
  });

  test("a message that only names another client is something it cannot do here", () => {
    expect(decideByRules("Do the same as you did for Hinata", all, "kumo")).toEqual({ action: "cannot", facts: [], clarify: null });
    expect(decideByRules("How is Sora Outdoor doing?", all, "kumo")).toEqual({ action: "cannot", facts: [], clarify: null });
  });

  test("the client's own name is fine", () => {
    expect(decideByRules("Run Kumo Coffee's sync again", all, "kumo")).toEqual({ action: "run", facts: [], clarify: null });
    expect(decideByRules("Use Hinata's login for the store.", all, "hinata")).toEqual({ action: "cannot", facts: [], clarify: null });
  });
});
