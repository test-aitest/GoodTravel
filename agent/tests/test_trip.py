import asyncio
import importlib
import json
import os
import re
from pathlib import Path

import pytest

from clientops.boundary import COMPUTERS_VARIABLE, ClientBoundary, JobComputer, put_logins, resolve_job
from clientops.claude_call import CLAUDE_EFFORT, CLAUDE_MODEL, SEAT_REASONS, ModelNotAllowed
from clientops.cost import CostLedger, JobMeter, metering
from clientops.externals.fake_ai import FakeClaude
from clientops.page import RUNNER_COMMAND, Page
from clientops.records import EventNotes
from clientops.compile_script import plan_of
from clientops.procedure import PROCEDURE_KEY
from clientops.trip import BOOK_TITLE, SEARCH_TITLE, run_trip_book, run_trip_search
from clientops.trip_procedure import TRIP_PROCEDURE_KEY
from approval_fakes import FOREVER, FakeDesk, MemoryStore, ticking_wall
from picking_claude import PickingClaude
from test_stock_sync import data_dir, quarter_second_clock, write_settings
from travel_fakes import CATALOG, HOLD, REFERENCE, RIDES, TRAVEL_ACCOUNTS, TRAVELERS, FakeTravel

__all__ = ["data_dir"]

CONTRACT = Path(__file__).parent / "contract" / "trip_job_notes.json"
RECORD_VARIABLE = "CLIENTOPS_RECORD_CONTRACTS"
SEARCH_JOB = "exec_trip_search"
BOOK_JOB = "exec_trip_book"
ASKED_AT = 1_794_646_848.0
SEARCH_EVENTS = [
    ("job.created", None),
    ("step.started", "sign-in"),
    ("step.done", "sign-in"),
    ("step.started", "search"),
    ("step.done", "search"),
    ("step.started", "seats"),
    ("step.done", "seats"),
    ("step.started", "suggest"),
    ("step.done", "suggest"),
    ("trip.options", None),
]
BOOK_EVENTS_TO_THE_ASK = [
    ("job.created", None),
    ("step.started", "sign-in"),
    ("step.done", "sign-in"),
    ("step.started", "seat"),
    ("step.done", "seat"),
    ("step.started", "hold"),
    ("trip.review", None),
    ("step.done", "hold"),
    ("step.started", "ask"),
    ("approval.requested", "ask"),
]
BOOKED_AFTER_THE_ASK = [
    ("approval.answered", None),
    ("step.decided", "ask"),
    ("step.done", "ask"),
    ("step.started", "confirm"),
    ("trip.booked", None),
    ("step.done", "confirm"),
]


async def no_wait(seconds: float) -> None:
    return None


def opener(site: FakeTravel):
    return lambda: Page(site, runner_command=RUNNER_COMMAND)


def collected():
    sent: list[dict] = []
    return sent, EventNotes(lambda message, tags: sent.append(json.loads(message)))


def shape(events):
    return [(event["type"], event.get("stepId")) for event in events]


def of_type(events, event_type):
    return [event for event in events if event["type"] == event_type]


def claude_answers(*answers) -> FakeClaude:
    fake = FakeClaude()
    for answer in answers:
        if isinstance(answer, dict):
            fake.reply_text(json.dumps(answer))
        else:
            answer(fake)
    return fake


def search(site, claude, *, to="Singapore", date="2026-11-21", client_id="hinata", memory=None):
    sent, notes = collected()
    result = asyncio.run(
        run_trip_search(
            notes, resolve_job(client_id), claude, to=to, date=date, memory=memory, open_page=opener(site), clock=quarter_second_clock(), sleep=no_wait
        )
    )
    assert [note["n"] for note in sent] == list(range(1, len(sent) + 1))
    return result, [note["event"] for note in sent], sent


