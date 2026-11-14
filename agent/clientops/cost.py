import asyncio
import json
import math
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .records import EventNotes

Clock = Callable[[], float]
CapReader = Callable[[], float]
ComputerStopper = Callable[[], bool]

MILLION = Decimal(1_000_000)
CLAUDE_PRICES_PER_MILLION = {
    "input": Decimal("4"),
    "output": Decimal("20"),
    "cache_write_5m": Decimal("5"),
    "cache_write_1h": Decimal("8"),
    "cache_read": Decimal("0.20"),
}
BILLED_REFUSAL_CATEGORIES = frozenset({"bio", "frontier_llm", "reasoning_extraction"})
REFUSED = "content_filter"
NEXT_CALL_RESERVE_USD = Decimal("0.05")
NOTE_USD_STEP = Decimal("0.000001")
COSTS_DIRECTORY = "costs"
LEDGER_VERSION = 1
LEDGER_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_TIME_ZONE = "Asia/Tokyo"
DEFAULT_ZONE = ZoneInfo(DEFAULT_TIME_ZONE)


class CostLedgerError(RuntimeError):
    pass


class Budget(StrEnum):
    OK = "ok"
    OVER = "over"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClaudeBill:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: Decimal = Decimal(0)

    def __add__(self, other: "ClaudeBill") -> "ClaudeBill":
        return ClaudeBill(
            self.calls + other.calls,
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.usd + other.usd,
        )


@dataclass(frozen=True)
class JobCost:
    claude: ClaudeBill = ClaudeBill()
    computer_seconds: float = 0.0
    estimated: bool = False

    def __add__(self, other: "JobCost") -> "JobCost":
        return JobCost(self.claude + other.claude, self.computer_seconds + other.computer_seconds, self.estimated or other.estimated)

    def note_fields(self) -> dict[str, Any]:
        return {
            "claude_calls": self.claude.calls,
            "input_tokens": self.claude.input_tokens,
            "output_tokens": self.claude.output_tokens,
            "cache_read_tokens": self.claude.cache_read_tokens,
            "cache_write_tokens": self.claude.cache_write_tokens,
            "claude_usd": float(self.claude.usd.quantize(NOTE_USD_STEP, rounding=ROUND_HALF_UP)),
            "computer_seconds": int(Decimal(str(self.computer_seconds)).to_integral_value(rounding=ROUND_HALF_UP)),
            "estimated": self.estimated,
        }


@dataclass(frozen=True)
class MonthTotal:
    claude: ClaudeBill
    computer_seconds: float
    jobs: int


def tokens(usage: Mapping[str, Any], name: str) -> int:
    return int(usage.get(name) or 0)


def hour_cache_writes(response: Any) -> int:
    try:
        details = response.raw_response.usage.prompt_tokens_details.cache_creation_token_details
    except AttributeError:
        return 0
    return int(getattr(details, "ephemeral_1h_input_tokens", None) or 0)


def stop_of(response: Any) -> tuple[str | None, str | None]:
    try:
        choice = response.raw_response.choices[0]
    except (AttributeError, IndexError, TypeError):
        return None, None
    fields = getattr(choice.message, "provider_specific_fields", None) or {}
    details = fields.get("stop_details") or {}
    return choice.finish_reason, details.get("category")


def claude_bill(response: Any) -> ClaudeBill:
    usage = getattr(response, "usage", None) or {}
    prompt = tokens(usage, "prompt_tokens")
    output = tokens(usage, "completion_tokens")
    read = tokens(usage, "cache_read_tokens")
    written = tokens(usage, "cache_creation_tokens")
    finish, category = stop_of(response)
    if finish == REFUSED and output == 0 and category not in BILLED_REFUSAL_CATEGORIES:
        return ClaudeBill(calls=1)
    uncached = max(prompt - read - written, 0)
    hour = min(hour_cache_writes(response), written)
    prices = CLAUDE_PRICES_PER_MILLION
    usd = (
        uncached * prices["input"]
        + (written - hour) * prices["cache_write_5m"]
        + hour * prices["cache_write_1h"]
        + read * prices["cache_read"]
        + output * prices["output"]
    ) / MILLION
    return ClaudeBill(1, uncached, output, read, written, usd)


def month_of(moment: datetime, zone: ZoneInfo = DEFAULT_ZONE) -> str:
    return moment.astimezone(zone).strftime("%Y-%m")


def months_between(start: datetime, end: datetime, zone: ZoneInfo = DEFAULT_ZONE) -> list[str]:
    local = start.astimezone(zone)
    year, month = local.year, local.month
    last = month_of(end, zone)
    found = []
    while True:
        key = f"{year:04d}-{month:02d}"
        found.append(key)
        if key >= last:
            return found
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def counted_number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{value!r} is not an amount")
    return Decimal(str(value))


