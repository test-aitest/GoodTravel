import asyncio
import json
from pathlib import Path

import pytest

from clientops.boundary import resolve_job
from clientops.compile_script import compile_read_script
from clientops.diff import StockChange
from clientops.procedure import (
    PROCEDURE_KEY,
    Procedure,
    ProcedureDraft,
    keep_if_verified,
    may_keep,
    saved_procedure,
)
from clientops.records import EventNotes
from approval_fakes import MemoryStore, ticking_wall
from procedure_fixtures import fields_for, kumo_draft
from test_stock_sync import VAULT, data_dir

__all__ = ["data_dir"]

FIXTURES = Path(__file__).parent / "fixtures"
CHANGES = [StockChange(**change) for change in json.loads((FIXTURES / "kumo-day1.json").read_text())["changes"]]
FULL_MATCH = {"status": "done", "written": 12, "checked": 12, "matched": 12, "maySaveProcedure": True, "saveStatus": "Saved. 12 products updated."}
NOON = 1_794_657_600.0


class Transcript:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.notes = EventNotes(lambda message, tags: self.sent.append(json.loads(message)), start=30)

    @property
    def events(self) -> list[dict]:
        return [note["event"] for note in self.sent]


def keep(memory, outcome=FULL_MATCH, *, client_id="kumo", draft=None, transcript=None):
    transcript = transcript or Transcript()
    kept = asyncio.run(
        keep_if_verified(
            transcript.notes,
            resolve_job(client_id),
            memory,
            draft or kumo_draft(),
            outcome,
            fields_for(CHANGES),
            CHANGES,
            wall=ticking_wall(NOON),
        )
    )
    return kept, transcript


@pytest.mark.parametrize(
    ("outcome", "kept"),
    [
        (FULL_MATCH, True),
        ({**FULL_MATCH, "matched": 11}, False),
        ({**FULL_MATCH, "maySaveProcedure": False}, False),
        ({"status": "failed", "reason": "verify-mismatch", "written": 12, "checked": 12, "matched": 9, "maySaveProcedure": False}, False),
        ({"status": "stopped", "decision": "rejected"}, False),
        ({"status": "failed", "reason": "error", "stepId": "save", "problem": "store-changed"}, False),
        ({"status": "done", "written": 0, "checked": 0, "matched": 0, "maySaveProcedure": True}, False),
    ],
)
def test_only_a_write_where_every_row_read_back_right_may_be_kept(outcome, kept):
    assert may_keep(outcome) is kept


def test_a_full_match_saves_version_1_for_this_client_and_says_so():
    memory = MemoryStore()

    procedure, transcript = keep(memory)

    assert procedure is not None
    assert transcript.events == [
        {"type": "procedure.saved", "procedureId": "kumo-stock-sync", "version": 1, "stepCount": 6, "expectedReplayMs": 4200}
    ]
    assert transcript.sent[0]["n"] == 31
    assert list(memory.values) == [PROCEDURE_KEY]
    stored = memory.values[PROCEDURE_KEY]
    assert stored["version"] == 1
    assert stored["procedureId"] == "kumo-stock-sync"
    assert stored["procedureVersion"] == 1
    assert stored["clientId"] == "kumo"
    assert stored["savedAt"] == "2026-11-14T12:00:00Z"
    assert stored["expectedReplayMs"] == 4200
    assert stored["script"] == compile_read_script(kumo_draft().read, resolve_job("kumo"))
    assert Procedure.from_json(json.loads(json.dumps(stored))) == procedure


def test_the_saved_steps_carry_a_selector_and_an_element_hint_for_every_element():
    memory = MemoryStore()

    keep(memory)

    steps = memory.values[PROCEDURE_KEY]["steps"]
    assert [step["stepId"] for step in steps] == ["sign-in", "read", "compare", "check-save", "ask", "save"]
    sign_in = steps[0]
    assert sign_in["site"] == "supplier"
    assert sign_in["landing"] == "/stock"
    assert sign_in["elements"]["user"] == {
        "css": 'form[action="/login"] input[name="username"]',
        "hint": {"role": "textbox", "name": "Account ID", "form": "/login"},
    }
    assert sign_in["elements"]["submit"] == {
        "css": 'form[action="/login"] button',
        "text": "Sign in",
        "hint": {"role": "button", "name": "Sign in", "form": "/login"},
    }
    assert steps[1]["elements"]["next"]["hint"] == {"role": "link", "name": "Next", "form": None}
    assert steps[2]["site"] == "store"
    assert steps[3]["elements"]["save"]["hint"] == {"role": "button", "name": "Save changes", "form": "/inventory"}
    assert steps[4] == {"stepId": "ask"}
    assert steps[5]["elements"]["field"] == {
        "scope": 'form[action="/inventory"] ',
        "tag": "input",
        "namePrefix": "available-",
        "nameSuffix": "",
        "hint": {"role": "spinbutton", "name": "Available for {name}", "form": "/inventory"},
    }
    assert steps[5]["elements"]["status"] == {"css": '[role="status"]', "hint": {"role": "status", "name": "", "form": None}}