def book(site, desk, *, option="ka-631", seat="12C", client_id="hinata", cancel=False):
    sent, notes = collected()

    async def job():
        work = asyncio.create_task(
            run_trip_book(
                notes,
                resolve_job(client_id),
                desk,
                option=option,
                seat=seat,
                job_id=BOOK_JOB,
                open_page=opener(site),
                clock=quarter_second_clock(),
                wall=ticking_wall(ASKED_AT),
                sleep=no_wait,
            )
        )
        if cancel:
            await desk.waiting.wait()
            work.cancel()
            return (await asyncio.gather(work, return_exceptions=True))[0]
        return await work

    result = asyncio.run(job())
    assert [note["n"] for note in sent] == list(range(1, len(sent) + 1))
    return result, [note["event"] for note in sent], sent


REPLAY_EVENTS = [
    ("job.created", None),
    ("step.started", "sign-in"),
    ("step.decided", "sign-in"),
    ("step.done", "sign-in"),
    ("step.started", "search"),
    ("step.decided", "search"),
    ("step.done", "search"),
    ("step.started", "seats"),
    ("step.decided", "seats"),
    ("step.done", "seats"),
    ("step.started", "suggest"),
    ("step.done", "suggest"),
    ("trip.options", None),
]


def two_suggestions() -> FakeClaude:
    return claude_answers({"seat": "12C", "reason": "exit-legroom"}, {"seat": "1B", "reason": "together"})


def test_the_first_search_saves_its_steps_and_the_next_one_replays_them_in_one_script_with_no_ai():
    memory = MemoryStore()
    learning = FakeTravel()
    _, learned, _ = search(learning, two_suggestions(), memory=memory)

    assert shape(learned) == [*SEARCH_EVENTS, ("procedure.saved", None)]
    assert learned[-1] == {"type": "procedure.saved", "procedureId": "trip-search", "version": 1, "stepCount": 4, "expectedReplayMs": learned[-1]["expectedReplayMs"]}
    assert set(memory.values) == {TRIP_PROCEDURE_KEY} and TRIP_PROCEDURE_KEY != PROCEDURE_KEY
    saved = memory.values[TRIP_PROCEDURE_KEY]
    assert saved["procedureVersion"] == 1 and saved["listName"] == "Options"
    assert {option: decision["seat"] for option, decision in saved["decisions"].items()} == {"ka-631": "12C", "mr-12": "1B"}

    replaying = FakeTravel()
    silent = FakeClaude()
    result, replayed, _ = search(replaying, silent, memory=memory)

    assert shape(replayed) == REPLAY_EVENTS
    assert replayed[0]["run"] == {"kind": "replay", "procedureVersion": 1}
    assert [event["rung"] for event in of_type(replayed, "step.decided")] == ["script", "script", "script"]
    assert [(event["stepId"], event["tookMs"], event.get("detail")) for event in of_type(replayed, "step.done")] == [
        ("sign-in", 180, None),
        ("search", 60, "2 options"),
        ("seats", 140, "360 seats"),
        ("suggest", 250, "Same seats as last time"),
    ]
    assert of_type(replayed, "trip.options") == of_type(learned, "trip.options")
    assert silent.received == [] and result["claudeCalls"] == 0 and result["procedureVersion"] == 1
    assert [command["command"] for command in replaying.commands] == ["script", "close"]
    assert len(learning.commands) > 10
    assert replaying.typed == ["$LP_TRAVEL_USERNAME", "$LP_TRAVEL_PASSWORD"]


def test_the_saved_script_carries_only_placeholders_and_the_steps_the_screen_shows():
    memory = MemoryStore()
    search(FakeTravel(), two_suggestions(), memory=memory)
    replaying = FakeTravel()
    search(replaying, FakeClaude(), memory=memory)

    [plan] = replaying.scripts
    assert [step["stepId"] for step in plan["steps"]] == ["sign-in", "search", "seats"]
    source = json.dumps(plan)
    assert "$LP_TRAVEL_USERNAME" in source and "$LP_TRAVEL_PASSWORD" in source
    assert not [value for value in replaying.vault.values() if value in source]
    assert plan["steps"][1]["ops"][0] == {"op": "goto", "url": "http://127.0.0.1:3003/search?from=Tokyo&to=Singapore&date=2026-11-21"}
    script = next(command["source"] for command in replaying.commands if command["command"] == "script")
    assert plan_of(script) == plan


