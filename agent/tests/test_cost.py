import json
import math
import stat
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
from zoneinfo import ZoneInfo

import pytest

from clientops.cost import (
    BILLED_REFUSAL_CATEGORIES,
    CLAUDE_PRICES_PER_MILLION,
    COSTS_DIRECTORY,
    NEXT_CALL_RESERVE_USD,
    Budget,
    ClaudeBill,
    CostLedger,
    CostLedgerError,
    JobCost,
    JobMeter,
    LedgerEntry,
    claude_bill,
    DEFAULT_TIME_ZONE,
    month_of,
)
from clientops.externals.fake_ai import app_ai_response, choice_text, wire_message, wire_usage
from clientops.records import EventNotes

TOKYO = ZoneInfo("Asia/Tokyo")
NOV_14 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC).timestamp()
OCT_31_LATE = datetime(2026, 10, 31, 23, 59, 30, tzinfo=TOKYO).timestamp()
NOV_1_EARLY = datetime(2026, 11, 1, 0, 0, 30, tzinfo=TOKYO).timestamp()


@pytest.fixture(autouse=True)
def offline_prices(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def reply(input_tokens=5200, output_tokens=640, *, cache_read=0, cache_write=0, hour_writes=0):
    usage = wire_usage(input_tokens, output_tokens, cache_read_tokens=cache_read, cache_write_tokens=cache_write)
    usage["cache_creation"] = {"ephemeral_5m_input_tokens": cache_write - hour_writes, "ephemeral_1h_input_tokens": hour_writes}
    return app_ai_response(wire_message(choice_text("a", "click", True), "end_turn", None, usage))


def refusal(category, *, input_tokens=5200, output_tokens=0):
    details = {"type": "refusal", "category": category, "explanation": None}
    text = None if output_tokens == 0 else '{"element_id": "a'
    return app_ai_response(wire_message(text, "refusal", details, wire_usage(input_tokens, output_tokens)))


def cut_off():
    return app_ai_response(wire_message('{"element_id":"e1', "max_tokens", None, wire_usage(5200, 16000)))


def usd(text: str) -> Decimal:
    return Decimal(text)


def ticking(*times: float):
    values = iter(times)
    return lambda: next(values)


def steady(start: float = 0.0, step: float = 0.25):
    ticks = count()
    return lambda: start + next(ticks) * step


def meter(tmp_path, *, client_id="kumo", job_id="exec_1", cap=10.0, estimated=False, clock=None, wall=None, resumed_since=None):
    return JobMeter(
        client_id,
        job_id,
        ledger=CostLedger(tmp_path),
        cap=lambda: cap,
        estimated=estimated,
        clock=clock or steady(),
        wall=wall or (lambda: NOV_14),
        resumed_since=resumed_since,
    )


def entry(job_id, client_id, at, *, dollars="0", calls=1, seconds=0.0, estimated=False):
    return LedgerEntry(
        job_id=job_id,
        client_id=client_id,
        at=datetime.fromtimestamp(at, UTC),
        cost=JobCost(ClaudeBill(calls=calls, usd=usd(dollars)), seconds, estimated),
    )


def test_the_prices_are_claude_opus_5_5s_per_million_tokens():
    assert CLAUDE_PRICES_PER_MILLION == {
        "input": Decimal("4"),
        "output": Decimal("20"),
        "cache_write_5m": Decimal("5"),
        "cache_write_1h": Decimal("8"),
        "cache_read": Decimal("0.20"),
    }


def test_an_ordinary_choice_costs_input_at_4_and_output_at_20_dollars_per_million():
    bill = claude_bill(reply(5200, 640))
    assert bill == ClaudeBill(calls=1, input_tokens=5200, output_tokens=640, usd=usd("0.0336"))


def test_cache_reads_and_five_minute_cache_writes_have_their_own_prices():
    bill = claude_bill(reply(400, 120, cache_read=2000, cache_write=1000))
    assert bill.input_tokens == 400
    assert bill.cache_read_tokens == 2000
    assert bill.cache_write_tokens == 1000
    assert bill.output_tokens == 120
    assert bill.usd == (400 * 4 + 1000 * 5 + 2000 * Decimal("0.2") + 120 * 20) / 1_000_000


def test_an_hour_long_cache_write_costs_8_dollars_per_million():
    bill = claude_bill(reply(400, 120, cache_read=2000, cache_write=1000, hour_writes=700))
    assert bill.cache_write_tokens == 1000
    assert bill.usd == (400 * 4 + 300 * 5 + 700 * 8 + 2000 * Decimal("0.2") + 120 * 20) / 1_000_000


def test_a_cut_off_reply_is_billed_in_full():
    assert claude_bill(cut_off()).usd == usd("0.3408")


@pytest.mark.parametrize(
    "response",
    [reply(5200, 640), reply(400, 120, cache_read=2000, cache_write=1000), reply(400, 120, cache_read=2000, cache_write=1000, hour_writes=700), cut_off()],
)
def test_billed_calls_cost_what_the_published_price_list_says(response):
    assert float(claude_bill(response).usd) == pytest.approx(response.cost_usd, abs=1e-12)


@pytest.mark.parametrize("category", ["cyber", "general_harms", None])
def test_a_refusal_before_any_output_in_an_unbilled_category_costs_nothing(category):
    response = refusal(category)
    assert response.cost_usd > 0
    assert claude_bill(response) == ClaudeBill(calls=1)


@pytest.mark.parametrize("category", sorted(BILLED_REFUSAL_CATEGORIES))
def test_a_refusal_before_any_output_in_a_billed_category_costs_its_input(category):
    assert claude_bill(refusal(category)) == ClaudeBill(calls=1, input_tokens=5200, usd=usd("0.0208"))


def test_only_three_refusal_categories_are_billed_before_any_output():
    assert BILLED_REFUSAL_CATEGORIES == frozenset({"bio", "frontier_llm", "reasoning_extraction"})


def test_a_refusal_after_some_output_is_billed_like_any_reply():
    bill = claude_bill(refusal("cyber", output_tokens=300))
    assert bill == ClaudeBill(calls=1, input_tokens=5200, output_tokens=300, usd=(5200 * 4 + 300 * 20) / Decimal(1_000_000))


def test_bills_add_up_call_by_call():
    total = claude_bill(reply(5200, 640)) + claude_bill(refusal("cyber")) + ClaudeBill(calls=1)
    assert total == ClaudeBill(calls=3, input_tokens=5200, output_tokens=640, usd=usd("0.0336"))


def test_months_are_calendar_months_in_the_clients_time_zone_tokyo_unless_set():
    late_october_in_utc = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)
    assert DEFAULT_TIME_ZONE == "Asia/Tokyo"
    assert month_of(late_october_in_utc) == "2026-11"
    assert month_of(late_october_in_utc, TOKYO) == "2026-11"
    assert month_of(late_october_in_utc, ZoneInfo("America/Los_Angeles")) == "2026-10"
    assert month_of(datetime.fromtimestamp(OCT_31_LATE, UTC)) == "2026-10"


