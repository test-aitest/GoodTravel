import asyncio
import json
import os
from pathlib import Path

import pytest

from clientops.approval import PendingWrite, see_approval_through, undo_last_sync
from clientops.boundary import resolve_job
from clientops.page import Locator
from clientops.records import EventNotes
from clientops.stock_sync import PreparedWrite, run_first_run
from approval_fakes import FOREVER, FakeDesk, MemoryStore, ticking_wall
from picking_claude import PickingClaude
from test_stock_sync import REQUEST, data_dir, install_sites, quarter_second_clock

__all__ = ["data_dir"]

CONTRACTS = Path(__file__).parent / "contract"
RECORD_VARIABLE = "CLIENTOPS_RECORD_CONTRACTS"
JOB_ID = "exec_first_run"
RUN_ID = "run_first_run"
UNDO_JOB_ID = "exec_undo"
ASKED_AT = 1_794_646_848.0
DECOY = "Save changes to all locations"


def pick_the_decoy(fake, user):
    [decoy] = [entry["id"] for entry in (json.loads(line) for line in user.splitlines() if line.startswith("{")) if entry["name"] == DECOY]
    fake.reply_choice(decoy, "click")


async def first_part(notes: EventNotes, claude) -> PendingWrite:
    result = await run_first_run(notes, resolve_job("kumo"), claude, request=REQUEST, job_id=JOB_ID, clock=quarter_second_clock())
    return PendingWrite(
        job_id=JOB_ID,
        run_id=RUN_ID,
        workflow_id=RUN_ID,
        client_id="kumo",
        approval_id=result["approvalId"],
        notes_written=notes.count,
        asked_at=ASKED_AT,
        write=PreparedWrite.from_json(result["write"]),
    )


def produce(kind: str, monkeypatch) -> dict:
    sites = install_sites(monkeypatch, extra_store_buttons=(DECOY,) if kind == "decoy" else ())
    sent: list[dict] = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)))
    memory = MemoryStore()
    claude = PickingClaude(scripts={"saves the changes": [pick_the_decoy]} if kind == "decoy" else None)

    async def job():
        pending = await first_part(notes, claude)
        desk = FakeDesk(FOREVER if kind == "stopped" else "approved")
        work = asyncio.create_task(
            see_approval_through(desk, notes, resolve_job("kumo"), pending, memory, clock=quarter_second_clock(), wall=ticking_wall(ASKED_AT + 12))
        )
        if kind == "stopped":
            await desk.waiting.wait()
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            return
        await work
        if kind == "undo":
            sent.clear()
            await undo_last_sync(EventNotes(lambda message, tags: sent.append(json.loads(message))), resolve_job("kumo"), memory, clock=quarter_second_clock())

    asyncio.run(job())
    assert sites.commands[-1] == {"command": "close"}
    job_id = UNDO_JOB_ID if kind == "undo" else JOB_ID
    return {"input": {"client_id": "kumo"}, "jobId": job_id, "notes": sent}


@pytest.mark.parametrize("kind", ["approved", "decoy", "stopped", "undo"])
def test_the_notes_after_the_approval_are_exactly_the_contract_the_screen_reads(monkeypatch, kind):
    produced = produce(kind, monkeypatch)
    path = CONTRACTS / f"{kind}_job_notes.json"
    if os.environ.get(RECORD_VARIABLE) == "1":
        path.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(path.read_text())


def test_the_approved_contract_starts_with_the_first_run_contract(monkeypatch):
    first_run = json.loads((CONTRACTS / "first_run_notes.json").read_text())
    approved = json.loads((CONTRACTS / "approved_job_notes.json").read_text())

    assert approved["notes"][: len(first_run["notes"])] == first_run["notes"]


def test_the_decoy_contract_was_written_with_the_decoy_button(monkeypatch):
    decoy = json.loads((CONTRACTS / "decoy_job_notes.json").read_text())
    events = [note["event"] for note in decoy["notes"]]

    assert events[-1]["reason"] == "verify-mismatch"
    assert Locator("button", DECOY, "/inventory").name == DECOY
