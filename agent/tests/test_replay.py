import asyncio
import json
from itertools import count
from pathlib import Path

import pytest

import clientops.replay as replay
from clientops.approval import UNDO_KEY, PendingWrite, see_approval_through
from clientops.boundary import resolve_job
from clientops.compile_script import compile_read_script
from clientops.diff import StockChange
from clientops.procedure import PROCEDURE_KEY, Procedure, field_pattern
from clientops.records import EventNotes
from clientops.replay import run_replay, script_writer
from clientops.stock_sync import PreparedWrite
from approval_fakes import FakeDesk, MemoryStore, ticking_wall
from procedure_fixtures import fields_for, kumo_draft
from test_stock_sync import KUMO, VAULT, data_dir, install_sites

__all__ = ["data_dir"]

FIXTURES = Path(__file__).parent / "fixtures"
KUMO_DAY2 = json.loads((FIXTURES / "kumo-day2.json").read_text())
DAY1_CHANGES = [StockChange(**change) for change in KUMO["changes"]]
DAY2_CHANGES = KUMO_DAY2["changes"]
REQUEST = "Daily at 18:00: sync supplier stock to the store admin."
JOB_ID = "exec_replay"
ASKED_AT = 1_794_646_848.0

READ_EVENTS = [
    ("job.created", None),
    ("step.started", "sign-in"),
    ("step.decided", "sign-in"),
    ("page.seen", "sign-in"),
    ("step.done", "sign-in"),
    ("step.started", "read"),
    ("step.decided", "read"),
    ("page.seen", "read"),
    ("step.done", "read"),
    ("step.started", "compare"),
    ("step.decided", "compare"),
    ("page.seen", "compare"),
    ("step.done", "compare"),
    ("step.started", "check-save"),
    ("step.decided", "check-save"),
    ("step.done", "check-save"),
    ("step.started", "ask"),
    ("approval.requested", "ask"),
]
AFTER_APPROVAL = [
    ("approval.answered", None),
    ("step.decided", "ask"),
    ("step.done", "ask"),
    ("step.started", "save"),
    ("step.decided", "save"),
    ("page.seen", "save"),
    ("page.seen", "save"),
    ("write.done", "save"),
    ("verify.done", "save"),
    ("step.done", "save"),
]


def kumo_procedure(version: int = 1) -> Procedure:
    draft = kumo_draft()
    return Procedure.from_draft(
        draft,
        field_pattern(fields_for(DAY1_CHANGES), DAY1_CHANGES),
        client_id="kumo",
        version=version,
        saved_at="2026-11-14T08:41:13Z",
        script=compile_read_script(draft.read, resolve_job("kumo")),
    )


def ticking_clock():
    ticks = count()
    return lambda: next(ticks) * 0.25


class Transcript:
    def __init__(self, start: int = 0) -> None:
        self.sent: list[dict] = []
        self.notes = EventNotes(lambda message, tags: self.sent.append(json.loads(message)), start=start)

    @property
    def events(self) -> list[dict]:
        return [note["event"] for note in self.sent]

    def shape(self) -> list[tuple[str, str | None]]:
        return [(event["type"], event.get("stepId")) for event in self.events]


def read(procedure=None, transcript=None) -> tuple[dict, Transcript]:
    transcript = transcript or Transcript()
    result = asyncio.run(
        run_replay(transcript.notes, resolve_job("kumo"), procedure or kumo_procedure(), request=REQUEST, job_id=JOB_ID, clock=ticking_clock())
    )
    return result, transcript


