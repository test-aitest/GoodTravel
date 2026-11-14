import asyncio

from clientops.approval import see_approval_through
from clientops.boundary import resolve_job
from clientops.procedure import PROCEDURE_KEY, Procedure, ProcedureDraft
from clientops.replay import run_replay, writer_for
from clientops.repair import RepairHelp
from approval_fakes import FakeDesk, MemoryStore, ticking_wall
from picking_claude import PickingClaude
from test_refind import SAVE_CSS
from test_replay import AFTER_APPROVAL, DAY2_CHANGES, JOB_ID, KUMO_DAY2, READ_EVENTS, REQUEST, Transcript, kumo_procedure, ticking_clock
from test_replay_refind import decided, script_steps, types, waiting_after
from test_stock_sync import data_dir, install_sites, write_settings

__all__ = ["data_dir"]

RENAMED = "Update inventory"
DECOY = "Save changes to all locations"
CHOICE_ID = f"cho-{JOB_ID}-check-save"
REPAIRED_AT_CHECK_SAVE = READ_EVENTS[:14] + [("step.decided", "check-save"), ("page.seen", "check-save")] + READ_EVENTS[15:]
ASKED_AT_CHECK_SAVE = READ_EVENTS[:14] + [("choice.requested", "check-save"), ("choice.answered", None), ("step.decided", "check-save"), ("page.seen", "check-save")] + READ_EVENTS[15:]
CHANGE = {"stepId": "check-save", "fromVersion": 1, "toVersion": 2, "before": "Save changes", "after": RENAMED}


def replayed(procedure: Procedure, *, claude=None, desk=None, transcript=None) -> tuple[dict, Transcript]:
    transcript = transcript or Transcript()
    help_ = RepairHelp(claude, desk or FakeDesk(), JOB_ID)
    result = asyncio.run(run_replay(transcript.notes, resolve_job("kumo"), procedure, request=REQUEST, job_id=JOB_ID, clock=ticking_clock(), repair=help_))
    return result, transcript


def approved(result: dict, transcript: Transcript, memory: MemoryStore, *answers) -> tuple[dict, Transcript]:
    pending = waiting_after(result, transcript.notes.count)
    after = Transcript(start=transcript.notes.count)

    async def see_through():
        writer = await writer_for(pending, resolve_job("kumo"), memory)
        return await see_approval_through(FakeDesk(*answers), after.notes, resolve_job("kumo"), pending, memory, writer=writer, wall=ticking_wall(1_794_646_900.0))

    return asyncio.run(see_through()), after


def event_of(transcript: Transcript, event_type: str) -> dict:
    [found] = [event for event in transcript.events if event["type"] == event_type]
    return found


def test_a_renamed_save_button_is_repaired_by_claude_and_the_script_carries_on_to_an_approval_that_shows_the_change(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED)
    claude = PickingClaude()

    result, transcript = replayed(kumo_procedure(), claude=claude)

    assert transcript.shape() == REPAIRED_AT_CHECK_SAVE
    assert decided(transcript) == [("sign-in", "script", None), ("read", "script", None), ("compare", "script", None), ("check-save", "claude", ["script", "tree"])]
    done = {event["stepId"]: event for event in transcript.events if event["type"] == "step.done"}
    assert (done["check-save"]["summary"], done["check-save"]["detail"]) == ("Found the new button", f"Save changes is now {RENAMED}")
    approval = event_of(transcript, "approval.requested")
    assert approval["question"] == "Save 4 stock changes with the new button?"
    assert approval["procedureChange"] == CHANGE
    assert approval["changes"] == DAY2_CHANGES
    assert result["status"] == "waiting"
    assert result["claudeCalls"] == 1
    assert [call["outcome"] for call in result["claudeUsage"]["check-save"]] == ["chosen"]
    assert claude.calls == 1
    draft = ProcedureDraft.from_json(result["procedure"])
    assert (draft.read.save_button.css, draft.read.save_button.text) == (SAVE_CSS, RENAMED)
    assert result["write"]["saveButton"] == {"role": "button", "name": RENAMED, "form": "/inventory"}
    assert result["replay"] == {"procedureVersion": 1}
    assert script_steps(sites) == [["sign-in", "read", "compare", "check-save"], ["check-save"]]
    assert sites.saved is False


def test_after_the_ok_the_new_button_saves_every_row_and_the_steps_become_version_2(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED)
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
    result, transcript = replayed(kumo_procedure(), claude=PickingClaude())

    outcome, after = approved(result, transcript, memory, "approved")

    assert after.shape() == AFTER_APPROVAL + [("procedure.saved", None)]
    assert outcome["status"] == "done" and outcome["matched"] == 4
    assert after.events[-1]["version"] == CHANGE["toVersion"]
    assert sites.saves == [RENAMED]
    stored = Procedure.from_json(memory.values[PROCEDURE_KEY])
    assert (stored.version, stored.read.save_button.css, stored.read.save_button.text) == (2, SAVE_CSS, RENAMED)
    assert stored.read.save_button.hint.name == RENAMED


