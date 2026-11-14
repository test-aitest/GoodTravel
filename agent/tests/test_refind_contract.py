import asyncio
import json
import os

from clientops.approval import see_approval_through
from clientops.boundary import resolve_job
from clientops.procedure import PROCEDURE_KEY, ProcedureDraft
from clientops.records import EventNotes
from clientops.replay import rebuilt_writer, run_replay
from approval_fakes import FakeDesk, MemoryStore, ticking_wall
from test_refind import CLASS_SAVE, recorded_with
from test_replay import KUMO_DAY2
from test_replay_contract import ASKED_AT, CONTRACTS, RECORD_VARIABLE, SCHEDULED_REQUEST, pending_after
from test_stock_sync import data_dir, install_sites, quarter_second_clock

__all__ = ["data_dir"]

CONTRACT = CONTRACTS / "refind_job_notes.json"
JOB_ID = "exec_refind"


def produce(monkeypatch) -> dict:
    sent: list[dict] = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)))
    memory = MemoryStore()

    async def refound():
        install_sites(monkeypatch, day=KUMO_DAY2)
        procedure = recorded_with("check-save", "save", CLASS_SAVE)
        memory.values[PROCEDURE_KEY] = procedure.to_json()
        result = await run_replay(notes, resolve_job("kumo"), procedure, request=SCHEDULED_REQUEST, job_id=JOB_ID, clock=quarter_second_clock())
        draft = ProcedureDraft.from_json(result["procedure"])
        pending = pending_after(result, JOB_ID, notes, draft=draft, replay_version=procedure.version)
        writer = rebuilt_writer(procedure, draft.read, clock=quarter_second_clock(), wall=ticking_wall(ASKED_AT + 24))
        await see_approval_through(FakeDesk("approved"), notes, resolve_job("kumo"), pending, memory, writer=writer, wall=ticking_wall(ASKED_AT + 24))

    asyncio.run(refound())
    return {"input": {"client_id": "kumo"}, "jobId": JOB_ID, "notes": sent}


def test_the_notes_of_a_replay_that_found_a_step_again_are_exactly_the_contract_the_screen_reads(monkeypatch):
    produced = produce(monkeypatch)
    if os.environ.get(RECORD_VARIABLE) == "1":
        CONTRACT.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(CONTRACT.read_text())


def test_the_contract_is_the_replay_with_one_step_found_by_the_tree_and_the_steps_saved_as_version_2():
    replayed = [note["event"] for note in json.loads((CONTRACTS / "replay_job_notes.json").read_text())["notes"]]
    refound = [note["event"] for note in json.loads(CONTRACT.read_text())["notes"]]

    shape = [(event["type"], event.get("stepId")) for event in refound]
    expected = [(event["type"], event.get("stepId")) for event in replayed]
    at = expected.index(("step.decided", "check-save")) + 1
    assert shape == [*expected[:at], ("page.seen", "check-save"), *expected[at:], ("procedure.saved", None)]
    assert [(event["stepId"], event["rung"], event.get("escalatedFrom")) for event in refound if event["type"] == "step.decided"] == [
        ("sign-in", "script", None),
        ("read", "script", None),
        ("compare", "script", None),
        ("check-save", "tree", ["script"]),
        ("ask", "human", None),
        ("save", "script", None),
    ]
    assert refound[0]["run"] == {"kind": "replay", "procedureVersion": 1}
    assert refound[-1] == {"type": "procedure.saved", "procedureId": "kumo-stock-sync", "version": 2, "stepCount": 6, "expectedReplayMs": 4200}
