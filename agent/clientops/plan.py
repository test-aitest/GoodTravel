import json
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from string import ascii_lowercase
from typing import Any

from .claude_call import ELEMENT_ACTIONS, NO_ELEMENT_ACTION, element_choice_call
from .cost import Budget, ClaudeBill, charge_claude, claude_bill, job_meter
from .page import Element, Locator, Tree, ai_view

AiCall = Callable[..., Awaitable[Any]]
Clock = Callable[[], float]

FILLABLE_ROLES = frozenset({"textbox", "searchbox", "spinbutton", "combobox"})
CLICKABLE_ROLES = frozenset(
    {"button", "link", "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio", "option", "switch", "tab", "treeitem"}
)
ROLES_FOR_ACTION = {"fill": FILLABLE_ROLES, "click": CLICKABLE_ROLES}
CHOICE_KEYS = frozenset({"element_id", "action", "confident"})
ATTEMPTS = 2
FINISHED = "stop"

SYSTEM_PROMPT = (
    "You help a back-office worker operate a web page. "
    "You get the goal of one step and the interactive elements of the current page. "
    "Each element has an id, a role, a name and the form it belongs to. "
    "Values on the page are hidden: [name] stands for a hidden name, [number] for a hidden number "
    "and [email] for a hidden email address. "
    "Pick the one element that does the step and say whether to click it or fill it. "
    "Only when the step says its element may be missing and no element on the page fits, "
    "answer with element_id null and action none. "
    "The worker's own code types any text and decides every address, so never suggest text, values or addresses. "
    "Set confident to false when you are not sure, including when more than one element could fit."
)


class Problem(StrEnum):
    UNAVAILABLE = "unavailable"
    DECLINED = "declined"
    CUT_OFF = "cut-off"
    NOT_JSON = "not-json"
    UNKNOWN_ELEMENT = "unknown-element"
    WRONG_ACTION = "wrong-action"
    ROLE_MISMATCH = "role-mismatch"
    REQUIRED_ELEMENT = "required-element"


PROBLEM_NOTES = {
    Problem.UNAVAILABLE: None,
    Problem.DECLINED: "the request was declined",
    Problem.CUT_OFF: "the answer was cut off before it was complete",
    Problem.NOT_JSON: "it was not the agreed JSON object",
    Problem.UNKNOWN_ELEMENT: "that id is not in the list",
    Problem.WRONG_ACTION: "this step needs the other action",
    Problem.ROLE_MISMATCH: "that element cannot take this action",
    Problem.REQUIRED_ELEMENT: "this step needs one of the elements in the list",
}

CHOSEN = "chosen"
UNSURE = "unsure"
ABSENT = "none"


class HandOffReason(StrEnum):
    CLAUDE_OFF = "claude-off"
    UNSURE = "unsure"
    NO_USABLE_ANSWER = "no-usable-answer"
    NOT_UNIQUE = "not-unique"
    OVER_BUDGET = "over-budget"
    BUDGET_UNKNOWN = "budget-unknown"


HAND_OFF_MESSAGES = {
    HandOffReason.CLAUDE_OFF: "Claude is turned off for this client, so a person needs to do this step. Nothing was changed.",
    HandOffReason.UNSURE: "Claude wasn't sure what to use on this page, so a person needs to do this step. Nothing was changed.",
    HandOffReason.NO_USABLE_ANSWER: "Claude's answer didn't match anything on this page, so a person needs to do this step. Nothing was changed.",
    HandOffReason.NOT_UNIQUE: "The button Claude picked isn't the only one like it on this page, so a person needs to check this step. Nothing was changed.",
    HandOffReason.OVER_BUDGET: "This would go over this client's monthly Claude budget, so a person needs to do this step. Nothing was changed.",
    HandOffReason.BUDGET_UNKNOWN: "I couldn't check this client's Claude spending this month, so a person needs to do this step. Nothing was changed.",
}
BUDGET_HAND_OFFS = {Budget.OVER: HandOffReason.OVER_BUDGET, Budget.UNKNOWN: HandOffReason.BUDGET_UNKNOWN}


@dataclass(frozen=True)
class Goal:
    text: str
    action: str
    optional: bool = False


@dataclass(frozen=True)
class ClaudeCall:
    outcome: str
    took_ms: int
    usage: dict[str, int]
    cost_usd: float | None

    def to_json(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "tookMs": self.took_ms, "usage": dict(self.usage), "costUsd": self.cost_usd}


@dataclass(frozen=True)
class Choice:
    element: Element
    action: str
    calls: tuple[ClaudeCall, ...]

    @property
    def locator(self) -> Locator:
        return Locator(self.element.role, self.element.name, self.element.form)


@dataclass(frozen=True)
class Absent:
    goal: Goal
    calls: tuple[ClaudeCall, ...]


@dataclass(frozen=True)
class HandOff:
    goal: Goal | None
    reason: HandOffReason
    calls: tuple[ClaudeCall, ...]

    @property
    def message(self) -> str:
        return HAND_OFF_MESSAGES[self.reason]


@dataclass(frozen=True)
class Verdict:
    outcome: str
    element: Element | None = None
    action: str | None = None


def letter_id(index: int) -> str:
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, len(ascii_lowercase))
        letters = ascii_lowercase[remainder] + letters
    return letters


def prompt_view(tree: Tree, known_values: Iterable[str] = ()) -> list[dict[str, Any]]:
    return [
        {"id": letter_id(index), "role": entry["role"], "name": entry["name"], "form": entry["form"]}
        for index, entry in enumerate(ai_view(tree, known_values))
    ]


