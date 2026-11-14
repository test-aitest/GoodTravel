import asyncio
import json
import os
import shlex
from datetime import date

import pytest

from clientops.approval import UNDO_KEY, undo_last_sync, write_approved
from clientops.boundary import COMPUTERS_VARIABLE, DATA_DIR_VARIABLE, resolve_job
from clientops.local_computer import LOCAL_RUNNER_COMMAND
from clientops.page import Page
from clientops.records import EventNotes
from approval_fakes import MemoryStore
from runner_replay import RUNNER_RECORDINGS, ReplayComputer
from test_approval import CHANGES, DECOY_BUTTON, pending
from test_first_run_integration import ALL_SWITCHES_OFF, STORE, SUPPLIER, control, logins_of, missing, put_local_vault, reset_sites
from test_stock_sync import data_dir

__all__ = ["data_dir"]

RECORD_VARIABLE = "CLIENTOPS_RECORD_RUNNER"
SESSIONS = ("kumo-write-approved", "kumo-undo", "kumo-write-decoy")


class Transcript:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.notes = EventNotes(lambda message, tags: self.sent.append(json.loads(message)))

    @property
    def events(self) -> list[dict]:
        return [note["event"] for note in self.sent]


def replaying(name: str):
    computer = ReplayComputer(name)
    return computer, (lambda: Page(computer))


def ticks():
    moments = iter(range(10_000))
    return lambda: next(moments) * 0.25


