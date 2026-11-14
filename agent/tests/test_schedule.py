import asyncio
import json
import re
import stat
import urllib.error
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from agentfield.exceptions import MemoryAccessError

from clientops.approval import PENDING_PREFIX
from clientops.boundary import DATA_DIR_VARIABLE, ClientNotSetUp, known_client, resolve_job
from clientops.procedure import PROCEDURE_KEY
from clientops.schedule import (
    DEFAULT_SCHEDULE,
    MISSED_RUN_GRACE,
    RUNS_DIRECTORY,
    SCHEDULED_REQUEST,
    SCHEDULER_VARIABLE,
    SCHEDULES_FILE,
    NothingStarted,
    RunLedger,
    Schedule,
    ScheduleError,
    Scheduler,
    due_slot,
    has_saved_steps,
    keep_schedule,
    latest_slot,
    next_slot,
    read_schedule,
    run_file_name,
    scheduler_enabled,
    slot_on,
    start_through_control_plane,
    waiting_job,
)
from approval_fakes import MemoryStore, MemoryUnavailable
from test_approval import pending
from test_replay import kumo_procedure

CONTRACT = json.loads((Path(__file__).parent / "contract" / "schedule.json").read_text())


def started_by(scripts: dict[str, str], name: str) -> str:
    command = scripts[name]
    called = re.search(r"\bbun (?:--\S+ )*run ([\w:]+)", command)
    return f"{command} {started_by(scripts, called.group(1))}" if called else command


TOKYO = "Asia/Tokyo"
NEW_YORK = "America/New_York"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def daily(at: str = "18:00", zone: str = TOKYO, *, is_on: bool = True, updated_at: str | None = None) -> Schedule:
    hour, minute = (int(part) for part in at.split(":"))
    return Schedule(is_on=is_on, at=time(hour, minute), time_zone=zone, updated_at=None if updated_at is None else utc(updated_at))


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    directory = tmp_path / "clientops-data"
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(directory))
    return directory


def write_schedules(directory: Path, content) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SCHEDULES_FILE).write_text(content if isinstance(content, str) else json.dumps(content))


class Starts:
    def __init__(self, *failures: BaseException) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failures = list(failures)

    async def __call__(self, client, request: str) -> str:
        self.calls.append((client.id, request))
        if self.failures:
            raise self.failures.pop(0)
        return f"exec_{client.id}_{len(self.calls)}"


class Waiting:
    def __init__(self, waiting: dict[str, str] | None = None, *, failing: int = 0) -> None:
        self.waiting = waiting or {}
        self.failing = failing
        self.asked: list[str] = []

    async def __call__(self, boundary) -> str | None:
        self.asked.append(boundary.client.id)
        if self.failing:
            self.failing -= 1
            raise MemoryUnavailable("memory is down")
        return self.waiting.get(boundary.client.id)


class Steps:
    def __init__(self, saved: dict[str, bool] | None = None, *, failing: int = 0) -> None:
        self.saved = saved if saved is not None else {"kumo": True, "hinata": True}
        self.failing = failing
        self.asked: list[str] = []

    async def __call__(self, boundary) -> bool:
        self.asked.append(boundary.client.id)
        if self.failing:
            self.failing -= 1
            raise MemoryUnavailable("memory is down")
        return self.saved.get(boundary.client.id, False)


def scheduler(now: str, *, schedules: dict[str, Schedule] | None = None, steps: Steps | None = None, waiting: Waiting | None = None, starts: Starts | None = None, ledger: RunLedger | None = None, clients=("kumo",)):
    known = schedules or {}
    moment = [utc(now)]
    built = Scheduler(
        has_saved_steps=steps or Steps(),
        waiting_job=waiting or Waiting(),
        start=starts or Starts(),
        ledger=ledger or RunLedger(),
        schedule_of=lambda client_id: known.get(client_id, daily()),
        clients=lambda: tuple(known_client(client_id) for client_id in clients),
        now=lambda: moment[0],
        warn=lambda message: None,
    )
    return built, moment


