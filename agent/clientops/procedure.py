import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from agentfield.exceptions import MemoryAccessError

from .boundary import ClientBoundary
from .compile_script import compile_read_script
from .diff import StockChange
from .page import Element
from .records import EventNotes

PROCEDURE_KEY = "stock-sync/procedure"
RECORD_VERSION = 1
STEP_IDS = ("sign-in", "read", "compare", "check-save", "ask", "save")
SITE_KINDS = ("supplier", "store")
NAME_SLOT = "{name}"
STATUS_CSS = '[role="status"]'
FIELD_TAGS = frozenset({"input", "textarea", "select"})
XPATH_STEP = re.compile(r"/([a-z][a-z0-9-]*)\[(\d+)\]")
FORM_XPATH = re.compile(r"(?:/[a-z][a-z0-9-]*\[\d+\])+")
SPACES = re.compile(r"\s+")


class NoStableSelector(ValueError):
    pass


def css_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\a ")
    return f'"{escaped}"'


def spaced(text: str) -> str:
    return SPACES.sub(" ", text).strip()


def form_scope(form: str | None) -> str:
    if form is None:
        return ""
    if FORM_XPATH.fullmatch(form):
        return " > ".join(tag if tag == "html" else f"{tag}:nth-of-type({index})" for tag, index in XPATH_STEP.findall(form)) + " "
    return f"form[action={css_text(form)}] "


@dataclass(frozen=True)
class Hint:
    role: str
    name: str
    form: str | None

    def to_json(self) -> dict[str, Any]:
        return {"role": self.role, "name": self.name, "form": self.form}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Hint":
        form = data["form"]
        if not isinstance(data["role"], str) or not isinstance(data["name"], str) or not (form is None or isinstance(form, str)):
            raise ValueError(f"{data!r} is not an element hint")
        return cls(data["role"], data["name"], form)


@dataclass(frozen=True)
class Target:
    css: str
    text: str | None
    hint: Hint

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {"css": self.css}
        if self.text is not None:
            found["text"] = self.text
        return {**found, "hint": self.hint.to_json()}

    def plan(self) -> dict[str, Any]:
        return {"css": self.css, "text": self.text}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Target":
        text = data.get("text")
        if not isinstance(data["css"], str) or not data["css"] or not (text is None or isinstance(text, str)):
            raise ValueError(f"{data!r} is not a target")
        return cls(data["css"], text, Hint.from_json(data["hint"]))


def target_for(element: Element) -> Target:
    tag = (element.tag or "").lower()
    if not tag:
        raise NoStableSelector(f"The {element.role} {element.name!r} has no tag to select it by")
    hint = Hint(element.role, element.name, element.form)
    scope = form_scope(element.form)
    if tag in FIELD_TAGS:
        if not element.field_name:
            raise NoStableSelector(f"The {element.role} {element.name!r} has no name attribute to select it by")
        css = f"{scope}{tag}[name={css_text(element.field_name)}]"
        if tag == "input" and element.input_type == "password":
            css += '[type="password"]'
        return Target(css, None, hint)
    name = spaced(element.name)
    if not name:
        raise NoStableSelector(f"The {element.role} has no name to tell it apart by")
    named = f"[name={css_text(element.field_name)}]" if element.field_name else ""
    return Target(f"{scope}{tag}{named}", name, hint)


@dataclass(frozen=True)
class FieldPattern:
    scope: str
    tag: str
    prefix: str
    suffix: str
    hint: Hint

    def target(self, sku: str, name: str) -> Target:
        css = f"{self.scope}{self.tag}[name={css_text(f'{self.prefix}{sku}{self.suffix}')}]"
        return Target(css, None, Hint(self.hint.role, self.hint.name.replace(NAME_SLOT, name), self.hint.form))

    def to_json(self) -> dict[str, Any]:
        return {"scope": self.scope, "tag": self.tag, "namePrefix": self.prefix, "nameSuffix": self.suffix, "hint": self.hint.to_json()}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "FieldPattern":
        values = (data["scope"], data["tag"], data["namePrefix"], data["nameSuffix"])
        if not all(isinstance(value, str) for value in values) or not data["tag"]:
            raise ValueError(f"{data!r} is not a field pattern")
        return cls(*values, Hint.from_json(data["hint"]))


