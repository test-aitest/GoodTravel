import asyncio
import importlib
import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from clientops.procedure import PROCEDURE_KEY
from clientops.approval import PENDING_PREFIX, PendingWrite
from clientops.boundary import data_directory
from clientops.schedule import SCHEDULED_REQUEST, SCHEDULES_FILE, RunLedger, Scheduler
from approval_fakes import FakeDesk, MemoryStore
from test_first_run_integration import HOW_TO_START, KUMO, local_computers, logins, missing, runner_sockets
from test_replay import kumo_procedure

__all__ = ["local_computers", "logins"]

pytestmark = pytest.mark.skipif(missing() is not None, reason=f"{missing()}: {HOW_TO_START}")

AT_18_00_TOKYO = datetime(2026, 11, 14, 9, 0, 12, tzinfo=UTC)


def test_a_due_slot_in_process_runs_the_saved_steps_on_the_local_computer_and_waits_for_approval(monkeypatch, logins):
    node = importlib.import_module("clientops.node")
    monkeypatch.setattr(node.app, "agentfield_server", "http://127.0.0.1:9")
    transcript: list[str] = []
    desk = FakeDesk()
    memory = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: transcript.append(message))
    monkeypatch.setattr(node, "approval_desk", lambda agent: desk)
    monkeypatch.setattr(node, "client_memory", lambda boundary, context: memory)
    monkeypatch.setattr(node, "claude_ai", lambda agent: pytest.fail("a scheduled run must not use Claude"))
    sockets_before = runner_sockets()
    data_directory().mkdir(parents=True, exist_ok=True)
    (data_directory() / SCHEDULES_FILE).write_text(json.dumps({"version": 1, "clients": {"kumo": {"isOn": True, "time": "18:00", "timeZone": "Asia/Tokyo"}}}))

    async def still_waiting_in_agentfield(job_id):
        return "waiting"

    monkeypatch.setattr(node, "execution_status", still_waiting_in_agentfield)

    async def scenario():
        jobs: list[asyncio.Task] = []

        async def start_in_process(client, request):
            jobs.append(asyncio.create_task(node.run_job(client_id=client.id, client_name=client.name, request=request)))
            return f"exec_scheduled_{len(jobs)}"

        def scheduler(at):
            return Scheduler(has_saved_steps=node.saved_steps_for, waiting_job=node.waiting_job_for, start=start_in_process, now=lambda: at)

        first = await scheduler(AT_18_00_TOKYO).tick()
        started_at = time.monotonic()
        await asyncio.wait_for(desk.waiting.wait(), timeout=60)
        ready_in = time.monotonic() - started_at
        after_restart = await scheduler(AT_18_00_TOKYO).tick()
        next_day = await scheduler(AT_18_00_TOKYO + timedelta(days=1)).tick()
        waiting = not jobs[0].done()
        while_waiting = list(transcript)
        waiting_ids = [PendingWrite.from_json(value).job_id for key, value in memory.values.items() if key.startswith(PENDING_PREFIX)]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        return first, after_restart, next_day, waiting, ready_in, while_waiting, waiting_ids

    first, after_restart, next_day, waiting, ready_in, while_waiting, waiting_ids = asyncio.run(scenario())

    events = [json.loads(message)["event"] for message in while_waiting]
    assert [(outcome.client_id, outcome.status) for outcome in first] == [("kumo", "started")]
    assert after_restart == []
    assert [(outcome.client_id, outcome.status, outcome.reason) for outcome in next_day] == [("kumo", "skipped", "waiting-for-approval")]
    assert waiting
    assert events[0]["type"] == "job.created" and events[0]["run"]["kind"] == "replay" and events[0]["request"] == SCHEDULED_REQUEST
    assert {event["rung"] for event in events if event["type"] == "step.decided"} == {"script"}
    [approval] = [event for event in events if event["type"] == "approval.requested"]
    assert approval["changes"] == KUMO["changes"]
    assert events[-1]["type"] == "approval.requested"
    assert len(desk.paused) == 1
    assert {"write.done", "verify.done"}.isdisjoint(event["type"] for event in events)
    everything = "\n".join(transcript)
    for value in logins.values():
        assert value not in everything
    assert runner_sockets() - sockets_before == set()
    started_record, skipped_record = RunLedger().records("kumo")
    assert started_record["status"] == "started" and started_record["jobId"] == "exec_scheduled_1"
    assert skipped_record["status"] == "skipped" and [skipped_record["waitingJobId"]] == waiting_ids
    print(f"\nscheduled slot to approval: {ready_in * 1000:.0f} ms on the local computer")