class TestSlots:
    def test_the_default_is_off_and_turned_on_at_18_00_tokyo_time(self):
        assert DEFAULT_SCHEDULE == Schedule(is_on=False, at=time(18, 0), time_zone=TOKYO, updated_at=None)

    def test_18_00_in_tokyo_is_09_00_utc_on_the_same_day(self):
        assert slot_on(daily(), date(2026, 11, 14)) == utc("2026-11-14T09:00:00Z")

    def test_the_next_run_before_18_00_tokyo_is_today(self):
        assert next_slot(daily(), utc("2026-11-14T00:33:00Z")) == utc("2026-11-14T09:00:00Z")

    def test_the_next_run_after_18_00_tokyo_is_tomorrow(self):
        assert next_slot(daily(), utc("2026-11-14T09:00:00Z")) == utc("2026-11-15T09:00:00Z")
        assert next_slot(daily(), utc("2026-11-14T12:00:00Z")) == utc("2026-11-15T09:00:00Z")

    def test_the_tokyo_date_decides_the_day_not_the_utc_date(self):
        just_after_midnight_in_tokyo = utc("2026-11-14T15:30:00Z")
        assert next_slot(daily("07:30"), just_after_midnight_in_tokyo) == utc("2026-11-14T22:30:00Z")
        assert latest_slot(daily("07:30"), just_after_midnight_in_tokyo) == utc("2026-11-13T22:30:00Z")

    def test_the_latest_run_is_the_last_one_at_or_before_now(self):
        assert latest_slot(daily(), utc("2026-11-14T09:00:00Z")) == utc("2026-11-14T09:00:00Z")
        assert latest_slot(daily(), utc("2026-11-14T08:59:59Z")) == utc("2026-11-13T09:00:00Z")

    def test_another_time_zone_moves_the_same_clock_time(self):
        assert slot_on(daily("18:00", NEW_YORK), date(2026, 11, 14)) == utc("2026-11-14T23:00:00Z")
        assert slot_on(daily("18:00", NEW_YORK), date(2026, 7, 14)) == utc("2026-07-14T22:00:00Z")

    def test_a_clock_time_skipped_by_daylight_saving_runs_once_just_after_the_jump(self):
        assert slot_on(daily("02:30", NEW_YORK), date(2026, 3, 8)) == utc("2026-03-08T07:30:00Z")
        slots = {latest_slot(daily("02:30", NEW_YORK), utc("2026-03-08T00:00:00Z") + timedelta(minutes=minute)) for minute in range(0, 48 * 60, 5)}
        assert sorted(slots) == [utc("2026-03-07T07:30:00Z"), utc("2026-03-08T07:30:00Z"), utc("2026-03-09T06:30:00Z")]

    def test_a_clock_time_repeated_by_daylight_saving_runs_only_the_first_time(self):
        slots = {latest_slot(daily("01:30", NEW_YORK), utc("2026-11-01T00:00:00Z") + timedelta(minutes=minute)) for minute in range(0, 48 * 60, 5)}
        assert sorted(slots) == [utc("2026-10-31T05:30:00Z"), utc("2026-11-01T05:30:00Z"), utc("2026-11-02T06:30:00Z")]


class TestDue:
    def test_a_slot_is_due_from_its_time_until_the_grace_runs_out(self):
        assert MISSED_RUN_GRACE == timedelta(hours=1)
        assert due_slot(daily(), utc("2026-11-14T08:59:59Z")) is None
        assert due_slot(daily(), utc("2026-11-14T09:00:00Z")) == utc("2026-11-14T09:00:00Z")
        assert due_slot(daily(), utc("2026-11-14T09:59:59Z")) == utc("2026-11-14T09:00:00Z")
        assert due_slot(daily(), utc("2026-11-14T10:00:01Z")) is None

    def test_nothing_is_due_when_the_schedule_is_off(self):
        assert due_slot(daily(is_on=False), utc("2026-11-14T09:00:30Z")) is None

    def test_a_time_set_after_it_already_passed_today_waits_for_tomorrow(self):
        set_at_18_05 = daily("18:00", updated_at="2026-11-14T09:05:00Z")
        assert due_slot(set_at_18_05, utc("2026-11-14T09:06:00Z")) is None
        assert due_slot(set_at_18_05, utc("2026-11-15T09:00:10Z")) == utc("2026-11-15T09:00:00Z")

    def test_a_time_set_a_minute_ahead_is_due_a_minute_later(self):
        set_at_10_04 = daily("10:05", updated_at="2026-11-14T01:04:20Z")
        assert due_slot(set_at_10_04, utc("2026-11-14T01:04:50Z")) is None
        assert due_slot(set_at_10_04, utc("2026-11-14T01:05:10Z")) == utc("2026-11-14T01:05:00Z")


