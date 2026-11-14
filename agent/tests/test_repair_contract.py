import asyncio
import json
import os

import pytest

from clientops.approval import see_approval_through
from clientops.boundary import resolve_job
from clientops.procedure import PROCEDURE_KEY, ProcedureDraft
from clientops.records import EventNotes
from clientops.replay import rebuilt_writer, run_replay
from clientops.repair import RepairHelp
from approval_fakes import FakeDesk, MemoryStore, ticking_wall
from picking_claude import PickingClaude
from test_replay import KUMO_DAY2, kumo_procedure
from test_replay_contract import ASKED_AT, CONTRACTS, RECORD_VARIABLE, SCHEDULED_REQUEST, pending_after
from test_stock_sync import data_dir, install_sites, quarter_second_clock

__all__ = ["data_dir"]

RENAMED = "Update inventory"
DECOY = "Save changes to all locations"
KINDS = {"repair": ((), ()), "choice": ((DECOY,), (("approved", "1016"),))}


def contract_of(kind: str):
    return CONTRACTS / f"{kind}_job_notes.json"


def produce(kind: str, monkeypatch) -> dict:
    sent: list[dict] = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)))
    memory = MemoryStore()
    job_id = f"exec_{kind}"
    extra_buttons, picks = KINDS[kind]

    async def repaired():
        install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED, extra_store_buttons=extra_buttons)
        procedure = kumo_procedure()
        memory.values[PROCEDURE_KEY] = procedure.to_json()
        help_ = RepairHelp(PickingClaude(), FakeDesk(*picks), job_id)
        result = await run_replay(notes, resolve_job("kumo"), procedure, request=SCHEDULED_REQUEST, job_id=job_id, clock=quarter_second_clock(), repair=help_)
        draft = ProcedureDraft.from_json(result["procedure"])
        pending = pending_after(result, job_id, notes, draft=draft, replay_version=procedure.version)
        writer = rebuilt_writer(procedure, draft.read, clock=quarter_second_clock(), wall=ticking_wall(ASKED_AT + 24))
        await see_approval_through(FakeDesk("approved"), notes, resolve_job("kumo"), pending, memory, writer=writer, wall=ticking_wall(ASKED_AT + 24))

    asyncio.run(repaired())
    return {"input": {"client_id": "kumo"}, "jobId": job_id, "notes": sent}


@pytest.mark.parametrize("kind", ["repair", "choice"])
def test_the_notes_of_a_repaired_replay_are_exactly_the_contract_the_screen_reads(monkeypatch, kind):
    produced = produce(kind, monkeypatch)
    if os.environ.get(RECORD_VARIABLE) == "1":
        contract_of(kind).write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(contract_of(kind).read_text())


def events_of(kind: str) -> list[dict]:
    return [note["event"] for note in json.loads(contract_of(kind).read_text())["notes"]]


def test_the_repair_contract_is_the_replay_with_the_save_button_decided_by_claude_and_version_2_saved():
    replayed = events_of("replay")
    repaired = events_of("repair")

    expected = [(event["type"], event.get("stepId")) for event in replayed]
    at = expected.index(("step.decided", "check-save")) + 1
    assert [(event["type"], event.get("stepId")) for event in repaired] == [*expected[:at], ("page.seen", "check-save"), *expected[at:], ("procedure.saved", None)]
    assert [(event["stepId"], event["rung"], event.get("escalatedFrom")) for event in repaired if event["type"] == "step.decided"][3] == ("check-save", "claude", ["script", "tree"])
    [approval] = [event for event in repaired if event["type"] == "approval.requested"]
    assert approval["question"] == "Save 4 stock changes with the new button?"
    assert approval["procedureChange"] == {"stepId": "check-save", "fromVersion": 1, "toVersion": 2, "before": "Save changes", "after": RENAMED}
    assert repaired[-1]["type"] == "procedure.saved" and repaired[-1]["version"] == 2


def test_the_choice_contract_asks_the_person_before_the_step_is_decided_by_them():
    chosen = events_of("choice")

    types = [event["type"] for event in chosen]
    asked = types.index("choice.requested")
    assert types[asked : asked + 3] == ["choice.requested", "choice.answered", "step.decided"]
    assert chosen[asked + 2] == {"type": "step.decided", "stepId": "check-save", "rung": "human", "tookMs": chosen[asked + 2]["tookMs"], "escalatedFrom": ["script", "tree", "claude"]}
    assert [candidate["name"] for candidate in chosen[asked]["candidates"]] == [RENAMED, DECOY]
    assert chosen[asked]["before"] == "Save changes"
    assert chosen[asked + 1]["ref"] == chosen[asked]["candidates"][0]["ref"]
    assert chosen[-1]["type"] == "procedure.saved" and chosen[-1]["version"] == 2
