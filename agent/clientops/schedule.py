import asyncio
import json
import os
import re
import secrets
import urllib.error
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agentfield.exceptions import AgentFieldClientError, MemoryAccessError

from .approval import PENDING_PREFIX, PendingWrite
from .boundary import Client, ClientBoundary, ClientNotSetUp, UnknownClient, data_directory, known_clients, resolve_job
from .computers import CLIENT_ID_PATTERN
from .procedure import PROCEDURE_KEY, saved_procedure

SCHEDULES_FILE = "schedules.json"
SCHEDULES_VERSION = 1
RUNS_DIRECTORY = "schedule-runs"
RUN_RECORD_VERSION = 1
DEFAULT_TIME_ZONE = "Asia/Tokyo"
MISSED_RUN_GRACE = timedelta(hours=1)
TICK_SECONDS = 30
SCHEDULED_REQUEST = "Sync today's supplier stock to the store admin"
SCHEDULER_VARIABLE = "CLIENTOPS_SCHEDULER"
CLOCK_TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
REASON_LENGTH = 300
NO_SAVED_STEPS = "no-saved-steps"
WAITING_FOR_APPROVAL = "waiting-for-approval"
STILL_WAITING_STATUSES = frozenset({"waiting", "running"})


class ScheduleError(RuntimeError):
    pass


class NothingStarted(RuntimeError):
    pass


@dataclass(frozen=True)
class Schedule:
    is_on: bool
    at: time
    time_zone: str
    updated_at: datetime | None = None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)


DEFAULT_SCHEDULE = Schedule(is_on=False, at=time(18, 0), time_zone=DEFAULT_TIME_ZONE)


def scheduler_enabled() -> bool:
    return os.environ.get(SCHEDULER_VARIABLE) == "on"


def schedules_path() -> Path:
    return data_directory() / SCHEDULES_FILE


def parse_instant(text: Any) -> datetime:
    if not isinstance(text, str):
        raise ValueError(f"{text!r} is not a time")
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"{text!r} has no time zone")
    return moment.astimezone(UTC)


def utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def schedule_from(entry: Any, path: Path, client_id: str) -> Schedule:
    where = f"The schedule for {client_id} in {path}"
    if not isinstance(entry, dict) or not isinstance(entry.get("isOn"), bool):
        raise ScheduleError(f"{where} must say isOn as true or false.")
    clock = CLOCK_TIME.fullmatch(entry.get("time", "")) if isinstance(entry.get("time"), str) else None
    if clock is None:
        raise ScheduleError(f"{where} must give the time as HH:MM.")
    zone = entry.get("timeZone")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ScheduleError(f"{where} has a time zone that does not exist.") from None
    updated = entry.get("updatedAt")
    try:
        updated_at = None if updated is None else parse_instant(updated)
    except ValueError:
        raise ScheduleError(f"{where} has an updatedAt that is not a time.") from None
    return Schedule(is_on=entry["isOn"], at=time(int(clock[1]), int(clock[2])), time_zone=zone, updated_at=updated_at)


def read_schedule(client_id: str) -> Schedule:
    path = schedules_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return DEFAULT_SCHEDULE
    except OSError as error:
        raise ScheduleError(f"The schedules file {path} cannot be read ({error.strerror}).") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise ScheduleError(f"The schedules file {path} is not valid JSON.") from None
    if not isinstance(data, dict) or data.get("version") != SCHEDULES_VERSION or not isinstance(data.get("clients", {}), dict):
        raise ScheduleError(f"The schedules file {path} must be version {SCHEDULES_VERSION} with clients keyed by client id.")
    clients = data.get("clients", {})
    if client_id not in clients:
        return DEFAULT_SCHEDULE
    return schedule_from(clients[client_id], path, client_id)


def slot_on(schedule: Schedule, day: date) -> datetime:
    return datetime.combine(day, schedule.at, tzinfo=schedule.zone).astimezone(UTC)