class TestStoredSchedules:
    def test_the_file_lives_in_the_data_directory(self, data_dir):
        write_schedules(data_dir, CONTRACT["schedulesFile"])
        assert read_schedule("kumo") == daily("07:30", updated_at="2026-11-14T00:40:00Z")

    def test_the_shared_contract_reads_the_same_as_on_the_screen_server(self, data_dir):
        write_schedules(data_dir, CONTRACT["schedulesFile"])
        for client_id, expected in CONTRACT["expected"].items():
            schedule = read_schedule(client_id)
            assert {"isOn": schedule.is_on, "time": schedule.at.strftime("%H:%M"), "timeZone": schedule.time_zone} == expected

    def test_without_a_file_no_client_is_scheduled(self, data_dir):
        assert read_schedule("kumo") == DEFAULT_SCHEDULE
        assert read_schedule("kumo").is_on is False

    def test_a_client_missing_from_the_file_uses_the_default(self, data_dir):
        write_schedules(data_dir, {"version": 1, "clients": {}})
        assert read_schedule("hinata") == DEFAULT_SCHEDULE

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            {"version": 2, "clients": {}},
            {"version": 1, "clients": []},
            {"version": 1, "clients": {"kumo": {"isOn": "yes", "time": "18:00", "timeZone": TOKYO}}},
            {"version": 1, "clients": {"kumo": {"isOn": True, "time": "6pm", "timeZone": TOKYO}}},
            {"version": 1, "clients": {"kumo": {"isOn": True, "time": "24:00", "timeZone": TOKYO}}},
            {"version": 1, "clients": {"kumo": {"isOn": True, "time": "18:00", "timeZone": "Mars/Olympus"}}},
            {"version": 1, "clients": {"kumo": {"isOn": True, "time": "18:00", "timeZone": TOKYO, "updatedAt": "yesterday"}}},
        ],
    )
    def test_a_file_that_cannot_be_trusted_is_refused_instead_of_guessed(self, data_dir, content):
        write_schedules(data_dir, content)
        with pytest.raises(ScheduleError):
            read_schedule("kumo")


