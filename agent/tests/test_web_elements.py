import json
import shlex
from pathlib import Path

import pytest

from clientops.computer_protocol import CommandResult
from clientops.elements import NOT_FOUND, ElementError, ElementOps, Located
from clientops.page import Page, PageError
from clientops.procedure import Hint
from clientops.web_elements import WebElements
from runner_replay import recorded_output

RUNNER_RECORDINGS = Path(__file__).parent / "contract" / "runner"
SAVE = Hint("button", "Save changes", "/inventory")
GUJI_200 = Hint("spinbutton", "Available for Ethiopia Guji 200g", "/inventory")
NEXT = Hint("link", "Next", None)


def recorded_tree_element(recording: str, index: int, ref: int) -> dict:
    data = json.loads((RUNNER_RECORDINGS / f"{recording}.json").read_text())
    answer = json.loads(data["exchanges"][index]["output"].strip().splitlines()[-1])
    return next(element for element in answer["elements"] if element["ref"] == ref)


def find_answer_with_value() -> dict:
    found = recorded_output(14)
    element = recorded_tree_element("kumo-undo", 5, 59)
    return {**found, "element": element, "selector": f'input[name="{element["fieldName"]}"]'}


class AnsweringComputer:
    client_id = "kumo"

    def __init__(self, answers: dict[str, dict]) -> None:
        self.answers = answers
        self.sent: list[dict] = []

    def run(self, command, *, timeout_seconds, env=None):
        sent = json.loads(shlex.split(command)[-1])
        self.sent.append(sent)
        return CommandResult(exit_code=0, output=json.dumps(self.answers[sent["command"]]) + "\n")

    def upload(self, content, remote_path):
        raise AssertionError("not used")


def web(answers: dict[str, dict]) -> tuple[WebElements, AnsweringComputer]:
    computer = AnsweringComputer(answers)
    return WebElements(Page(computer)), computer


def test_the_web_surface_has_the_common_shape():
    elements, _ = web({})
    assert isinstance(elements, ElementOps)
    assert elements.surface == "web"


def test_find_asks_the_runner_for_exactly_one_element_by_the_saved_hint():
    elements, computer = web({"find": recorded_output(14)})
    assert elements.find(SAVE) == Located("1016", "button", "Save changes", "/inventory")
    assert computer.sent == [{"command": "find", "role": "button", "name": "Save changes", "form": "/inventory"}]


def test_a_hint_without_a_form_asks_for_an_element_outside_any_form():
    elements, computer = web({"find": recorded_output(14)})
    elements.find(NEXT)
    assert computer.sent == [{"command": "find", "role": "link", "name": "Next", "form": None}]


def test_the_runners_not_found_keeps_its_code_and_count():
    elements, _ = web({"find": recorded_output(15)})
    with pytest.raises(ElementError) as caught:
        elements.find(Hint("button", "Update inventory", "/inventory"))
    error = caught.value
    assert (error.action, error.code, error.count) == ("find", NOT_FOUND, 0)
    assert str(error) == 'No button named "Update inventory" in form /inventory on this page'
    assert isinstance(error.__cause__, PageError)


def test_press_is_one_click_on_the_hint_without_waiting():
    elements, computer = web({"click": recorded_output(17)})
    assert elements.press(SAVE) == Located("1016", "button", "Save changes", "/inventory")
    assert computer.sent == [{"command": "click", "target": {"role": "button", "name": "Save changes", "form": "/inventory"}}]


def test_set_value_is_one_fill_and_vault_placeholders_go_to_the_runner_as_names():
    elements, computer = web({"fill": recorded_output(16)})
    assert elements.set_value(GUJI_200, "6") == Located("726", "spinbutton", "Available for Ethiopia Guji 200g", "/inventory")
    elements.set_value(Hint("textbox", "Username", "/login"), "$LP_STORE_USERNAME")
    assert computer.sent == [
        {"command": "fill", "target": GUJI_200.to_json(), "text": "6"},
        {"command": "fill", "target": {"role": "textbox", "name": "Username", "form": "/login"}, "text": "$LP_STORE_USERNAME"},
    ]


def test_read_value_is_the_value_of_the_one_element_the_runner_finds():
    elements, computer = web({"find": find_answer_with_value()})
    assert elements.read_value(GUJI_200) == "6"
    assert computer.sent == [{"command": "find", **GUJI_200.to_json()}]


def test_an_element_with_no_value_reads_as_none():
    elements, _ = web({"find": recorded_output(14)})
    assert elements.read_value(SAVE) is None


def test_other_runner_errors_keep_the_runners_code():
    elements, _ = web({"fill": recorded_output(8)})
    with pytest.raises(ElementError) as caught:
        elements.set_value(Hint("textbox", "Username", "/login"), "$LP_NOT_SET")
    assert (caught.value.action, caught.value.code) == ("set-value", "placeholder-unset")
    assert "LP_NOT_SET" in str(caught.value)
