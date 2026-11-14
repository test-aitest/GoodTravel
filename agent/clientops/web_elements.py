from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Final

from .elements import ElementError, Located
from .page import Element, Locator, Page, PageError
from .procedure import Hint


def locator_of(hint: Hint) -> Locator:
    return Locator(hint.role, hint.name, hint.form)


def located_of(element: Element) -> Located:
    return Located(str(element.ref), element.role, element.name, element.form)


@contextmanager
def runner_errors(action: str) -> Iterator[None]:
    try:
        yield
    except PageError as error:
        raise ElementError(action, error.code, str(error), count=error.count) from error


class WebElements:
    surface: Final = "web"

    def __init__(self, page: Page) -> None:
        self._page = page

    def find(self, hint: Hint) -> Located:
        return located_of(self._found(hint, "find"))

    def press(self, hint: Hint) -> Located:
        return self._acted("press", lambda: self._page.click(locator_of(hint)).element)

    def set_value(self, hint: Hint, value: str) -> Located:
        return self._acted("set-value", lambda: self._page.fill(locator_of(hint), value).element)

    def read_value(self, hint: Hint) -> str | None:
        return self._found(hint, "read-value").value

    def _found(self, hint: Hint, action: str) -> Element:
        with runner_errors(action):
            return self._page.find(locator_of(hint)).element

    @staticmethod
    def _acted(action: str, act: Callable[[], Element | None]) -> Located:
        with runner_errors(action):
            element = act()
        if element is None:
            raise ElementError(action, "no-answer", f"The runner did not say which element it used to {action}")
        return located_of(element)