def field_pattern(fields: Mapping[str, Element], changes: Iterable[StockChange]) -> FieldPattern:
    patterns: set[FieldPattern] = set()
    for change in changes:
        element = fields.get(change.sku)
        tag = (element.tag or "").lower() if element else ""
        if element is None or tag not in FIELD_TAGS or not element.field_name or element.field_name.count(change.sku) != 1:
            raise NoStableSelector(f"The field for {change.sku} has no name that carries the SKU")
        prefix, _, suffix = element.field_name.partition(change.sku)
        hint_name = element.name.replace(change.name, NAME_SLOT) if change.name and change.name in element.name else element.name
        patterns.add(FieldPattern(form_scope(element.form), tag, prefix, suffix, Hint(element.role, hint_name, element.form)))
    if len(patterns) != 1:
        raise NoStableSelector("The fields that were written do not follow one pattern")
    return patterns.pop()


@dataclass(frozen=True)
class SignInSteps:
    site: str
    landing: str
    user: Target
    password: Target
    submit: Target

    def elements(self) -> dict[str, Target]:
        return {"user": self.user, "password": self.password, "submit": self.submit}

    def to_json(self) -> dict[str, Any]:
        return {"site": self.site, "landing": self.landing, "elements": {name: target.to_json() for name, target in self.elements().items()}}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "SignInSteps":
        if data["site"] not in SITE_KINDS or not isinstance(data["landing"], str) or not data["landing"].startswith("/"):
            raise ValueError(f"{data!r} is not a sign-in step")
        elements = data["elements"]
        return cls(data["site"], data["landing"], *(Target.from_json(elements[name]) for name in ("user", "password", "submit")))


@dataclass(frozen=True)
class ReadSteps:
    supplier: SignInSteps
    next_page: Target | None
    store: SignInSteps
    save_button: Target


@dataclass(frozen=True)
class ProcedureDraft:
    read: ReadSteps
    expected_replay_ms: int

    def to_json(self) -> dict[str, Any]:
        return {"version": RECORD_VERSION, "expectedReplayMs": self.expected_replay_ms, "steps": read_steps_json(self.read)}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ProcedureDraft":
        if data.get("version") != RECORD_VERSION:
            raise ValueError("A procedure draft must be version 1")
        return cls(read_steps_from(data["steps"]), whole(data["expectedReplayMs"]))


