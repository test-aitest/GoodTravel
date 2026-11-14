import type { FactId } from "./chatFacts";
import type { ChatDecision } from "./chatStore";
import { clientNames } from "./clients";

const phrases = (...patterns: string[]) => new RegExp(String.raw`\b(?:${patterns.join("|")})\b`);

const undoWords = phrases("undo", "undone", "put (?:it|them|that|everything|the stock|the numbers) back", "put back", "revert", "reverted", "roll ?back", "change (?:it|them) back");
const scheduleWords = phrases(
  "schedule",
  "scheduled",
  "daily",
  "every ?day",
  "each day",
  "every (?:morning|evening|night|weekday)",
  "what time",
  String.raw`at \d{1,2}(?::\d{2})?(?: ?[ap]m)?`,
  String.raw`\d{1,2}:\d{2}`,
  "(?:change|move|set) the time",
  "automatic(?:ally)?",
);
const runStart = /^(?:please |ok |okay |now )?(?:run|rerun|re-run|sync|resync|start|refresh|go ahead|do it)\b/;
const runWords = phrases("run", "rerun", "re-run", "sync", "resync", "again", "do it", "start", "go ahead", "refresh", "update (?:the )?stock");
const questionStart = /^(?:did|didn't|didnt|was|wasn't|were|weren't|has|hasn't|had|why|how|what|which|when|who|where)\b/;
const vagueMaxWords = 3;

type Topic = { words: RegExp; facts: readonly FactId[]; firstOnly?: boolean };

const topics: readonly Topic[] = [
  { words: phrases("suspicious", "instructions?", "injections?", "hidden (?:note|notes|text|message|instructions?)", "ignored"), facts: ["last.ignored"] },
  { words: phrases("cost", "costs", "costly", "spend", "spent", "spending", "price of claude", "bill", "billed", "budget", "expensive", "cheap", "dollars?", "usd"), facts: ["last.cost"] },
  {
    words: phrases("how long", "how much time", "time", "took", "takes? (?:so )?long", "fast", "faster", "slow", "slower", "quick", "quicker", "speed", "minutes?", "seconds?", "duration"),
    facts: ["last.timing"],
  },
  { words: phrases("why", "how did", "how does", "buttons?"), facts: ["last.stepsChange", "last.steps"], firstOnly: true },
  { words: phrases("who decided", "decided", "decide", "decides", "steps?", "procedure"), facts: ["last.steps", "last.stepsChange"], firstOnly: true },
  {
    words: phrases("how many", "what changed", "which products", "changes", "changed", "change", "products?", "items?", "skus?", "stock levels?", "left alone", "skipped"),
    facts: ["last.changes", "last.leftAlone"],
  },
  {
    words: phrases(
      "results?",
      "status",
      "done",
      "finished",
      "finish",
      "complete",
      "completed",
      "did (?:it|that|this|the (?:sync|run|job)) (?:work|go|succeed)",
      "worked",
      "succeed(?:ed)?",
      "success(?:ful)?",
      "fail(?:ed|ure)?",
      "errors?",
      "what happened",
    ),
    facts: ["last.result"],
  },
];

const decision = (action: ChatDecision["action"], facts: FactId[] = [], clarify: ChatDecision["clarify"] = null): ChatDecision => ({ action, facts, clarify });

function plain(message: string) {
  return message.toLowerCase().replaceAll("’", "'").replaceAll("‘", "'").split(/\s+/).filter(Boolean).join(" ");
}

function answerWith(facts: FactId[]): ChatDecision {
  return facts.length > 0 ? decision("answer", facts) : decision("clarify", [], "which-job");
}

function askedFacts(text: string, catalog: ReadonlySet<FactId>): FactId[] | null {
  const found = topics.flatMap((topic, index) => {
    const match = topic.words.exec(text);
    return match ? [{ start: match.index, index, topic }] : [];
  });
  if (found.length === 0) return null;
  const facts: FactId[] = [];
  for (const { topic } of found.toSorted((a, b) => a.start - b.start || a.index - b.index)) {
    const present = topic.facts.filter((fact) => catalog.has(fact));
    for (const fact of topic.firstOnly ? present.slice(0, 1) : present) if (!facts.includes(fact)) facts.push(fact);
  }
  return facts;
}

const resourceWords = /\b(?:logins?|log in|sign in|signin|accounts?|passwords?|credentials?|vault|computer|portal|admin|store)\b/;

function spoken(text: string) {
  return text.toLowerCase().replaceAll("’", "'").replaceAll("‘", "'").replaceAll(/(\w)'s\b/g, "$1").replaceAll(/(\w)s'(?=\W|$)/g, "$1s");
}

function otherClientsIn(text: string, clientId: string) {
  const words = ` ${spoken(text).split(/[^a-z0-9]+/).filter(Boolean).join(" ")} `;
  return clientNames.filter((client) => client.id !== clientId && client.names.some((name) => words.includes(` ${spoken(name).split(/[^a-z0-9]+/).filter(Boolean).join(" ")} `)));
}

function siteOfAnotherClient(text: string, clientId: string) {
  const lowered = text.toLowerCase();
  return clientNames.some(
    (client) => client.id !== clientId && client.names.some((name) => new RegExp(String.raw`\b${spoken(name).split(/[^a-z0-9]+/).filter(Boolean).join("-")}\.example\b`).test(lowered)),
  );
}

function asksForAnotherClientsThings(message: string, clientId: string) {
  if (siteOfAnotherClient(message, clientId)) return true;
  return message.split(/[.!?;\n]+/).some((sentence) => resourceWords.test(spoken(sentence)) && otherClientsIn(sentence, clientId).length > 0);
}

function route(message: string, catalog: readonly FactId[]): ChatDecision {
  const present = new Set(catalog);
  const text = plain(message);
  if (!text) return decision("clarify", [], "what-to-do");
  const asking = questionStart.test(text);
  if (undoWords.test(text) && !asking) return decision("undo");
  if (scheduleWords.test(text)) return decision("schedule", present.has("schedule") ? ["schedule"] : []);
  if (runStart.test(text)) return decision("run");
  const facts = askedFacts(text, present);
  if (facts) return answerWith(facts);
  if (runWords.test(text) || undoWords.test(text)) return asking ? answerWith(present.has("last.result") ? ["last.result"] : []) : decision("run");
  if (text.split(" ").length <= vagueMaxWords) return decision("clarify", [], "what-to-do");
  return decision("cannot");
}

export function decideByRules(message: string, facts: readonly FactId[], clientId: string): ChatDecision {
  if (asksForAnotherClientsThings(message, clientId)) return decision("run");
  if (otherClientsIn(message, clientId).length > 0) return decision("cannot");
  return route(message, facts);
}
