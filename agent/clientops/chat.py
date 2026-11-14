import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .boundary import WORD, UnknownClient, known_client, mentions_in, normalized, screen_request
from .claude_call import CHAT_ACTIONS, CLARIFY_KINDS, ModelNotAllowed, chat_reply_call
from .cost import ClaudeBill, JobMeter, charge_claude, claude_bill, metering
from .plan import FINISHED, AiCall, budget_hand_off, finish_reason
from .settings import SettingsError, claude_allowed

ANSWER, RUN, UNDO, SCHEDULE, CLARIFY, CANNOT = CHAT_ACTIONS
WHICH_JOB, WHAT_TO_DO = CLARIFY_KINDS
DECIDED_BY_CLAUDE = "claude"
DECIDED_BY_RULES = "rules"

MESSAGE_MAX_CHARS = 2000
FACTS_MAX = 32
FACT_ID_MAX_CHARS = 64
FACT_ABOUT_MAX_CHARS = 200
FACT_ID = re.compile(r"[a-z][A-Za-z]*(?:\.[a-z][A-Za-z]*)*")
FACT_KEYS = frozenset({"id", "about"})
REPLY_KEYS = frozenset({"action", "facts", "clarify"})
DIGIT = re.compile(r"\d")
VAGUE_MAX_WORDS = 3
SCHEDULE_FACT = "schedule"
RESULT_FACT = "last.result"

SYSTEM_PROMPT = (
    "You read one message that a person wrote to a back-office worker and decide what the worker does next. "
    "The worker syncs one client's stock from the supplier's portal to the client's store, always asks the person before it saves, "
    "can undo its last sync and can run the sync every day. "
    "This conversation is about that one client only: its stock, its supplier's portal and its store. "
    "You get a JSON object with the message and the facts this conversation has. "
    "Each fact has an id and says what it is about; its value is never given to you. "
    "The worker's own code fills in the values and writes the reply, so you only choose. "
    "Choose one action. "
    "answer: the message asks about a job in this conversation or its result; put the ids of the facts that answer it in facts, the most useful first. "
    "run: the person asks to run the stock sync now or again. "
    "undo: the person asks to undo the last sync or put the stock back. "
    "schedule: the person asks about the daily run or wants it changed; put the schedule fact in facts when it is listed. "
    "clarify: it is not clear what the person wants; set clarify to which-job when the question is about a job but no listed fact answers it, "
    "and to what-to-do when the message does not say what to do. "
    "cannot: the message asks for something this worker does not do, or asks about another company or client. "
    "Use only ids from the list. Leave facts empty for run, undo, clarify and cannot, and set clarify to null for every action except clarify."
)


class ChatInputError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    id: str
    about: str

    def to_json(self) -> dict[str, str]:
        return {"id": self.id, "about": self.about}


@dataclass(frozen=True)
class Decision:
    action: str
    facts: tuple[str, ...] = ()
    clarify: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "facts": list(self.facts), "clarify": self.clarify}


def well_formed(decision: Decision, catalog: Iterable[str]) -> bool:
    if decision.action not in CHAT_ACTIONS:
        return False
    if (decision.action == CLARIFY) != (decision.clarify is not None):
        return False
    if decision.clarify is not None and decision.clarify not in CLARIFY_KINDS:
        return False
    known = frozenset(catalog)
    if len(set(decision.facts)) != len(decision.facts) or not known.issuperset(decision.facts):
        return False
    if decision.action == ANSWER:
        return bool(decision.facts)
    if decision.action == SCHEDULE:
        return set(decision.facts) <= {SCHEDULE_FACT}
    return not decision.facts