def test_the_replay_reaches_the_approval_with_day_twos_four_changes_without_any_ai(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    result, transcript = read()

    assert transcript.shape() == READ_EVENTS
    events = transcript.events
    assert events[0] == {
        "type": "job.created",
        "clientName": "Kumo Coffee Roasters",
        "title": "Supplier stock sync",
        "request": REQUEST,
        "run": {"kind": "replay", "procedureVersion": 1},
        "plan": [
            {"stepId": "sign-in", "title": "Sign in to Maruyama Supply"},
            {"stepId": "read", "title": "Read today's stock"},
            {"stepId": "compare", "title": "Compare with the store admin"},
            {"stepId": "check-save", "title": "Check the save button"},
            {"stepId": "ask", "title": "Ask you before saving"},
            {"stepId": "save", "title": "Save and check every row"},
        ],
    }
    decided = [event for event in events if event["type"] == "step.decided"]
    assert [(event["stepId"], event["rung"]) for event in decided] == [("sign-in", "script"), ("read", "script"), ("compare", "script"), ("check-save", "script")]
    assert all("escalatedFrom" not in event for event in decided)
    done = {event["stepId"]: event for event in events if event["type"] == "step.done"}
    assert done["read"]["detail"] == "42 products"
    assert done["compare"]["detail"] == "4 products differ"
    assert done["check-save"]["summary"] == "Save button found"
    assert {event["host"] for event in events if event["type"] == "page.seen"} == {"portal.maruyama-supply.example", "admin.kumo-coffee.example"}
    approval = events[-1]
    assert approval["approvalId"] == f"apr-{JOB_ID}"
    assert approval["question"] == "Save 4 stock changes to the store admin?"
    assert approval["changes"] == DAY2_CHANGES
    assert approval["undoable"] is True
    assert result["status"] == "waiting"
    assert result["changes"] == 4
    assert result["claudeCalls"] == 0
    assert result["claudeUsage"] == {}
    assert result["readyInMs"] == 250
    assert result["replay"] == {"procedureVersion": 1}
    assert PreparedWrite.from_json(result["write"]).changes == tuple(StockChange(**change) for change in DAY2_CHANGES)
    assert sites.saved is False


def test_every_step_runs_inside_one_script_with_no_round_trip_per_action(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    read()

    assert [command["command"] for command in sites.commands] == ["script"]
    assert [step["stepId"] for step in sites.scripts[0]["steps"]] == ["sign-in", "read", "compare", "check-save"]
    assert sites.typed == ["$LP_SUPPLIER_USERNAME", "$LP_SUPPLIER_PASSWORD", "$LP_STORE_USERNAME", "$LP_STORE_PASSWORD"]


def test_the_script_steps_report_their_own_times(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)

    _, transcript = read()

    done = [event for event in transcript.events if event["type"] == "step.done" and event["stepId"] != "ask"]
    assert [event["tookMs"] for event in done] == [10, 10, 10, 10]


def test_when_nothing_changed_at_the_supplier_there_is_nothing_to_approve(monkeypatch):
    day = {**KUMO_DAY2, "store": KUMO_DAY2["supplier"], "changes": []}
    install_sites(monkeypatch, day=day)

    result, transcript = read()

    assert transcript.shape()[-3:] == [("step.done", "check-save"), ("step.done", "ask"), ("step.done", "save")]
    assert [event["summary"] for event in transcript.events[-2:]] == ["Nothing to save", "Nothing to save"]
    assert result["status"] == "done"
    assert result["changes"] == 0
    assert result["claudeCalls"] == 0


def test_a_renamed_save_button_stops_the_replay_at_that_step_and_hands_it_to_the_refind_hook(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label="Update inventory")
    handed: list = []

    async def recover(failed):
        handed.append(failed)
        return None

    monkeypatch.setattr(replay, "recover_failed_step", recover)

    result, transcript = read()

    assert transcript.shape() == READ_EVENTS[:14] + [("job.failed", "check-save")]
    done = {event["stepId"]: event for event in transcript.events if event["type"] == "step.done"}
    assert done["compare"]["detail"] == "4 products differ"
    assert transcript.events[-1] == {
        "type": "job.failed",
        "reason": "error",
        "stepId": "check-save",
        "message": "The store admin doesn't match the saved steps any more, so I stopped. Nothing was changed.",
    }
    [failed] = handed
    assert failed.step_id == "check-save"
    assert failed.code == "wrong-name"
    assert failed.done_steps == ("sign-in", "read", "compare")
    assert failed.script.table("store").rows
    assert result == {"status": "failed", "reason": "error", "stepId": "check-save", "problem": "wrong-name", "claudeCalls": 0, "claudeUsage": {}}
    assert sites.saved is False


def test_a_login_missing_from_the_vault_is_refused_before_the_script_runs(monkeypatch):
    vault = {name: value for name, value in VAULT.items() if name != "LP_SUPPLIER_PASSWORD"}
    sites = install_sites(monkeypatch, day=KUMO_DAY2, vault=vault)

    result, transcript = read()

    assert transcript.shape() == [("job.created", None), ("step.started", "sign-in"), ("job.failed", "sign-in")]
    assert transcript.events[-1]["message"] == "The Maruyama Supply login isn't saved on this client's computer, so I stopped. Nothing was changed."
    assert result["problem"] == "placeholder-unset"
    assert sites.scripts == []


def test_a_login_the_site_turns_down_stops_at_sign_in(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, accounts={"supplier": ("someone", "else"), "store": ("kumo-admin", "kumo-store-demo")})

    result, transcript = read()

    assert transcript.events[-1] == {
        "type": "job.failed",
        "reason": "error",
        "stepId": "sign-in",
        "message": "I couldn't sign in to Maruyama Supply with the saved login, so I stopped. Nothing was changed.",
    }
    assert result["problem"] == "not-signed-in"


def pending_from(result: dict, notes_written: int) -> PendingWrite:
    return PendingWrite(
        job_id=JOB_ID,
        run_id="run_replay",
        workflow_id="run_replay",
        client_id="kumo",
        approval_id=result["approvalId"],
        notes_written=notes_written,
        asked_at=ASKED_AT,
        write=PreparedWrite.from_json(result["write"]),
        replay_version=1,
    )


def approve_and_write(monkeypatch, desk, memory, *, procedure=None, before_answer=None):
    procedure = procedure or kumo_procedure()
    transcript = Transcript()
    result = asyncio.run(
        run_replay(transcript.notes, resolve_job("kumo"), procedure, request=REQUEST, job_id=JOB_ID, clock=ticking_clock())
    )
    if before_answer:
        before_answer()
    pending = pending_from(result, transcript.notes.count)
    after = Transcript(start=transcript.notes.count)
    outcome = asyncio.run(
        see_approval_through(
            desk,
            after.notes,
            resolve_job("kumo"),
            pending,
            memory,
            writer=script_writer(procedure, clock=ticking_clock(), wall=ticking_wall(ASKED_AT + 20)),
        )
    )
    return outcome, after


def test_after_the_ok_the_script_writes_our_four_numbers_and_every_row_is_read_back(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()

    outcome, after = approve_and_write(monkeypatch, FakeDesk("approved"), memory)

    assert after.shape() == AFTER_APPROVAL
    events = after.events
    assert events[3] == {"type": "step.started", "stepId": "save", "activity": "Saving 4 changes"}
    assert events[4] == {"type": "step.decided", "stepId": "save", "rung": "script", "tookMs": 0}
    assert events[7] == {"type": "write.done", "stepId": "save", "written": 4}
    assert events[8] == {"type": "verify.done", "stepId": "save", "checked": 4, "matched": 4}
    assert events[9]["summary"] == "Saved and checked every row"
    assert outcome["status"] == "done"
    assert outcome["saveStatus"] == "Saved. 4 products updated."
    assert {row["sku"]: row["available"] for row in sites.store_rows} == {row["sku"]: row["available"] for row in KUMO_DAY2["supplier"]}
    write = sites.scripts[-1]
    assert [step["stepId"] for step in write["steps"]] == ["save-prepare", "save-submit"]
    [fields] = [op for op in write["steps"][0]["ops"] if op["op"] == "fields"]
    assert [(item["sku"], item["expect"], item["value"]) for item in fields["items"]] == [
        (change["sku"], str(change["store"]), str(change["supplier"])) for change in DAY2_CHANGES
    ]
    assert sites.saves == ["Save changes"]


def test_the_undo_copy_is_kept_before_the_script_writes(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()

    approve_and_write(monkeypatch, FakeDesk("approved"), memory)

    record = memory.values[UNDO_KEY]
    assert record["jobId"] == JOB_ID
    assert record["rows"] == [{"sku": change["sku"], "name": change["name"], "before": change["store"], "after": change["supplier"]} for change in DAY2_CHANGES]
    assert record["saveButton"] == {"role": "button", "name": "Save changes", "form": "/inventory"}
    stored = json.dumps(record)
    for value in VAULT.values():
        assert value not in stored


def test_nothing_is_written_without_the_ok(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()

    outcome, after = approve_and_write(monkeypatch, FakeDesk("rejected"), memory)

    assert outcome == {"status": "stopped", "decision": "rejected"}
    assert after.shape() == [("job.failed", "ask")]
    assert len(sites.scripts) == 1
    assert sites.saved is False
    assert UNDO_KEY not in memory.values


def test_a_store_that_changed_after_the_ok_is_not_overwritten_and_the_last_undo_copy_stays(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()
    earlier = {"version": 1, "jobId": "exec_first", "rows": []}
    memory.values[UNDO_KEY] = earlier

    def someone_edits_the_store():
        sites.store_rows[0]["available"] = 99

    outcome, after = approve_and_write(monkeypatch, FakeDesk("approved"), memory, before_answer=someone_edits_the_store)

    assert after.events[-1] == {
        "type": "job.failed",
        "reason": "error",
        "stepId": "save",
        "message": "The store admin changed after you approved, so I stopped. Nothing was changed.",
    }
    assert outcome["problem"] == "store-changed"
    assert sites.saved is False
    assert memory.values[UNDO_KEY] == earlier


def test_a_save_button_renamed_after_the_ok_stops_before_anything_is_saved(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()

    def rename():
        sites.save_label = "Update inventory"

    outcome, after = approve_and_write(monkeypatch, FakeDesk("approved"), memory, before_answer=rename)

    assert after.events[-1]["message"] == "The store admin doesn't match the saved steps any more, so I stopped. Nothing was changed."
    assert outcome["problem"] == "wrong-name"
    assert sites.saved is False
    assert UNDO_KEY not in memory.values


def approve_with_ready_time(monkeypatch, desk, memory, *, ready_in_ms=117, mismatch=False):
    procedure = kumo_procedure()
    memory.values[PROCEDURE_KEY] = procedure.to_json()
    transcript = Transcript()
    result = asyncio.run(run_replay(transcript.notes, resolve_job("kumo"), procedure, request=REQUEST, job_id=JOB_ID, clock=ticking_clock()))
    pending = PendingWrite(**{**pending_from(result, transcript.notes.count).__dict__, "ready_in_ms": ready_in_ms})
    after = Transcript(start=transcript.notes.count)
    outcome = asyncio.run(
        see_approval_through(desk, after.notes, resolve_job("kumo"), pending, memory, writer=script_writer(procedure, clock=ticking_clock(), wall=ticking_wall(ASKED_AT + 20)))
    )
    return outcome, after


def test_a_replay_that_saved_and_matched_every_row_keeps_its_time_to_the_approval_as_the_new_estimate(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()

    outcome, after = approve_with_ready_time(monkeypatch, FakeDesk("approved"), memory)

    assert outcome["status"] == "done"
    stored = memory.values[PROCEDURE_KEY]
    assert stored["expectedReplayMs"] == 117
    assert stored["procedureVersion"] == 1
    assert "procedure.saved" not in {event["type"] for event in after.events}


def test_a_replay_that_was_turned_down_leaves_the_estimate_alone(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()

    approve_with_ready_time(monkeypatch, FakeDesk("rejected"), memory)

    assert memory.values[PROCEDURE_KEY]["expectedReplayMs"] == kumo_procedure().expected_replay_ms


def test_the_time_to_the_approval_travels_with_the_waiting_record():
    from test_node_approval import waiting_record

    record = PendingWrite(**{**waiting_record().__dict__, "replay_version": 1, "ready_in_ms": 117})

    assert record.to_json()["readyInMs"] == 117
    assert PendingWrite.from_json(json.loads(json.dumps(record.to_json()))) == record
    assert "readyInMs" not in waiting_record().to_json()
