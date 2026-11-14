from datetime import UTC, datetime
from decimal import Decimal

import pytest

from clientops.cost import COSTS_DIRECTORY, ClaudeBill, CostLedger, JobMeter, metering
from clientops.externals.fake_ai import FakeClaude
from clientops.plan import Choice, HandOff, HandOffReason
from picking_claude import PickingClaude
from test_plan import SIGN_IN, SUPPLIER_LOGIN, choose, chooser, id_of
from test_stock_sync import data_dir, install_sites, run, shape, write_settings

__all__ = ["data_dir"]

NOV_14 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC).timestamp()


def job_meter(tmp_path, *, cap=10.0, clock=None):
    return JobMeter("kumo", "exec-test", ledger=CostLedger(tmp_path), cap=lambda: cap, estimated=False, wall=lambda: NOV_14, **({"clock": clock} if clock else {}))


def sign_in_reply(fake, **tokens):
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click", **tokens)


def test_over_the_cap_claude_is_not_called_and_the_step_goes_to_a_person(tmp_path):
    fake = FakeClaude()
    sign_in_reply(fake)

    with metering(job_meter(tmp_path, cap=0.01)) as meter:
        result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.OVER_BUDGET
    assert result.goal == SIGN_IN
    assert result.calls == ()
    assert fake.received == []
    assert meter.cost.claude == ClaudeBill()


def test_within_the_cap_each_call_is_charged_to_the_job_at_its_real_price(tmp_path):
    fake = FakeClaude()
    sign_in_reply(fake, input_tokens=1800, output_tokens=400)

    with metering(job_meter(tmp_path)) as meter:
        result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, Choice)
    [call] = result.calls
    assert call.cost_usd == pytest.approx((1800 * 4 + 400 * 20) / 1e6)
    assert meter.cost.claude == ClaudeBill(calls=1, input_tokens=1800, output_tokens=400, usd=Decimal("0.0152"))


def test_the_retry_is_skipped_when_the_first_answer_used_up_the_budget(tmp_path):
    fake = FakeClaude()
    fake.reply_choice("zz", "click")
    sign_in_reply(fake)

    with metering(job_meter(tmp_path, cap=0.06)) as meter:
        result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.OVER_BUDGET
    assert [call.outcome for call in result.calls] == ["unknown-element"]
    assert len(fake.received) == 1
    assert meter.cost.claude.calls == 1


def test_a_refusal_claude_does_not_bill_is_kept_at_zero_dollars(tmp_path):
    fake = FakeClaude()
    fake.reply_refusal("cyber")
    sign_in_reply(fake)

    with metering(job_meter(tmp_path)) as meter:
        result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert [call.outcome for call in result.calls] == ["declined", "chosen"]
    assert [call.cost_usd for call in result.calls] == [0, pytest.approx(0.0336)]
    assert meter.cost.claude == ClaudeBill(calls=2, input_tokens=5200, output_tokens=640, usd=Decimal("0.0336"))


def test_a_call_that_failed_counts_as_a_call_that_cost_nothing(tmp_path):
    fake = FakeClaude()
    fake.fail_overloaded()
    sign_in_reply(fake)

    with metering(job_meter(tmp_path)) as meter:
        choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert meter.cost.claude.calls == 2
    assert meter.cost.claude.usd == Decimal("0.0336")


def test_when_the_spending_cannot_be_checked_the_step_goes_to_a_person(tmp_path):
    folder = tmp_path / COSTS_DIRECTORY
    folder.mkdir()
    (folder / "2026-11.jsonl").write_text("not json\n")
    fake = FakeClaude()
    sign_in_reply(fake)

    with metering(job_meter(tmp_path)):
        result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.BUDGET_UNKNOWN
    assert fake.received == []


def test_claude_turned_off_is_still_told_as_off_when_the_budget_is_used_up(tmp_path):
    with metering(job_meter(tmp_path, cap=0)):
        result = choose(chooser(None, use_claude=False), SUPPLIER_LOGIN, SIGN_IN)
    assert result.reason is HandOffReason.CLAUDE_OFF


def test_the_two_budget_messages_are_plain_and_different_from_claude_off():
    messages = {reason: HandOff(goal=None, reason=reason, calls=()).message for reason in HandOffReason}
    assert messages[HandOffReason.OVER_BUDGET] == "This would go over this client's monthly Claude budget, so a person needs to do this step. Nothing was changed."
    assert messages[HandOffReason.BUDGET_UNKNOWN] == "I couldn't check this client's Claude spending this month, so a person needs to do this step. Nothing was changed."
    assert len(set(messages.values())) == len(messages)


def test_a_first_run_over_the_cap_hands_the_first_step_to_a_person_like_claude_turned_off(monkeypatch, data_dir, tmp_path):
    sites = install_sites(monkeypatch)
    write_settings(data_dir, {"kumo": {"claudeMonthlyCapUsd": 0}})
    claude = PickingClaude()

    with metering(JobMeter("kumo", "exec-test", ledger=CostLedger(tmp_path), cap=lambda: 0.0, estimated=False)):
        result, events = run(claude)

    assert claude.calls == 0
    assert shape(events) == [("job.created", None), ("step.started", "sign-in"), ("page.seen", "sign-in"), ("job.failed", "sign-in")]
    assert events[-1]["reason"] == "error"
    assert events[-1]["message"] == HandOff(goal=None, reason=HandOffReason.OVER_BUDGET, calls=()).message
    assert result == {
        "status": "failed",
        "reason": "needs_person",
        "stepId": "sign-in",
        "handOff": "over-budget",
        "claudeCalls": 0,
        "claudeUsage": {},
    }
    assert sites.typed == []
    assert sites.commands[-1] == {"command": "close"}


def test_a_first_run_within_the_cap_charges_every_call_and_the_computer_time(monkeypatch, tmp_path):
    install_sites(monkeypatch)
    claude = PickingClaude()

    with metering(job_meter(tmp_path)) as meter:
        result, _ = run(claude)
        meter.end_phase()

    assert result["status"] == "waiting"
    assert meter.cost.claude.calls == result["claudeCalls"] == 8
    paid = sum(Decimal(str(call["costUsd"])) for calls in result["claudeUsage"].values() for call in calls)
    assert meter.cost.claude.usd == paid
    assert meter.cost.computer_seconds > 0