@dataclass(frozen=True)
class LedgerEntry:
    job_id: str
    client_id: str
    at: datetime
    cost: JobCost
    time_zone: str = DEFAULT_TIME_ZONE

    @property
    def month(self) -> str:
        return month_of(self.at, ZoneInfo(self.time_zone))

    def to_json(self) -> dict[str, Any]:
        claude = self.cost.claude
        return {
            "version": LEDGER_VERSION,
            "jobId": self.job_id,
            "clientId": self.client_id,
            "at": self.at.astimezone(UTC).strftime(LEDGER_TIME_FORMAT),
            "claudeCalls": claude.calls,
            "inputTokens": claude.input_tokens,
            "outputTokens": claude.output_tokens,
            "cacheReadTokens": claude.cache_read_tokens,
            "cacheWriteTokens": claude.cache_write_tokens,
            "claudeUsd": float(claude.usd),
            "computerSeconds": round(self.cost.computer_seconds, 3),
            "estimated": self.cost.estimated,
            "timeZone": self.time_zone,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "LedgerEntry":
        if data.get("version") != LEDGER_VERSION or not isinstance(data.get("estimated"), bool):
            raise ValueError("not a version 1 cost entry")
        counts = [counted_number(data[name]) for name in ("claudeCalls", "inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")]
        if any(count != count.to_integral_value() for count in counts):
            raise ValueError("token and call counts are whole numbers")
        calls, input_tokens, output_tokens, read, written = (int(count) for count in counts)
        try:
            zone = ZoneInfo(data.get("timeZone", DEFAULT_TIME_ZONE))
        except (ZoneInfoNotFoundError, TypeError):
            raise ValueError("not a time zone") from None
        return cls(
            job_id=str(data["jobId"]),
            client_id=str(data["clientId"]),
            at=datetime.strptime(str(data["at"]), LEDGER_TIME_FORMAT).replace(tzinfo=UTC),
            cost=JobCost(
                ClaudeBill(calls, input_tokens, output_tokens, read, written, counted_number(data["claudeUsd"])),
                float(counted_number(data["computerSeconds"])),
                data["estimated"],
            ),
            time_zone=zone.key,
        )


class CostLedger:
    def __init__(self, data_directory: Path) -> None:
        self.directory = Path(data_directory) / COSTS_DIRECTORY

    def path_for(self, month: str) -> Path:
        return self.directory / f"{month}.jsonl"

    def add(self, entry: LedgerEntry) -> None:
        line = (json.dumps(entry.to_json(), ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            descriptor = os.open(self.path_for(entry.month), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, line)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise CostLedgerError(f"The cost ledger in {self.directory} cannot be written ({error.strerror}).") from None

    def entries(self, month: str) -> list[LedgerEntry]:
        path = self.path_for(month)
        try:
            text = path.read_text()
        except FileNotFoundError:
            return []
        except OSError as error:
            raise CostLedgerError(f"The cost ledger {path} cannot be read ({error.strerror}).") from None
        found = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                found.append(LedgerEntry.from_json(json.loads(line)))
            except (AttributeError, KeyError, TypeError, ValueError):
                raise CostLedgerError(f"Line {number} of the cost ledger {path} is not a cost entry.") from None
        return found

    def month_total(self, client_id: str, month: str) -> MonthTotal:
        mine = [entry for entry in self.entries(month) if entry.client_id == client_id]
        claude = sum((entry.cost.claude for entry in mine), ClaudeBill())
        return MonthTotal(claude, sum(entry.cost.computer_seconds for entry in mine), len({entry.job_id for entry in mine}))

    def job_entries(self, job_id: str, *, since: float, until: float, zone: ZoneInfo = DEFAULT_ZONE) -> list[LedgerEntry]:
        months = months_between(datetime.fromtimestamp(since, UTC), datetime.fromtimestamp(until, UTC), zone)
        return [entry for month in months for entry in self.entries(month) if entry.job_id == job_id]


class JobMeter:
    def __init__(
        self,
        client_id: str,
        job_id: str,
        *,
        ledger: CostLedger,
        cap: CapReader,
        estimated: bool,
        clock: Clock = time.monotonic,
        wall: Clock = time.time,
        zone: ZoneInfo | None = DEFAULT_ZONE,
        stop_computer: ComputerStopper | None = None,
        auto_stop_seconds: float | None = None,
        resumed_since: float | None = None,
    ) -> None:
        self.client_id = client_id
        self.job_id = job_id
        self.estimated = estimated
        self.zone = zone
        self.auto_stop_seconds = auto_stop_seconds
        self._ledger = ledger
        self._cap = cap
        self._clock = clock
        self._wall = wall
        self._stop_computer = stop_computer
        self._resumed_since = resumed_since
        self._claude = ClaudeBill()
        self._computer_seconds = 0.0
        self._opened_at: float | None = None
        self._idle_since: float | None = None
        self._used_computer = False
        self._guessed_time = False
        self._lock = threading.Lock()

    @property
    def cost(self) -> JobCost:
        with self._lock:
            return JobCost(self._claude, self._computer_seconds, self.estimated or self._guessed_time)

    @property
    def using_computer(self) -> bool:
        return self._opened_at is not None

    def charge(self, bill: ClaudeBill) -> None:
        with self._lock:
            self._claude += bill

    def computer_opened(self) -> None:
        with self._lock:
            if self._opened_at is not None:
                return
            now = self._clock()
            self._settle_idle(now, stopped_now=None)
            self._opened_at = now
            self._used_computer = True

    def end_phase(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            now = self._clock()
            self._computer_seconds += max(now - self._opened_at, 0.0)
            self._opened_at = None
            if self.auto_stop_seconds is not None:
                self._idle_since = now

    def release_computer(self, *, others_using: bool) -> None:
        if self._stop_computer is None or not self._used_computer:
            return
        stopped = None if others_using else self._stop_computer()
        now = self._clock()
        with self._lock:
            if self._opened_at is None:
                self._settle_idle(now, stopped_now=stopped)
                return
            self._computer_seconds += max(now - self._opened_at, 0.0)
            self._opened_at = None
            if stopped is False:
                self._left_to_auto_stop()

    def claude_budget(self) -> Budget:
        cap = Decimal(str(self._cap()))
        if self.zone is None:
            return Budget.UNKNOWN
        try:
            spent = self._ledger.month_total(self.client_id, month_of(self._now(), self.zone)).claude.usd
        except CostLedgerError:
            return Budget.UNKNOWN
        return Budget.OK if spent + self.cost.claude.usd + NEXT_CALL_RESERVE_USD <= cap else Budget.OVER

    def suspend(self) -> JobCost:
        self.end_phase()
        with self._lock:
            if self._idle_since is not None:
                self._idle_since = None
                self._left_to_auto_stop()
        own = self.cost
        self._write(own)
        return own

    def close(self) -> JobCost:
        self.end_phase()
        with self._lock:
            if self._idle_since is not None:
                self._settle_idle(self._clock(), stopped_now=None)
        earlier = self._earlier()
        own = self.cost
        self._write(own)
        return sum(earlier, JobCost()) + own

    def _settle_idle(self, now: float, *, stopped_now: bool | None) -> None:
        if self._idle_since is None:
            return
        idle = max(now - self._idle_since, 0.0)
        self._idle_since = None
        if stopped_now is True or (stopped_now is None and idle <= self.auto_stop_seconds):
            self._computer_seconds += idle
            return
        self._computer_seconds += min(idle, self.auto_stop_seconds)
        self._guessed_time = True

    def _left_to_auto_stop(self) -> None:
        self._computer_seconds += self.auto_stop_seconds
        self._guessed_time = True

    def _earlier(self) -> list[JobCost]:
        if self._resumed_since is None:
            return []
        found = self._ledger.job_entries(self.job_id, since=self._resumed_since, until=self._wall(), zone=self._filing_zone)
        return [entry.cost for entry in found if entry.client_id == self.client_id]

    def _write(self, cost: JobCost) -> None:
        self._ledger.add(LedgerEntry(self.job_id, self.client_id, self._now(), cost, self._filing_zone.key))

    @property
    def _filing_zone(self) -> ZoneInfo:
        return self.zone or DEFAULT_ZONE

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._wall(), UTC)


current_meter: ContextVar[JobMeter | None] = ContextVar("clientops_job_meter", default=None)


@dataclass(frozen=True)
class OpenJob:
    meter: JobMeter
    notes: EventNotes
    context: Context


open_jobs: dict[str, OpenJob] = {}


def job_meter() -> JobMeter | None:
    return current_meter.get()


def computer_in_use() -> None:
    meter = current_meter.get()
    if meter is not None:
        meter.computer_opened()


def charge_claude(bill: ClaudeBill) -> None:
    meter = current_meter.get()
    if meter is not None:
        meter.charge(bill)


@contextmanager
def metering(meter: JobMeter) -> Iterator[JobMeter]:
    token = current_meter.set(meter)
    try:
        yield meter
    finally:
        current_meter.reset(token)


def open_job(meter: JobMeter, notes: EventNotes) -> JobMeter:
    current_meter.set(meter)
    open_jobs[meter.job_id] = OpenJob(meter, notes, copy_context())
    return meter


def suspend_job(job_id: str) -> JobCost | None:
    job = open_jobs.pop(job_id, None)
    return None if job is None else job.meter.suspend()


def close_job(job_id: str, payload: dict[str, Any] | None = None) -> JobCost | None:
    job = open_jobs.pop(job_id, None)
    if job is None:
        return None
    total = job.meter.close()
    job.context.run(job.notes.cost_recorded, **total.note_fields())
    result = (payload or {}).get("result")
    if isinstance(result, dict) and "events" in result:
        result["events"] = job.notes.count
    return total


async def finish_job(job_id: str, payload: dict[str, Any] | None = None) -> JobCost | None:
    job = open_jobs.get(job_id)
    if job is None:
        return None
    others_using = any(
        other.meter.client_id == job.meter.client_id and other.meter.using_computer for other_id, other in open_jobs.items() if other_id != job_id
    )
    await asyncio.to_thread(job.meter.release_computer, others_using=others_using)
    return close_job(job_id, payload)