def test_each_entry_is_filed_under_its_clients_month(tmp_path):
    ledger = CostLedger(tmp_path)
    at = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)
    ledger.add(LedgerEntry("exec_1", "kumo", at, JobCost(ClaudeBill(calls=1, usd=usd("1")))))
    ledger.add(LedgerEntry("exec_2", "sora", at, JobCost(ClaudeBill(calls=1, usd=usd("2"))), "America/New_York"))

    assert [entry.job_id for entry in ledger.entries("2026-11")] == ["exec_1"]
    assert [entry.job_id for entry in ledger.entries("2026-10")] == ["exec_2"]
    assert ledger.entries("2026-10")[0].time_zone == "America/New_York"
    assert ledger.month_total("kumo", "2026-11").claude.usd == usd("1")
    assert ledger.month_total("sora", "2026-10").claude.usd == usd("2")


def test_an_entry_without_a_time_zone_that_exists_is_not_a_cost_entry(tmp_path):
    folder = tmp_path / COSTS_DIRECTORY
    folder.mkdir()
    good = LedgerEntry("exec_1", "kumo", datetime.fromtimestamp(NOV_14, UTC), JobCost()).to_json()
    (folder / "2026-11.jsonl").write_text(json.dumps({**good, "timeZone": "Mars/Olympus"}) + "\n")
    with pytest.raises(CostLedgerError):
        CostLedger(tmp_path).entries("2026-11")


def test_the_cap_counts_the_clients_month_not_the_utc_month(tmp_path):
    CostLedger(tmp_path).add(LedgerEntry("exec_0", "kumo", datetime(2026, 10, 31, 16, 0, tzinfo=UTC), JobCost(ClaudeBill(calls=1, usd=usd("5")))))
    assert meter(tmp_path, cap=6.0).claude_budget() is Budget.OK
    assert meter(tmp_path, cap=5.0).claude_budget() is Budget.OVER


