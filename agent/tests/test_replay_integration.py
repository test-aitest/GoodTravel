import asyncio
import importlib
import json
import re
import time
from pathlib import Path

import pytest

from clientops.boundary import data_directory
from clientops.procedure import PROCEDURE_KEY, Procedure
from clientops.settings import SETTINGS_FILE
from approval_fakes import FakeDesk, MemoryStore
from picking_claude import PickingClaude
from test_first_run_integration import HOW_TO_START, KUMO, REQUEST, STORE, SUPPLIER, control, local_computers, logins, missing, runner_sockets

__all__ = ["local_computers", "logins"]

pytestmark = pytest.mark.skipif(missing() is not None, reason=f"{missing()}: {HOW_TO_START}")

KUMO_DAY2 = json.loads((Path(__file__).parent / "fixtures" / "kumo-day2.json").read_text())


class Seams:
    def __init__(self, monkeypatch, *answers) -> None:
        self.node = importlib.import_module("clientops.node")
        self.transcript: list[tuple[float, str]] = []
        self.memory = MemoryStore()
        self.claude = PickingClaude()
        monkeypatch.setattr(self.node.app, "note", lambda message, tags=None: self.transcript.append((time.monotonic(), message)))
        monkeypatch.setattr(self.node, "claude_ai", lambda agent: self.claude)
        monkeypatch.setattr(self.node, "approval_desk", lambda agent: FakeDesk(*answers))
        monkeypatch.setattr(self.node, "client_memory", lambda boundary, context: self.memory)

    @property
    def events(self) -> list[dict]:
        return [json.loads(message)["event"] for _, message in self.transcript]

    def ready_in_ms(self) -> int:
        times = {json.loads(message)["event"]["type"]: at for at, message in reversed(self.transcript)}
        return round((times["approval.requested"] - times["job.created"]) * 1000)

    def run_job(self) -> dict:
        self.transcript.clear()
        return asyncio.run(self.node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))


def day(number: int) -> None:
    for site in (SUPPLIER, STORE):
        control(site, "/__control/day", {"day": number})


def test_learn_once_then_day_two_is_replayed_by_the_saved_script_with_no_claude_and_four_changes(monkeypatch, logins):
    sockets_before = runner_sockets()
    seams = Seams(monkeypatch, "approved")

    learned = seams.run_job()

    assert learned["status"] == "done" and learned["matched"] == 12
    saved = seams.events[-1]
    assert saved["type"] == "procedure.saved" and saved["version"] == 1 and saved["stepCount"] == 6
    procedure = Procedure.from_json(seams.memory.values[PROCEDURE_KEY])
    first_ready_ms = seams.ready_in_ms()
    claude_calls = seams.claude.calls

    day(2)
    replayed = seams.run_job()

    events = seams.events
    assert events[0]["run"] == {"kind": "replay", "procedureVersion": procedure.version}
    decided = [(event["stepId"], event["rung"]) for event in events if event["type"] == "step.decided"]
    assert decided == [("sign-in", "script"), ("read", "script"), ("compare", "script"), ("check-save", "script"), ("ask", "human"), ("save", "script")]
    assert seams.claude.calls == claude_calls
    [approval] = [event for event in events if event["type"] == "approval.requested"]
    assert approval["changes"] == KUMO_DAY2["changes"]
    assert [event for event in events if event["type"] == "write.done"] == [{"type": "write.done", "stepId": "save", "written": 4}]
    assert [event for event in events if event["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "save", "checked": 4, "matched": 4}]
    assert replayed["status"] == "done"
    assert replayed["claudeCalls"] == 0
    assert replayed["saveStatus"] == "Saved. 4 products updated."
    assert "procedure.saved" not in {event["type"] for event in events}
    everything = "\n".join(message for _, message in seams.transcript)
    for value in logins.values():
        assert value not in everything
    assert not re.search(r"\d", seams.claude.everything_sent())
    assert runner_sockets() - sockets_before == set()
    print(f"\nready for approval: first run {first_ready_ms} ms (Claude faked), replay {seams.ready_in_ms()} ms (readyInMs {replayed['readyInMs']} ms)")


def test_with_claude_off_a_renamed_save_button_is_asked_of_you_and_nothing_changes_when_you_pick_none(monkeypatch, logins):
    seams = Seams(monkeypatch, "approved")
    seams.run_job()
    day(2)
    control(STORE, "/__control/switches", {"renameButton": True})
    data_directory().mkdir(parents=True, exist_ok=True)
    (data_directory() / SETTINGS_FILE).write_text(json.dumps({"version": 1, "clients": {"kumo": {"useClaude": False}}}))
    monkeypatch.setattr(seams.node, "approval_desk", lambda agent: FakeDesk("rejected"))
    calls_before = seams.claude.calls

    result = seams.run_job()

    [asked] = [event for event in seams.events if event["type"] == "choice.requested"]
    assert (asked["stepId"], asked["before"], [candidate["name"] for candidate in asked["candidates"]]) == ("check-save", "Save changes", ["Update inventory"])
    assert seams.claude.calls == calls_before
    assert seams.events[-1] == {"type": "job.failed", "reason": "error", "stepId": "check-save", "message": "You picked none of the buttons, so I stopped. Nothing was changed."}
    assert result["problem"] == "none-chosen"
    assert "approval.requested" not in {event["type"] for event in seams.events}
    assert Procedure.from_json(seams.memory.values[PROCEDURE_KEY]).version == 1