def test_a_seat_map_that_changed_asks_claude_again_for_that_ride_only(monkeypatch):
    memory = MemoryStore()
    search(FakeTravel(), two_suggestions(), memory=memory)
    changed = RIDES["mr-12"].taken | {"1B"}
    monkeypatch.setitem(RIDES, "mr-12", RIDES["mr-12"].__class__(**{**RIDES["mr-12"].__dict__, "taken": changed}))
    monkeypatch.setitem(CATALOG, "Singapore", (RIDES["ka-631"], RIDES["mr-12"]))
    claude = claude_answers({"seat": "1C", "reason": "aisle"})

    result, events, _ = search(FakeTravel(), claude, memory=memory)

    [found] = of_type(events, "trip.options")
    assert [option["suggestion"] for option in found["options"]] == [
        {"seat": "12C", "reason": "exit-legroom", "by": "claude"},
        {"seat": "1C", "reason": "aisle", "by": "claude"},
    ]
    assert len(claude.received) == 1 and result["claudeCalls"] == 1
    assert memory.values[TRIP_PROCEDURE_KEY]["decisions"]["mr-12"]["seat"] == "1C"


@pytest.mark.parametrize("breaks_at", ["sign-in", "search", "seats"])
def test_a_saved_step_that_fails_falls_back_to_reading_the_page_for_that_run_and_saves_again(breaks_at):
    memory = MemoryStore()
    search(FakeTravel(), two_suggestions(), memory=memory)
    site = FakeTravel(script_breaks_at=breaks_at)

    result, events, _ = search(site, FakeClaude(), memory=memory)

    decided = {event["stepId"]: event for event in of_type(events, "step.decided")}
    assert decided[breaks_at] == {"type": "step.decided", "stepId": breaks_at, "rung": "tree", "tookMs": 0, "escalatedFrom": ["script"]}
    assert [event["stepId"] for event in of_type(events, "step.started")] == ["sign-in", "search", "seats", "suggest"]
    assert [event["stepId"] for event in of_type(events, "step.done")] == ["sign-in", "search", "seats", "suggest"]
    assert len(of_type(events, "trip.options")[0]["options"]) == 2
    assert events[-1]["type"] == "procedure.saved" and events[-1]["version"] == 2
    assert memory.values[TRIP_PROCEDURE_KEY]["procedureVersion"] == 2
    assert result["status"] == "done" and result["procedureVersion"] == 2 and result["claudeCalls"] == 0
    assert any(command["command"] == "tree" for command in site.commands)


def test_a_saved_procedure_that_cannot_be_read_is_learned_again():
    memory = MemoryStore()
    memory.values[TRIP_PROCEDURE_KEY] = {"version": 1, "procedureVersion": "two"}

    _, events, _ = search(FakeTravel(), None, memory=memory)

    assert events[0]["run"] == {"kind": "learn"}
    assert events[-1]["type"] == "procedure.saved" and events[-1]["version"] == 1


def test_the_travel_site_is_resolved_for_hinata_and_kumo_with_its_own_vault_names():
    for client_id in ("hinata", "kumo"):
        boundary = resolve_job(client_id)
        assert boundary.travel is not None
        assert (boundary.travel.host, boundary.travel.name, boundary.travel.url) == ("travel.hikari-travel.example", "Hikari Travel", "http://127.0.0.1:3003")
        assert boundary.travel.sign_in_url == "http://127.0.0.1:3003/login"
        assert boundary.trip_names == ("LP_TRAVEL_USERNAME", "LP_TRAVEL_PASSWORD", "LP_TRAVELER_NAME", "LP_TRAVELER_EMAIL")
        assert not set(boundary.trip_names) & set(boundary.vault.placeholders)


