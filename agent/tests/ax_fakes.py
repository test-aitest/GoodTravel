from dataclasses import dataclass, field
from typing import Any

from clientops.mac_desktop import AX_CANNOT_COMPLETE, AXFailure


@dataclass(eq=False)
class FakeElement:
    attributes: dict[str, Any]
    children: list["FakeElement"] = field(default_factory=list)
    actions: tuple[str, ...] = ()
    settable: frozenset[str] = frozenset()
    failure: int | None = None


def window(title: str, *children: FakeElement, subrole: str = "AXStandardWindow") -> FakeElement:
    return FakeElement({"AXRole": "AXWindow", "AXSubrole": subrole, "AXTitle": title}, list(children))


def sheet(*children: FakeElement) -> FakeElement:
    return FakeElement({"AXRole": "AXSheet"}, list(children))


def group(*children: FakeElement) -> FakeElement:
    return FakeElement({"AXRole": "AXGroup"}, list(children))


def button(title: str) -> FakeElement:
    return FakeElement({"AXRole": "AXButton", "AXTitle": title}, actions=("AXPress", "AXShowMenu"))


def text_field(label: str, value: Any = "", *, subrole: str | None = None) -> FakeElement:
    attributes = {"AXRole": "AXTextField", "AXTitle": "", "AXDescription": label, "AXValue": value}
    if subrole is not None:
        attributes["AXSubrole"] = subrole
    return FakeElement(attributes, actions=("AXConfirm",), settable=frozenset({"AXValue"}))


def static_text(value: str) -> FakeElement:
    return FakeElement({"AXRole": "AXStaticText", "AXValue": value})


def application(*children: FakeElement) -> FakeElement:
    return FakeElement({"AXRole": "AXApplication", "AXTitle": "Order Entry"}, list(children))


class FakeAX:
    def __init__(self, apps: dict[int, FakeElement], *, trusted: bool = True) -> None:
        self.apps = apps
        self.is_trusted = trusted
        self.reads = 0
        self.performed: list[tuple[FakeElement, str]] = []
        self.written: list[tuple[FakeElement, str, Any]] = []
        self.failures: dict[str, int] = {}

    def trusted(self) -> bool:
        return self.is_trusted

    def application(self, pid: int) -> FakeElement:
        return self.apps.get(pid) or FakeElement({}, failure=AX_CANNOT_COMPLETE)

    def attribute(self, element: FakeElement, name: str) -> Any:
        self.reads += 1
        self._fail_if_asked(element, "attribute")
        if name == "AXChildren":
            return list(element.children)
        return element.attributes.get(name)

    def actions(self, element: FakeElement) -> tuple[str, ...]:
        self._fail_if_asked(element, "actions")
        return element.actions

    def settable(self, element: FakeElement, name: str) -> bool:
        self._fail_if_asked(element, "settable")
        return name in element.settable

    def set_attribute(self, element: FakeElement, name: str, value: Any) -> None:
        self._fail_if_asked(element, "set_attribute")
        self.written.append((element, name, value))
        element.attributes[name] = value

    def perform(self, element: FakeElement, action: str) -> None:
        self._fail_if_asked(element, "perform")
        self.performed.append((element, action))

    def _fail_if_asked(self, element: FakeElement, call: str) -> None:
        if element.failure is not None:
            raise AXFailure(element.failure)
        if call in self.failures:
            raise AXFailure(self.failures[call])
