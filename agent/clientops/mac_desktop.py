import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final, Protocol

from .elements import (
    CANNOT,
    ELEMENT_LIMIT,
    FAILED,
    NOT_FOUND,
    UNAVAILABLE,
    ElementError,
    Located,
    NodeView,
    flatten,
    located_node,
    refuse_placeholder,
)
from .procedure import Hint
from .roles import web_role_of_ax

if sys.platform == "darwin":
    import ApplicationServices

AX_SUCCESS: Final = 0
AX_INVALID_ELEMENT: Final = -25202
AX_CANNOT_COMPLETE: Final = -25204
AX_ATTRIBUTE_UNSUPPORTED: Final = -25205
AX_ACTION_UNSUPPORTED: Final = -25206
AX_API_DISABLED: Final = -25211
AX_NO_VALUE: Final = -25212

PERMISSION_PATH: Final = "System Settings > Privacy & Security > Accessibility"
PRESS_ACTION: Final = "AXPress"
VALUE_ATTRIBUTE: Final = "AXValue"
ABSENT: Final = frozenset({AX_NO_VALUE, AX_ATTRIBUTE_UNSUPPORTED})
COMMON_CODES: Final = {
    AX_API_DISABLED: UNAVAILABLE,
    AX_CANNOT_COMPLETE: UNAVAILABLE,
    AX_INVALID_ELEMENT: NOT_FOUND,
    AX_ACTION_UNSUPPORTED: CANNOT,
    AX_ATTRIBUTE_UNSUPPORTED: CANNOT,
}

AXNode = tuple[Any, str]
NodePair = tuple[Located, AXNode]


class AXFailure(RuntimeError):
    def __init__(self, code: int) -> None:
        super().__init__(f"macOS Accessibility error {code}")
        self.code = code


class AXApi(Protocol):
    def trusted(self) -> bool: ...

    def application(self, pid: int) -> Any: ...

    def attribute(self, element: Any, name: str) -> Any: ...

    def actions(self, element: Any) -> tuple[str, ...]: ...

    def settable(self, element: Any, name: str) -> bool: ...

    def set_attribute(self, element: Any, name: str, value: Any) -> None: ...

    def perform(self, element: Any, action: str) -> None: ...


def accessibility_allowed() -> bool:
    if sys.platform != "darwin":
        return False
    options = {ApplicationServices.kAXTrustedCheckOptionPrompt: False}
    return bool(ApplicationServices.AXIsProcessTrustedWithOptions(options))


def checked(error: int) -> None:
    if error != AX_SUCCESS:
        raise AXFailure(error)


def value_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class SystemAX:
    def trusted(self) -> bool:
        return accessibility_allowed()

    def application(self, pid: int) -> Any:
        return ApplicationServices.AXUIElementCreateApplication(pid)

    def attribute(self, element: Any, name: str) -> Any:
        error, value = ApplicationServices.AXUIElementCopyAttributeValue(element, name, None)
        if error in ABSENT:
            return None
        checked(error)
        return value

    def actions(self, element: Any) -> tuple[str, ...]:
        error, names = ApplicationServices.AXUIElementCopyActionNames(element, None)
        if error in ABSENT:
            return ()
        checked(error)
        return tuple(str(name) for name in names or ())

    def settable(self, element: Any, name: str) -> bool:
        error, settable = ApplicationServices.AXUIElementIsAttributeSettable(element, name, None)
        if error in ABSENT:
            return False
        checked(error)
        return bool(settable)

    def set_attribute(self, element: Any, name: str, value: Any) -> None:
        checked(ApplicationServices.AXUIElementSetAttributeValue(element, name, value))

    def perform(self, element: Any, action: str) -> None:
        checked(ApplicationServices.AXUIElementPerformAction(element, action))


