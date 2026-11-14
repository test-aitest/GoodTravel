import asyncio
import json
from dataclasses import replace

import pytest

import clientops.replay as replay
from clientops.approval import PendingWrite, see_approval_through
from clientops.boundary import resolve_job
from clientops.compile_script import compile_read_script
from clientops.procedure import PROCEDURE_KEY, Hint, Procedure, ProcedureDraft, keep_rebuilt
from clientops.records import EventNotes
from clientops.refind import FailedStep, NotFound, Recovered, rebuilt
from clientops.replay import run_replay, writer_for
from clientops.stock_sync import PreparedWrite
from approval_fakes import FakeDesk, MemoryStore, ticking_wall
from test_procedure import FULL_MATCH
from test_refind import CLASS_SAVE, CLASS_STORE_PASSWORD, CLASS_SUPPLIER_USER, SAVE_CSS, SUPPLIER_USER_CSS, recorded_with
from test_replay import AFTER_APPROVAL, ASKED_AT, DAY2_CHANGES, JOB_ID, KUMO_DAY2, READ_EVENTS, REQUEST, Transcript, kumo_procedure, ticking_clock
from test_stock_sync import VAULT, data_dir, install_sites

__all__ = ["data_dir"]

REFOUND_AT_CHECK_SAVE = READ_EVENTS[:15] + [("page.seen", "check-save")] + READ_EVENTS[15:]
DOESNT_MATCH = "The store admin doesn't match the saved steps any more, so I stopped. Nothing was changed."


def replay_of(procedure: Procedure) -> tuple[dict, Transcript]:
    transcript = Transcript()
    result = asyncio.run(run_replay(transcript.notes, resolve_job("kumo"), procedure, request=REQUEST, job_id=JOB_ID, clock=ticking_clock()))
    return result, transcript


def decided(transcript: Transcript) -> list[tuple[str, str, list[str] | None]]:
    return [(event["stepId"], event["rung"], event.get("escalatedFrom")) for event in transcript.events if event["type"] == "step.decided"]


def types(transcript: Transcript) -> set[str]:
    return {event["type"] for event in transcript.events}


def script_steps(sites) -> list[list[str]]:
    return [[step["stepId"] for step in plan["steps"]] for plan in sites.scripts]