class TestLedger:
    def test_the_file_name_is_the_slot_in_utc(self):
        assert run_file_name(utc(CONTRACT["runRecord"]["slot"])) == CONTRACT["runFileName"]

    def test_a_slot_is_claimed_once(self, data_dir):
        ledger = RunLedger()
        slot = utc("2026-11-14T09:00:00Z")
        assert ledger.claim("kumo", slot, TOKYO, utc("2026-11-14T09:00:12Z")) is True
        assert ledger.claim("kumo", slot, TOKYO, utc("2026-11-14T09:00:42Z")) is False
        assert RunLedger().claim("kumo", slot, TOKYO, utc("2026-11-14T09:01:12Z")) is False

    def test_claims_are_per_client_and_per_slot(self, data_dir):
        ledger = RunLedger()
        assert ledger.claim("kumo", utc("2026-11-14T09:00:00Z"), TOKYO, utc("2026-11-14T09:00:12Z"))
        assert ledger.claim("hinata", utc("2026-11-14T09:00:00Z"), TOKYO, utc("2026-11-14T09:00:12Z"))
        assert ledger.claim("kumo", utc("2026-11-15T09:00:00Z"), TOKYO, utc("2026-11-15T09:00:12Z"))

    def test_a_run_skipped_while_another_waits_is_written_in_the_shared_shape(self, data_dir):
        ledger = RunLedger()
        slot = utc("2026-11-15T09:00:00Z")
        ledger.claim("kumo", slot, TOKYO, utc("2026-11-15T09:00:08Z"))
        ledger.settle("kumo", slot, "skipped", reason="waiting-for-approval", waiting_job_id="exec_20261114_090012_kumo")
        path = data_dir / RUNS_DIRECTORY / "kumo" / CONTRACT["skippedFileName"]
        assert json.loads(path.read_text()) == CONTRACT["skippedRecord"]

    def test_a_started_run_is_written_in_the_shared_shape(self, data_dir):
        ledger = RunLedger()
        slot = utc("2026-11-14T09:00:00Z")
        ledger.claim("kumo", slot, TOKYO, utc("2026-11-14T09:00:12Z"))
        ledger.settle("kumo", slot, "started", job_id="exec_20261114_090012_kumo")
        path = data_dir / RUNS_DIRECTORY / "kumo" / CONTRACT["runFileName"]
        assert json.loads(path.read_text()) == CONTRACT["runRecord"]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert ledger.records("kumo") == [CONTRACT["runRecord"]]

    def test_a_released_claim_can_be_claimed_again(self, data_dir):
        ledger = RunLedger()
        slot = utc("2026-11-14T09:00:00Z")
        ledger.claim("kumo", slot, TOKYO, utc("2026-11-14T09:00:12Z"))
        ledger.release("kumo", slot)
        assert ledger.claim("kumo", slot, TOKYO, utc("2026-11-14T09:00:42Z")) is True

    def test_records_skip_files_that_cannot_be_read(self, data_dir):
        ledger = RunLedger()
        ledger.claim("kumo", utc("2026-11-14T09:00:00Z"), TOKYO, utc("2026-11-14T09:00:12Z"))
        (data_dir / RUNS_DIRECTORY / "kumo" / "20261113T0900Z.json").write_text("")
        assert [record["slot"] for record in ledger.records("kumo")] == ["2026-11-14T09:00:00Z"]
        assert ledger.records("hinata") == []

    def test_a_client_id_that_is_not_a_plain_id_is_refused(self, data_dir):
        with pytest.raises(ValueError):
            RunLedger().claim("../kumo", utc("2026-11-14T09:00:00Z"), TOKYO, utc("2026-11-14T09:00:12Z"))