def user_prompt(goal: Goal, view: list[dict[str, Any]], problem: Problem | None) -> str:
    if goal.optional:
        presence = "This element may be missing from this page. If it is missing, answer with element_id null and action none."
    else:
        presence = "The element must be one of the elements in the list."
    lines = [
        f"Step goal: {goal.text}",
        f"This step needs one element to {goal.action}. {presence}",
        "Elements on the page, one JSON object per line:",
        *(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in view),
    ]
    note = PROBLEM_NOTES.get(problem) if problem else None
    if note:
        lines.append(f"Your previous answer could not be used: {note}. Choose again from the same list.")
    return "\n".join(lines)


PromptBuilder = Callable[[Goal, list[dict[str, Any]], Problem | None], str]


def finish_reason(response: Any) -> str | None:
    try:
        return response.raw_response.choices[0].finish_reason
    except (AttributeError, IndexError, TypeError):
        return None


def judge(response: Any, elements: Mapping[str, Element], goal: Goal) -> Verdict:
    reason = finish_reason(response)
    if reason == "content_filter":
        return Verdict(Problem.DECLINED)
    if reason == "length":
        return Verdict(Problem.CUT_OFF)
    if reason != FINISHED:
        return Verdict(Problem.NOT_JSON)
    try:
        reply = json.loads(response.text)
    except (TypeError, ValueError):
        return Verdict(Problem.NOT_JSON)
    if not isinstance(reply, dict) or set(reply) != CHOICE_KEYS:
        return Verdict(Problem.NOT_JSON)
    element_id, action, confident = reply["element_id"], reply["action"], reply["confident"]
    if not (element_id is None or isinstance(element_id, str)) or not isinstance(action, str) or not isinstance(confident, bool):
        return Verdict(Problem.NOT_JSON)
    action = action.strip().lower()
    if action not in (*ELEMENT_ACTIONS, NO_ELEMENT_ACTION) or (action == NO_ELEMENT_ACTION) != (element_id is None):
        return Verdict(Problem.NOT_JSON)
    if not confident:
        return Verdict(UNSURE)
    if action == NO_ELEMENT_ACTION:
        return Verdict(ABSENT) if goal.optional else Verdict(Problem.REQUIRED_ELEMENT)
    element = elements.get(element_id.strip())
    if element is None:
        return Verdict(Problem.UNKNOWN_ELEMENT)
    if action != goal.action:
        return Verdict(Problem.WRONG_ACTION)
    if element.role not in ROLES_FOR_ACTION[action]:
        return Verdict(Problem.ROLE_MISMATCH)
    return Verdict(CHOSEN, element, action)


def elapsed_ms(start: float, end: float) -> int:
    return round((end - start) * 1000)


def budget_hand_off() -> HandOffReason | None:
    meter = job_meter()
    return None if meter is None else BUDGET_HAND_OFFS.get(meter.claude_budget())


class ElementChooser:
    def __init__(
        self,
        ai: AiCall | None,
        *,
        use_claude: bool,
        clock: Clock = time.monotonic,
        system: str = SYSTEM_PROMPT,
        prompt: PromptBuilder = user_prompt,
    ) -> None:
        if use_claude and ai is None:
            raise ValueError("Claude is on for this client but there is no way to call it")
        self._ai = ai
        self._use_claude = use_claude
        self._clock = clock
        self._system = system
        self._prompt = prompt

    async def choose(self, tree: Tree, goal: Goal, known_values: Iterable[str] = ()) -> Choice | Absent | HandOff:
        if not self._use_claude:
            return HandOff(goal, HandOffReason.CLAUDE_OFF, ())
        view = prompt_view(tree, known_values)
        elements = {entry["id"]: element for entry, element in zip(view, tree.elements, strict=True)}
        calls: list[ClaudeCall] = []
        problem: Problem | None = None
        for _ in range(ATTEMPTS):
            over = budget_hand_off()
            if over is not None:
                return HandOff(goal, over, tuple(calls))
            verdict, call = await self._ask(goal, view, elements, problem)
            calls.append(call)
            if verdict.outcome == CHOSEN:
                return Choice(verdict.element, verdict.action, tuple(calls))
            if verdict.outcome == ABSENT:
                return Absent(goal, tuple(calls))
            if verdict.outcome == UNSURE:
                return HandOff(goal, HandOffReason.UNSURE, tuple(calls))
            problem = Problem(verdict.outcome)
        return HandOff(goal, HandOffReason.NO_USABLE_ANSWER, tuple(calls))

    async def _ask(
        self, goal: Goal, view: list[dict[str, Any]], elements: Mapping[str, Element], problem: Problem | None
    ) -> tuple[Verdict, ClaudeCall]:
        started = self._clock()
        try:
            response = await self._ai(**element_choice_call(system=self._system, user=self._prompt(goal, view, problem)))
        except Exception:
            charge_claude(ClaudeBill(calls=1))
            return Verdict(Problem.UNAVAILABLE), ClaudeCall(Problem.UNAVAILABLE.value, elapsed_ms(started, self._clock()), {}, None)
        took_ms = elapsed_ms(started, self._clock())
        verdict = judge(response, elements, goal)
        usage = {name: int(value) for name, value in (getattr(response, "usage", None) or {}).items()}
        bill = claude_bill(response)
        charge_claude(bill)
        call = ClaudeCall(str(verdict.outcome), took_ms, usage, float(bill.usd))
        return verdict, call