def whole(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{value!r} is not a whole number of 0 or more")
    return value


def read_steps_json(read: ReadSteps) -> list[dict[str, Any]]:
    return [
        {"stepId": "sign-in", **read.supplier.to_json()},
        {"stepId": "read", "elements": {} if read.next_page is None else {"next": read.next_page.to_json()}},
        {"stepId": "compare", **read.store.to_json()},
        {"stepId": "check-save", "elements": {"save": read.save_button.to_json()}},
    ]


def read_steps_from(steps: list[Mapping[str, Any]]) -> ReadSteps:
    by_id = {step["stepId"]: step for step in steps}
    next_page = by_id["read"]["elements"].get("next")
    supplier = SignInSteps.from_json(by_id["sign-in"])
    store = SignInSteps.from_json(by_id["compare"])
    if (supplier.site, store.site) != SITE_KINDS:
        raise ValueError("The saved sign-in steps are for the wrong sites")
    return ReadSteps(
        supplier=supplier,
        next_page=None if next_page is None else Target.from_json(next_page),
        store=store,
        save_button=Target.from_json(by_id["check-save"]["elements"]["save"]),
    )


def procedure_id(client_id: str) -> str:
    return f"{client_id}-stock-sync"


@dataclass(frozen=True)
class Procedure:
    procedure_id: str
    version: int
    client_id: str
    saved_at: str
    expected_replay_ms: int
    read: ReadSteps
    field: FieldPattern
    status: Target
    script: str

    @property
    def step_count(self) -> int:
        return len(STEP_IDS)

    @classmethod
    def from_draft(cls, draft: ProcedureDraft, field: FieldPattern, *, client_id: str, version: int, saved_at: str, script: str) -> "Procedure":
        return cls(
            procedure_id=procedure_id(client_id),
            version=version,
            client_id=client_id,
            saved_at=saved_at,
            expected_replay_ms=draft.expected_replay_ms,
            read=draft.read,
            field=field,
            status=Target(STATUS_CSS, None, Hint("status", "", None)),
            script=script,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": RECORD_VERSION,
            "procedureId": self.procedure_id,
            "procedureVersion": self.version,
            "clientId": self.client_id,
            "savedAt": self.saved_at,
            "expectedReplayMs": self.expected_replay_ms,
            "steps": [
                *read_steps_json(self.read),
                {"stepId": "ask"},
                {"stepId": "save", "elements": {"field": self.field.to_json(), "status": self.status.to_json()}},
            ],
            "script": self.script,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Procedure":
        if not isinstance(data, Mapping) or data.get("version") != RECORD_VERSION:
            raise ValueError("A saved procedure must be version 1")
        steps = data["steps"]
        if [step["stepId"] for step in steps] != list(STEP_IDS):
            raise ValueError("A saved procedure must have the six stock sync steps")
        save = steps[-1]["elements"]
        version = whole(data["procedureVersion"])
        if version < 1 or not isinstance(data["script"], str) or not isinstance(data["savedAt"], str):
            raise ValueError("A saved procedure needs a version, a time and a script")
        return cls(
            procedure_id=str(data["procedureId"]),
            version=version,
            client_id=str(data["clientId"]),
            saved_at=data["savedAt"],
            expected_replay_ms=whole(data["expectedReplayMs"]),
            read=read_steps_from(steps[:4]),
            field=FieldPattern.from_json(save["field"]),
            status=Target.from_json(save["status"]),
            script=data["script"],
        )


def version_of(stored: Any) -> int:
    version = stored.get("procedureVersion") if isinstance(stored, Mapping) else None
    return version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else 0


def may_keep(outcome: Mapping[str, Any]) -> bool:
    checked = outcome.get("checked")
    return (
        outcome.get("status") == "done"
        and outcome.get("maySaveProcedure") is True
        and isinstance(checked, int)
        and checked > 0
        and outcome.get("matched") == checked
    )


async def saved_procedure(memory: Any, boundary: ClientBoundary) -> Procedure | None:
    try:
        stored = await memory.get(PROCEDURE_KEY)
    except MemoryAccessError:
        return None
    if stored is None:
        return None
    try:
        procedure = Procedure.from_json(stored)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if procedure.client_id != boundary.client.id or procedure.procedure_id != procedure_id(boundary.client.id):
        return None
    return procedure


async def note_replay_time(memory: Any, boundary: ClientBoundary, *, version: int, ready_in_ms: int) -> bool:
    try:
        stored = await memory.get(PROCEDURE_KEY)
    except MemoryAccessError:
        return False
    if stored is None:
        return False
    try:
        procedure = Procedure.from_json(stored)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    if procedure.client_id != boundary.client.id or procedure.version != version:
        return False
    try:
        await memory.set(PROCEDURE_KEY, {**procedure.to_json(), "expectedReplayMs": whole(ready_in_ms)})
    except MemoryAccessError:
        return False
    return True


def announce_saved(notes: EventNotes, procedure: Procedure) -> None:
    notes.emit(
        "procedure.saved",
        procedureId=procedure.procedure_id,
        version=procedure.version,
        stepCount=procedure.step_count,
        expectedReplayMs=procedure.expected_replay_ms,
    )


async def keep_if_verified(
    notes: EventNotes,
    boundary: ClientBoundary,
    memory: Any,
    draft: ProcedureDraft,
    outcome: Mapping[str, Any],
    fields: Mapping[str, Element],
    changes: Iterable[StockChange],
    *,
    wall=time.time,
) -> Procedure | None:
    if not may_keep(outcome):
        return None
    try:
        field = field_pattern(fields, changes)
        script = compile_read_script(draft.read, boundary)
    except NoStableSelector:
        return None
    try:
        stored = await memory.get(PROCEDURE_KEY)
        procedure = Procedure.from_draft(
            draft,
            field,
            client_id=boundary.client.id,
            version=version_of(stored) + 1,
            saved_at=datetime.fromtimestamp(wall(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            script=script,
        )
        await memory.set(PROCEDURE_KEY, procedure.to_json())
    except MemoryAccessError:
        return None
    announce_saved(notes, procedure)
    return procedure


async def keep_rebuilt(
    notes: EventNotes,
    boundary: ClientBoundary,
    memory: Any,
    procedure: Procedure,
    outcome: Mapping[str, Any],
    *,
    wall=time.time,
) -> Procedure | None:
    if not may_keep(outcome):
        return None
    try:
        if version_of(await memory.get(PROCEDURE_KEY)) != procedure.version:
            return None
        kept = replace(
            procedure,
            version=procedure.version + 1,
            saved_at=datetime.fromtimestamp(wall(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            script=compile_read_script(procedure.read, boundary),
        )
        await memory.set(PROCEDURE_KEY, kept.to_json())
    except MemoryAccessError:
        return None
    announce_saved(notes, kept)
    return kept