def test_turned_down_the_repaired_steps_are_not_kept(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED)
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
    result, transcript = replayed(kumo_procedure(), claude=PickingClaude())

    outcome, after = approved(result, transcript, memory, "rejected")

    assert outcome["status"] == "stopped"
    assert "procedure.saved" not in types(after)
    assert Procedure.from_json(memory.values[PROCEDURE_KEY]).version == 1
    assert sites.saved is False


def test_the_replay_after_version_2_runs_every_step_on_the_script_with_no_claude(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED)
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
    result, transcript = replayed(kumo_procedure(), claude=PickingClaude())
    approved(result, transcript, memory, "approved")
    version_2 = Procedure.from_json(memory.values[PROCEDURE_KEY])
    install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED)
    claude = PickingClaude()

    again, later = replayed(version_2, claude=claude)

    assert later.events[0]["run"] == {"kind": "replay", "procedureVersion": 2}
    assert {rung for _, rung, _ in decided(later)} == {"script"}
    assert "procedureChange" not in event_of(later, "approval.requested")
    assert again["claudeCalls"] == 0 and claude.calls == 0
    assert "procedure" not in again


def test_two_save_like_buttons_ask_the_person_and_their_pick_carries_on_to_the_approval(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED, extra_store_buttons=(DECOY,))
    desk = FakeDesk(("approved", "1016"))

    result, transcript = replayed(kumo_procedure(), claude=PickingClaude(), desk=desk)

    assert transcript.shape() == ASKED_AT_CHECK_SAVE
    asked = event_of(transcript, "choice.requested")
    assert asked == {
        "type": "choice.requested",
        "choiceId": CHOICE_ID,
        "stepId": "check-save",
        "question": "Which button saves the changes now?",
        "before": "Save changes",
        "candidates": [{"ref": "1016", "role": "button", "name": RENAMED}, {"ref": "1100", "role": "button", "name": DECOY}],
    }
    assert event_of(transcript, "choice.answered") == {"type": "choice.answered", "choiceId": CHOICE_ID, "ref": "1016", "by": "You"}
    assert decided(transcript)[-1] == ("check-save", "human", ["script", "tree", "claude"])
    approval = event_of(transcript, "approval.requested")
    assert approval["question"] == "Save 4 stock changes with the new button?"
    assert approval["procedureChange"] == CHANGE
    assert result["status"] == "waiting" and result["claudeCalls"] == 1
    assert [paused["approvalId"] for paused in desk.paused] == [CHOICE_ID]
    assert sites.saved is False


def test_none_of_these_stops_the_job_and_nothing_is_written(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED, extra_store_buttons=(DECOY,))

    result, transcript = replayed(kumo_procedure(), claude=PickingClaude(), desk=FakeDesk(("rejected", "")))

    assert transcript.shape()[-3:] == [("choice.requested", "check-save"), ("choice.answered", None), ("job.failed", "check-save")]
    assert transcript.events[-1] == {"type": "job.failed", "reason": "error", "stepId": "check-save", "message": "You picked none of the buttons, so I stopped. Nothing was changed."}
    assert result == {"status": "failed", "reason": "error", "stepId": "check-save", "problem": "none-chosen", "claudeCalls": 1, "claudeUsage": result["claudeUsage"]}
    assert "approval.requested" not in types(transcript)
    assert sites.saved is False


def test_with_claude_off_the_person_is_asked_and_claude_is_never_called(monkeypatch, data_dir):
    write_settings(data_dir, {"kumo": {"useClaude": False}})
    install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED)
    claude = PickingClaude()

    result, transcript = replayed(kumo_procedure(), claude=claude, desk=FakeDesk(("approved", "1016")))

    assert decided(transcript)[-1] == ("check-save", "human", ["script", "tree"])
    assert event_of(transcript, "approval.requested")["procedureChange"] == CHANGE
    assert claude.calls == 0
    assert result["claudeCalls"] == 0 and result["claudeUsage"] == {}


def test_stopping_while_the_person_picks_saves_nothing(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED, extra_store_buttons=(DECOY,))
    transcript = Transcript()
    desk = FakeDesk()

    async def stop_while_waiting():
        task = asyncio.create_task(
            run_replay(transcript.notes, resolve_job("kumo"), kumo_procedure(), request=REQUEST, job_id=JOB_ID, clock=ticking_clock(), repair=RepairHelp(PickingClaude(), desk, JOB_ID))
        )
        await desk.waiting.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "finished"

    assert asyncio.run(stop_while_waiting()) == "cancelled"
    assert transcript.events[-1] == {"type": "job.failed", "reason": "stopped", "message": "Stopped before saving. Nothing was changed.", "stepId": "check-save"}
    assert desk.released == [CHOICE_ID]
    assert sites.saved is False