def test_the_search_reads_every_option_and_its_seats_and_asks_claude_once_per_option():
    site = FakeTravel()
    claude = claude_answers({"seat": "12C", "reason": "exit-legroom"}, {"seat": "1B", "reason": "together"})

    result, events, _ = search(site, claude)

    assert shape(events) == SEARCH_EVENTS
    assert events[0] == {
        "type": "job.created",
        "clientName": "Hinata Skincare",
        "title": SEARCH_TITLE,
        "request": "Find trips from Tokyo to Singapore on 2026-11-21",
        "run": {"kind": "learn"},
        "plan": [
            {"stepId": "sign-in", "title": "Sign in to Hikari Travel"},
            {"stepId": "search", "title": "Find trips to Singapore"},
            {"stepId": "seats", "title": "Read the seats"},
            {"stepId": "suggest", "title": "Suggest a seat"},
        ],
    }
    [found] = of_type(events, "trip.options")
    assert (found["to"], found["date"]) == ("Singapore", "2026-11-21")
    assert [
        {key: option[key] for key in ("id", "name", "kind", "depart", "arrive", "currency", "price")} for option in found["options"]
    ] == [
        {"id": "ka-631", "name": "Kestrel Air KA 631", "kind": "flight", "depart": "08:05", "arrive": "14:35", "currency": "JPY", "price": 72800},
        {"id": "mr-12", "name": "Meridian MR 12", "kind": "flight", "depart": "10:40", "arrive": "17:20", "currency": "JPY", "price": 68500},
    ]
    ka, mr = found["options"]
    assert len(ka["seats"]) == 180 and len(mr["seats"]) == 180
    assert ka["seats"][0] == {"id": "1A", "position": "window", "exitRow": False, "available": False}
    assert next(seat for seat in ka["seats"] if seat["id"] == "12C") == {"id": "12C", "position": "aisle", "exitRow": True, "available": True}
    assert next(seat for seat in ka["seats"] if seat["id"] == "12A")["available"] is False
    assert ka["suggestion"] == {"seat": "12C", "reason": "exit-legroom", "by": "claude"}
    assert mr["suggestion"] == {"seat": "1B", "reason": "together", "by": "claude"}
    assert result["status"] == "done" and result["options"] == 2 and result["claudeCalls"] == 2
    assert [call["outcome"] for call in result["claudeUsage"]["suggest"]] == ["chosen", "chosen"]
    assert [call.model for call in claude.received] == [CLAUDE_MODEL, CLAUDE_MODEL]
    for call in claude.received:
        assert call.output_config["effort"] == CLAUDE_EFFORT
        schema = call.output_config["format"]["schema"]
        assert call.output_config["format"]["type"] == "json_schema"
        assert schema["properties"]["reason"]["enum"] == list(SEAT_REASONS)
        assert "1A" not in schema["properties"]["seat"]["enum"]
    assert site.typed == ["$LP_TRAVEL_USERNAME", "$LP_TRAVEL_PASSWORD"]
    assert site.clicked == ["Sign in", "Choose seats for Kestrel Air KA 631", "Choose seats for Meridian MR 12"]
    assert site.held is None and not site.booked
    assert site.commands[-1] == {"command": "close"}


@pytest.mark.parametrize(("to", "ids", "kind"), [("Kyoto", ["nozomi-21", "nozomi-27"], "rail"), ("Seoul", ["ka-712", "ns-104"], "flight")])
def test_rail_and_other_flights_are_read_the_same_way(to, ids, kind):
    result, events, _ = search(FakeTravel(), None, to=to)

    [found] = of_type(events, "trip.options")
    assert [option["id"] for option in found["options"]] == ids
    assert {option["kind"] for option in found["options"]} == {kind}
    seats = len(RIDES[ids[0]].seats())
    assert all(len(option["seats"]) == seats for option in found["options"])
    assert result["status"] == "done"


def test_with_claude_off_the_rule_suggests_the_first_free_window_seat_from_the_front(data_dir):
    write_settings(data_dir, {"hinata": {"useClaude": False}})
    claude = FakeClaude()

    result, events, _ = search(FakeTravel(), claude)

    [found] = of_type(events, "trip.options")
    assert [option["suggestion"] for option in found["options"]] == [
        {"seat": "3F", "reason": "window", "by": "rule"},
        {"seat": "2F", "reason": "window", "by": "rule"},
    ]
    assert claude.received == [] and result["claudeCalls"] == 0
    assert of_type(events, "step.done")[-1]["detail"] == "By the seat rule"


