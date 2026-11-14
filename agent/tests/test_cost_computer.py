import asyncio
import json
from datetime import UTC, datetime

import pytest

from clientops import cost
from clientops.cost import CostLedger, JobMeter, finish_job, open_job
from clientops.records import EventNotes

NOV_14 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC).timestamp()
AUTO_STOP = 15 * 60


@pytest.fixture(autouse=True)
def fresh_books(monkeypatch):
    monkeypatch.setattr(cost, "open_jobs", {})


class FakeStop:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.answers.pop(0)


def ticking(*times: float):
    values = iter(times)
    return lambda: next(values)


def on_daytona(tmp_path, clock, stop=None, *, client_id="kumo", job_id="exec_1"):
    return JobMeter(
        client_id,
        job_id,
        ledger=CostLedger(tmp_path),
        cap=lambda: 10.0,
        estimated=False,
        clock=clock,
        wall=lambda: NOV_14,
        stop_computer=stop or FakeStop(True),
        auto_stop_seconds=AUTO_STOP,
    )


def test_when_a_job_ends_its_computer_is_stopped_and_counted_until_the_stop(tmp_path):
    stop = FakeStop(True)
    job = on_daytona(tmp_path, ticking(0, 40), stop)
    job.computer_opened()

    job.release_computer(others_using=False)
    total = job.close()

    assert stop.calls == 1
    assert (total.computer_seconds, total.estimated) == (40, False)


def test_a_short_wait_for_approval_counts_as_running_time(tmp_path):
    job = on_daytona(tmp_path, ticking(0, 30, 330, 360))
    job.computer_opened()
    job.end_phase()
    job.computer_opened()
    job.release_computer(others_using=False)
    total = job.close()
    assert (total.computer_seconds, total.estimated) == (30 + 300 + 30, False)


def test_a_wait_longer_than_the_auto_stop_counts_fifteen_minutes_as_an_estimate(tmp_path):
    job = on_daytona(tmp_path, ticking(0, 30, 30 + 3600, 30 + 3600 + 30))
    job.computer_opened()
    job.end_phase()
    job.computer_opened()
    job.release_computer(others_using=False)
    total = job.close()
    assert (total.computer_seconds, total.estimated) == (30 + AUTO_STOP + 30, True)


def test_a_job_that_ends_while_waiting_stops_the_still_running_computer_and_counts_the_wait(tmp_path):
    stop = FakeStop(True)
    job = on_daytona(tmp_path, ticking(0, 30, 230), stop)
    job.computer_opened()
    job.end_phase()
    job.release_computer(others_using=False)
    total = job.close()
    assert stop.calls == 1
    assert (total.computer_seconds, total.estimated) == (30 + 200, False)


def test_a_job_that_ends_after_the_auto_stop_counts_fifteen_minutes_as_an_estimate(tmp_path):
    stop = FakeStop(False)
    job = on_daytona(tmp_path, ticking(0, 30, 30 + 2000), stop)
    job.computer_opened()
    job.end_phase()
    job.release_computer(others_using=False)
    total = job.close()
    assert stop.calls == 1
    assert (total.computer_seconds, total.estimated) == (30 + AUTO_STOP, True)


def test_a_stop_that_did_not_happen_leaves_the_computer_to_the_auto_stop(tmp_path):
    job = on_daytona(tmp_path, ticking(0, 40), FakeStop(False))
    job.computer_opened()
    job.release_computer(others_using=False)
    total = job.close()
    assert (total.computer_seconds, total.estimated) == (40 + AUTO_STOP, True)


def test_a_job_that_never_opened_the_computer_does_not_stop_it(tmp_path):
    stop = FakeStop()
    job = on_daytona(tmp_path, ticking(), stop)
    job.release_computer(others_using=False)
    assert job.close().computer_seconds == 0
    assert stop.calls == 0


def test_the_computer_is_not_stopped_while_another_job_of_the_client_uses_it(tmp_path):
    stop = FakeStop()
    job = on_daytona(tmp_path, ticking(0, 40), stop)
    job.computer_opened()
    job.release_computer(others_using=True)
    total = job.close()
    assert stop.calls == 0
    assert (total.computer_seconds, total.estimated) == (40, False)


def test_a_job_held_for_the_next_start_leaves_the_computer_to_the_auto_stop(tmp_path):
    stop = FakeStop()
    job = on_daytona(tmp_path, ticking(0, 30), stop)
    job.computer_opened()
    job.end_phase()
    part = job.suspend()
    assert stop.calls == 0
    assert (part.computer_seconds, part.estimated) == (30 + AUTO_STOP, True)


def test_the_local_computer_is_never_stopped_and_has_no_auto_stop_time(tmp_path):
    job = JobMeter("kumo", "exec_1", ledger=CostLedger(tmp_path), cap=lambda: 10.0, estimated=True, clock=ticking(0, 30, 5000, 5010), wall=lambda: NOV_14)
    job.computer_opened()
    job.end_phase()
    job.computer_opened()
    job.release_computer(others_using=False)
    total = job.close()
    assert (total.computer_seconds, total.estimated) == (40, True)


def test_finishing_a_job_stops_its_computer_then_writes_its_bill(tmp_path):
    sent = []
    stop = FakeStop(True)

    async def job():
        meter = open_job(on_daytona(tmp_path, ticking(0, 40), stop), EventNotes(lambda message, tags: sent.append(json.loads(message)), start=5))
        meter.computer_opened()
        payload = {"status": "succeeded", "result": {"events": 5}}
        await finish_job("exec_1", payload)
        return payload

    payload = asyncio.run(job())

    assert stop.calls == 1
    assert sent[-1]["event"]["computerSeconds"] == 40
    assert payload["result"]["events"] == 6
    assert "exec_1" not in cost.open_jobs


def test_finishing_a_job_leaves_the_computer_running_for_another_job_of_the_same_client(tmp_path):
    stop = FakeStop()

    async def jobs():
        other = open_job(on_daytona(tmp_path, ticking(0), stop, job_id="exec_2"), EventNotes(lambda message, tags: None))
        other.computer_opened()
        mine = open_job(on_daytona(tmp_path, ticking(10, 50), stop), EventNotes(lambda message, tags: None))
        mine.computer_opened()
        return await finish_job("exec_1")

    total = asyncio.run(jobs())

    assert stop.calls == 0
    assert total.computer_seconds == 40


def test_a_waiting_job_of_the_same_client_does_not_keep_the_computer_running(tmp_path):
    stop = FakeStop(True)

    async def jobs():
        waiting = open_job(on_daytona(tmp_path, ticking(0, 5), FakeStop(), job_id="exec_2"), EventNotes(lambda message, tags: None))
        waiting.computer_opened()
        waiting.end_phase()
        mine = open_job(on_daytona(tmp_path, ticking(10, 50), stop), EventNotes(lambda message, tags: None))
        mine.computer_opened()
        return await finish_job("exec_1")

    asyncio.run(jobs())

    assert stop.calls == 1


def test_finishing_a_job_that_is_not_open_does_nothing():
    assert asyncio.run(finish_job("exec_unknown")) is None
