import asyncio
import json
import os
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from clientops import cost
from clientops.approval import see_approval_through
from clientops.boundary import resolve_job
from clientops.cost import CostLedger, JobMeter, close_job, open_job
from clientops.records import EventNotes
from approval_fakes import FakeDesk, MemoryStore, ticking_wall
from picking_claude import PickingClaude
from test_approval_contract import ASKED_AT, JOB_ID, first_part
from test_stock_sync import data_dir, install_sites, quarter_second_clock

__all__ = ["data_dir"]

CONTRACTS = Path(__file__).parent / "contract"
RECORD_VARIABLE = "CLIENTOPS_RECORD_CONTRACTS"
NOV_14 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC).timestamp()


@pytest.fixture(autouse=True)
def fresh_books(monkeypatch):
    monkeypatch.setattr(cost, "open_jobs", {})


def produce(monkeypatch, ledger_dir: Path) -> dict:
    sites = install_sites(monkeypatch)
    sent: list[dict] = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)))
    ticks = count()

    async def job() -> dict:
        meter = open_job(
            JobMeter("kumo", JOB_ID, ledger=CostLedger(ledger_dir), cap=lambda: 10.0, estimated=False, clock=lambda: next(ticks) * 30, wall=lambda: NOV_14),
            notes,
        )
        pending = await first_part(notes, PickingClaude())
        meter.end_phase()
        await see_approval_through(FakeDesk("approved"), notes, resolve_job("kumo"), pending, MemoryStore(), clock=quarter_second_clock(), wall=ticking_wall(ASKED_AT + 12))
        payload = {"status": "succeeded", "result": {"status": "done", "events": notes.count}}
        close_job(JOB_ID, payload)
        return payload

    payload = asyncio.run(job())
    assert sites.commands[-1] == {"command": "close"}
    return {"input": {"client_id": "kumo"}, "jobId": JOB_ID, "events": payload["result"]["events"], "notes": sent}


def test_the_notes_of_a_billed_job_are_exactly_the_contract_the_screen_reads(monkeypatch, tmp_path):
    produced = produce(monkeypatch, tmp_path)
    path = CONTRACTS / "cost_job_notes.json"
    if os.environ.get(RECORD_VARIABLE) == "1":
        path.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(path.read_text())


def test_the_billed_contract_is_the_approved_contract_plus_one_cost_note():
    approved = json.loads((CONTRACTS / "approved_job_notes.json").read_text())["notes"]
    billed = json.loads((CONTRACTS / "cost_job_notes.json").read_text())

    assert billed["notes"][:-1] == approved
    assert billed["events"] == len(billed["notes"]) == len(approved) + 1
    assert billed["notes"][-1] == {
        "n": len(approved) + 1,
        "event": {
            "type": "cost.recorded",
            "claudeCalls": 8,
            "inputTokens": 41600,
            "outputTokens": 5120,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
            "claudeUsd": 0.2688,
            "computerSeconds": 60,
            "estimated": False,
        },
    }