class TestScheduler:
    def test_at_the_time_it_starts_the_saved_job_once_and_writes_down_the_job(self, data_dir):
        starts = Starts()
        built, _ = scheduler("2026-11-14T09:00:12Z", starts=starts)

        outcomes = asyncio.run(built.tick())

        assert starts.calls == [("kumo", SCHEDULED_REQUEST)]
        assert [(outcome.client_id, outcome.status, outcome.job_id) for outcome in outcomes] == [("kumo", "started", "exec_kumo_1")]
        [record] = RunLedger().records("kumo")
        assert record["status"] == "started" and record["jobId"] == "exec_kumo_1" and record["slot"] == "2026-11-14T09:00:00Z"

    def test_before_the_time_nothing_happens(self, data_dir):
        starts = Starts()
        built, _ = scheduler("2026-11-14T08:59:50Z", starts=starts)
        assert asyncio.run(built.tick()) == []
        assert starts.calls == []
        assert not (data_dir / RUNS_DIRECTORY).exists()

    def test_every_later_tick_in_the_same_slot_starts_nothing(self, data_dir):
        starts = Starts()
        built, moment = scheduler("2026-11-14T09:00:12Z", starts=starts)
        asyncio.run(built.tick())
        for seconds in (30, 60, 600, 3500):
            moment[0] = utc("2026-11-14T09:00:12Z") + timedelta(seconds=seconds)
            assert asyncio.run(built.tick()) == []
        assert len(starts.calls) == 1

    def test_after_a_restart_the_same_slot_does_not_run_again(self, data_dir):
        first = Starts()
        asyncio.run(scheduler("2026-11-14T09:00:12Z", starts=first)[0].tick())
        after_restart = Starts()
        asyncio.run(scheduler("2026-11-14T09:02:40Z", starts=after_restart)[0].tick())
        assert len(first.calls) == 1 and after_restart.calls == []

    def test_two_schedulers_ticking_together_start_one_job(self, data_dir):
        starts = Starts()

        async def both():
            return await asyncio.gather(scheduler("2026-11-14T09:00:12Z", starts=starts)[0].tick(), scheduler("2026-11-14T09:00:12Z", starts=starts)[0].tick())

        asyncio.run(both())
        assert starts.calls == [("kumo", SCHEDULED_REQUEST)]

    def test_the_next_day_runs_again(self, data_dir):
        starts = Starts()
        built, moment = scheduler("2026-11-14T09:00:12Z", starts=starts)
        asyncio.run(built.tick())
        moment[0] = utc("2026-11-15T09:00:05Z")
        asyncio.run(built.tick())
        assert len(starts.calls) == 2
        assert [record["slot"] for record in RunLedger().records("kumo")] == ["2026-11-14T09:00:00Z", "2026-11-15T09:00:00Z"]

    def test_a_worker_back_within_the_hour_still_runs_the_missed_slot_and_not_later(self, data_dir):
        late = Starts()
        asyncio.run(scheduler("2026-11-14T09:40:00Z", starts=late)[0].tick())
        too_late = Starts()
        asyncio.run(scheduler("2026-11-15T10:05:00Z", starts=too_late)[0].tick())
        assert len(late.calls) == 1 and too_late.calls == []

    def test_each_client_runs_at_its_own_time_in_its_own_zone(self, data_dir):
        starts = Starts()
        schedules = {"kumo": daily("18:00", TOKYO), "hinata": daily("18:00", NEW_YORK)}
        built, moment = scheduler("2026-11-14T09:00:12Z", schedules=schedules, starts=starts, clients=("kumo", "hinata"))
        asyncio.run(built.tick())
        assert starts.calls == [("kumo", SCHEDULED_REQUEST)]
        moment[0] = utc("2026-11-14T23:00:12Z")
        asyncio.run(built.tick())
        assert starts.calls == [("kumo", SCHEDULED_REQUEST), ("hinata", SCHEDULED_REQUEST)]

    def test_two_clients_at_the_same_time_both_run(self, data_dir):
        starts = Starts()
        built, _ = scheduler("2026-11-14T09:00:12Z", starts=starts, clients=("kumo", "hinata"))
        asyncio.run(built.tick())
        assert sorted(client_id for client_id, _ in starts.calls) == ["hinata", "kumo"]

    def test_a_schedule_that_is_off_never_runs(self, data_dir):
        starts = Starts()
        built, _ = scheduler("2026-11-14T09:00:12Z", schedules={"kumo": daily(is_on=False)}, starts=starts)
        assert asyncio.run(built.tick()) == []
        assert starts.calls == []

    def test_without_saved_steps_the_slot_is_skipped_and_nothing_starts(self, data_dir):
        starts = Starts()
        built, moment = scheduler("2026-11-14T09:00:12Z", steps=Steps({"kumo": False}), starts=starts)
        [outcome] = asyncio.run(built.tick())
        assert (outcome.status, outcome.reason) == ("skipped", "no-saved-steps")
        assert starts.calls == []
        moment[0] = utc("2026-11-14T09:10:00Z")
        assert asyncio.run(built.tick()) == []
        [record] = RunLedger().records("kumo")
        assert record["status"] == "skipped" and "jobId" not in record

    def test_a_client_that_is_not_set_up_is_left_alone(self, data_dir):
        starts = Starts()
        steps = Steps()
        built, _ = scheduler("2026-11-14T09:00:12Z", steps=steps, starts=starts, clients=("sora",))
        with pytest.raises(ClientNotSetUp):
            resolve_job("sora")
        assert asyncio.run(built.tick()) == []
        assert starts.calls == [] and steps.asked == []

    def test_when_the_saved_steps_cannot_be_read_it_tries_again_on_the_next_tick(self, data_dir):
        starts = Starts()
        built, moment = scheduler("2026-11-14T09:00:12Z", steps=Steps(failing=1), starts=starts)
        [outcome] = asyncio.run(built.tick())
        assert outcome.status == "retry"
        assert starts.calls == []
        moment[0] = utc("2026-11-14T09:00:42Z")
        asyncio.run(built.tick())
        assert len(starts.calls) == 1

    def test_when_nothing_was_started_it_tries_again_on_the_next_tick(self, data_dir):
        starts = Starts(NothingStarted("AgentField is not reachable"))
        built, moment = scheduler("2026-11-14T09:00:12Z", starts=starts)
        [outcome] = asyncio.run(built.tick())
        assert outcome.status == "retry"
        moment[0] = utc("2026-11-14T09:00:42Z")
        [outcome] = asyncio.run(built.tick())
        assert outcome.status == "started"
        assert len(starts.calls) == 2

    def test_when_it_is_unclear_whether_a_job_started_it_does_not_risk_a_second_one(self, data_dir):
        starts = Starts(TimeoutError("AgentField did not answer in time"))
        built, moment = scheduler("2026-11-14T09:00:12Z", starts=starts)
        [outcome] = asyncio.run(built.tick())
        assert outcome.status == "failed"
        moment[0] = utc("2026-11-14T09:00:42Z")
        assert asyncio.run(built.tick()) == []
        assert len(starts.calls) == 1
        [record] = RunLedger().records("kumo")
        assert record["status"] == "failed" and "AgentField did not answer in time" in record["reason"]

    def test_while_the_last_run_still_waits_for_approval_the_new_slot_is_skipped_and_written_down(self, data_dir):
        starts = Starts()
        waiting = Waiting({"kumo": "exec_waiting_since_yesterday"})
        built, moment = scheduler("2026-11-15T09:00:08Z", waiting=waiting, starts=starts)
        [outcome] = asyncio.run(built.tick())
        assert (outcome.status, outcome.reason) == ("skipped", "waiting-for-approval")
        assert starts.calls == []
        moment[0] = utc("2026-11-15T09:20:00Z")
        waiting.waiting.clear()
        assert asyncio.run(built.tick()) == []
        assert starts.calls == []
        [record] = RunLedger().records("kumo")
        assert record["status"] == "skipped" and record["reason"] == "waiting-for-approval" and record["waitingJobId"] == "exec_waiting_since_yesterday"
        assert "jobId" not in record

    def test_once_nothing_waits_the_next_slot_runs_again(self, data_dir):
        starts = Starts()
        waiting = Waiting({"kumo": "exec_waiting"})
        built, moment = scheduler("2026-11-15T09:00:08Z", waiting=waiting, starts=starts)
        asyncio.run(built.tick())
        waiting.waiting.clear()
        moment[0] = utc("2026-11-16T09:00:08Z")
        [outcome] = asyncio.run(built.tick())
        assert outcome.status == "started"
        assert len(starts.calls) == 1

    def test_another_clients_waiting_run_does_not_stop_this_one(self, data_dir):
        starts = Starts()
        built, _ = scheduler("2026-11-15T09:00:08Z", waiting=Waiting({"hinata": "exec_hinata"}), starts=starts, clients=("kumo", "hinata"))
        outcomes = asyncio.run(built.tick())
        assert [(outcome.client_id, outcome.status) for outcome in outcomes] == [("kumo", "started"), ("hinata", "skipped")]
        assert starts.calls == [("kumo", SCHEDULED_REQUEST)]

    def test_when_it_cannot_tell_whether_a_run_waits_it_tries_again_on_the_next_tick(self, data_dir):
        starts = Starts()
        built, moment = scheduler("2026-11-15T09:00:08Z", waiting=Waiting(failing=1), starts=starts)
        [outcome] = asyncio.run(built.tick())
        assert outcome.status == "retry"
        moment[0] = utc("2026-11-15T09:00:38Z")
        [outcome] = asyncio.run(built.tick())
        assert outcome.status == "started"

    def test_without_saved_steps_it_does_not_even_ask_what_is_waiting(self, data_dir):
        waiting = Waiting()
        built, _ = scheduler("2026-11-15T09:00:08Z", steps=Steps({"kumo": False}), waiting=waiting)
        asyncio.run(built.tick())
        assert waiting.asked == []

    def test_a_broken_schedules_file_runs_nobody(self, data_dir):
        write_schedules(data_dir, "not json")
        starts = Starts()
        warnings: list[str] = []
        built = Scheduler(has_saved_steps=Steps(), waiting_job=Waiting(), start=starts, now=lambda: utc("2026-11-14T09:00:12Z"), warn=warnings.append)
        assert asyncio.run(built.tick()) == []
        assert starts.calls == [] and warnings

    def test_by_default_it_reads_the_stored_schedules_and_the_client_list(self, data_dir):
        write_schedules(data_dir, {"version": 1, "clients": {"kumo": {"isOn": True, "time": "07:30", "timeZone": TOKYO}, "hinata": {"isOn": False, "time": "18:00", "timeZone": TOKYO}}})
        starts = Starts()
        built = Scheduler(has_saved_steps=Steps(), waiting_job=Waiting(), start=starts, now=lambda: utc("2026-11-13T22:30:05Z"))
        asyncio.run(built.tick())
        assert starts.calls == [("kumo", SCHEDULED_REQUEST)]

    def test_by_default_a_client_without_a_stored_schedule_never_runs(self, data_dir):
        starts = Starts()
        built = Scheduler(has_saved_steps=Steps(), waiting_job=Waiting(), start=starts, now=lambda: utc("2026-11-14T09:00:12Z"))
        assert asyncio.run(built.tick()) == []
        assert starts.calls == []


