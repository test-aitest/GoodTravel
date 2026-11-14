import asyncio
import importlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from clientops.boundary import known_client, resolve_job
from clientops.procedure import PROCEDURE_KEY
from clientops.approval import PENDING_PREFIX, PendingWrite
from clientops.schedule import SCHEDULED_REQUEST, SCHEDULER_VARIABLE, SCHEDULES_FILE, RunLedger, Scheduler
from approval_fakes import MemoryStore
from test_approval import pending
from test_node_approval import Seams
from test_replay import DAY2_CHANGES, KUMO_DAY2, kumo_procedure
from test_stock_sync import data_dir, install_sites

__all__ = ["data_dir"]

AT_18_00_TOKYO = datetime(2026, 11, 14, 9, 0, 12, tzinfo=UTC)
UNREACHABLE = "http://127.0.0.1:9"


def load_node():
    return importlib.import_module("clientops.node")


def turn_on_kumo_at_18(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / SCHEDULES_FILE).write_text(json.dumps({"version": 1, "clients": {"kumo": {"isOn": True, "time": "18:00", "timeZone": "Asia/Tokyo"}}}))


def agentfield_says(monkeypatch, node, statuses: dict[str, str] | None = None, default: str | None = "waiting") -> list[str]:
    asked: list[str] = []

    async def execution_status(job_id):
        asked.append(job_id)
        return (statuses or {}).get(job_id, default)

    monkeypatch.setattr(node, "execution_status", execution_status)
    return asked


def away_from_the_shared_agentfield(monkeypatch, node) -> None:
    monkeypatch.setattr(node.app, "agentfield_server", UNREACHABLE)


def with_saved_steps(seams: Seams) -> None:
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[PROCEDURE_KEY] = kumo_procedure().to_json()


def test_a_due_slot_runs_the_saved_steps_up_to_the_approval_and_writes_nothing_while_it_waits(monkeypatch, data_dir):
    turn_on_kumo_at_18(data_dir)
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    store_before = [dict(row) for row in sites.store_rows]
    node = load_node()
    away_from_the_shared_agentfield(monkeypatch, node)
    seams = Seams(monkeypatch, node)
    with_saved_steps(seams)
    agentfield_says(monkeypatch, node)

    async def scenario():
        jobs: list[asyncio.Task] = []

        async def start_in_process(client, request):
            jobs.append(asyncio.create_task(node.run_job(client_id=client.id, client_name=client.name, request=request)))
            return f"exec_scheduled_{len(jobs)}"

        def scheduler(at):
            return Scheduler(has_saved_steps=node.saved_steps_for, waiting_job=node.waiting_job_for, start=start_in_process, now=lambda: at)

        first = await scheduler(AT_18_00_TOKYO).tick()
        await asyncio.wait_for(seams.desk.waiting.wait(), timeout=30)
        again = await scheduler(AT_18_00_TOKYO).tick()
        next_day = await scheduler(AT_18_00_TOKYO + timedelta(days=1)).tick()
        still_waiting = not jobs[0].done()
        while_waiting = list(seams.events)
        waiting_ids = [PendingWrite.from_json(value).job_id for key, value in seams.memories["kumo"].values.items() if key.startswith(PENDING_PREFIX)]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        return first, again, next_day, still_waiting, len(jobs), while_waiting, waiting_ids

    first, again, next_day, still_waiting, started, events, waiting_ids = asyncio.run(scenario())

    assert [(outcome.client_id, outcome.status, outcome.job_id, outcome.reason) for outcome in first] == [("kumo", "started", "exec_scheduled_1", None)]
    assert again == [] and started == 1
    assert [(outcome.client_id, outcome.status, outcome.reason) for outcome in next_day] == [("kumo", "skipped", "waiting-for-approval")]
    assert still_waiting
    assert events[0]["type"] == "job.created" and events[0]["run"] == {"kind": "replay", "procedureVersion": 1}
    assert events[0]["request"] == SCHEDULED_REQUEST
    approval = next(event for event in events if event["type"] == "approval.requested")
    assert approval["changes"] == DAY2_CHANGES
    assert events[-1]["type"] == "approval.requested"
    assert seams.claude.calls == 0
    assert len(seams.desk.paused) == 1
    assert {"write.done", "verify.done"}.isdisjoint(event["type"] for event in events)
    assert sites.store_rows == store_before
    started_record, skipped_record = RunLedger().records("kumo")
    assert started_record["status"] == "started" and started_record["jobId"] == "exec_scheduled_1" and started_record["slot"] == "2026-11-14T09:00:00Z"
    assert skipped_record["slot"] == "2026-11-15T09:00:00Z" and skipped_record["reason"] == "waiting-for-approval"
    assert [skipped_record["waitingJobId"]] == waiting_ids
    assert "jobId" not in skipped_record


