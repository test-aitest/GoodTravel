import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count

import pytest
from agentfield import Agent

from clientops import cost, externals
from clientops.approval import PENDING_PREFIX
from clientops.boundary import COMPUTERS_VARIABLE, data_directory, resolve_job
from clientops.cost import Budget, ClaudeBill, CostLedger, JobCost, JobMeter, LedgerEntry
from approval_fakes import FOREVER, MemoryStore
from test_node_approval import AFTER_APPROVAL, Seams, in_execution, load_node, waiting_record
from test_stock_sync import FIRST_RUN_EVENTS, REQUEST, data_dir, install_sites, write_settings

__all__ = ["data_dir"]

NOV_14 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC).timestamp()
STEP_SECONDS = 30
KUMO_JOB = {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": REQUEST}


@pytest.fixture(autouse=True)
def fresh_books(monkeypatch):
    monkeypatch.setattr(cost, "open_jobs", {})


def as_agentfield_runs(reasoner):
    return reasoner._original_func


def steady_meters(node, monkeypatch, *, cap=10.0):
    def cost_meter(boundary, job_id, *, resumed_since=None):
        ticks = count()
        return JobMeter(
            boundary.client.id,
            job_id,
            ledger=CostLedger(data_directory()),
            cap=lambda: cap,
            estimated=False,
            clock=lambda: next(ticks) * STEP_SECONDS,
            wall=lambda: NOV_14,
            resumed_since=resumed_since,
        )

    monkeypatch.setattr(node, "cost_meter", cost_meter)


def captured_posts(monkeypatch) -> list:
    posted = []

    async def post(self, callback_url, payload, execution_id, max_retries=5):
        posted.append((execution_id, json.loads(json.dumps(payload))))

    monkeypatch.setattr(Agent, "_post_execution_status", post)
    return posted


def report(node, payload, execution_id="exec_kumo_1"):
    asyncio.run(node.app._post_execution_status("http://localhost:8001/callback", payload, execution_id))


def expected_claude(result) -> dict:
    calls = [call for step in result["claudeUsage"].values() for call in step]
    return {
        "claudeCalls": len(calls),
        "inputTokens": sum(call["usage"]["prompt_tokens"] - call["usage"]["cache_read_tokens"] - call["usage"]["cache_creation_tokens"] for call in calls),
        "outputTokens": sum(call["usage"]["completion_tokens"] for call in calls),
        "cacheReadTokens": sum(call["usage"]["cache_read_tokens"] for call in calls),
        "cacheWriteTokens": sum(call["usage"]["cache_creation_tokens"] for call in calls),
        "claudeUsd": float(sum(Decimal(str(call["costUsd"])) for call in calls)),
    }


def test_run_job_writes_no_cost_note_itself(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    steady_meters(node, monkeypatch)

    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))

    assert seams.events[-1]["type"] == "procedure.saved"
    assert result["events"] == len(seams.written)
    assert "exec_kumo_1" in cost.open_jobs


def test_the_bill_is_written_once_when_the_finished_job_is_reported_to_agentfield(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    steady_meters(node, monkeypatch)
    posted = captured_posts(monkeypatch)
    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))
    written = len(seams.written)

    report(node, {"status": "succeeded", "result": result, "execution_id": "exec_kumo_1"})

    assert len(seams.written) == written + 1
    note = seams.written[-1]
    assert note["n"] == written + 1
    assert note["event"] == {
        "type": "cost.recorded",
        **expected_claude(result),
        "computerSeconds": 2 * STEP_SECONDS,
        "estimated": False,
    }
    assert note["event"]["claudeCalls"] == 8
    assert note["event"]["claudeUsd"] == pytest.approx(8 * 0.0336)
    [(execution_id, payload)] = posted
    assert execution_id == "exec_kumo_1"
    assert payload["result"] == {**json.loads(json.dumps(result)), "events": written + 1}
    month = CostLedger(data_directory()).month_total("kumo", "2026-11")
    assert month.claude.usd == Decimal("0.2688")
    assert month.computer_seconds == 2 * STEP_SECONDS
    assert month.jobs == 1