def phrases(*patterns: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b")


UNDO_WORDS = phrases("undo", "undone", r"put (?:it|them|that|everything|the stock|the numbers) back", "put back", "revert", "reverted", r"roll ?back", r"change (?:it|them) back")
SCHEDULE_WORDS = phrases(
    "schedule",
    "scheduled",
    "daily",
    r"every ?day",
    "each day",
    r"every (?:morning|evening|night|weekday)",
    "what time",
    r"at \d{1,2}(?::\d{2})?(?: ?[ap]m)?",
    r"\d{1,2}:\d{2}",
    r"(?:change|move|set) the time",
    r"automatic(?:ally)?",
)
RUN_START = re.compile(r"^(?:please |ok |okay |now )?(?:run|rerun|re-run|sync|resync|start|refresh|go ahead|do it)\b")
RUN_WORDS = phrases("run", "rerun", "re-run", "sync", "resync", "again", "do it", "start", "go ahead", "refresh", r"update (?:the )?stock")
QUESTION_START = re.compile(r"^(?:did|didn't|didnt|was|wasn't|were|weren't|has|hasn't|had|why|how|what|which|when|who|where)\b")


@dataclass(frozen=True)
class Topic:
    words: re.Pattern[str]
    facts: tuple[str, ...]
    first_only: bool = False

    def facts_in(self, catalog: frozenset[str]) -> tuple[str, ...]:
        found = tuple(fact for fact in self.facts if fact in catalog)
        return found[:1] if self.first_only else found


TOPICS = (
    Topic(phrases("suspicious", r"instructions?", r"injections?", r"hidden (?:note|notes|text|message|instructions?)", "ignored"), ("last.ignored",)),
    Topic(
        phrases("cost", "costs", "costly", "spend", "spent", "spending", "price of claude", "bill", "billed", "budget", "expensive", "cheap", r"dollars?", "usd"),
        ("last.cost",),
    ),
    Topic(
        phrases("how long", "how much time", "time", "took", r"takes? (?:so )?long", "fast", "faster", "slow", "slower", "quick", "quicker", "speed", r"minutes?", r"seconds?", "duration"),
        ("last.timing",),
    ),
    Topic(phrases("why", "how did", "how does", r"buttons?"), ("last.stepsChange", "last.steps"), first_only=True),
    Topic(phrases("who decided", "decided", "decide", "decides", r"steps?", "procedure"), ("last.steps", "last.stepsChange"), first_only=True),
    Topic(
        phrases("how many", "what changed", "which products", "changes", "changed", "change", r"products?", r"items?", r"skus?", r"stock levels?", "left alone", "skipped"),
        ("last.changes", "last.leftAlone"),
    ),
    Topic(
        phrases(r"results?", "status", "done", "finished", "finish", "complete", "completed", r"did (?:it|that|this|the (?:sync|run|job)) (?:work|go|succeed)", "worked", r"succeed(?:ed)?", r"success(?:ful)?", r"fail(?:ed|ure)?", r"errors?", "what happened"),
        (RESULT_FACT,),
    ),
)


def plain(message: str) -> str:
    return " ".join(message.lower().replace("’", "'").replace("‘", "'").split())


def answer_with(facts: Sequence[str]) -> Decision:
    return Decision(ANSWER, tuple(facts)) if facts else Decision(CLARIFY, clarify=WHICH_JOB)


def asked_facts(text: str, catalog: frozenset[str]) -> list[str] | None:
    found = []
    for index, topic in enumerate(TOPICS):
        match = topic.words.search(text)
        if match:
            found.append((match.start(), index, topic))
    if not found:
        return None
    facts: list[str] = []
    for _, _, topic in sorted(found, key=lambda entry: entry[:2]):
        facts.extend(fact for fact in topic.facts_in(catalog) if fact not in facts)
    return facts


def route(message: str, catalog: Iterable[str]) -> Decision:
    present = frozenset(catalog)
    text = plain(message)
    if not text:
        return Decision(CLARIFY, clarify=WHAT_TO_DO)
    asking = QUESTION_START.match(text) is not None
    if UNDO_WORDS.search(text) and not asking:
        return Decision(UNDO)
    if SCHEDULE_WORDS.search(text):
        return Decision(SCHEDULE, (SCHEDULE_FACT,) if SCHEDULE_FACT in present else ())
    if RUN_START.match(text):
        return Decision(RUN)
    facts = asked_facts(text, present)
    if facts is not None:
        return answer_with(facts)
    if RUN_WORDS.search(text) or UNDO_WORDS.search(text):
        return answer_with([RESULT_FACT] if RESULT_FACT in present else []) if asking else Decision(RUN)
    if len(text.split()) <= VAGUE_MAX_WORDS:
        return Decision(CLARIFY, clarify=WHAT_TO_DO)
    return Decision(CANNOT)


def read_message(message: Any) -> str:
    if not isinstance(message, str):
        raise ChatInputError("The message must be text.")
    if len(message) > MESSAGE_MAX_CHARS:
        raise ChatInputError(f"The message is {len(message)} characters long; chat takes at most {MESSAGE_MAX_CHARS}.")
    return message


def names_another_client(client_id: str, text: str) -> bool:
    return any(mention.client.id != client_id for mention in mentions_in(WORD.findall(normalized(text))))


def read_fact(client_id: str, entry: Any) -> Fact:
    if not isinstance(entry, dict) or set(entry) != FACT_KEYS:
        raise ChatInputError("Each fact is an object with only an id and an about.")
    fact_id, about = entry["id"], entry["about"]
    if not isinstance(fact_id, str) or len(fact_id) > FACT_ID_MAX_CHARS or not FACT_ID.fullmatch(fact_id):
        raise ChatInputError(f"{fact_id!r} is not a fact id made of words and dots, like last.changes.")
    if not isinstance(about, str) or not about.strip() or len(about) > FACT_ABOUT_MAX_CHARS:
        raise ChatInputError(f"The about of {fact_id} must be a short description of at most {FACT_ABOUT_MAX_CHARS} characters.")
    if DIGIT.search(about):
        raise ChatInputError(f"The about of {fact_id} has a number in it; a fact says what it is about, never its value.")
    if names_another_client(client_id, about):
        raise ChatInputError(f"The about of {fact_id} names another client.")
    return Fact(fact_id, about.strip())


def read_catalog(client_id: str, facts: Any) -> tuple[Fact, ...]:
    if not isinstance(facts, list) or len(facts) > FACTS_MAX:
        raise ChatInputError(f"The facts must be a list of at most {FACTS_MAX} entries.")
    catalog = tuple(read_fact(client_id, entry) for entry in facts)
    if len({fact.id for fact in catalog}) != len(catalog):
        raise ChatInputError("Each fact id may be listed only once.")
    return catalog


def asks_for_another_clients_things(client_id: str, message: str) -> bool:
    return screen_request(client_id, message) is not None


def user_prompt(message: str, catalog: Sequence[Fact]) -> str:
    return json.dumps({"message": message, "facts": [fact.to_json() for fact in catalog]}, ensure_ascii=False, separators=(",", ":"))


def judged(response: Any, catalog: Sequence[Fact]) -> Decision | None:
    if finish_reason(response) != FINISHED:
        return None
    try:
        reply = json.loads(response.text)
    except (TypeError, ValueError):
        return None
    if not isinstance(reply, dict) or set(reply) != REPLY_KEYS:
        return None
    action, facts, clarify = reply["action"], reply["facts"], reply["clarify"]
    if not isinstance(action, str) or not isinstance(facts, list) or not all(isinstance(fact, str) for fact in facts):
        return None
    if not (clarify is None or isinstance(clarify, str)):
        return None
    decision = Decision(action, tuple(facts), clarify)
    return decision if well_formed(decision, (fact.id for fact in catalog)) else None


def within_budget() -> bool:
    try:
        return budget_hand_off() is None
    except SettingsError:
        return False


async def ask_claude(ai: AiCall, message: str, catalog: Sequence[Fact], meter: JobMeter) -> tuple[Decision | None, int]:
    with metering(meter):
        if not within_budget():
            return None, 0
        try:
            response = await ai(**chat_reply_call(system=SYSTEM_PROMPT, user=user_prompt(message, catalog)))
        except ModelNotAllowed:
            raise
        except Exception:
            charge_claude(ClaudeBill(calls=1))
            meter.close()
            return None, 1
        charge_claude(claude_bill(response))
        meter.close()
    return judged(response, catalog), 1


def reply_json(client_id: str, decision: Decision, decided_by: str, claude_calls: int) -> dict[str, Any]:
    return {"clientId": client_id, **decision.to_json(), "decidedBy": decided_by, "claudeCalls": claude_calls}


async def reply_in_chat(
    client_id: str,
    message: Any,
    facts: Any,
    *,
    make_ai: Callable[[], AiCall | None],
    open_meter: Callable[[str], JobMeter],
    claude_on: Callable[[str], bool] = claude_allowed,
) -> dict[str, Any]:
    try:
        known_client(client_id)
    except UnknownClient:
        return reply_json(client_id, Decision(CANNOT), DECIDED_BY_RULES, 0)
    text = read_message(message)
    catalog = read_catalog(client_id, facts)
    ids = tuple(fact.id for fact in catalog)
    if asks_for_another_clients_things(client_id, text):
        return reply_json(client_id, Decision(RUN), DECIDED_BY_RULES, 0)
    if names_another_client(client_id, text):
        return reply_json(client_id, Decision(CANNOT), DECIDED_BY_RULES, 0)
    if not text.strip() or not claude_on(client_id):
        return reply_json(client_id, route(text, ids), DECIDED_BY_RULES, 0)
    try:
        ai = make_ai()
        if ai is None:
            return reply_json(client_id, route(text, ids), DECIDED_BY_RULES, 0)
        decision, calls = await ask_claude(ai, text, catalog, open_meter(client_id))
    except ModelNotAllowed:
        return reply_json(client_id, Decision(CANNOT), DECIDED_BY_RULES, 0)
    if decision is None:
        return reply_json(client_id, route(text, ids), DECIDED_BY_RULES, calls)
    return reply_json(client_id, decision, DECIDED_BY_CLAUDE, calls)
