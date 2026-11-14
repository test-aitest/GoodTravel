import asyncio
import json
import secrets
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agentfield.exceptions import AgentFieldClientError

from .approval import APPROVED, APPROVER, STOPPED_BEFORE_SAVING, ApprovalDesk, wait_for_answer
from .boundary import known_clients
from .page import Element, Snapshot, Tree, redact_elements
from .plan import PROBLEM_NOTES, AiCall, Choice, ClaudeCall, ElementChooser, Goal, HandOff, Problem, elapsed_ms
from .procedure import Hint, NoStableSelector, Target, target_for
from .records import EventNotes
from .refind import LOOK_ERRORS, FailedStep, NotFound, Recovered, close, page_opener, reach, rebuilt
from .settings import claude_allowed
from .stock_sync import NEXT_PAGE_NEEDED, NOTHING_CHANGED, SAVE_BUTTON, STORE_PLACE, SUPPLIER_PLACE, sign_in_goals

CLAUDE_RUNG = "claude"
HUMAN_RUNG = "human"
CLIMBED = ("script", "tree")
REJECTED = "rejected"
BEFORE_MARK = "The element this step used before: "
SIGN_IN_ELEMENTS = ("user", "password", "submit")

REPAIR_SYSTEM_PROMPT = (
    "You help a back-office worker repair one saved step of a routine on a web page. "
    "The page changed, so the element the step used before is no longer there under its old name. "
    "You get the goal of the step, the role, name and form of the element it used before, "
    "and the buttons and fields in that same form on the page now. Each has an id, a role and a name. "
    "Values on the page are hidden: [name] stands for a hidden name, [number] for a hidden number "
    "and [email] for a hidden email address. "
    "Pick the one element that now does what the old element did and say whether to click it or fill it. "
    "The worker's own code types any text and decides every address, so never suggest text, values or addresses. "
    "Set confident to false when you are not sure, including when more than one element could fit."
)

QUESTIONS = {
    "save": "Which button saves the changes now?",
    "user": "Which field takes the username now?",
    "password": "Which field takes the password now?",
    "submit": "Which button signs in now?",
    "next": "Which link goes to the next page now?",
}
NOUNS = {"button": "button", "link": "link"}


class RepairStopped(Exception):
    def __init__(self, message: str, problem: str) -> None:
        super().__init__(message)
        self.message = message
        self.problem = problem


@dataclass(frozen=True)
class RepairHelp:
    ai: AiCall | None
    desk: ApprovalDesk | None
    execution_id: str | None
    make_ai: Callable[[], AiCall | None] | None = None


@dataclass
class RepairSession:
    help: RepairHelp
    notes: EventNotes
    job_id: str | None
    usage: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    made_ai: AiCall | None = None
    waited_ms: int = 0

    def claude(self) -> AiCall | None:
        if self.help.ai is not None or self.help.make_ai is None:
            return self.help.ai
        if self.made_ai is None:
            self.made_ai = self.help.make_ai()
        return self.made_ai

    @property
    def claude_calls(self) -> int:
        return sum(len(calls) for calls in self.usage.values())

    def count(self, step_id: str, calls: Iterable[ClaudeCall]) -> None:
        listed = [call.to_json() for call in calls]
        if listed:
            self.usage.setdefault(step_id, []).extend(listed)


current_repair: ContextVar[RepairSession | None] = ContextVar("clientops_repair", default=None)


@dataclass(frozen=True)
class Look:
    tree: Tree
    in_form: tuple[Element, ...]
    snapshot: Snapshot | None


def goal_for(step_id: str, element: str) -> Goal:
    if step_id in ("sign-in", "compare"):
        goals = sign_in_goals(SUPPLIER_PLACE if step_id == "sign-in" else STORE_PLACE)
        return dict(zip(SIGN_IN_ELEMENTS, goals, strict=True))[element]
    if element == "next":
        return NEXT_PAGE_NEEDED
    return SAVE_BUTTON


def question_for(element: str) -> str:
    return QUESTIONS[element]


def noun_for(role: str) -> str:
    return NOUNS.get(role, "field")


def known_values(failed: FailedStep) -> list[str]:
    names = [text for client in known_clients() for text in (client.name, client.short_name, client.nickname)]
    names.append(failed.boundary.supplier.name)
    for table in failed.script.tables.values():
        if isinstance(table, Mapping):
            names.extend(str(cell) for row in table.get("rows", ()) for cell in row)
    return names


def repair_prompt(before: Mapping[str, Any]):
    def prompt(goal: Goal, view: list[dict[str, Any]], problem: Problem | None) -> str:
        lines = [
            f"Step goal: {goal.text}",
            f"This step needs one element to {goal.action}. The element must be one of the elements in the list.",
            f"{BEFORE_MARK}{json.dumps(before, ensure_ascii=False, separators=(',', ':'))}",
            "Buttons and fields in the same form on the page now, one JSON object per line:",
            *(json.dumps({"id": entry["id"], "role": entry["role"], "name": entry["name"]}, ensure_ascii=False, separators=(",", ":")) for entry in view),
        ]
        note = PROBLEM_NOTES.get(problem) if problem else None
        if note:
            lines.append(f"Your previous answer could not be used: {note}. Choose again from the same list.")
        return "\n".join(lines)

    return prompt


