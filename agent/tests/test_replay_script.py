import json
import shlex

import pytest

from clientops.computer_protocol import CommandResult
from clientops.page import Page, PageError, ScriptFailure, ScriptResult

PAGE = {"url": "http://127.0.0.1:3002/inventory", "host": "127.0.0.1:3002", "title": "Inventory", "html": "<html></html>", "bytes": 13, "truncated": False}
PASSED = {
    "ok": True,
    "command": "script",
    "outcome": "passed",
    "tookMs": 212,
    "steps": [
        {"stepId": "sign-in", "status": "done", "tookMs": 41, "checks": 6, "pages": []},
        {"stepId": "compare", "status": "done", "tookMs": 30, "checks": 8, "pages": [PAGE]},
    ],
    "tables": {"store": {"columns": ["SKU", "Product", "Available"], "rows": [["KC-ETH-200", "Ethiopia Guji 200g", "6"]], "pages": 1}},
    "result": {"said": {}},
}
CHECK_FAILED = {
    "ok": True,
    "command": "script",
    "outcome": "check-failed",
    "tookMs": 180,
    "steps": [
        {"stepId": "sign-in", "status": "done", "tookMs": 40, "checks": 6, "pages": []},
        {"stepId": "check-save", "status": "failed", "tookMs": 0, "checks": 0, "pages": []},
    ],
    "tables": {"supplier": {"columns": ["SKU", "Available"], "rows": [["KC-ETH-200", "2"]], "pages": 3}},
    "failure": {"stepId": "check-save", "kind": "check", "code": "wrong-name", "message": 'button says "Update inventory", expected "Save changes"'},
}
REFUSED = {
    "ok": False,
    "command": "script",
    "error": {"code": "placeholder-unset", "message": "LP_STORE_PASSWORD is not in the vault on this computer", "missing": ["LP_STORE_PASSWORD"]},
}


class OneAnswer:
    client_id = "kumo"

    def __init__(self, answer: dict, exit_code: int = 0) -> None:
        self.answer = answer
        self.exit_code = exit_code
        self.runs: list[tuple[str, int, object]] = []

    def run(self, command: str, *, timeout_seconds: int, env=None) -> CommandResult:
        self.runs.append((command, timeout_seconds, env))
        return CommandResult(exit_code=self.exit_code, output=json.dumps(self.answer) + "\n")

    def upload(self, content: bytes, remote_path: str) -> None:
        raise AssertionError("scripts are not uploaded")


def test_a_script_goes_to_the_runner_as_one_command_with_no_environment():
    computer = OneAnswer(PASSED)

    Page(computer, runner_command="bun runner.ts").run_script("const plan = {};\nreturn 1;")

    [(command, timeout, env)] = computer.runs
    assert shlex.split(command)[:2] == ["bun", "runner.ts"]
    assert json.loads(shlex.split(command)[-1]) == {"command": "script", "source": "const plan = {};\nreturn 1;"}
    assert timeout == 120
    assert env is None


def test_a_passing_script_gives_every_step_with_its_time_checks_and_pages():
    result = Page(OneAnswer(PASSED)).run_script("x")

    assert isinstance(result, ScriptResult)
    assert result.passed
    assert result.took_ms == 212
    assert [(step.step_id, step.status, step.took_ms, step.checks) for step in result.steps] == [("sign-in", "done", 41, 6), ("compare", "done", 30, 8)]
    assert result.step("compare").pages[0].host == "127.0.0.1:3002"
    assert result.failure is None
    store = result.table("store")
    assert store.columns == ("SKU", "Product", "Available")
    assert store.rows == (("KC-ETH-200", "Ethiopia Guji 200g", "6"),)
    assert result.to_json()["steps"][1] == {"stepId": "compare", "status": "done", "tookMs": 30, "checks": 8, "pages": 1}


def test_a_failed_check_names_the_step_so_only_that_step_needs_finding_again():
    result = Page(OneAnswer(CHECK_FAILED)).run_script("x")

    assert not result.passed
    assert result.outcome == "check-failed"
    assert result.failure == ScriptFailure("check-save", "check", "wrong-name", 'button says "Update inventory", expected "Save changes"')
    assert result.done_steps() == ("sign-in",)
    assert result.table("supplier").rows == (("KC-ETH-200", "2"),)
    assert result.table("supplier").pages == 3
    assert result.to_json()["failure"] == {"stepId": "check-save", "kind": "check", "code": "wrong-name", "message": 'button says "Update inventory", expected "Save changes"'}


def test_a_missing_placeholder_is_refused_by_the_runner_before_the_script_runs():
    with pytest.raises(PageError) as raised:
        Page(OneAnswer(REFUSED, exit_code=1)).run_script("x")

    assert raised.value.command == "script"
    assert raised.value.code == "placeholder-unset"
    assert raised.value.missing == ("LP_STORE_PASSWORD",)


def test_a_table_the_script_did_not_read_is_a_page_error():
    with pytest.raises(PageError) as raised:
        Page(OneAnswer(PASSED)).run_script("x").table("supplier")

    assert raised.value.code == "no-table"