class TestSavedSteps:
    def test_saved_steps_for_this_client_count(self, data_dir):
        memory = MemoryStore()
        memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
        assert asyncio.run(has_saved_steps(memory, resolve_job("kumo"))) is True

    def test_nothing_saved_or_another_clients_steps_do_not_count(self, data_dir):
        assert asyncio.run(has_saved_steps(MemoryStore(), resolve_job("kumo"))) is False
        memory = MemoryStore()
        memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
        assert asyncio.run(has_saved_steps(memory, resolve_job("hinata"))) is False

    def test_memory_that_cannot_be_reached_is_not_mistaken_for_no_saved_steps(self, data_dir):
        with pytest.raises(MemoryAccessError):
            asyncio.run(has_saved_steps(MemoryStore(failing=("get",)), resolve_job("kumo")))


class TestWaitingJob:
    def memory_with(self, *entries: tuple[str, str]) -> MemoryStore:
        memory = MemoryStore()
        for index, (client_id, job_id) in enumerate(entries):
            memory.values[f"{PENDING_PREFIX}approval-{index}"] = self.record(client_id, job_id)
        return memory

    def record(self, client_id: str, job_id: str) -> dict:
        return replace(pending(client_id=client_id), job_id=job_id).to_json()

    def test_a_run_of_this_client_still_waiting_in_agentfield_counts(self, data_dir):
        memory = self.memory_with(("kumo", "exec_waiting"))
        statuses = {"exec_waiting": "waiting"}

        async def status_of(job_id):
            return statuses.get(job_id)

        assert asyncio.run(waiting_job(memory, resolve_job("kumo"), status_of)) == "exec_waiting"

    def test_a_finished_or_forgotten_run_does_not_count(self, data_dir):
        memory = self.memory_with(("kumo", "exec_done"), ("kumo", "exec_gone"))
        statuses = {"exec_done": "succeeded"}

        async def status_of(job_id):
            return statuses.get(job_id)

        assert asyncio.run(waiting_job(memory, resolve_job("kumo"), status_of)) is None

    def test_another_clients_waiting_run_and_unreadable_records_do_not_count(self, data_dir):
        memory = self.memory_with(("hinata", "exec_hinata"))
        memory.values[f"{PENDING_PREFIX}broken"] = {"nonsense": True}
        memory.values["stock-sync/procedure"] = {"not": "a pending write"}
        asked: list[str] = []

        async def status_of(job_id):
            asked.append(job_id)
            return "waiting"

        assert asyncio.run(waiting_job(memory, resolve_job("kumo"), status_of)) is None
        assert asked == []

    def test_memory_or_agentfield_that_cannot_answer_is_not_mistaken_for_nothing_waiting(self, data_dir):
        with pytest.raises(MemoryAccessError):
            asyncio.run(waiting_job(MemoryStore(failing=("list",)), resolve_job("kumo"), lambda job_id: None))
        memory = self.memory_with(("kumo", "exec_waiting"))

        async def unreachable(job_id):
            raise OSError("AgentField is not reachable")

        with pytest.raises(OSError):
            asyncio.run(waiting_job(memory, resolve_job("kumo"), unreachable))