def test_saving_again_increments_the_version():
    memory = MemoryStore()
    keep(memory)

    second, transcript = keep(memory, draft=kumo_draft(expected_replay_ms=3900))

    assert second.version == 2
    assert transcript.events[0]["version"] == 2
    assert transcript.events[0]["expectedReplayMs"] == 3900
    assert memory.values[PROCEDURE_KEY]["procedureVersion"] == 2


@pytest.mark.parametrize(
    "outcome",
    [
        {**FULL_MATCH, "status": "failed", "reason": "verify-mismatch", "matched": 9, "maySaveProcedure": False},
        {"status": "stopped", "decision": "rejected"},
    ],
)
def test_nothing_is_saved_or_said_when_the_write_did_not_fully_match(outcome):
    memory = MemoryStore()

    procedure, transcript = keep(memory, outcome)

    assert procedure is None
    assert transcript.events == []
    assert memory.calls == []


def test_a_mismatch_after_a_saved_version_leaves_that_version_alone():
    memory = MemoryStore()
    keep(memory)
    before = json.dumps(memory.values[PROCEDURE_KEY])

    keep(memory, {**FULL_MATCH, "matched": 10, "maySaveProcedure": False})

    assert json.dumps(memory.values[PROCEDURE_KEY]) == before


def test_no_login_value_is_stored_with_the_steps():
    memory = MemoryStore()

    keep(memory)

    stored = json.dumps(memory.values[PROCEDURE_KEY])
    for value in VAULT.values():
        assert value not in stored


def test_when_memory_cannot_take_the_steps_the_job_is_not_told_they_were_saved():
    memory = MemoryStore(failing=("set",))

    procedure, transcript = keep(memory)

    assert procedure is None
    assert transcript.events == []


def test_a_field_without_the_sku_in_its_name_means_the_steps_are_not_saved():
    from clientops.page import Element

    memory = MemoryStore()
    transcript = Transcript()
    fields = {change.sku: Element(1, "spinbutton", "Quantity", "/inventory", "1", "input", "quantity", "number") for change in CHANGES}

    kept = asyncio.run(keep_if_verified(transcript.notes, resolve_job("kumo"), memory, kumo_draft(), FULL_MATCH, fields, CHANGES, wall=ticking_wall(NOON)))

    assert kept is None
    assert transcript.events == []
    assert PROCEDURE_KEY not in memory.values


def test_the_saved_steps_are_read_back_for_the_same_client_only():
    memory = MemoryStore()
    procedure, _ = keep(memory)

    assert asyncio.run(saved_procedure(memory, resolve_job("kumo"))) == procedure
    assert asyncio.run(saved_procedure(memory, resolve_job("hinata"))) is None
    assert asyncio.run(saved_procedure(MemoryStore(), resolve_job("kumo"))) is None


@pytest.mark.parametrize("stored", ["not a record", {"version": 2}, {"version": 1, "procedureId": "kumo-stock-sync"}])
def test_a_record_that_cannot_be_read_counts_as_no_saved_steps(stored):
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = stored

    assert asyncio.run(saved_procedure(memory, resolve_job("kumo"))) is None


def test_when_memory_cannot_be_read_there_are_no_saved_steps_to_use():
    assert asyncio.run(saved_procedure(MemoryStore(failing=("get",)), resolve_job("kumo"))) is None


def test_the_draft_from_the_first_run_round_trips_through_the_waiting_record():
    draft = kumo_draft()

    assert ProcedureDraft.from_json(json.loads(json.dumps(draft.to_json()))) == draft


def test_a_successful_replay_time_replaces_the_estimate_and_keeps_the_version():
    from clientops.procedure import note_replay_time

    memory = MemoryStore()
    procedure, _ = keep(memory)
    before = dict(memory.values[PROCEDURE_KEY])

    noted = asyncio.run(note_replay_time(memory, resolve_job("kumo"), version=1, ready_in_ms=117))

    assert noted is True
    after = memory.values[PROCEDURE_KEY]
    assert after["expectedReplayMs"] == 117
    assert after["procedureVersion"] == 1
    assert {key: value for key, value in after.items() if key != "expectedReplayMs"} == {key: value for key, value in before.items() if key != "expectedReplayMs"}


@pytest.mark.parametrize("version", [2, 0])
def test_a_replay_time_for_another_version_of_the_steps_is_not_kept(version):
    from clientops.procedure import note_replay_time

    memory = MemoryStore()
    keep(memory)

    assert asyncio.run(note_replay_time(memory, resolve_job("kumo"), version=version, ready_in_ms=117)) is False
    assert memory.values[PROCEDURE_KEY]["expectedReplayMs"] == 4200


def test_a_replay_time_with_nothing_saved_or_memory_down_changes_nothing():
    from clientops.procedure import note_replay_time

    empty = MemoryStore()
    assert asyncio.run(note_replay_time(empty, resolve_job("kumo"), version=1, ready_in_ms=117)) is False
    assert empty.values == {}
    down = MemoryStore()
    keep(down)
    down.failing.add("set")
    assert asyncio.run(note_replay_time(down, resolve_job("kumo"), version=1, ready_in_ms=117)) is False