def test_a_client_whose_time_zone_could_not_be_read_has_an_unknown_budget(tmp_path):
    job = JobMeter("kumo", "exec_1", ledger=CostLedger(tmp_path), cap=lambda: 10.0, estimated=False, wall=lambda: NOV_14, zone=None)
    assert job.claude_budget() is Budget.UNKNOWN


def test_a_job_is_filed_under_its_clients_month(tmp_path):
    late_october_in_utc = datetime(2026, 10, 31, 16, 0, tzinfo=UTC).timestamp()
    tokyo = JobMeter("kumo", "exec_1", ledger=CostLedger(tmp_path), cap=lambda: 10.0, estimated=False, wall=lambda: late_october_in_utc)
    los_angeles = JobMeter("hinata", "exec_2", ledger=CostLedger(tmp_path), cap=lambda: 10.0, estimated=False, wall=lambda: late_october_in_utc, zone=ZoneInfo("America/Los_Angeles"))
    tokyo.close()
    los_angeles.close()
    assert [(entry.job_id, entry.time_zone) for entry in CostLedger(tmp_path).entries("2026-11")] == [("exec_1", "Asia/Tokyo")]
    assert [(entry.job_id, entry.time_zone) for entry in CostLedger(tmp_path).entries("2026-10")] == [("exec_2", "America/Los_Angeles")]


