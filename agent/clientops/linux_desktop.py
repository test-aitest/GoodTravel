from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

from daytona import DaytonaA11yUnavailableError, DaytonaError, DaytonaNotFoundError
from daytona_toolbox_api_client import ComputerUseAccessibilityNode

from .computers import DaytonaComputer, possessive
from .elements import (
    CANNOT,
    FAILED,
    NOT_FOUND,
    TOO_BIG,
    UNAVAILABLE,
    ElementError,
    Located,
    NodeView,
    flatten,
    located_node,
    refuse_placeholder,
)
from .procedure import Hint
from .roles import web_role_of_atspi

NodePair = tuple[Located, ComputerUseAccessibilityNode]


def node_view(node: ComputerUseAccessibilityNode) -> NodeView:
    return NodeView(web_role_of_atspi(node.role), node.name or "", node.id or "")


def node_children(node: ComputerUseAccessibilityNode) -> list[ComputerUseAccessibilityNode]:
    return list(node.children or [])


class LinuxDesktop:
    surface: Final = "linux"

    def __init__(self, accessibility: Any, *, client_id: str, pid: int | None = None) -> None:
        self._accessibility = accessibility
        self._client_id = client_id
        self._pid = pid

    def elements(self, action: str = "find") -> list[NodePair]:
        scope = "all" if self._pid is None else "pid"
        with self._daytona_errors(action):
            tree = self._accessibility.get_tree(scope=scope, pid=self._pid, max_depth=None)
        if tree.truncated:
            raise ElementError(action, TOO_BIG, f"Daytona cut the desktop tree on {self._where} short, so its elements cannot be told apart")
        if tree.root is None:
            return []
        return flatten(tree.root, node_view, node_children, action=action)

    def find(self, hint: Hint) -> Located:
        return self._one(hint, "find")[0]

    def press(self, hint: Hint) -> Located:
        located, node = self._one(hint, "press")
        if node.actions is not None and not node.actions:
            raise ElementError("press", CANNOT, f'The {hint.role} "{hint.name}" on {self._where} has nothing to press')
        with self._daytona_errors("press"):
            self._accessibility.invoke_node(located.handle)
        return located

    def set_value(self, hint: Hint, value: str) -> Located:
        refuse_placeholder(value, "set-value")
        located, _ = self._one(hint, "set-value")
        with self._daytona_errors("set-value"):
            self._accessibility.set_node_value(located.handle, value)
        return located

    def read_value(self, hint: Hint) -> str | None:
        self._one(hint, "read-value")
        raise ElementError(
            "read-value",
            CANNOT,
            f'Found the {hint.role} "{hint.name}" on {self._where}, but Daytona\'s accessibility API has no call that reads a value.',
        )

    @property
    def _where(self) -> str:
        return f"{possessive(self._client_id)} computer"

    def _one(self, hint: Hint, action: str) -> NodePair:
        return located_node(self.elements(action), hint, action)

    @contextmanager
    def _daytona_errors(self, action: str) -> Iterator[None]:
        try:
            yield
        except DaytonaA11yUnavailableError as error:
            raise ElementError(
                action,
                UNAVAILABLE,
                f"The desktop on {self._where} is not readable yet. Start computer use first. (Daytona: {error})",
            ) from error
        except DaytonaNotFoundError as error:
            raise ElementError(action, NOT_FOUND, f"The element went away on {self._where}. (Daytona: {error})", count=0) from error
        except DaytonaError as error:
            raise ElementError(action, FAILED, f"Could not {action} on {self._where}. (Daytona: {error})") from error


def open_linux_desktop(computer: DaytonaComputer, *, pid: int | None = None) -> LinuxDesktop:
    computer_use = computer.sandbox.computer_use
    desktop = LinuxDesktop(computer_use.accessibility, client_id=computer.client_id, pid=pid)
    with desktop._daytona_errors("open"):
        computer_use.start()
    return desktop