def test_without_saved_steps_the_slot_starts_no_job(monkeypatch, data_dir):
    turn_on_kumo_at_18(data_dir)
    install_sites(monkeypatch)
    node = load_node()
    away_from_the_shared_agentfield(monkeypatch, node)
    seams = Seams(monkeypatch, node)
    started: list[str] = []

    async def start(client, request):
        started.append(client.id)
        return "exec_never"

    [outcome] = asyncio.run(Scheduler(has_saved_steps=node.saved_steps_for, waiting_job=node.waiting_job_for, start=start, now=lambda: AT_18_00_TOKYO, clients=lambda: (known_client("kumo"),)).tick())

    assert (outcome.status, outcome.reason) == ("skipped", "no-saved-steps")
    assert started == [] and seams.written == []


def test_the_scheduled_start_asks_agentfield_for_run_job_like_the_screen(monkeypatch):
    node = load_node()
    sent: list[tuple] = []

    def control_plane_call(method, path, body=None):
        sent.append((method, path, body))
        return 200, {"execution_id": "exec_from_agentfield", "run_id": "run_1"}

    monkeypatch.setattr(node, "control_plane_call", control_plane_call)

    job_id = asyncio.run(node.start_scheduled_job(known_client("kumo"), SCHEDULED_REQUEST))

    assert job_id == "exec_from_agentfield"
    assert sent == [("POST", "/api/v1/execute/async/clientops.run_job", {"input": {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": SCHEDULED_REQUEST}})]


def test_the_worker_keeps_the_schedule_only_when_switched_on(monkeypatch):
    node = load_node()
    kept: list[Scheduler] = []

    async def keep(scheduler, **_):
        kept.append(scheduler)
        await asyncio.Event().wait()

    async def resume():
        return None

    monkeypatch.setattr(node, "keep_schedule", keep)
    monkeypatch.setattr(node, "resume_when_ready", resume)

    async def start_and_stop():
        async with node.resume_on_start(None):
            await asyncio.sleep(0)

    monkeypatch.setenv(SCHEDULER_VARIABLE, "on")
    asyncio.run(start_and_stop())
    assert kept == [node.scheduler]
    assert node.scheduler.start is node.start_scheduled_job and node.scheduler.has_saved_steps is node.saved_steps_for
    assert node.scheduler.waiting_job is node.waiting_job_for

    kept.clear()
    monkeypatch.delenv(SCHEDULER_VARIABLE)
    asyncio.run(start_and_stop())
    assert kept == []


def test_the_node_finds_this_clients_run_waiting_for_approval_in_its_memory_and_asks_agentfield_if_it_still_waits(monkeypatch):
    node = load_node()
    seams = Seams(monkeypatch, node)
    memory = seams.memories.setdefault("kumo", MemoryStore())
    memory.values[f"{PENDING_PREFIX}approval-1"] = replace(pending(), job_id="exec_finished").to_json()
    memory.values[f"{PENDING_PREFIX}approval-2"] = replace(pending(), job_id="exec_waiting").to_json()
    asked = agentfield_says(monkeypatch, node, {"exec_finished": "succeeded", "exec_waiting": "waiting"}, default=None)

    assert asyncio.run(node.waiting_job_for(resolve_job("kumo"))) == "exec_waiting"
    assert "exec_waiting" in asked
    assert asyncio.run(node.waiting_job_for(resolve_job("hinata"))) is None
