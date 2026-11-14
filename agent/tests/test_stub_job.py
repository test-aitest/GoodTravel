import asyncio
import json
from itertools import count
from pathlib import Path

from clientops.records import EventNotes
from clientops.stub_job import run_stub_job

contract = json.loads((Path(__file__).parent / "contract" / "stub_job_notes.json").read_text())


def quarter_second_clock():
    ticks = count()
    return lambda: next(ticks) * 0.25


def recorded_notes():
    sent = []
    return sent, EventNotes(lambda message, tags: sent.append(json.loads(message)))


def skill_calls(replay_ok: dict[str, bool], refind_found: dict[str, bool]):
    calls = []

    async def call_skill(name: str, **arguments):
        calls.append((name, arguments))
        step_id = arguments["step_id"]
        if name == "replay_script":
            return {"ok": replay_ok.get(step_id, True)}
        if name == "refind_by_tree":
            return {"found": refind_found.get(step_id, True)}
        raise AssertionError(name)

    return calls, call_skill


def run(call_skill, notes):
    return asyncio.run(
        run_stub_job(
            notes,
            call_skill,
            client_name=contract["input"]["client_name"],
            request=contract["input"]["request"],
            clock=quarter_second_clock(),
        )
    )


def test_the_stub_job_writes_exactly_the_contract_notes():
    sent, notes = recorded_notes()
    calls, call_skill = skill_calls({"read-supplier": False}, {})

    result = run(call_skill, notes)

    assert sent == contract["notes"]
    assert result == {"status": "done", "steps": 3}
    assert calls == [
        ("replay_script", {"step_id": "open-computer"}),
        ("replay_script", {"step_id": "read-supplier"}),
        ("refind_by_tree", {"step_id": "read-supplier"}),
        ("replay_script", {"step_id": "read-store"}),
    ]


def test_a_step_neither_skill_can_do_fails_the_job_at_that_step():
    sent, notes = recorded_notes()
    _, call_skill = skill_calls({"read-store": False}, {"read-store": False})

    result = run(call_skill, notes)

    assert result == {"status": "failed", "stepId": "read-store"}
    last = sent[-1]["event"]
    assert last["type"] == "job.failed"
    assert last["reason"] == "error"
    assert last["stepId"] == "read-store"
    assert [note["event"]["type"] for note in sent].count("step.done") == 2