def test_only_the_failed_step_climbs_to_the_tree_and_the_script_carries_on_to_the_approval(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    result, transcript = replay_of(recorded_with("check-save", "save", CLASS_SAVE))

    assert transcript.shape() == REFOUND_AT_CHECK_SAVE
    assert decided(transcript) == [("sign-in", "script", None), ("read", "script", None), ("compare", "script", None), ("check-save", "tree", ["script"])]
    tree = next(event for event in transcript.events if event["type"] == "step.decided" and event["rung"] == "tree")
    assert tree["tookMs"] > 0
    seen = next(event for event in transcript.events if event["type"] == "page.seen" and event["stepId"] == "check-save")
    assert seen["host"] == "admin.kumo-coffee.example"
    done = {event["stepId"]: event for event in transcript.events if event["type"] == "step.done"}
    assert done["check-save"]["summary"] == "Save button found"
    assert done["check-save"]["tookMs"] > tree["tookMs"]
    assert done["compare"]["detail"] == "4 products differ"
    assert transcript.events[-1]["changes"] == DAY2_CHANGES
    assert result["status"] == "waiting"
    assert result["claudeCalls"] == 0
    assert result["replay"] == {"procedureVersion": 1}
    assert ProcedureDraft.from_json(result["procedure"]).read.save_button.css == SAVE_CSS
    assert script_steps(sites) == [["sign-in", "read", "compare", "check-save"], ["check-save"]]
    assert sites.saved is False


def test_a_restyled_store_climbs_at_compare_and_at_check_save_only_and_the_script_carries_on(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, restyled=True)

    result, transcript = replay_of(kumo_procedure())

    assert decided(transcript) == [("sign-in", "script", None), ("read", "script", None), ("compare", "tree", ["script"]), ("check-save", "tree", ["script"])]
    assert script_steps(sites) == [["sign-in", "read", "compare", "check-save"], ["compare", "check-save"], ["check-save"]]
    assert transcript.events[-1]["changes"] == DAY2_CHANGES
    assert result["claudeCalls"] == 0
    draft = ProcedureDraft.from_json(result["procedure"])
    assert (draft.read.store.user.css, draft.read.save_button.css) == ('form[action="/login"] input[name="f-username"]', 'form[action="/inventory"] button[name="f-action"]')
    assert draft.read.supplier == kumo_procedure().read.supplier


def test_after_the_ok_a_restyled_store_is_written_with_the_rebuilt_steps_and_they_become_version_2(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, restyled=True)
    memory = MemoryStore()

    outcome, after = approve_rebuilt(FakeDesk("approved"), memory, kumo_procedure())

    assert after.shape() == AFTER_APPROVAL + [("procedure.saved", None)]
    assert (outcome["status"], outcome["matched"]) == ("done", 4)
    stored = Procedure.from_json(memory.values[PROCEDURE_KEY])
    assert stored.version == 2
    assert stored.read.store.password.css == 'form[action="/login"] input[name="f-password"][type="password"]'
    assert sites.saves == ["Save changes"]


def test_a_replay_that_needed_no_help_hands_back_no_rebuilt_steps(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)

    result, _ = replay_of(kumo_procedure())

    assert "procedure" not in result


def test_a_supplier_sign_in_field_found_again_lets_every_later_step_run_in_the_script(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    result, transcript = replay_of(recorded_with("sign-in", "user", CLASS_SUPPLIER_USER))

    assert decided(transcript) == [("sign-in", "tree", ["script"]), ("read", "script", None), ("compare", "script", None), ("check-save", "script", None)]
    assert script_steps(sites) == [["sign-in", "read", "compare", "check-save"], ["sign-in", "read", "compare", "check-save"]]
    done = {event["stepId"]: event for event in transcript.events if event["type"] == "step.done"}
    assert done["read"]["detail"] == "42 products"
    assert done["compare"]["detail"] == "4 products differ"
    assert transcript.events[-1]["changes"] == DAY2_CHANGES
    assert ProcedureDraft.from_json(result["procedure"]).read.supplier.user.css == SUPPLIER_USER_CSS


def test_a_store_sign_in_field_found_again_carries_on_from_compare(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    result, transcript = replay_of(recorded_with("compare", "password", CLASS_STORE_PASSWORD))

    assert decided(transcript) == [("sign-in", "script", None), ("read", "script", None), ("compare", "tree", ["script"]), ("check-save", "script", None)]
    assert script_steps(sites) == [["sign-in", "read", "compare", "check-save"], ["compare", "check-save"]]
    assert transcript.events[-1]["changes"] == DAY2_CHANGES
    assert result["status"] == "waiting"


def test_two_broken_steps_each_climb_once_and_the_rest_stays_in_the_script(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    both = recorded_with("check-save", "save", CLASS_SAVE, base=recorded_with("sign-in", "user", CLASS_SUPPLIER_USER))

    result, transcript = replay_of(both)

    assert decided(transcript) == [("sign-in", "tree", ["script"]), ("read", "script", None), ("compare", "script", None), ("check-save", "tree", ["script"])]
    assert script_steps(sites) == [["sign-in", "read", "compare", "check-save"], ["sign-in", "read", "compare", "check-save"], ["check-save"]]
    draft = ProcedureDraft.from_json(result["procedure"])
    assert (draft.read.supplier.user.css, draft.read.save_button.css) == (SUPPLIER_USER_CSS, SAVE_CSS)


def test_a_renamed_button_the_tree_cannot_find_goes_to_the_hook_for_claude_and_then_stops(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label="Update inventory")
    handed: list[tuple[FailedStep, NotFound]] = []

    async def repair(failed, not_found):
        handed.append((failed, not_found))
        return None

    monkeypatch.setattr(replay, "repair_failed_step", repair)

    result, transcript = replay_of(kumo_procedure())

    [(failed, not_found)] = handed
    assert (failed.step_id, failed.code, failed.done_steps) == ("check-save", "wrong-name", ("sign-in", "read", "compare"))
    assert not_found == NotFound("check-save", "save", Hint("button", "Save changes", "/inventory"), 0, not_found.took_ms)
    assert transcript.shape() == READ_EVENTS[:14] + [("job.failed", "check-save")]
    assert transcript.events[-1]["message"] == DOESNT_MATCH
    assert result["problem"] == "wrong-name"
    assert result["claudeCalls"] == 0
    assert sites.saved is False


def test_when_the_hook_for_claude_hands_back_rebuilt_steps_the_script_carries_on_from_that_step(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, save_label="Update inventory")

    async def repair(failed, not_found):
        renamed = replace(failed.procedure.read.save_button, text="Update inventory", hint=Hint("button", "Update inventory", "/inventory"))
        return Recovered("check-save", "save", rebuilt(failed.procedure, failed.boundary, "check-save", "save", renamed), "claude", ("script", "tree"), 2600)

    monkeypatch.setattr(replay, "repair_failed_step", repair)

    result, transcript = replay_of(kumo_procedure())

    assert decided(transcript)[-1] == ("check-save", "claude", ["script", "tree"])
    assert result["status"] == "waiting"
    assert ProcedureDraft.from_json(result["procedure"]).read.save_button.text == "Update inventory"


def test_a_step_that_still_fails_after_it_was_found_again_stops_without_climbing_twice(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    asked: list[str | None] = []

    async def recover(failed):
        asked.append(failed.step_id)
        return Recovered("check-save", "save", failed.procedure, "tree", ("script",), 250)

    monkeypatch.setattr(replay, "recover_failed_step", recover)

    result, transcript = replay_of(recorded_with("check-save", "save", CLASS_SAVE))

    assert asked == ["check-save"]
    assert transcript.shape()[-3:] == [("step.started", "check-save"), ("step.decided", "check-save"), ("job.failed", "check-save")]
    assert transcript.events[-1]["message"] == DOESNT_MATCH
    assert result["problem"] == "not-found"
    assert "approval.requested" not in types(transcript)
    assert len(sites.scripts) == 2


def test_when_nothing_changed_the_found_selector_is_not_handed_on_to_be_saved(monkeypatch):
    install_sites(monkeypatch, day={**KUMO_DAY2, "store": KUMO_DAY2["supplier"], "changes": []})

    result, transcript = replay_of(recorded_with("check-save", "save", CLASS_SAVE))

    assert result["status"] == "done"
    assert result["changes"] == 0
    assert decided(transcript)[-1] == ("check-save", "tree", ["script"])
    assert "procedure" not in result


def waiting_after(result: dict, notes_written: int) -> PendingWrite:
    pending = PendingWrite(
        job_id=JOB_ID,
        run_id="run_replay",
        workflow_id="run_replay",
        client_id="kumo",
        approval_id=result["approvalId"],
        notes_written=notes_written,
        asked_at=ASKED_AT,
        write=PreparedWrite.from_json(result["write"]),
        procedure=ProcedureDraft.from_json(result["procedure"]),
        replay_version=result["replay"]["procedureVersion"],
        ready_in_ms=result["readyInMs"],
    )
    return PendingWrite.from_json(json.loads(json.dumps(pending.to_json())))


def approve_rebuilt(desk, memory, procedure, *, before_answer=None):
    memory.values[PROCEDURE_KEY] = procedure.to_json()
    result, transcript = replay_of(procedure)
    if before_answer:
        before_answer()
    pending = waiting_after(result, transcript.notes.count)
    writer = asyncio.run(writer_for(pending, resolve_job("kumo"), memory))
    after = Transcript(start=transcript.notes.count)
    outcome = asyncio.run(see_approval_through(desk, after.notes, resolve_job("kumo"), pending, memory, writer=writer, wall=ticking_wall(ASKED_AT + 20)))
    return outcome, after


def test_after_the_ok_the_rebuilt_steps_write_and_a_full_match_saves_them_as_version_2(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()

    outcome, after = approve_rebuilt(FakeDesk("approved"), memory, recorded_with("check-save", "save", CLASS_SAVE))

    assert after.shape() == AFTER_APPROVAL + [("procedure.saved", None)]
    assert after.events[-1] == {"type": "procedure.saved", "procedureId": "kumo-stock-sync", "version": 2, "stepCount": 6, "expectedReplayMs": 4200}
    assert outcome["status"] == "done"
    assert (outcome["written"], outcome["checked"], outcome["matched"]) == (4, 4, 4)
    stored = Procedure.from_json(memory.values[PROCEDURE_KEY])
    assert stored.version == 2
    assert stored.read.save_button.css == SAVE_CSS
    assert stored.expected_replay_ms == 4200
    assert stored.script == compile_read_script(stored.read, resolve_job("kumo"))
    [save] = [op for op in sites.scripts[-1]["steps"][0]["ops"] if op["op"] == "one" and op["as"] == "save"]
    assert save["css"] == SAVE_CSS
    assert sites.saves == ["Save changes"]
    for value in VAULT.values():
        assert value not in json.dumps(memory.values[PROCEDURE_KEY])


def test_turning_the_changes_down_keeps_version_1_as_it_was(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()
    recorded = recorded_with("check-save", "save", CLASS_SAVE)

    outcome, after = approve_rebuilt(FakeDesk("rejected"), memory, recorded)

    assert outcome == {"status": "stopped", "decision": "rejected"}
    assert memory.values[PROCEDURE_KEY] == recorded.to_json()
    assert "procedure.saved" not in types(after)
    assert sites.saved is False


def test_a_store_that_changed_after_the_ok_keeps_version_1_as_it_was(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()
    recorded = recorded_with("check-save", "save", CLASS_SAVE)

    def someone_edits_the_store():
        sites.store_rows[0]["available"] = 99

    outcome, after = approve_rebuilt(FakeDesk("approved"), memory, recorded, before_answer=someone_edits_the_store)

    assert outcome["problem"] == "store-changed"
    assert memory.values[PROCEDURE_KEY] == recorded.to_json()
    assert "procedure.saved" not in types(after)


def test_rebuilt_steps_whose_saved_version_moved_on_while_waiting_write_nothing(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    result, transcript = replay_of(recorded_with("check-save", "save", CLASS_SAVE))
    pending = waiting_after(result, transcript.notes.count)
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure(version=2).to_json()
    commands_before = len(sites.commands)

    writer = asyncio.run(writer_for(pending, resolve_job("kumo"), memory))
    after = Transcript(start=transcript.notes.count)
    outcome = asyncio.run(writer(after.notes, resolve_job("kumo"), pending, memory))

    assert outcome["problem"] == "procedure-changed"
    assert len(sites.commands) == commands_before
    assert memory.values[PROCEDURE_KEY]["procedureVersion"] == 2


def test_the_next_replay_with_version_2_runs_every_step_in_the_script_again(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    memory = MemoryStore()
    approve_rebuilt(FakeDesk("approved"), memory, recorded_with("check-save", "save", CLASS_SAVE))
    version_2 = Procedure.from_json(memory.values[PROCEDURE_KEY])
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    result, transcript = replay_of(version_2)

    assert transcript.events[0]["run"] == {"kind": "replay", "procedureVersion": 2}
    assert {rung for _, rung, _ in decided(transcript)} == {"script"}
    assert [command["command"] for command in sites.commands] == ["script"]
    assert result["status"] == "waiting"
    assert "procedure" not in result


class Kept:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.notes = EventNotes(lambda message, tags: self.sent.append(json.loads(message)), start=40)


def kept(memory, outcome, procedure=None):
    procedure = procedure or recorded_with("check-save", "save", SAVE_CSS)
    notes = Kept()
    result = asyncio.run(keep_rebuilt(notes.notes, resolve_job("kumo"), memory, procedure, outcome, wall=ticking_wall(1_794_657_600.0)))
    return result, notes.sent


def test_a_full_match_keeps_the_rebuilt_steps_as_the_next_version_and_says_so():
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
    rebuilt_steps = recorded_with("check-save", "save", SAVE_CSS)

    result, sent = kept(memory, FULL_MATCH, rebuilt_steps)

    assert result.version == 2
    assert [note["event"] for note in sent] == [
        {"type": "procedure.saved", "procedureId": "kumo-stock-sync", "version": 2, "stepCount": 6, "expectedReplayMs": 4200}
    ]
    assert sent[0]["n"] == 41
    stored = memory.values[PROCEDURE_KEY]
    assert stored["procedureVersion"] == 2
    assert stored["savedAt"] == "2026-11-14T12:00:00Z"
    assert Procedure.from_json(stored).read == rebuilt_steps.read


@pytest.mark.parametrize(
    "outcome",
    [
        {**FULL_MATCH, "matched": 11},
        {"status": "failed", "reason": "verify-mismatch", "written": 4, "checked": 4, "matched": 3, "maySaveProcedure": False},
        {"status": "stopped", "decision": "rejected"},
        {"status": "failed", "reason": "error", "stepId": "save", "problem": "store-changed"},
    ],
)
def test_anything_short_of_a_full_match_keeps_the_old_version(outcome):
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()

    result, sent = kept(memory, outcome)

    assert result is None
    assert sent == []
    assert memory.values[PROCEDURE_KEY] == kumo_procedure().to_json()


def test_rebuilt_steps_are_not_kept_over_a_version_that_moved_on_or_when_memory_is_down():
    moved = MemoryStore()
    moved.values[PROCEDURE_KEY] = kumo_procedure(version=3).to_json()
    down = MemoryStore(failing=("set",))
    down.values[PROCEDURE_KEY] = kumo_procedure().to_json()

    for memory in (moved, down):
        result, sent = kept(memory, FULL_MATCH)
        assert result is None
        assert sent == []
    assert moved.values[PROCEDURE_KEY]["procedureVersion"] == 3
    assert down.values[PROCEDURE_KEY]["procedureVersion"] == 1
