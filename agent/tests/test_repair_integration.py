import json
import re
from pathlib import Path

import pytest

from clientops.procedure import PROCEDURE_KEY, Procedure
from approval_fakes import FakeDesk
from test_first_run_integration import HOW_TO_START, STORE, control, local_computers, logins, missing, runner_sockets
from test_replay_integration import Seams, day

__all__ = ["local_computers", "logins"]

pytestmark = pytest.mark.skipif(missing() is not None, reason=f"{missing()}: {HOW_TO_START}")

KUMO_DAY3 = json.loads((Path(__file__).parent / "fixtures" / "kumo-day3.json").read_text())
SAVE_CSS = 'form[action="/inventory"] button[name="action"]'
RENAMED = "Update inventory"
DECOY = "Save changes to all locations"


class PickingByName(FakeDesk):
    def __init__(self, seams: Seams, name: str, *answers) -> None:
        super().__init__(*answers)
        self.seams = seams
        self.name = name

    async def _next(self, approval_request_id: str, execution_id: str):
        if approval_request_id.startswith("cho-"):
            [asked] = [event for event in self.seams.events if event["type"] == "choice.requested" and event["choiceId"] == approval_request_id]
            self.answers.appendleft(("approved", next(candidate["ref"] for candidate in asked["candidates"] if candidate["name"] == self.name)))
        return await super()._next(approval_request_id, execution_id)


def decided(seams: Seams) -> list[tuple[str, str, list[str] | None]]:
    return [(event["stepId"], event["rung"], event.get("escalatedFrom")) for event in seams.events if event["type"] == "step.decided"]


def learned_then_day_3(monkeypatch, *switches: str) -> Seams:
    seams = Seams(monkeypatch, "approved")
    learned = seams.run_job()
    assert learned["status"] == "done" and Procedure.from_json(seams.memory.values[PROCEDURE_KEY]).version == 1
    day(3)
    control(STORE, "/__control/switches", {switch: True for switch in switches})
    return seams


def test_a_renamed_save_button_is_fixed_by_claude_shown_as_a_change_and_kept_as_version_2_after_seven_rows_match(monkeypatch, logins):
    sockets_before = runner_sockets()
    seams = learned_then_day_3(monkeypatch, "renameButton")
    calls_before = seams.claude.calls

    repaired = seams.run_job()

    events = seams.events
    assert decided(seams) == [
        ("sign-in", "script", None),
        ("read", "script", None),
        ("compare", "script", None),
        ("check-save", "claude", ["script", "tree"]),
        ("ask", "human", None),
        ("save", "script", None),
    ]
    [approval] = [event for event in events if event["type"] == "approval.requested"]
    assert approval["question"] == "Save 7 stock changes with the new button?"
    assert approval["changes"] == KUMO_DAY3["changes"]
    assert approval["procedureChange"] == {"stepId": "check-save", "fromVersion": 1, "toVersion": 2, "before": "Save changes", "after": RENAMED}
    assert [event for event in events if event["type"] == "write.done"] == [{"type": "write.done", "stepId": "save", "written": 7}]
    assert [event for event in events if event["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "save", "checked": 7, "matched": 7}]
    assert events[-1] == {"type": "procedure.saved", "procedureId": "kumo-stock-sync", "version": 2, "stepCount": 6, "expectedReplayMs": events[-1]["expectedReplayMs"]}
    assert repaired["status"] == "done" and repaired["claudeCalls"] == 1
    assert seams.claude.calls - calls_before == 1
    version_2 = Procedure.from_json(seams.memory.values[PROCEDURE_KEY])
    assert (version_2.version, version_2.read.save_button.css, version_2.read.save_button.text) == (2, SAVE_CSS, RENAMED)
    repair_prompt = seams.claude.fake.received[-1].user
    assert "Sign out" not in repair_prompt
    assert not re.search(r"\d", repair_prompt)
    for value in logins.values():
        assert value not in "\n".join(message for _, message in seams.transcript)

    again = seams.run_job()

    assert again["status"] == "done" and again["changes"] == 0
    assert seams.events[0]["run"] == {"kind": "replay", "procedureVersion": 2}
    assert {rung for _, rung, _ in decided(seams)} == {"script"}
    assert runner_sockets() - sockets_before == set()


def test_two_save_like_buttons_ask_the_person_whose_pick_carries_on_to_the_approval_and_version_2(monkeypatch, logins):
    seams = learned_then_day_3(monkeypatch, "renameButton", "decoyButton")
    desk = PickingByName(seams, RENAMED, "approved")
    monkeypatch.setattr(seams.node, "approval_desk", lambda agent: desk)

    result = seams.run_job()

    events = seams.events
    [asked] = [event for event in events if event["type"] == "choice.requested"]
    assert asked["stepId"] == "check-save"
    assert asked["question"] == "Which button saves the changes now?"
    assert [(candidate["role"], candidate["name"]) for candidate in asked["candidates"]] == [("button", RENAMED), ("button", DECOY)]
    [answered] = [event for event in events if event["type"] == "choice.answered"]
    assert answered["ref"] == asked["candidates"][0]["ref"]
    assert decided(seams)[3] == ("check-save", "human", ["script", "tree", "claude"])
    [approval] = [event for event in events if event["type"] == "approval.requested"]
    assert approval["question"] == "Save 7 stock changes with the new button?"
    assert approval["procedureChange"]["after"] == RENAMED
    assert [paused["approvalId"][:4] for paused in desk.paused] == ["cho-", "apr-"]
    assert result["status"] == "done" and result["matched"] == 7
    assert Procedure.from_json(seams.memory.values[PROCEDURE_KEY]).read.save_button.text == RENAMED


def test_none_of_these_stops_before_anything_is_saved(monkeypatch, logins):
    seams = learned_then_day_3(monkeypatch, "renameButton", "decoyButton")
    desk = FakeDesk(("rejected", ""))
    monkeypatch.setattr(seams.node, "approval_desk", lambda agent: desk)

    result = seams.run_job()

    assert seams.events[-1] == {"type": "job.failed", "reason": "error", "stepId": "check-save", "message": "You picked none of the buttons, so I stopped. Nothing was changed."}
    assert result["problem"] == "none-chosen"
    assert "approval.requested" not in {event["type"] for event in seams.events}
    assert Procedure.from_json(seams.memory.values[PROCEDURE_KEY]).version == 1