def test_a_job_is_billed_only_once(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    steady_meters(node, monkeypatch)
    posted = captured_posts(monkeypatch)
    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))

    report(node, {"status": "succeeded", "result": dict(result), "execution_id": "exec_kumo_1"})
    report(node, {"status": "succeeded", "result": dict(result), "execution_id": "exec_kumo_1"})

    assert [note["event"]["type"] for note in seams.written].count("cost.recorded") == 1
    assert len(posted) == 2
    assert len(CostLedger(data_directory()).entries("2026-11")) == 1


def test_the_cost_note_names_no_client_and_carries_no_values_from_the_sites(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    steady_meters(node, monkeypatch)
    captured_posts(monkeypatch)
    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))

    report(node, {"status": "succeeded", "result": result, "execution_id": "exec_kumo_1"})

    event = seams.written[-1]["event"]
    assert set(event) == {"type", "claudeCalls", "inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "claudeUsd", "computerSeconds", "estimated"}
    text = json.dumps(event).lower()
    assert "kumo" not in text
    assert "maruyama" not in text


def test_a_stopped_job_still_gets_its_bill(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, FOREVER)
    steady_meters(node, monkeypatch)
    posted = captured_posts(monkeypatch)

    async def run_then_stop():
        task = asyncio.create_task(as_agentfield_runs(node.run_job)(**KUMO_JOB))
        await seams.desk.waiting.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    in_execution(node, run_then_stop)
    report(node, {"status": "cancelled", "execution_id": "exec_kumo_1"})

    assert [note["event"]["type"] for note in seams.written[-2:]] == ["job.failed", "cost.recorded"]
    assert seams.written[-1]["event"]["claudeCalls"] == 8
    assert seams.written[-1]["event"]["computerSeconds"] == STEP_SECONDS
    assert posted == [("exec_kumo_1", {"status": "cancelled", "execution_id": "exec_kumo_1"})]


def test_a_job_held_for_the_next_start_writes_its_part_of_the_bill_and_no_note(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, FOREVER)
    steady_meters(node, monkeypatch)
    posted = captured_posts(monkeypatch)

    async def run_then_shut_down():
        task = asyncio.create_task(as_agentfield_runs(node.run_job)(**KUMO_JOB))
        await seams.desk.waiting.wait()
        seams.desk.stopping_the_agent = True
        task.cancel()
        return await task

    result = in_execution(node, run_then_shut_down)
    written = len(seams.written)
    report(node, {"status": "succeeded", "result": result, "execution_id": "exec_kumo_1"})

    assert result["resumable"] is True
    assert len(seams.written) == written
    assert posted == []
    assert "exec_kumo_1" not in cost.open_jobs
    [part] = CostLedger(data_directory()).entries("2026-11")
    assert part.job_id == "exec_kumo_1"
    assert part.cost.claude.calls == 8
    assert part.cost.computer_seconds == STEP_SECONDS


def test_a_job_picked_up_after_a_restart_is_billed_with_the_part_before_the_restart(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    real_report = node.report_finished
    pending = waiting_record()
    seams = Seams(monkeypatch, node, "approved", statuses=("pending",))
    monkeypatch.setattr(node, "report_finished", real_report)
    steady_meters(node, monkeypatch)
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[f"{PENDING_PREFIX}{pending.approval_id}"] = pending.to_json()
    before = JobCost(ClaudeBill(calls=8, input_tokens=41600, output_tokens=5120, usd=Decimal("0.2688")), 45, False)
    CostLedger(data_directory()).add(LedgerEntry("exec_kumo_1", "kumo", datetime.fromtimestamp(pending.asked_at + 60, UTC), before))
    sent = []

    def control_plane_call(method, path, body=None):
        sent.append((method, path, json.loads(json.dumps(body))))
        return 200, None

    async def execution_status(execution_id):
        return "waiting"

    monkeypatch.setattr(node, "control_plane_call", control_plane_call)
    monkeypatch.setattr(node, "execution_status", execution_status)

    async def restart():
        tasks = await node.resume_waiting_jobs()
        await asyncio.gather(*tasks)

    asyncio.run(restart())

    assert [note["n"] for note in seams.written] == list(range(len(FIRST_RUN_EVENTS) + 1, len(FIRST_RUN_EVENTS) + AFTER_APPROVAL + 2))
    assert seams.written[-1]["event"] == {
        "type": "cost.recorded",
        "claudeCalls": 8,
        "inputTokens": 41600,
        "outputTokens": 5120,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "claudeUsd": 0.2688,
        "computerSeconds": 45 + STEP_SECONDS,
        "estimated": False,
    }
    [(method, path, payload)] = sent
    assert (method, path) == ("POST", "/api/v1/executions/exec_kumo_1/status")
    assert payload["result"]["events"] == len(FIRST_RUN_EVENTS) + AFTER_APPROVAL + 1
    entries = CostLedger(data_directory()).entries("2026-11")
    assert [entry.cost.computer_seconds for entry in entries] == [45, STEP_SECONDS]


def test_undo_is_its_own_job_with_its_own_bill_and_no_claude(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    steady_meters(node, monkeypatch)
    captured_posts(monkeypatch)
    in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))
    seams.written.clear()

    result = in_execution(node, lambda: as_agentfield_runs(node.undo_sync)(client_id="kumo"), execution_id="exec_kumo_undo", run_id="run_kumo_undo")
    report(node, {"status": "succeeded", "result": result, "execution_id": "exec_kumo_undo"}, execution_id="exec_kumo_undo")

    assert seams.written[-1]["event"] == {
        "type": "cost.recorded",
        "claudeCalls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "claudeUsd": 0,
        "computerSeconds": STEP_SECONDS,
        "estimated": False,
    }


def test_a_job_over_the_cap_goes_to_a_person_at_the_first_step_and_is_billed_nothing_for_claude(monkeypatch, data_dir):
    install_sites(monkeypatch)
    write_settings(data_dir, {"kumo": {"claudeMonthlyCapUsd": 0}})
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    captured_posts(monkeypatch)

    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))
    report(node, {"status": "succeeded", "result": result, "execution_id": "exec_kumo_1"})

    assert seams.claude.calls == 0
    assert result["handOff"] == "over-budget"
    failed = next(event for event in seams.events if event["type"] == "job.failed")
    assert failed["message"] == "This would go over this client's monthly Claude budget, so a person needs to do this step. Nothing was changed."
    assert seams.events[-1]["type"] == "cost.recorded"
    assert seams.events[-1]["claudeCalls"] == 0


