import asyncio
import importlib
import json
import re

import pytest

from approval_fakes import FakeDesk, MemoryStore
from picking_claude import PickingClaude
from test_first_run_integration import (
    ALL_SWITCHES_OFF,
    EXPECTED,
    HOW_TO_START,
    KUMO,
    REQUEST,
    STORE,
    SUPPLIER,
    control,
    local_computers,
    logins,
    missing,
    runner_sockets,
)

__all__ = ["local_computers", "logins"]

pytestmark = pytest.mark.skipif(missing() is not None, reason=f"{missing()}: {HOW_TO_START}")

DECOY = "Save changes to all locations"
AFTER_APPROVAL = [
    ("approval.answered", None),
    ("step.decided", "ask"),
    ("step.done", "ask"),
    ("step.started", "save"),
    ("page.seen", "save"),
    ("page.seen", "save"),
    ("write.done", "save"),
    ("verify.done", "save"),
    ("step.done", "save"),
]


def pick_the_decoy(fake, user):
    [decoy] = [entry["id"] for entry in (json.loads(line) for line in user.splitlines() if line.startswith("{")) if entry["name"] == DECOY]
    fake.reply_choice(decoy, "click")


class Seams:
    def __init__(self, monkeypatch, *answers, claude=None) -> None:
        self.node = importlib.import_module("clientops.node")
        self.transcript: list[str] = []
        self.memory = MemoryStore()
        self.claude = claude or PickingClaude()
        monkeypatch.setattr(self.node.app, "note", lambda message, tags=None: self.transcript.append(message))
        monkeypatch.setattr(self.node, "claude_ai", lambda agent: self.claude)
        monkeypatch.setattr(self.node, "approval_desk", lambda agent: FakeDesk(*answers))
        monkeypatch.setattr(self.node, "client_memory", lambda boundary, context: self.memory)

    @property
    def events(self) -> list[dict]:
        return [json.loads(message)["event"] for message in self.transcript]

    def run_job(self) -> dict:
        self.transcript.clear()
        return asyncio.run(self.node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))

    def undo(self) -> dict:
        self.transcript.clear()
        return asyncio.run(self.node.undo_sync(client_id="kumo"))


def shape(events):
    return [(event["type"], event.get("stepId")) for event in events]


def test_approve_writes_the_twelve_changes_on_the_real_store_admin_and_all_twelve_read_back(monkeypatch, logins):
    sockets_before = runner_sockets()
    seams = Seams(monkeypatch, "approved")

    result = seams.run_job()

    events = seams.events
    assert shape(events) == EXPECTED + AFTER_APPROVAL + [("procedure.saved", None)]
    assert [event for event in events if event["type"] == "write.done"] == [{"type": "write.done", "stepId": "save", "written": 12}]
    assert [event for event in events if event["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "save", "checked": 12, "matched": 12}]
    assert result["status"] == "done"
    assert result["maySaveProcedure"] is True
    assert result["saveStatus"] == "Saved. 12 products updated."
    assert {event["host"] for event in events if event["type"] == "page.seen" and event["stepId"] == "save"} == {"admin.kumo-coffee.example"}
    everything = "\n".join(seams.transcript)
    for value in logins.values():
        assert value not in everything
    assert not re.search(r"\d", seams.claude.everything_sent())
    assert runner_sockets() - sockets_before == set()

    again = Seams(monkeypatch, "approved").run_job()
    assert again["status"] == "done"
    assert again["changes"] == 0


def test_the_decoy_button_on_the_real_store_admin_is_caught_by_reading_every_row_back(monkeypatch, logins):
    for site in (SUPPLIER, STORE):
        control(site, "/__control/switches", {**ALL_SWITCHES_OFF, "decoyButton": True})
    seams = Seams(monkeypatch, "approved", claude=PickingClaude(scripts={"saves the changes": [pick_the_decoy]}))

    result = seams.run_job()

    events = seams.events
    assert shape(events) == EXPECTED + AFTER_APPROVAL[:-2] + [("verify.done", "save"), ("job.failed", "save")]
    verified = events[-2]
    assert verified["checked"] == 12 and verified["matched"] < 12
    assert events[-1]["reason"] == "verify-mismatch"
    assert events[-1]["message"].endswith("I stopped and didn't save the steps.")
    assert result["status"] == "failed"
    assert result["maySaveProcedure"] is False
    assert result["saveStatus"].startswith("Saved to all locations.")


def test_undo_writes_back_the_values_from_before_and_reads_every_row(monkeypatch, logins):
    seams = Seams(monkeypatch, "approved")
    seams.run_job()

    result = seams.undo()

    assert result == {"clientId": "kumo", "status": "done", "written": 12, "checked": 12, "matched": 12, "events": len(seams.events)}
    assert [event for event in seams.events if event["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "undo", "checked": 12, "matched": 12}]
    monkeypatch.setattr(seams.node, "approval_desk", lambda agent: FakeDesk("rejected"))
    seams.run_job()
    [approval] = [event for event in seams.events if event["type"] == "approval.requested"]
    assert approval["changes"] == KUMO["changes"]


def test_a_stop_while_waiting_leaves_the_real_store_untouched(monkeypatch, logins):
    seams = Seams(monkeypatch)
    node = seams.node

    async def stop_while_waiting():
        task = asyncio.create_task(node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))
        while not any('"approval.requested"' in message for message in seams.transcript):
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(stop_while_waiting())

    assert seams.events[-1] == {"type": "job.failed", "reason": "stopped", "stepId": "ask", "message": "Stopped before saving. Nothing was changed."}
    monkeypatch.setattr(node, "approval_desk", lambda agent: FakeDesk("rejected"))
    seams.run_job()
    [approval] = [event for event in seams.events if event["type"] == "approval.requested"]
    assert approval["changes"] == KUMO["changes"]
