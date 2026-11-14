import asyncio
import json
import os
from pathlib import Path

import pytest

from clientops.approval import PendingWrite, see_approval_through
from clientops.boundary import resolve_job
from clientops.procedure import ProcedureDraft
from clientops.records import EventNotes
from clientops.replay import learning_writer, run_replay, script_writer
from clientops.stock_sync import PreparedWrite, run_first_run
from approval_fakes import FakeDesk, MemoryStore, ticking_wall
from picking_claude import PickingClaude
from test_replay import KUMO_DAY2, kumo_procedure
from test_stock_sync import REQUEST, data_dir, install_sites, quarter_second_clock

__all__ = ["data_dir"]

CONTRACTS = Path(__file__).parent / "contract"
RECORD_VARIABLE = "CLIENTOPS_RECORD_CONTRACTS"
SCHEDULED_REQUEST = "Daily at 18:00: sync supplier stock to the store admin."
ASKED_AT = 1_794_646_848.0


def pending_after(result: dict, job_id: str, notes: EventNotes, *, draft=None, replay_version=None) -> PendingWrite:
    return PendingWrite(
        job_id=job_id,
        run_id=f"run_{job_id}",
        workflow_id=f"run_{job_id}",
        client_id="kumo",
        approval_id=result["approvalId"],
        notes_written=notes.count,
        asked_at=ASKED_AT,
        write=PreparedWrite.from_json(result["write"]),
        procedure=draft,
        replay_version=replay_version,
    )


def produce(kind: str, monkeypatch) -> dict:
    sent: list[dict] = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)))
    memory = MemoryStore()
    job_id = f"exec_{kind.replace('-', '_')}"

    async def learned():
        install_sites(monkeypatch)
        result = await run_first_run(notes, resolve_job("kumo"), PickingClaude(), request=REQUEST, job_id=job_id, clock=quarter_second_clock())
        pending = pending_after(result, job_id, notes, draft=ProcedureDraft.from_json(result["procedure"]))
        writer = learning_writer(clock=quarter_second_clock(), wall=ticking_wall(ASKED_AT + 12))
        await see_approval_through(FakeDesk("approved"), notes, resolve_job("kumo"), pending, memory, writer=writer, wall=ticking_wall(ASKED_AT + 12))

    async def replayed(save_label: str = "Save changes"):
        install_sites(monkeypatch, day=KUMO_DAY2, save_label=save_label)
        procedure = kumo_procedure()
        result = await run_replay(notes, resolve_job("kumo"), procedure, request=SCHEDULED_REQUEST, job_id=job_id, clock=quarter_second_clock())
        if result["status"] != "waiting":
            return
        pending = pending_after(result, job_id, notes, replay_version=procedure.version)
        writer = script_writer(procedure, clock=quarter_second_clock(), wall=ticking_wall(ASKED_AT + 24))
        await see_approval_through(FakeDesk("approved"), notes, resolve_job("kumo"), pending, memory, writer=writer, wall=ticking_wall(ASKED_AT + 24))

    runs = {"learned": learned, "replay": replayed, "replay-renamed-button": lambda: replayed("Update inventory")}
    asyncio.run(runs[kind]())
    return {"input": {"client_id": "kumo"}, "jobId": job_id, "notes": sent}


@pytest.mark.parametrize("kind", ["learned", "replay", "replay-renamed-button"])
def test_the_notes_of_learning_and_replaying_are_exactly_the_contract_the_screen_reads(monkeypatch, kind):
    produced = produce(kind, monkeypatch)
    path = CONTRACTS / f"{kind.replace('-', '_')}_job_notes.json"
    if os.environ.get(RECORD_VARIABLE) == "1":
        path.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(path.read_text())


def test_the_learned_contract_is_the_approved_contract_plus_the_saved_steps():
    approved = json.loads((CONTRACTS / "approved_job_notes.json").read_text())["notes"]
    learned = json.loads((CONTRACTS / "learned_job_notes.json").read_text())["notes"]

    assert [note["event"]["type"] for note in learned] == [note["event"]["type"] for note in approved] + ["procedure.saved"]
    assert learned[-1]["event"]["procedureId"] == "kumo-stock-sync"
    assert learned[-1]["event"]["version"] == 1
    assert learned[-1]["event"]["stepCount"] == 6


def test_the_replay_contract_has_no_claude_step_and_no_new_saved_steps():
    replay = [note["event"] for note in json.loads((CONTRACTS / "replay_job_notes.json").read_text())["notes"]]

    assert replay[0]["run"] == {"kind": "replay", "procedureVersion": 1}
    assert {event["rung"] for event in replay if event["type"] == "step.decided"} == {"script", "human"}
    assert all(event["type"] != "procedure.saved" for event in replay)