def taken_seat(fake):
    fake.reply_text(json.dumps({"seat": "12A", "reason": "exit-legroom"}))


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ({"seat": "12C", "reason": "window"}, "wrong-reason"),
        ({"seat": "12C", "reason": "exit-legroom", "note": "the customer is Aoi"}, "not-json"),
        ({"seat": "99Z", "reason": "aisle"}, "unknown-seat"),
        (taken_seat, "unknown-seat"),
        (lambda fake: fake.reply_text("Seat 12C because of the legroom."), "not-json"),
        (lambda fake: fake.reply_cut_off('{"seat": "12'), "not-json"),
        (lambda fake: fake.fail_overloaded(), "unavailable"),
    ],
)
def test_any_answer_that_is_not_a_free_seat_with_a_true_reason_falls_back_to_the_rule(answer, outcome):
    claude = claude_answers(answer, {"seat": "1B", "reason": "together"})

    result, events, _ = search(FakeTravel(), claude)

    [found] = of_type(events, "trip.options")
    assert found["options"][0]["suggestion"] == {"seat": "3F", "reason": "window", "by": "rule"}
    assert found["options"][1]["suggestion"]["by"] == "claude"
    assert result["claudeUsage"]["suggest"][0]["outcome"] == outcome


def test_the_fake_claude_used_without_a_key_suggests_a_free_window_seat():
    claude = PickingClaude()

    result, events, _ = search(FakeTravel(), claude)

    [found] = of_type(events, "trip.options")
    assert [option["suggestion"] for option in found["options"]] == [
        {"seat": "3F", "reason": "window", "by": "claude"},
        {"seat": "2F", "reason": "window", "by": "claude"},
    ]
    assert result["claudeCalls"] == 2


def test_a_refused_model_setting_falls_back_to_the_rule_without_another_call():
    calls = []

    async def refused(**call):
        calls.append(call)
        raise ModelNotAllowed("not the pinned model")

    result, events, _ = search(FakeTravel(), refused)

    [found] = of_type(events, "trip.options")
    assert {option["suggestion"]["by"] for option in found["options"]} == {"rule"}
    assert len(calls) == 1 and result["claudeCalls"] == 0


def test_over_the_monthly_budget_the_rule_suggests_and_claude_is_never_called(data_dir):
    claude = FakeClaude()
    meter = JobMeter("hinata", SEARCH_JOB, ledger=CostLedger(data_dir), cap=lambda: 0.0, estimated=True)

    async def job():
        sent, notes = collected()
        with metering(meter):
            return await run_trip_search(notes, resolve_job("hinata"), claude, to="Singapore", date="2026-11-21", open_page=opener(FakeTravel()), clock=quarter_second_clock(), sleep=no_wait), sent

    result, sent = asyncio.run(job())

    [found] = of_type([note["event"] for note in sent], "trip.options")
    assert {option["suggestion"]["by"] for option in found["options"]} == {"rule"}
    assert claude.received == [] and result["claudeCalls"] == 0


def test_a_slow_page_is_read_again_until_it_has_moved_on():
    site = FakeTravel(lagging=True)

    result, events, _ = search(site, None)

    tree_reads = [command for command in site.commands if command["command"] == "tree"]
    assert len(tree_reads) > len([command for command in site.commands if command["command"] in ("open", "click")])
    assert result["status"] == "done"


def test_a_refused_login_stops_the_search_with_nothing_booked():
    result, events, _ = search(FakeTravel(refuse_login=True), None)

    assert shape(events) == [("job.created", None), ("step.started", "sign-in"), ("job.failed", "sign-in")]
    assert events[-1]["message"] == "I couldn't sign in to Hikari Travel with the saved login, so I stopped. Nothing was booked."
    assert result["status"] == "failed" and result["problem"] == "wait-failed"


def test_a_missing_traveller_login_is_named_without_its_value():
    site = FakeTravel(vault={"LP_TRAVEL_USERNAME": "hinata-trips"})

    result, events, _ = search(site, None)

    assert events[-1]["type"] == "job.failed"
    assert "login or the traveller's details aren't saved" in events[-1]["message"]
    assert result["problem"] == "placeholder-unset"