def test_a_months_spending_from_earlier_jobs_counts_toward_the_cap(monkeypatch, data_dir):
    install_sites(monkeypatch)
    write_settings(data_dir, {"kumo": {"claudeMonthlyCapUsd": 1}})
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    CostLedger(data_directory()).add(
        LedgerEntry("exec_earlier", "kumo", datetime.now(UTC), JobCost(ClaudeBill(calls=20, usd=Decimal("0.96")), 0, False))
    )

    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))

    assert seams.claude.calls == 0
    assert result["handOff"] == "over-budget"


def test_the_node_meters_each_job_with_the_clients_cap_the_data_directorys_ledger_and_the_kind_of_computer(monkeypatch, data_dir):
    write_settings(data_dir, {"kumo": {"claudeMonthlyCapUsd": 0}})
    node = load_node()

    meter = node.cost_meter(resolve_job("kumo"), "exec_1")
    assert (meter.client_id, meter.job_id, meter.estimated) == ("kumo", "exec_1", False)
    assert meter.claude_budget() is Budget.OVER
    meter.close()
    assert (data_dir / "costs").is_dir()

    monkeypatch.setenv(COMPUTERS_VARIABLE, "local")
    assert node.cost_meter(resolve_job("hinata"), "exec_2").estimated is True


def stops_of(daytona) -> int:
    return [call.method for call in daytona.calls].count("stop")