def latest_slot(schedule: Schedule, now: datetime) -> datetime:
    today = now.astimezone(schedule.zone).date()
    slot = slot_on(schedule, today)
    return slot if slot <= now else slot_on(schedule, today - timedelta(days=1))


def next_slot(schedule: Schedule, now: datetime) -> datetime:
    today = now.astimezone(schedule.zone).date()
    slot = slot_on(schedule, today)
    return slot if slot > now else slot_on(schedule, today + timedelta(days=1))


def due_slot(schedule: Schedule, now: datetime, grace: timedelta = MISSED_RUN_GRACE) -> datetime | None:
    if not schedule.is_on:
        return None
    slot = latest_slot(schedule, now)
    if now - slot > grace:
        return None
    if schedule.updated_at is not None and slot < schedule.updated_at:
        return None
    return slot


def run_file_name(slot: datetime) -> str:
    return f"{slot.astimezone(UTC):%Y%m%dT%H%MZ}.json"


class RunLedger:
    def __init__(self, directory: Callable[[], Path] = lambda: data_directory() / RUNS_DIRECTORY) -> None:
        self.directory = directory

    def folder(self, client_id: str) -> Path:
        if not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ValueError(f"{client_id!r} is not a client id")
        return self.directory() / client_id

    def path(self, client_id: str, slot: datetime) -> Path:
        return self.folder(client_id) / run_file_name(slot)

    def claim(self, client_id: str, slot: datetime, time_zone: str, now: datetime) -> bool:
        folder = self.folder(client_id)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.chmod(0o700)
        record = {"version": RUN_RECORD_VERSION, "clientId": client_id, "slot": utc_text(slot), "timeZone": time_zone, "claimedAt": utc_text(now), "status": "claimed"}
        try:
            descriptor = os.open(self.path(client_id, slot), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w") as file:
            file.write(json.dumps(record))
            file.flush()
            os.fsync(file.fileno())
        return True

    def record(self, client_id: str, slot: datetime) -> dict | None:
        try:
            data = json.loads(self.path(client_id, slot).read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def settle(
        self,
        client_id: str,
        slot: datetime,
        status: str,
        *,
        job_id: str | None = None,
        reason: str | None = None,
        waiting_job_id: str | None = None,
    ) -> None:
        claimed = self.record(client_id, slot) or {}
        record = {key: value for key, value in claimed.items() if key not in {"status", "jobId", "reason", "waitingJobId"}}
        record.update({"version": RUN_RECORD_VERSION, "clientId": client_id, "slot": utc_text(slot), "status": status})
        if job_id is not None:
            record["jobId"] = job_id
        if reason is not None:
            record["reason"] = reason[:REASON_LENGTH]
        if waiting_job_id is not None:
            record["waitingJobId"] = waiting_job_id
        path = self.path(client_id, slot)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(json.dumps(record))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)

    def release(self, client_id: str, slot: datetime) -> None:
        self.path(client_id, slot).unlink(missing_ok=True)

    def records(self, client_id: str) -> list[dict]:
        folder = self.folder(client_id)
        if not folder.is_dir():
            return []
        found = []
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("clientId") == client_id:
                found.append(data)
        return found


@dataclass(frozen=True)
class SlotOutcome:
    client_id: str
    slot: datetime
    status: str
    job_id: str | None = None
    reason: str | None = None


async def has_saved_steps(memory: Any, boundary: ClientBoundary) -> bool:
    if await memory.get(PROCEDURE_KEY) is None:
        return False
    return await saved_procedure(memory, boundary) is not None


async def waiting_job(memory: Any, boundary: ClientBoundary, status_of: Callable[[str], Awaitable[str | None]]) -> str | None:
    for key in await memory.list_keys():
        if not key.startswith(PENDING_PREFIX):
            continue
        try:
            pending = PendingWrite.from_json(await memory.get(key))
        except (KeyError, TypeError, ValueError):
            continue
        if pending.client_id != boundary.client.id:
            continue
        if await status_of(pending.job_id) in STILL_WAITING_STATUSES:
            return pending.job_id
    return None


@dataclass
class Scheduler:
    has_saved_steps: Callable[[ClientBoundary], Awaitable[bool]]
    waiting_job: Callable[[ClientBoundary], Awaitable[str | None]]
    start: Callable[[Client, str], Awaitable[str]]
    ledger: RunLedger = field(default_factory=RunLedger)
    schedule_of: Callable[[str], Schedule] = read_schedule
    clients: Callable[[], Iterable[Client]] = known_clients
    resolve: Callable[[str], ClientBoundary] = resolve_job
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    warn: Callable[[str], None] = print

    async def tick(self) -> list[SlotOutcome]:
        now = self.now()
        outcomes = []
        for client in self.clients():
            try:
                schedule = self.schedule_of(client.id)
            except ScheduleError as error:
                self.warn(f"Schedule for {client.id}: {error}")
                continue
            slot = due_slot(schedule, now)
            if slot is None:
                continue
            try:
                boundary = self.resolve(client.id)
            except (ClientNotSetUp, UnknownClient):
                continue
            if not self.ledger.claim(client.id, slot, schedule.time_zone, now):
                continue
            outcomes.append(await self.run_slot(client, boundary, slot))
        return outcomes

    async def run_slot(self, client: Client, boundary: ClientBoundary, slot: datetime) -> SlotOutcome:
        try:
            saved = await self.has_saved_steps(boundary)
            waiting = await self.waiting_job(boundary) if saved else None
        except (MemoryAccessError, AgentFieldClientError, OSError) as error:
            self.ledger.release(client.id, slot)
            return SlotOutcome(client.id, slot, "retry", reason=str(error))
        if not saved:
            self.ledger.settle(client.id, slot, "skipped", reason=NO_SAVED_STEPS)
            return SlotOutcome(client.id, slot, "skipped", reason=NO_SAVED_STEPS)
        if waiting is not None:
            self.ledger.settle(client.id, slot, "skipped", reason=WAITING_FOR_APPROVAL, waiting_job_id=waiting)
            return SlotOutcome(client.id, slot, "skipped", reason=WAITING_FOR_APPROVAL)
        try:
            job_id = await self.start(client, SCHEDULED_REQUEST)
        except NothingStarted as error:
            self.ledger.release(client.id, slot)
            return SlotOutcome(client.id, slot, "retry", reason=str(error))
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            self.ledger.settle(client.id, slot, "failed", reason=reason)
            self.warn(f"Scheduled run for {client.id} at {utc_text(slot)} may not have started: {reason}")
            return SlotOutcome(client.id, slot, "failed", reason=reason)
        self.ledger.settle(client.id, slot, "started", job_id=job_id)
        return SlotOutcome(client.id, slot, "started", job_id=job_id)


def refused(error: BaseException) -> bool:
    if isinstance(error, ConnectionRefusedError):
        return True
    return isinstance(error, urllib.error.URLError) and isinstance(error.reason, ConnectionRefusedError)


async def start_through_control_plane(call: Callable[..., tuple[int, Any]], target: str, client: Client, request: str) -> str:
    body = {"input": {"client_id": client.id, "client_name": client.name, "request": request}}
    try:
        status, answer = await asyncio.to_thread(call, "POST", f"/api/v1/execute/async/{target}", body)
    except OSError as error:
        if refused(error):
            raise NothingStarted("AgentField is not reachable") from error
        raise
    if status >= 400:
        raise NothingStarted(f"AgentField answered {status}")
    job_id = answer.get("execution_id") if isinstance(answer, dict) else None
    if not isinstance(job_id, str) or not job_id:
        raise AgentFieldClientError("AgentField did not return an execution id")
    return job_id


async def keep_schedule(scheduler: Any, *, every: float = TICK_SECONDS, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    while True:
        try:
            await scheduler.tick()
        except Exception as error:
            scheduler.warn(f"Schedule tick failed: {type(error).__name__}: {error}")
        await sleep(every)
