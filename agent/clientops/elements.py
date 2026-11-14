import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, Protocol, TypeVar, runtime_checkable

from .procedure import Hint, spaced
from .roles import CONTAINER_ROLES

NOT_FOUND: Final = "not-found"
NOT_UNIQUE: Final = "not-unique"
CANNOT: Final = "cannot"
UNAVAILABLE: Final = "unavailable"
TOO_BIG: Final = "too-big"
FAILED: Final = "failed"
ELEMENT_LIMIT: Final = 5000

VAULT_PLACEHOLDER = re.compile(r"\$LP_[A-Za-z0-9_]+")

Node = TypeVar("Node")


class ElementError(RuntimeError):
    def __init__(self, action: str, code: str, message: str, *, count: int | None = None) -> None:
        super().__init__(message)
        self.action = action
        self.code = code
        self.count = count


@dataclass(frozen=True, slots=True)
class Located:
    handle: str
    role: str
    name: str
    form: str | None


@dataclass(frozen=True, slots=True)
class NodeView:
    role: str | None
    name: str
    handle: str


@runtime_checkable
class ElementOps(Protocol):
    surface: str

    def find(self, hint: Hint) -> Located: ...

    def press(self, hint: Hint) -> Located: ...

    def set_value(self, hint: Hint, value: str) -> Located: ...

    def read_value(self, hint: Hint) -> str | None: ...


def where(hint: Hint) -> str:
    return "outside any form or window" if hint.form is None else f"in {hint.form}"


def describe(hint: Hint, count: int) -> str:
    role = f"{hint.role}s" if count > 1 else hint.role
    return f'{count or "No"} {role} named "{hint.name}" {where(hint)}'


def matches(located: Located, hint: Hint) -> bool:
    return located.role == hint.role and spaced(located.name) == spaced(hint.name) and located.form == hint.form


def locate(candidates: Iterable[Located], hint: Hint, action: str) -> Located:
    found = [located for located in candidates if matches(located, hint)]
    if len(found) == 1:
        return found[0]
    raise ElementError(action, NOT_UNIQUE if found else NOT_FOUND, describe(hint, len(found)), count=len(found))


def located_node(pairs: Iterable[tuple[Located, Node]], hint: Hint, action: str) -> tuple[Located, Node]:
    listed = list(pairs)
    chosen = locate((located for located, _ in listed), hint, action)
    return next(pair for pair in listed if pair[0] is chosen)


def hint_of(located: Located) -> Hint:
    return Hint(located.role, located.name, located.form)


def flatten(
    root: Node,
    view: Callable[[Node], NodeView],
    children: Callable[[Node], Iterable[Node]],
    *,
    action: str,
    limit: int = ELEMENT_LIMIT,
) -> list[tuple[Located, Node]]:
    found: list[tuple[Located, Node]] = []
    pending: list[tuple[Node, str | None]] = [(root, None)]
    visited = 0
    while pending:
        node, form = pending.pop()
        visited += 1
        if visited > limit:
            raise ElementError(action, TOO_BIG, f"The app shows more than {limit} elements, too many to read one by one")
        seen = view(node)
        name = spaced(seen.name)
        if seen.role is not None:
            found.append((Located(seen.handle, seen.role, name, form), node))
        inner = name if seen.role in CONTAINER_ROLES and name else form
        pending.extend((child, inner) for child in reversed(list(children(node))))
    return found


def refuse_placeholder(value: str, action: str) -> None:
    if VAULT_PLACEHOLDER.search(value):
        raise ElementError(
            action,
            CANNOT,
            "Vault placeholders are filled only by the web runner inside the client's computer; "
            "a desktop app cannot take one yet.",
        )
