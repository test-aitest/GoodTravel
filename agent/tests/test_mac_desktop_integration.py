import subprocess
import sys
import time
from pathlib import Path

import pytest

from clientops.elements import ElementError
from clientops.mac_desktop import PERMISSION_PATH, MacDesktop, accessibility_allowed
from clientops.procedure import Hint

WINDOW_SCRIPT = Path(__file__).parent / "mac_test_window.py"
TITLE = "ClientOps test window"
QUANTITY = Hint("textbox", "Quantity", TITLE)
ADD_ONE = Hint("button", "Add one", TITLE)
APPEAR_SECONDS = 15


def missing() -> str | None:
    if sys.platform != "darwin":
        return "the macOS Accessibility check runs only on macOS"
    if not accessibility_allowed():
        return (
            "this process has no macOS Accessibility access, so the real AX check is skipped. "
            f"To run it, open {PERMISSION_PATH} and turn on the app you run the tests from "
            "(for example Terminal, iTerm or Ghostty), then run the tests again. "
            "The tests never change this setting and never ask for it."
        )
    return None


pytestmark = pytest.mark.skipif(missing() is not None, reason=str(missing()))


def found_in_time(desktop: MacDesktop, hint: Hint) -> None:
    deadline = time.monotonic() + APPEAR_SECONDS
    while True:
        try:
            desktop.find(hint)
            return
        except ElementError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def test_the_same_hints_set_press_and_read_in_a_window_this_test_opens_and_closes_itself():
    window = subprocess.Popen([sys.executable, str(WINDOW_SCRIPT)], stdout=subprocess.PIPE, text=True)
    try:
        pid = int(window.stdout.readline())
        assert pid == window.pid
        desktop = MacDesktop(pid)
        found_in_time(desktop, ADD_ONE)

        desktop.set_value(QUANTITY, "4")
        assert desktop.read_value(QUANTITY) == "4"
        desktop.press(ADD_ONE)
        assert desktop.read_value(QUANTITY) == "5"
    finally:
        window.terminate()
        window.wait(timeout=10)