@pytest.mark.parametrize(("to", "date"), [("Kumo Coffee's login", "2026-11-21"), ("Singapore", "tomorrow"), ("Singapore", "2026-02-30")])
def test_a_place_or_date_outside_the_contract_stops_before_the_computer_is_opened(to, date):
    site = FakeTravel()

    result, events, _ = search(site, None, to=to, date=date)

    assert shape(events) == [("job.created", None), ("job.failed", None)]
    assert site.commands == []
    assert result["problem"] == "bad-input"


def test_the_booking_holds_the_seat_asks_and_after_the_ok_confirms_and_reads_the_reference():
    site = FakeTravel()
    desk = FakeDesk("approved")

    result, events, _ = book(site, desk)

    assert shape(events) == BOOK_EVENTS_TO_THE_ASK + BOOKED_AFTER_THE_ASK
    assert events[0]["title"] == BOOK_TITLE and events[0]["request"] == "Book seat 12C on ka-631"
    [review] = of_type(events, "trip.review")
    assert review == {
        "type": "trip.review",
        "option": "ka-631",
        "seat": "12C",
        "name": "Kestrel Air KA 631",
        "depart": "08:05",
        "arrive": "14:35",
        "currency": "JPY",
        "price": 72800,
        "hold": HOLD,
    }
    [asked] = of_type(events, "approval.requested")
    assert asked == {
        "type": "approval.requested",
        "approvalId": f"apr-{BOOK_JOB}",
        "stepId": "ask",
        "question": "Book seat 12C on Kestrel Air KA 631?",
        "undoable": False,
        "booking": {"option": "ka-631", "seat": "12C"},
    }
    assert of_type(events, "approval.answered") == [{"type": "approval.answered", "approvalId": f"apr-{BOOK_JOB}", "decision": "approved", "by": "You"}]
    assert of_type(events, "trip.booked") == [{"type": "trip.booked", "option": "ka-631", "seat": "12C", "reference": REFERENCE}]
    assert desk.paused == [{"approvalId": f"apr-{BOOK_JOB}", "executionId": BOOK_JOB, "expiresInHours": 720, "timeout": 60}]
    assert result == {"status": "done", "reference": REFERENCE}
    assert site.typed == ["$LP_TRAVEL_USERNAME", "$LP_TRAVEL_PASSWORD", "$LP_TRAVELER_NAME", "$LP_TRAVELER_EMAIL"]
    assert site.clicked == ["Sign in", "Seat 12C, aisle, exit row, available", "Hold seat", "Confirm booking"]
    assert site.booked and site.held["name"] == TRAVELERS["hinata"][0]
    assert site.commands[-1] == {"command": "close"}


@pytest.mark.parametrize("decision", ["rejected", "expired"])
def test_a_no_leaves_the_hold_unconfirmed(decision):
    site = FakeTravel()
    statuses = (decision,) if decision == "expired" else ()

    result, events, _ = book(site, FakeDesk(decision, statuses=statuses))

    assert shape(events) == BOOK_EVENTS_TO_THE_ASK + [("job.failed", "ask")]
    assert events[-1]["reason"] == "stopped"
    assert events[-1]["message"].endswith("The seat is held but not booked.")
    assert result["status"] == "stopped" and result["held"] is True
    assert "Confirm booking" not in site.clicked and not site.booked


def test_stopping_while_it_waits_leaves_the_hold_unconfirmed_and_releases_the_approval():
    site = FakeTravel()
    desk = FakeDesk(FOREVER)

    outcome, events, _ = book(site, desk, cancel=True)

    assert isinstance(outcome, asyncio.CancelledError)
    assert shape(events) == BOOK_EVENTS_TO_THE_ASK + [("job.failed", "ask")]
    assert events[-1] == {"type": "job.failed", "reason": "stopped", "message": "Stopped before booking. The seat is held but not booked.", "stepId": "ask"}
    assert desk.released == [f"apr-{BOOK_JOB}"]
    assert not site.booked and site.commands[-1] == {"command": "close"}