def computer_states(daytona) -> list[str]:
    return [sandbox.state.value for sandbox in daytona.list()]


def test_when_a_job_ends_the_clients_daytona_computer_is_stopped(monkeypatch):
    install_sites(monkeypatch)
    daytona = externals.shared_fake_daytona()
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    captured_posts(monkeypatch)
    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))
    assert computer_states(daytona) == ["started"]

    report(node, {"status": "succeeded", "result": result, "execution_id": "exec_kumo_1"})

    assert stops_of(daytona) == 1
    assert computer_states(daytona) == ["stopped"]
    assert seams.written[-1]["event"]["type"] == "cost.recorded"
    assert seams.written[-1]["event"]["estimated"] is False


def test_while_a_job_waits_for_approval_its_computer_is_left_to_the_auto_stop_and_stopped_when_the_job_is_stopped(monkeypatch):
    install_sites(monkeypatch)
    daytona = externals.shared_fake_daytona()
    node = load_node()
    seams = Seams(monkeypatch, node, FOREVER)
    captured_posts(monkeypatch)
    states_while_waiting = []

    async def run_then_stop():
        task = asyncio.create_task(as_agentfield_runs(node.run_job)(**KUMO_JOB))
        await seams.desk.waiting.wait()
        states_while_waiting.extend(computer_states(daytona))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    in_execution(node, run_then_stop)
    report(node, {"status": "cancelled", "execution_id": "exec_kumo_1"})

    assert states_while_waiting == ["started"]
    assert stops_of(daytona) == 1
    assert computer_states(daytona) == ["stopped"]


def test_a_job_held_for_the_next_start_does_not_stop_the_computer(monkeypatch):
    install_sites(monkeypatch)
    daytona = externals.shared_fake_daytona()
    node = load_node()
    seams = Seams(monkeypatch, node, FOREVER)
    captured_posts(monkeypatch)

    async def run_then_shut_down():
        task = asyncio.create_task(as_agentfield_runs(node.run_job)(**KUMO_JOB))
        await seams.desk.waiting.wait()
        seams.desk.stopping_the_agent = True
        task.cancel()
        return await task

    result = in_execution(node, run_then_shut_down)
    report(node, {"status": "succeeded", "result": result, "execution_id": "exec_kumo_1"})

    assert stops_of(daytona) == 0
    assert computer_states(daytona) == ["started"]
    [part] = CostLedger(data_directory()).entries("2026-11")
    assert part.cost.estimated is True


def test_daytona_jobs_count_the_fifteen_minute_auto_stop_and_local_jobs_do_not(monkeypatch):
    node = load_node()
    assert node.cost_meter(resolve_job("kumo"), "exec_1").auto_stop_seconds == 15 * 60
    monkeypatch.setenv(COMPUTERS_VARIABLE, "local")
    assert node.cost_meter(resolve_job("kumo"), "exec_2").auto_stop_seconds is None


def write_schedules(data_dir, content) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "schedules.json").write_text(content if isinstance(content, str) else json.dumps(content))


def test_the_month_follows_the_time_zone_in_the_clients_schedule(data_dir):
    write_schedules(data_dir, {"version": 1, "clients": {"kumo": {"isOn": False, "time": "18:00", "timeZone": "America/Los_Angeles"}}})
    node = load_node()
    assert node.cost_meter(resolve_job("kumo"), "exec_1").zone.key == "America/Los_Angeles"
    assert node.cost_meter(resolve_job("hinata"), "exec_2").zone.key == "Asia/Tokyo"


def test_when_the_schedules_cannot_be_read_claude_is_not_used_and_the_step_goes_to_a_person(monkeypatch, data_dir):
    install_sites(monkeypatch)
    write_schedules(data_dir, "{not json")
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")

    assert node.cost_meter(resolve_job("kumo"), "exec_1").zone is None
    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(**KUMO_JOB))

    assert seams.claude.calls == 0
    assert result["handOff"] == "budget-unknown"