def test_the_ledger_keeps_one_private_file_per_month_in_the_data_directory(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.add(entry("exec_1", "kumo", NOV_14, dollars="0.42", calls=8, seconds=61.5))

    path = tmp_path / COSTS_DIRECTORY / "2026-11.jsonl"
    [line] = path.read_text().splitlines()
    assert json.loads(line) == {
        "version": 1,
        "jobId": "exec_1",
        "clientId": "kumo",
        "at": "2026-11-14T09:00:00Z",
        "claudeCalls": 8,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "claudeUsd": 0.42,
        "computerSeconds": 61.5,
        "estimated": False,
        "timeZone": "Asia/Tokyo",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_the_ledger_only_adds_lines(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.add(entry("exec_1", "kumo", NOV_14, dollars="0.42"))
    ledger.add(entry("exec_2", "kumo", NOV_14, dollars="0.05"))
    assert [found.job_id for found in ledger.entries("2026-11")] == ["exec_1", "exec_2"]


def test_the_monthly_total_is_per_client_and_per_month(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.add(entry("exec_1", "kumo", NOV_14, dollars="0.42", calls=8, seconds=60))
    ledger.add(entry("exec_2", "kumo", NOV_14, dollars="0.05", calls=1, seconds=30))
    ledger.add(entry("exec_3", "hinata", NOV_14, dollars="1.00", calls=20, seconds=10))
    ledger.add(entry("exec_4", "kumo", OCT_31_LATE, dollars="3.00", calls=60, seconds=900))

    november = ledger.month_total("kumo", "2026-11")
    assert november.claude.usd == usd("0.47")
    assert november.claude.calls == 9
    assert november.computer_seconds == 90
    assert november.jobs == 2
    assert ledger.month_total("hinata", "2026-11").claude.usd == usd("1.00")
    assert ledger.month_total("kumo", "2026-10").claude.usd == usd("3.00")
    assert ledger.month_total("kumo", "2026-12").claude.usd == 0
    assert ledger.month_total("kumo", "2026-12").jobs == 0


def test_two_entries_of_one_job_count_as_one_job(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.add(entry("exec_1", "kumo", NOV_14, dollars="0.42"))
    ledger.add(entry("exec_1", "kumo", NOV_14, dollars="0"))
    assert ledger.month_total("kumo", "2026-11").jobs == 1


def test_a_line_that_is_not_a_cost_entry_is_an_error_instead_of_a_guess(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.add(entry("exec_1", "kumo", NOV_14, dollars="0.42"))
    with (tmp_path / COSTS_DIRECTORY / "2026-11.jsonl").open("a") as file:
        file.write('{"jobId": "exec_2"\n')
    with pytest.raises(CostLedgerError):
        ledger.month_total("kumo", "2026-11")


def test_a_ledger_that_cannot_be_written_is_an_error(tmp_path):
    (tmp_path / COSTS_DIRECTORY).write_text("not a folder")
    with pytest.raises(CostLedgerError):
        CostLedger(tmp_path).add(entry("exec_1", "kumo", NOV_14))


def test_the_entries_of_one_job_can_span_two_months(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.add(entry("exec_1", "kumo", OCT_31_LATE, dollars="0.42", calls=8, seconds=40))
    ledger.add(entry("exec_2", "kumo", NOV_1_EARLY, dollars="0.05"))
    ledger.add(entry("exec_1", "kumo", NOV_1_EARLY, dollars="0", calls=0, seconds=20))

    found = ledger.job_entries("exec_1", since=OCT_31_LATE - 60, until=NOV_1_EARLY + 60)
    assert [item.cost.computer_seconds for item in found] == [40, 20]


def test_the_meter_counts_computer_time_from_opening_to_the_end_of_each_phase_and_skips_the_wait(tmp_path):
    job = meter(tmp_path, clock=ticking(10, 70, 1000, 1030))
    job.computer_opened()
    job.computer_opened()
    job.end_phase()
    job.computer_opened()
    total = job.close()
    assert total.computer_seconds == 90


def test_a_job_that_never_opened_the_computer_used_no_computer_time(tmp_path):
    job = meter(tmp_path)
    job.end_phase()
    assert job.close().computer_seconds == 0


def test_closing_a_job_writes_one_ledger_entry_and_returns_the_jobs_totals(tmp_path):
    job = meter(tmp_path, clock=ticking(0, 45))
    job.charge(claude_bill(reply(5200, 640)))
    job.charge(claude_bill(refusal("cyber")))
    job.computer_opened()

    total = job.close()

    assert total == JobCost(ClaudeBill(calls=2, input_tokens=5200, output_tokens=640, usd=usd("0.0336")), 45, False)
    [written] = CostLedger(tmp_path).entries("2026-11")
    assert (written.job_id, written.client_id, written.cost) == ("exec_1", "kumo", total)
    assert CostLedger(tmp_path).month_total("kumo", "2026-11").claude.usd == usd("0.0336")


def test_a_local_computers_time_is_marked_as_an_estimate(tmp_path):
    job = meter(tmp_path, estimated=True, clock=ticking(0, 5))
    job.computer_opened()
    total = job.close()
    assert total.estimated is True
    assert total.note_fields()["estimated"] is True


def test_a_job_resumed_after_a_restart_adds_the_part_written_before_the_restart(tmp_path):
    before = meter(tmp_path, clock=ticking(0, 40), wall=lambda: OCT_31_LATE)
    before.charge(claude_bill(reply(5200, 640)))
    before.computer_opened()
    before.end_phase()
    before.suspend()
    CostLedger(tmp_path).add(entry("exec_other", "kumo", NOV_1_EARLY, dollars="9"))

    after = meter(tmp_path, clock=ticking(100, 120), wall=lambda: NOV_1_EARLY, resumed_since=OCT_31_LATE)
    after.computer_opened()
    total = after.close()

    assert total.claude.calls == 1
    assert total.claude.usd == usd("0.0336")
    assert total.computer_seconds == 60
    ledger = CostLedger(tmp_path)
    assert ledger.month_total("kumo", "2026-10").claude.usd == usd("0.0336")
    assert ledger.month_total("kumo", "2026-11").computer_seconds == 20


def test_claude_is_allowed_while_this_month_plus_one_more_call_stays_within_the_cap(tmp_path):
    CostLedger(tmp_path).add(entry("exec_0", "kumo", NOV_14, dollars="0.90"))
    job = meter(tmp_path, cap=1.0)
    assert NEXT_CALL_RESERVE_USD == usd("0.05")
    assert job.claude_budget() is Budget.OK
    job.charge(ClaudeBill(calls=1, usd=usd("0.05")))
    assert job.claude_budget() is Budget.OK
    job.charge(ClaudeBill(calls=1, usd=usd("0.0001")))
    assert job.claude_budget() is Budget.OVER


def test_other_clients_and_last_month_do_not_count_toward_the_cap(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.add(entry("exec_0", "hinata", NOV_14, dollars="50"))
    ledger.add(entry("exec_1", "kumo", OCT_31_LATE, dollars="50"))
    assert meter(tmp_path, cap=1.0).claude_budget() is Budget.OK
    assert meter(tmp_path, client_id="hinata", cap=1.0).claude_budget() is Budget.OVER


def test_a_cap_of_zero_never_allows_claude(tmp_path):
    assert meter(tmp_path, cap=0).claude_budget() is Budget.OVER


def test_a_ledger_that_cannot_be_read_leaves_the_budget_unknown(tmp_path):
    folder = tmp_path / COSTS_DIRECTORY
    folder.mkdir()
    (folder / "2026-11.jsonl").write_text("not json\n")
    assert meter(tmp_path).claude_budget() is Budget.UNKNOWN


def test_the_cap_is_read_again_for_every_check(tmp_path):
    caps = iter([10.0, 0.0])
    job = JobMeter("kumo", "exec_1", ledger=CostLedger(tmp_path), cap=lambda: next(caps), estimated=False, wall=lambda: NOV_14)
    assert job.claude_budget() is Budget.OK
    assert job.claude_budget() is Budget.OVER


def test_the_note_fields_are_only_counts_dollars_whole_seconds_and_the_estimate_flag():
    cost = JobCost(ClaudeBill(calls=8, input_tokens=41600, output_tokens=5120, cache_read_tokens=100, cache_write_tokens=200, usd=usd("0.42345678")), 61.6, False)
    assert cost.note_fields() == {
        "claude_calls": 8,
        "input_tokens": 41600,
        "output_tokens": 5120,
        "cache_read_tokens": 100,
        "cache_write_tokens": 200,
        "claude_usd": 0.423457,
        "computer_seconds": 62,
        "estimated": False,
    }


def test_the_cost_becomes_one_cost_recorded_note():
    sent = []
    notes = EventNotes(lambda message, tags: sent.append((json.loads(message), tags)), start=30)
    cost = JobCost(ClaudeBill(calls=8, input_tokens=41600, output_tokens=5120, usd=usd("0.42")), 61, False)

    notes.cost_recorded(**cost.note_fields())

    assert sent == [
        (
            {
                "n": 31,
                "event": {
                    "type": "cost.recorded",
                    "claudeCalls": 8,
                    "inputTokens": 41600,
                    "outputTokens": 5120,
                    "cacheReadTokens": 0,
                    "cacheWriteTokens": 0,
                    "claudeUsd": 0.42,
                    "computerSeconds": 61,
                    "estimated": False,
                },
            },
            ["clientops.event"],
        )
    ]


GOOD = {
    "claude_calls": 1,
    "input_tokens": 10,
    "output_tokens": 10,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "claude_usd": 0.0002,
    "computer_seconds": 5,
    "estimated": False,
}


@pytest.mark.parametrize(
    "change",
    [
        {"claude_calls": -1},
        {"claude_calls": True},
        {"input_tokens": 1.5},
        {"output_tokens": "10"},
        {"cache_read_tokens": -3},
        {"cache_write_tokens": None},
        {"claude_usd": -0.01},
        {"claude_usd": math.nan},
        {"claude_usd": math.inf},
        {"claude_usd": "0.01"},
        {"claude_usd": True},
        {"computer_seconds": 1.5},
        {"computer_seconds": -1},
        {"estimated": 1},
        {"claude_calls": 0},
        {"claude_calls": 0, "input_tokens": 0, "output_tokens": 0},
    ],
)
def test_a_cost_note_the_screen_would_reject_is_refused_before_any_note_is_written(change):
    sent = []
    notes = EventNotes(lambda message, tags: sent.append(message))
    with pytest.raises(ValueError):
        notes.cost_recorded(**{**GOOD, **change})
    assert sent == []
    assert notes.count == 0


def test_a_job_with_no_claude_calls_and_nothing_spent_is_a_fine_cost_note():
    sent = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)))
    notes.cost_recorded(**JobCost(ClaudeBill(), 12, True).note_fields())
    assert sent[0]["event"]["claudeUsd"] == 0
    assert sent[0]["event"]["estimated"] is True


def test_entries_written_before_time_zones_were_kept_count_in_tokyos_month(tmp_path):
    folder = tmp_path / COSTS_DIRECTORY
    folder.mkdir()
    older = {"version": 1, "jobId": "exec_0", "clientId": "kumo", "at": "2026-11-14T01:03:10Z", "claudeCalls": 1, "inputTokens": 5200, "outputTokens": 640, "cacheReadTokens": 0, "cacheWriteTokens": 0, "claudeUsd": 0.0336, "computerSeconds": 1.2, "estimated": True}
    (folder / "2026-11.jsonl").write_text(json.dumps(older) + "\n")

    [entry] = CostLedger(tmp_path).entries("2026-11")

    assert (entry.time_zone, entry.month) == (DEFAULT_TIME_ZONE, "2026-11")
    assert CostLedger(tmp_path).month_total("kumo", "2026-11").claude.usd == Decimal("0.0336")
    assert meter(tmp_path).claude_budget() is Budget.OK