class MacDesktop:
    surface: Final = "macos"

    def __init__(self, pid: int, ax: AXApi | None = None, *, limit: int = ELEMENT_LIMIT) -> None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError(f"pid must name one running app by its process ID above 0, not {pid!r}")
        self._pid = pid
        self._ax = ax if ax is not None else SystemAX()
        self._limit = limit

    def elements(self, action: str = "find") -> list[NodePair]:
        if not self._ax.trusted():
            raise ElementError(action, UNAVAILABLE, self._permission_message)
        with self._ax_errors(action):
            root = (self._ax.application(self._pid), f"ax:{self._pid}")
            return flatten(root, self._view, self._children, action=action, limit=self._limit)

    def find(self, hint: Hint) -> Located:
        return self._one(hint, "find")[0]

    def press(self, hint: Hint) -> Located:
        located, (element, _) = self._one(hint, "press")
        with self._ax_errors("press"):
            actions = self._ax.actions(element)
        if PRESS_ACTION not in actions:
            raise ElementError("press", CANNOT, f'The {hint.role} "{hint.name}" in {self._app} has nothing to press')
        with self._ax_errors("press"):
            self._ax.perform(element, PRESS_ACTION)
        return located

    def set_value(self, hint: Hint, value: str) -> Located:
        refuse_placeholder(value, "set-value")
        located, (element, _) = self._one(hint, "set-value")
        with self._ax_errors("set-value"):
            settable = self._ax.settable(element, VALUE_ATTRIBUTE)
        if not settable:
            raise ElementError("set-value", CANNOT, f'The {hint.role} "{hint.name}" in {self._app} does not take a typed value')
        with self._ax_errors("set-value"):
            self._ax.set_attribute(element, VALUE_ATTRIBUTE, value)
        return located

    def read_value(self, hint: Hint) -> str | None:
        _, (element, _) = self._one(hint, "read-value")
        with self._ax_errors("read-value"):
            return value_text(self._ax.attribute(element, VALUE_ATTRIBUTE))

    @property
    def _app(self) -> str:
        return f"the app with process ID {self._pid}"

    @property
    def _permission_message(self) -> str:
        return (
            f"ClientOps cannot read {self._app}: this process has no macOS Accessibility access. "
            f"To allow it, turn on the app that runs ClientOps in {PERMISSION_PATH}. "
            "ClientOps never changes this setting."
        )

    def _one(self, hint: Hint, action: str) -> NodePair:
        return located_node(self.elements(action), hint, action)

    def _text(self, element: Any, name: str) -> str:
        value = self._ax.attribute(element, name)
        return value if isinstance(value, str) else ""

    def _view(self, node: AXNode) -> NodeView:
        element, handle = node
        role = web_role_of_ax(self._text(element, "AXRole"), self._text(element, "AXSubrole"))
        if role is None:
            return NodeView(None, "", handle)
        return NodeView(role, self._text(element, "AXTitle") or self._text(element, "AXDescription"), handle)

    def _children(self, node: AXNode) -> list[AXNode]:
        element, handle = node
        return [(child, f"{handle}/{index}") for index, child in enumerate(self._ax.attribute(element, "AXChildren") or ())]

    def _explained(self, action: str, failure: AXFailure) -> str:
        if failure.code == AX_API_DISABLED:
            return self._permission_message
        if failure.code == AX_CANNOT_COMPLETE:
            return f"ClientOps could not {action} in {self._app}: it did not answer. It may have quit or be busy. ({failure})"
        if failure.code == AX_INVALID_ELEMENT:
            return f"The element went away in {self._app} before ClientOps could {action} it. ({failure})"
        if COMMON_CODES.get(failure.code) == CANNOT:
            return f"That element in {self._app} does not support {action}. ({failure})"
        return f"Could not {action} in {self._app}. ({failure})"

    @contextmanager
    def _ax_errors(self, action: str) -> Iterator[None]:
        try:
            yield
        except AXFailure as failure:
            code = COMMON_CODES.get(failure.code, FAILED)
            raise ElementError(
                action, code, self._explained(action, failure), count=0 if code == NOT_FOUND else None
            ) from failure
