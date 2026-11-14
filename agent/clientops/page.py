import json
import math
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from .ai_instructions import instruction_passages
from .computer_protocol import Computer

RUNNER_COMMAND: Final = "bun /opt/clientops/runner/main.ts"
RUNNER_TIMEOUT_SECONDS: Final = 120
WAIT_MARGIN_SECONDS: Final = 30

NAME_MASK: Final = "[name]"
NUMBER_MASK: Final = "[number]"
EMAIL_MASK: Final = "[email]"
OTHER_ROLE: Final = "other"
NAME_LIMIT: Final = 100

PLACEHOLDER_NAME = re.compile(r"LP_[A-Za-z0-9_]+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NUMBER = re.compile(r"[-+]?[$¥€£]?\d+(?:[.,:]\d+)*")
KNOWN_ROLES: Final = frozenset(
    {
        "button", "link", "textbox", "searchbox", "spinbutton", "checkbox", "radio", "combobox", "listbox",
        "option", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "slider", "tab", "treeitem",
        "gridcell", "cell", "row", "columnheader", "rowheader", "scrollbar", "img", "heading", "menu",
        "menubar", "tablist", "tree", "grid", "dialog", "form", "navigation", "region", "search", "generic",
    }
)

TreeMode = Literal["interactive", "full"]


class PageError(RuntimeError):
    def __init__(self, command: str, code: str, message: str, *, count: int | None = None, missing: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.command = command
        self.code = code
        self.count = count
        self.missing = tuple(missing)


class AnyForm:
    _instance: "AnyForm | None" = None

    def __new__(cls) -> "AnyForm":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "ANY_FORM"


ANY_FORM: Final = AnyForm()


@dataclass(frozen=True, slots=True)
class Locator:
    role: str
    name: str
    form: str | None | AnyForm = ANY_FORM

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {"role": self.role, "name": self.name}
        if not isinstance(self.form, AnyForm):
            found["form"] = self.form
        return found

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Locator":
        if set(data) - {"role", "name", "form"} or not isinstance(data.get("role"), str) or not isinstance(data.get("name"), str):
            raise ValueError(f"{data!r} is not a locator")
        form = data["form"] if "form" in data else ANY_FORM
        if not (form is None or isinstance(form, (str, AnyForm))):
            raise ValueError(f"{data!r} is not a locator")
        return cls(data["role"], data["name"], form)


Target = int | Locator


@dataclass(frozen=True, slots=True)
class PageInfo:
    url: str
    title: str
    status: int | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "PageInfo":
        return cls(url=data["url"], title=data["title"], status=data.get("status"))


@dataclass(frozen=True, slots=True)
class Element:
    ref: int
    role: str
    name: str
    form: str | None
    value: str | None = None
    tag: str | None = None
    field_name: str | None = None
    input_type: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Element":
        return cls(
            ref=data["ref"],
            role=data["role"],
            name=data["name"],
            form=data["form"],
            value=data.get("value"),
            tag=data.get("tag"),
            field_name=data.get("fieldName"),
            input_type=data.get("inputType"),
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    url: str
    host: str
    title: str
    html: str
    bytes: int
    truncated: bool

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> "Snapshot | None":
        if data is None:
            return None
        return cls(
            url=data["url"],
            host=data["host"],
            title=data["title"],
            html=data["html"],
            bytes=data["bytes"],
            truncated=data["truncated"],
        )


@dataclass(frozen=True, slots=True)
class Step:
    page: PageInfo
    element: Element | None = None
    snapshot: Snapshot | None = None

    @classmethod
    def from_result(cls, answer: Mapping[str, Any]) -> "Step":
        element = answer.get("element")
        return cls(
            page=PageInfo.from_json(answer["page"]),
            element=None if element is None else Element.from_json(element),
            snapshot=Snapshot.from_json(answer.get("snapshot")),
        )


@dataclass(frozen=True, slots=True)
class Tree:
    page: PageInfo
    mode: TreeMode
    text: str
    elements: tuple[Element, ...]
    cells: tuple[str, ...]
    snapshot: Snapshot | None = None

    @classmethod
    def from_result(cls, answer: Mapping[str, Any]) -> "Tree":
        return cls(
            page=PageInfo.from_json(answer["page"]),
            mode=answer["mode"],
            text=answer["text"],
            elements=tuple(Element.from_json(element) for element in answer["elements"]),
            cells=tuple(answer["cells"]),
            snapshot=Snapshot.from_json(answer.get("snapshot")),
        )


@dataclass(frozen=True, slots=True)
class Found:
    page: PageInfo
    element: Element
    selector: str


@dataclass(frozen=True, slots=True)
class TableRead:
    page: PageInfo
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    pages: int


@dataclass(frozen=True, slots=True)
class ScriptStep:
    step_id: str
    status: str
    took_ms: int
    checks: int
    pages: tuple[Snapshot, ...]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ScriptStep":
        return cls(
            step_id=data["stepId"],
            status=data["status"],
            took_ms=data["tookMs"],
            checks=data["checks"],
            pages=tuple(Snapshot.from_json(page) for page in data["pages"]),
        )

    def to_json(self) -> dict[str, Any]:
        return {"stepId": self.step_id, "status": self.status, "tookMs": self.took_ms, "checks": self.checks, "pages": len(self.pages)}


@dataclass(frozen=True, slots=True)
class ScriptFailure:
    step_id: str | None
    kind: str
    code: str | None
    message: str

    def to_json(self) -> dict[str, Any]:
        return {"stepId": self.step_id, "kind": self.kind, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ScriptResult:
    outcome: str
    took_ms: int
    steps: tuple[ScriptStep, ...]
    failure: ScriptFailure | None
    tables: Mapping[str, Any]
    result: Mapping[str, Any] | None

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    @classmethod
    def from_answer(cls, answer: Mapping[str, Any]) -> "ScriptResult":
        failure = answer.get("failure")
        return cls(
            outcome=answer["outcome"],
            took_ms=answer["tookMs"],
            steps=tuple(ScriptStep.from_json(step) for step in answer["steps"]),
            failure=None if failure is None else ScriptFailure(failure["stepId"], failure["kind"], failure["code"], failure["message"]),
            tables=answer.get("tables") or {},
            result=answer.get("result"),
        )

    def step(self, step_id: str) -> ScriptStep | None:
        return next((step for step in self.steps if step.step_id == step_id), None)

    def done_steps(self) -> tuple[str, ...]:
        return tuple(step.step_id for step in self.steps if step.status == "done")

    def table(self, name: str) -> TableRead:
        table = self.tables.get(name)
        if not isinstance(table, Mapping):
            raise PageError("script", "no-table", f"The script did not read the {name} table")
        return TableRead(
            page=PageInfo(url="", title=""),
            columns=tuple(table["columns"]),
            rows=tuple(tuple(row) for row in table["rows"]),
            pages=table["pages"],
        )

    def said(self, name: str) -> str | None:
        text = ((self.result or {}).get("said") or {}).get(name)
        return text if isinstance(text, str) else None

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {"outcome": self.outcome, "tookMs": self.took_ms, "steps": [step.to_json() for step in self.steps]}
        if self.failure is not None:
            found["failure"] = self.failure.to_json()
        return found


def placeholder(name: str) -> str:
    if not PLACEHOLDER_NAME.fullmatch(name):
        raise ValueError(f"{name!r} is not a vault placeholder name (LP_ followed by letters, digits or _)")
    return f"${name}"


def _target_json(target: Target) -> dict[str, Any]:
    return {"ref": target} if isinstance(target, int) else target.to_json()


def _last_answer(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            answer = json.loads(text)
        except ValueError:
            continue
        if isinstance(answer, dict) and "ok" in answer:
            return answer
    return None


class Page:
    def __init__(self, computer: Computer, *, runner_command: str = RUNNER_COMMAND, timeout_seconds: int = RUNNER_TIMEOUT_SECONDS) -> None:
        self._computer = computer
        self._runner = runner_command
        self._timeout = timeout_seconds

    def open(self, url: str, *, snapshot: bool = False) -> Step:
        return Step.from_result(self._call({"command": "open", "url": url, **self._snapshot(snapshot)}))

    def tree(self, mode: TreeMode = "interactive", *, snapshot: bool = False) -> Tree:
        return Tree.from_result(self._call({"command": "tree", "mode": mode, **self._snapshot(snapshot)}))

    def find(self, locator: Locator) -> Found:
        answer = self._call({"command": "find", **locator.to_json()})
        return Found(page=PageInfo.from_json(answer["page"]), element=Element.from_json(answer["element"]), selector=answer["selector"])

    def click(self, target: Target, *, wait_for: str | None = None, timeout_ms: int | None = None, snapshot: bool = False) -> Step:
        command: dict[str, Any] = {"command": "click", "target": _target_json(target)}
        if wait_for is not None:
            command["waitFor"] = wait_for
        if timeout_ms is not None:
            command["timeoutMs"] = timeout_ms
        return Step.from_result(self._call({**command, **self._snapshot(snapshot)}, self._timeout_for(timeout_ms)))

    def fill(self, target: Target, text: str, *, snapshot: bool = False) -> Step:
        return Step.from_result(self._call({"command": "fill", "target": _target_json(target), "text": text, **self._snapshot(snapshot)}))

    def wait(self, selector: str, *, timeout_ms: int | None = None) -> str:
        command: dict[str, Any] = {"command": "wait", "selector": selector}
        if timeout_ms is not None:
            command["timeoutMs"] = timeout_ms
        return self._call(command, self._timeout_for(timeout_ms))["text"]

    def read_table(
        self,
        *,
        next: Locator | None = None,
        wait_for: str | None = None,
        max_pages: int | None = None,
        table: int | None = None,
    ) -> TableRead:
        command: dict[str, Any] = {"command": "readTable"}
        if next is not None:
            command["next"] = next.to_json()
        for key, value in (("waitFor", wait_for), ("maxPages", max_pages), ("table", table)):
            if value is not None:
                command[key] = value
        answer = self._call(command)
        return TableRead(
            page=PageInfo.from_json(answer["page"]),
            columns=tuple(answer["columns"]),
            rows=tuple(tuple(row) for row in answer["rows"]),
            pages=answer["pages"],
        )

    def snapshot(self, *, max_bytes: int | None = None) -> Snapshot:
        command: dict[str, Any] = {"command": "snapshot"}
        if max_bytes is not None:
            command["maxBytes"] = max_bytes
        return Snapshot.from_json(self._call(command)["snapshot"])

    def close(self) -> None:
        self._call({"command": "close"})

    def run_script(self, source: str) -> ScriptResult:
        return ScriptResult.from_answer(self._call({"command": "script", "source": source}))

    @staticmethod
    def _snapshot(wanted: bool) -> dict[str, bool]:
        return {"snapshot": True} if wanted else {}

    def _timeout_for(self, timeout_ms: int | None) -> int:
        if timeout_ms is None:
            return self._timeout
        return max(self._timeout, math.ceil(timeout_ms / 1000) + WAIT_MARGIN_SECONDS)

    def _call(self, command: dict[str, Any], timeout_seconds: int | None = None) -> dict[str, Any]:
        line = f"{self._runner} {shlex.quote(json.dumps(command, ensure_ascii=False, separators=(',', ':')))}"
        result = self._computer.run(line, timeout_seconds=timeout_seconds or self._timeout)
        answer = _last_answer(result.output)
        if answer is None:
            tail = result.output.strip()[-300:]
            raise PageError(command["command"], "no-answer", f"The runner printed no result (exit code {result.exit_code}): {tail}")
        if not answer["ok"]:
            error = answer.get("error", {})
            raise PageError(
                command["command"],
                error.get("code", "unknown"),
                error.get("message", "The runner failed without a message"),
                count=error.get("count"),
                missing=error.get("missing", ()),
            )
        return answer


def _masker(terms: Iterable[str]):
    ordered = sorted({term.strip() for term in terms if len(term.strip()) >= 2 and not term.strip().isdigit()}, key=len, reverse=True)
    known = re.compile("|".join(re.escape(term) for term in ordered), re.IGNORECASE) if ordered else None

    def mask(text: str) -> str:
        masked = EMAIL.sub(EMAIL_MASK, text)
        if known is not None:
            masked = known.sub(NAME_MASK, masked)
        masked = NUMBER.sub(NUMBER_MASK, masked)
        return masked if len(masked) <= NAME_LIMIT else masked[: NAME_LIMIT - 1] + "…"

    return mask


def redact_elements(elements: Iterable[Element], known_values: Iterable[str] = ()) -> list[dict[str, Any]]:
    listed = list(elements)
    mask = _masker([*known_values, *(element.value for element in listed if element.value)])
    instructions = instruction_passages([element.name for element in listed])
    return [
        {
            "ref": element.ref,
            "role": element.role if element.role in KNOWN_ROLES else OTHER_ROLE,
            "name": NAME_MASK if index in instructions else mask(element.name),
            "form": None if element.form is None else mask(element.form),
        }
        for index, element in enumerate(listed)
    ]


def ai_view(tree: Tree, known_values: Iterable[str] = ()) -> list[dict[str, Any]]:
    return redact_elements(tree.elements, [*known_values, *tree.cells])