class TestStartThroughAgentField:
    def call_answering(self, *answers):
        sent: list[tuple[str, str, dict]] = []
        replies = list(answers)

        def call(method, path, body=None):
            sent.append((method, path, body))
            reply = replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return reply

        return call, sent

    def test_it_starts_run_job_like_the_screen_does(self):
        call, sent = self.call_answering((200, {"execution_id": "exec_1", "run_id": "run_1", "status": "queued"}))
        job_id = asyncio.run(start_through_control_plane(call, "clientops.run_job", known_client("kumo"), SCHEDULED_REQUEST))
        assert job_id == "exec_1"
        assert sent == [("POST", "/api/v1/execute/async/clientops.run_job", {"input": {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": SCHEDULED_REQUEST}})]

    @pytest.mark.parametrize(
        "answer",
        [
            urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
            ConnectionRefusedError(61, "Connection refused"),
            (503, None),
            (404, None),
        ],
    )
    def test_a_refusal_means_nothing_started(self, answer):
        call, _ = self.call_answering(answer)
        with pytest.raises(NothingStarted):
            asyncio.run(start_through_control_plane(call, "clientops.run_job", known_client("kumo"), SCHEDULED_REQUEST))

    @pytest.mark.parametrize("answer", [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out")), (200, {"status": "queued"})])
    def test_an_unclear_answer_is_not_called_nothing_started(self, answer):
        call, _ = self.call_answering(answer)
        with pytest.raises(Exception) as raised:
            asyncio.run(start_through_control_plane(call, "clientops.run_job", known_client("kumo"), SCHEDULED_REQUEST))
        assert not isinstance(raised.value, NothingStarted)


class TestLoop:
    def test_the_loop_keeps_ticking_after_a_tick_fails(self):
        ticks: list[int] = []
        warnings: list[str] = []

        class Flaky:
            def warn(self, message):
                warnings.append(message)

            async def tick(self):
                ticks.append(len(ticks))
                if len(ticks) == 1:
                    raise OSError("disk is busy")
                if len(ticks) == 3:
                    raise asyncio.CancelledError
                return []

        async def no_wait(seconds):
            assert seconds == 30

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(keep_schedule(Flaky(), sleep=no_wait))
        assert len(ticks) == 3 and len(warnings) == 1

    def test_only_the_worker_started_with_bun_run_agent_keeps_the_schedule(self, monkeypatch):
        monkeypatch.delenv(SCHEDULER_VARIABLE, raising=False)
        assert scheduler_enabled() is False
        monkeypatch.setenv(SCHEDULER_VARIABLE, "on")
        assert scheduler_enabled() is True
        monkeypatch.setenv(SCHEDULER_VARIABLE, "off")
        assert scheduler_enabled() is False
        scripts = json.loads((Path(__file__).parents[2] / "package.json").read_text())["scripts"]
        assert f"{SCHEDULER_VARIABLE}=on" in started_by(scripts, "agent")