def masked_before(hint: Hint, tree: Tree, known: Iterable[str]) -> dict[str, Any]:
    [masked] = redact_elements([Element(0, hint.role, hint.name, hint.form)], [*known, *tree.cells])
    return {"role": masked["role"], "name": masked["name"], "form": masked["form"]}


def look_at_form(page: Any, failed: FailedStep, hint: Hint) -> Look:
    reach(page, failed.boundary, failed.procedure.read, failed.step_id)
    tree = page.tree(snapshot=True)
    return Look(tree, tuple(element for element in tree.elements if element.form == hint.form), tree.snapshot)


def stable(element: Element) -> Target | None:
    try:
        return target_for(element)
    except NoStableSelector:
        return None


def only_one(look: Look, element: Element) -> bool:
    return sum(1 for other in look.tree.elements if (other.role, other.name, other.form) == (element.role, element.name, element.form)) == 1


async def ask_claude(session: RepairSession, failed: FailedStep, not_found: NotFound, look: Look) -> Choice | HandOff:
    known = known_values(failed)
    ai = session.claude() if claude_allowed(failed.boundary.client.id) else None
    chooser = ElementChooser(
        ai,
        use_claude=ai is not None,
        clock=failed.clock,
        system=REPAIR_SYSTEM_PROMPT,
        prompt=repair_prompt(masked_before(not_found.hint, look.tree, known)),
    )
    form_only = Tree(look.tree.page, look.tree.mode, "", look.in_form, look.tree.cells)
    result = await chooser.choose(form_only, goal_for(not_found.step_id, not_found.element), known)
    session.count(not_found.step_id, result.calls)
    return result


def stop(message: str, problem: str) -> RepairStopped:
    return RepairStopped(f"{message} {NOTHING_CHANGED}", problem)


async def ask_person(session: RepairSession, failed: FailedStep, not_found: NotFound, candidates: list[Element]) -> Element:
    desk = session.help.desk
    noun = noun_for(not_found.hint.role)
    if desk is None or not session.help.execution_id:
        raise stop(f"No one could be asked which {noun} to use, so I stopped.", "no-person")
    choice_id = f"cho-{session.job_id or secrets.token_hex(6)}-{not_found.step_id}"
    by_ref = {str(element.ref): element for element in candidates}
    session.notes.emit(
        "choice.requested",
        choiceId=choice_id,
        stepId=not_found.step_id,
        question=question_for(not_found.element),
        before=not_found.hint.name,
        candidates=[{"ref": ref, "role": element.role, "name": element.name} for ref, element in by_ref.items()],
    )
    asked_at = failed.clock()
    try:
        answer = await wait_for_answer(desk, choice_id, session.help.execution_id)
    except asyncio.CancelledError:
        session.notes.emit("job.failed", reason="stopped", message=STOPPED_BEFORE_SAVING, stepId=not_found.step_id)
        if not desk.shutting_down():
            await desk.release(choice_id)
        raise
    except AgentFieldClientError:
        raise stop("I couldn't hold the job for your pick, so I stopped.", "pause-unavailable") from None
    session.waited_ms += elapsed_ms(asked_at, failed.clock())
    if answer.decision == REJECTED:
        session.notes.emit("choice.answered", choiceId=choice_id, ref=None, by=APPROVER)
        raise stop(f"You picked none of the {noun}s, so I stopped.", "none-chosen")
    if answer.decision != APPROVED:
        raise RepairStopped(f"The question ended without an answer. {NOTHING_CHANGED}", "choice-unanswered")
    picked = by_ref.get((answer.feedback or "").strip())
    if picked is None:
        raise stop(f"I couldn't tell which {noun} you picked, so I stopped.", "choice-unreadable")
    session.notes.emit("choice.answered", choiceId=choice_id, ref=str(picked.ref), by=APPROVER)
    return picked


async def repair_step(failed: FailedStep, not_found: NotFound, session: RepairSession) -> Recovered | None:
    started = failed.clock()
    try:
        page = await asyncio.to_thread(page_opener(failed.boundary, failed.open_page))
    except LOOK_ERRORS:
        return None
    try:
        look = await asyncio.to_thread(look_at_form, page, failed, not_found.hint)
    except LOOK_ERRORS:
        return None
    finally:
        await close(page)
    candidates = [element for element in look.in_form if element.role == not_found.hint.role and stable(element) is not None]
    if not candidates:
        return None

    def recovered(element: Element, rung: str, escalated_from: tuple[str, ...]) -> Recovered:
        target = target_for(element)
        procedure = rebuilt(failed.procedure, failed.boundary, not_found.step_id, not_found.element, target)
        took_ms = not_found.took_ms + elapsed_ms(started, failed.clock())
        return Recovered(not_found.step_id, not_found.element, procedure, rung, escalated_from, took_ms, look.snapshot)

    decided = await ask_claude(session, failed, not_found, look)
    if isinstance(decided, Choice) and only_one(look, decided.element) and stable(decided.element) is not None:
        return recovered(decided.element, CLAUDE_RUNG, CLIMBED)
    escalated = (*CLIMBED, CLAUDE_RUNG) if decided.calls else CLIMBED
    picked = await ask_person(session, failed, not_found, candidates)
    return recovered(picked, HUMAN_RUNG, escalated)