def test_a_booking_page_without_a_reference_says_to_check_the_site_instead_of_claiming_nothing_was_booked(monkeypatch):
    monkeypatch.setattr("travel_fakes.REFERENCE", "nope")
    site = FakeTravel()

    result, events, _ = book(site, FakeDesk("approved"))

    assert events[-1]["type"] == "job.failed" and events[-1]["stepId"] == "confirm"
    assert events[-1]["message"] == (
        "The booking didn't show a reference on Hikari Travel, so I stopped. "
        "I pressed Confirm booking, so check the booking on the travel site before trying again."
    )
    assert result["problem"] == "no-reference" and of_type(events, "trip.booked") == []


@pytest.mark.parametrize(("seat", "problem"), [("1A", "seat-taken"), ("31A", "no-such-seat")])
def test_a_seat_that_is_taken_or_missing_stops_before_anything_is_held(seat, problem):
    site = FakeTravel()
    desk = FakeDesk("approved")

    result, events, _ = book(site, desk, seat=seat)

    assert events[-1]["type"] == "job.failed" and events[-1]["stepId"] == "seat"
    assert events[-1]["message"].endswith("Nothing was booked.")
    assert result["problem"] == problem
    assert site.held is None and desk.paused == []


@pytest.mark.parametrize(("option", "seat"), [("../admin", "12C"), ("ka-631", "12C; reboot"), ("ka-631", "")])
def test_an_option_or_seat_outside_the_contract_stops_before_the_computer_is_opened(option, seat):
    site = FakeTravel()

    result, events, _ = book(site, FakeDesk("approved"), option=option, seat=seat)

    assert shape(events) == [("job.created", None), ("job.failed", None)]
    assert site.commands == [] and result["problem"] == "bad-input"


def load_node():
    return importlib.import_module("clientops.node")


def test_the_node_runs_both_trip_jobs_on_the_clients_own_computer(monkeypatch):
    node = load_node()
    site = FakeTravel()
    written: list[dict] = []
    opened: list[str] = []
    memories: dict[str, MemoryStore] = {}
    claude = claude_answers({"seat": "12C", "reason": "exit-legroom"}, {"seat": "1B", "reason": "together"})

    def job_computer(boundary: ClientBoundary) -> JobComputer:
        opened.append(boundary.client.id)
        return JobComputer(site, RUNNER_COMMAND)

    async def at_once(seconds):
        return None

    monkeypatch.setattr(ClientBoundary, "job_computer", job_computer)
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: written.append(json.loads(message)))
    monkeypatch.setattr(node, "claude_ai", lambda agent: claude)
    monkeypatch.setattr(node, "approval_desk", lambda agent: FakeDesk("approved"))
    monkeypatch.setattr(node, "client_memory", lambda boundary, context: memories.setdefault(boundary.client.id, MemoryStore()))
    monkeypatch.setattr("clientops.trip.asyncio.sleep", at_once)

    searched = asyncio.run(node.trip_search(client_id="hinata", to="Singapore", date="2026-11-21"))
    booked = asyncio.run(node.trip_book(client_id="hinata", option="ka-631", seat="12C"))
    replayed = asyncio.run(node.trip_search(client_id="hinata", to="Singapore", date="2026-11-21"))

    assert searched["clientId"] == "hinata" and searched["status"] == "done" and searched["claudeCalls"] == 2
    assert replayed["status"] == "done" and replayed["claudeCalls"] == 0 and replayed["procedureVersion"] == 1
    assert set(memories) == {"hinata"} and TRIP_PROCEDURE_KEY in memories["hinata"].values
    assert booked["status"] == "done" and booked["reference"] == REFERENCE
    assert opened == ["hinata", "hinata", "hinata"]
    assert [note["event"]["type"] for note in written].count("trip.booked") == 1


def test_the_node_refuses_a_trip_for_a_client_without_a_computer(monkeypatch):
    node = load_node()
    written: list[dict] = []
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: written.append(json.loads(message)))

    result = asyncio.run(node.trip_search(client_id="sora", to="Singapore", date="2026-11-21"))

    assert result["status"] == "failed" and result["reason"] == "not_set_up"
    assert [note["event"]["type"] for note in written] == ["job.created", "job.failed"]


