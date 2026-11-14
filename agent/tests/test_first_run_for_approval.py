import asyncio
import json

import pytest

from clientops.boundary import resolve_job
from clientops.diff import StockChange
from clientops.page import Locator
from clientops.records import EventNotes
from clientops.stock_sync import PreparedWrite, run_first_run
from picking_claude import PickingClaude
from test_stock_sync import KUMO, REQUEST, data_dir, install_sites, of_type, quarter_second_clock, run

__all__ = ["data_dir"]

STORE_SIGN_IN = (
    Locator("textbox", "Username", "/login"),
    Locator("textbox", "Password", "/login"),
    Locator("button", "Sign in", "/login"),
)
SAVE_BUTTON = Locator("button", "Save changes", "/inventory")


def test_resumed_notes_carry_on_numbering_after_the_last_one_written():
    sent = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)), start=21)

    notes.emit("approval.answered", approvalId="apr-1", decision="approved", by="You")

    assert sent == [{"n": 22, "event": {"type": "approval.answered", "approvalId": "apr-1", "decision": "approved", "by": "You"}}]
    assert notes.count == 22


@pytest.mark.parametrize("start", [-1, True, 1.5, "3"])
def test_a_resumed_count_must_be_a_whole_number_of_notes(start):
    with pytest.raises(ValueError):
        EventNotes(lambda message, tags: None, start=start)


def test_the_waiting_first_run_hands_over_what_the_save_needs_without_any_login(monkeypatch):
    install_sites(monkeypatch)

    result, _ = run(PickingClaude())

    prepared = PreparedWrite.from_json(result["write"])
    assert prepared.sign_in == STORE_SIGN_IN
    assert prepared.save_button == SAVE_BUTTON
    assert prepared.changes == tuple(StockChange(**change) for change in KUMO["changes"])
    written = json.dumps(result["write"])
    for value in ("kumo-admin", "kumo-store-demo", "kumo-roasters", "kumo-supply-demo", "LP_"):
        assert value not in written


def test_the_prepared_write_survives_a_round_trip_through_json():
    prepared = PreparedWrite(STORE_SIGN_IN, SAVE_BUTTON, (StockChange("KC-ETH-200", "Ethiopia Guji 200g", 18, 6),))

    assert PreparedWrite.from_json(json.loads(json.dumps(prepared.to_json()))) == prepared


def test_only_a_waiting_first_run_hands_over_a_write(monkeypatch):
    same = {**KUMO, "store": KUMO["supplier"], "changes": []}
    install_sites(monkeypatch, day=same)

    result, _ = run(PickingClaude())

    assert result["status"] == "done"
    assert "write" not in result


@pytest.mark.parametrize(
    ("only_in_store", "only_at_supplier", "detail"),
    [
        (2, 0, "No products differ · 2 only in the store"),
        (1, 0, "No products differ · 1 only in the store"),
        (0, 1, "No products differ · 1 only at the supplier"),
        (2, 3, "No products differ · 2 only in the store · 3 only at the supplier"),
        (0, 0, "No products differ"),
    ],
)
def test_nothing_left_alone_goes_unmentioned_when_no_product_differs(monkeypatch, only_in_store, only_at_supplier, detail):
    in_store = [{"sku": f"KC-OLD-{index}", "name": f"Retired Blend {index}", "available": 4} for index in range(only_in_store)]
    at_supplier = [{"sku": f"KC-NEW-{index}", "name": f"New Arrival {index}", "available": 9} for index in range(only_at_supplier)]
    day = {**KUMO, "supplier": [*KUMO["supplier"], *at_supplier], "store": [*KUMO["supplier"], *in_store], "changes": []}
    install_sites(monkeypatch, day=day)

    result, events = run(PickingClaude())

    done = {event["stepId"]: event for event in of_type(events, "step.done")}
    assert done["compare"]["detail"] == detail
    assert of_type(events, "approval.requested") == []
    assert result["status"] == "done"
    assert all(row["sku"] not in json.dumps(events) for row in [*in_store, *at_supplier])


def test_a_job_that_needs_claude_but_has_no_way_to_call_it_stops_with_a_clear_message(monkeypatch):
    sites = install_sites(monkeypatch)
    sent = []
    notes = EventNotes(lambda message, tags: sent.append(json.loads(message)))

    result = asyncio.run(run_first_run(notes, resolve_job("kumo"), None, request=REQUEST, job_id="exec-test", clock=quarter_second_clock()))

    events = [note["event"] for note in sent]
    assert [event["type"] for event in events] == ["job.created", "job.failed"]
    assert events[-1] == {
        "type": "job.failed",
        "reason": "error",
        "message": "Claude isn't set up on this server, so I stopped. Nothing was changed.",
    }
    assert result["status"] == "failed"
    assert result["problem"] == "MissingExternalKey"
    assert sites.commands == []