def test_the_recorded_approved_write_fills_our_twelve_numbers_saves_once_and_reads_twelve_of_twelve_back():
    computer, open_page = replaying("kumo-write-approved")
    transcript = Transcript()
    memory = MemoryStore()

    result = asyncio.run(write_approved(transcript.notes, resolve_job("kumo"), pending(), memory, open_page=open_page, clock=ticks()))

    assert result == {"status": "done", "written": 12, "checked": 12, "matched": 12, "maySaveProcedure": True, "saveStatus": "Saved. 12 products updated."}
    sent = [json.loads(shlex.split(command)[-1]) for command, _, _ in computer.runs]
    fills = [command for command in sent if command["command"] == "fill"]
    assert [command["text"] for command in fills[:2]] == ["$LP_STORE_USERNAME", "$LP_STORE_PASSWORD"]
    assert {command["target"]["name"]: command["text"] for command in fills[2:]} == {f"Available for {change.name}": str(change.supplier) for change in CHANGES}
    assert [command["target"]["name"] for command in sent if command["command"] == "click"] == ["Sign in", "Save changes"]
    assert sent[-1] == {"command": "close"}
    assert [event for event in transcript.events if event["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "save", "checked": 12, "matched": 12}]
    assert memory.values[UNDO_KEY]["rows"][0] == {"sku": CHANGES[0].sku, "name": CHANGES[0].name, "before": CHANGES[0].store, "after": CHANGES[0].supplier}


def test_the_recorded_decoy_write_is_a_mismatch_and_keeps_no_steps():
    _, open_page = replaying("kumo-write-decoy")
    transcript = Transcript()

    result = asyncio.run(write_approved(transcript.notes, resolve_job("kumo"), pending(save_button=DECOY_BUTTON), MemoryStore(), open_page=open_page, clock=ticks()))

    assert result["status"] == "failed"
    assert result["reason"] == "verify-mismatch"
    assert result["maySaveProcedure"] is False
    verified, failed = transcript.events[-2:]
    assert verified["checked"] == 12 and verified["matched"] == 3
    assert failed["type"] == "job.failed" and failed["reason"] == "verify-mismatch"


def test_the_recorded_undo_writes_the_old_numbers_back_and_reads_twelve_of_twelve():
    _, write_page = replaying("kumo-write-approved")
    memory = MemoryStore()
    asyncio.run(write_approved(Transcript().notes, resolve_job("kumo"), pending(), memory, open_page=write_page, clock=ticks()))
    computer, undo_page = replaying("kumo-undo")
    transcript = Transcript()

    result = asyncio.run(undo_last_sync(transcript.notes, resolve_job("kumo"), memory, open_page=undo_page, clock=ticks()))

    assert result == {"status": "done", "written": 12, "checked": 12, "matched": 12}
    fills = [json.loads(shlex.split(command)[-1]) for command, _, _ in computer.runs]
    typed = {command["target"]["name"]: command["text"] for command in fills if command["command"] == "fill"}
    assert {name: text for name, text in typed.items() if name.startswith("Available for ")} == {f"Available for {change.name}": str(change.store) for change in CHANGES}
    assert UNDO_KEY not in memory.values


def test_the_recordings_hold_no_login_values():
    values = {"kumo-admin", "kumo-store-demo", "kumo-roasters", "kumo-supply-demo"}
    for name in SESSIONS:
        text = (RUNNER_RECORDINGS / f"{name}.json").read_text()
        assert [value for value in values if value in text] == []


class RecordingComputer:
    def __init__(self, computer) -> None:
        self.computer = computer
        self.client_id = computer.client_id
        self.exchanges: list[dict] = []

    def run(self, command, *, timeout_seconds, env=None):
        result = self.computer.run(command, timeout_seconds=timeout_seconds, env=env)
        self.exchanges.append({"command": json.loads(shlex.split(command)[-1]), "exitCode": result.exit_code, "output": result.output})
        return result

    def upload(self, content, remote_path):
        self.computer.upload(content, remote_path)


def save_recording(name: str, description: str, recorder: RecordingComputer) -> None:
    data = {
        "description": description,
        "observed": {"date": date.today().isoformat(), "lightpanda": "1.0.4", "recordedBy": f"{RECORD_VARIABLE}=1 pytest tests/test_approval_replay.py"},
        "exchanges": recorder.exchanges,
    }
    (RUNNER_RECORDINGS / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@pytest.mark.skipif(os.environ.get(RECORD_VARIABLE) != "1" or missing() is not None, reason=f"set {RECORD_VARIABLE}=1 with Lightpanda and the sites running to record again")
def test_record_the_runner_sessions_again(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path / "data"))
    monkeypatch.setenv(COMPUTERS_VARIABLE, "local")
    put_local_vault("kumo")
    assert set(logins_of("kumo").values()) == {"kumo-admin", "kumo-store-demo", "kumo-roasters", "kumo-supply-demo"}
    boundary = resolve_job("kumo")
    memory = MemoryStore()

    def recorded(recorder):
        return lambda: Page(recorder, runner_command=LOCAL_RUNNER_COMMAND)

    async def record():
        reset_sites()
        recorder = RecordingComputer(boundary.local_computer())
        await write_approved(EventNotes(lambda message, tags: None), boundary, pending(), memory, open_page=recorded(recorder))
        save_recording("kumo-write-approved", "The approved write on Kumo day 1 (12 changes, Save changes), recorded from write_approved against real Lightpanda and the demo sites.", recorder)
        recorder = RecordingComputer(boundary.local_computer())
        await undo_last_sync(EventNotes(lambda message, tags: None), boundary, memory, open_page=recorded(recorder))
        save_recording("kumo-undo", "The undo right after kumo-write-approved, recorded from undo_last_sync against real Lightpanda and the demo sites.", recorder)
        reset_sites()
        for site in (SUPPLIER, STORE):
            control(site, "/__control/switches", {**ALL_SWITCHES_OFF, "decoyButton": True})
        recorder = RecordingComputer(boundary.local_computer())
        await write_approved(EventNotes(lambda message, tags: None), boundary, pending(save_button=DECOY_BUTTON), MemoryStore(), open_page=recorded(recorder))
        save_recording("kumo-write-decoy", "The same approved write pressing the decoy button (Save changes to all locations), recorded against real Lightpanda and the demo sites.", recorder)
        reset_sites()

    asyncio.run(record())