def test_the_travel_login_and_the_traveller_go_into_the_local_vault_with_the_store_logins(monkeypatch, data_dir):
    monkeypatch.setenv(COMPUTERS_VARIABLE, "local")
    username, password = TRAVEL_ACCOUNTS["hinata"]
    traveller, email = TRAVELERS["hinata"]
    values = {"LP_TRAVEL_USERNAME": username, "LP_TRAVEL_PASSWORD": password, "LP_TRAVELER_NAME": traveller, "LP_TRAVELER_EMAIL": email}

    status = put_logins("hinata", values)

    assert status.present == () and status.missing == resolve_job("hinata").vault.placeholders
    vault = json.loads((data_dir / "computers" / "hinata" / "vault.json").read_text())
    assert vault["values"] == values
    assert oct((data_dir / "computers" / "hinata" / "vault.json").stat().st_mode & 0o777) == "0o600"


def contract_runs(monkeypatch) -> dict:
    memory = MemoryStore()
    _, _, searched = search(FakeTravel(), two_suggestions(), memory=memory)
    _, _, replayed = search(FakeTravel(), FakeClaude(), memory=memory)
    _, _, booked = book(FakeTravel(), FakeDesk("approved"))
    _, _, stopped = book(FakeTravel(), FakeDesk(FOREVER), cancel=True)
    return {
        "input": {"client_id": "hinata", "to": "Singapore", "date": "2026-11-21"},
        "jobId": SEARCH_JOB,
        "notes": searched,
        "replay": {"input": {"client_id": "hinata", "to": "Singapore", "date": "2026-11-21"}, "jobId": SEARCH_JOB, "notes": replayed},
        "book": {"input": {"client_id": "hinata", "option": "ka-631", "seat": "12C"}, "jobId": BOOK_JOB, "notes": booked},
        "stopped": {"input": {"client_id": "hinata", "option": "ka-631", "seat": "12C"}, "jobId": BOOK_JOB, "notes": stopped},
    }


def test_the_trip_notes_are_exactly_the_contract_the_screen_reads(monkeypatch):
    produced = contract_runs(monkeypatch)
    if os.environ.get(RECORD_VARIABLE) == "1":
        CONTRACT.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(CONTRACT.read_text())


def test_nothing_sent_to_claude_or_written_in_a_trip_carries_a_traveller_a_login_a_name_or_a_price(monkeypatch, caplog, capsys):
    caplog.set_level("DEBUG")
    claude = claude_answers({"seat": "12C", "reason": "exit-legroom"}, {"seat": "1B", "reason": "together"})
    searching = FakeTravel()
    replaying = FakeTravel()
    booking = FakeTravel()
    memory = MemoryStore()

    _, _, searched = search(searching, claude, memory=memory)
    _, _, replayed = search(replaying, FakeClaude(), memory=memory)
    _, _, booked = book(booking, FakeDesk("approved"))

    to_claude = claude.everything_sent()
    notes = json.dumps([*searched, *replayed, *booked], ensure_ascii=False)
    commands = json.dumps([*searching.commands, *replaying.commands, *booking.commands], ensure_ascii=False)
    records = json.dumps(memory.values, ensure_ascii=False)
    captured = capsys.readouterr()
    logs = "\n".join([caplog.text, captured.out, captured.err])
    vault_values = list(booking.vault.values())
    rides = [ride for ride in CATALOG["Singapore"]]
    ride_words = [text for ride in rides for text in (ride.name, ride.depart, ride.arrive, f"{ride.price:,}", str(ride.price))]

    def found(texts, where):
        return [text for text in texts if text.lower() in where.lower()]

    assert found([*vault_values, *TRAVELERS["hinata"], *ride_words, "JPY", "Hinata", "Singapore", "Kestrel", "Meridian", "@"], to_claude) == []
    assert not re.search(r"\d{1,3},\d{3}|\d{2}:\d{2}", to_claude)
    for written in (notes, commands, logs, records):
        assert found([*vault_values, *TRAVELERS["hinata"]], written) == []
    assert {command["text"] for command in [*searching.commands, *booking.commands] if command["command"] == "fill"} == {
        "$LP_TRAVEL_USERNAME",
        "$LP_TRAVEL_PASSWORD",
        "$LP_TRAVELER_NAME",
        "$LP_TRAVELER_EMAIL",
    }
