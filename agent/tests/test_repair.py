import asyncio
import json
import re
from datetime import UTC, datetime

import pytest

import clientops.replay as replay
from clientops.claude_call import CLAUDE_EFFORT, CLAUDE_MODEL, ELEMENT_CHOICE_SCHEMA
from clientops.compile_script import compile_read_script
from clientops.cost import CostLedger, JobMeter, metering
from clientops.externals.fake_ai import FakeClaude
from clientops.procedure import Hint, Target
from clientops.records import EventNotes
from clientops.refind import NotFound, Recovered, refind_by_tree
from clientops.repair import BEFORE_MARK, RepairHelp, RepairSession, RepairStopped, goal_for, question_for, repair_step
from clientops.stock_sync import NEXT_PAGE_NEEDED, SAVE_BUTTON, STORE_PLACE, SUPPLIER_PLACE, sign_in_goals
from approval_fakes import FakeDesk
from picking_claude import PickingClaude
from test_refind import SAVE_CSS, commands_after_the_script, failed_run
from test_replay import KUMO_DAY2, kumo_procedure
from test_stock_sync import VAULT, data_dir, install_sites, write_settings

__all__ = ["data_dir"]

RENAMED = "Update inventory"
DECOY = "Save changes to all locations"
CHOICE_ID = "cho-exec_replay-check-save"
NOV_14 = datetime(2026, 11, 14, 9, 0, tzinfo=UTC).timestamp()


class Session:
    def __init__(self, ai=None, desk=None) -> None:
        self.sent: list[dict] = []
        self.notes = EventNotes(lambda message, tags: self.sent.append(json.loads(message)))
        self.desk = desk or FakeDesk()
        self.session = RepairSession(RepairHelp(ai, self.desk, "exec_replay"), self.notes, "exec_replay")

    @property
    def events(self) -> list[dict]:
        return [note["event"] for note in self.sent]


def renamed_failure(monkeypatch, **options):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED, **options)
    failed = failed_run(kumo_procedure())
    not_found = asyncio.run(refind_by_tree(failed))
    assert isinstance(not_found, NotFound)
    return sites, failed, not_found


def repair(session: Session, failed, not_found):
    return asyncio.run(repair_step(failed, not_found, session.session))


def sent_lines(claude) -> list[str]:
    [call] = claude.fake.received if isinstance(claude, PickingClaude) else claude.received
    return call.user.splitlines()


def test_claude_gets_only_the_old_hint_and_the_names_of_the_buttons_and_fields_in_that_form(monkeypatch):
    sites, failed, not_found = renamed_failure(monkeypatch)
    claude = PickingClaude()

    repair(Session(claude), failed, not_found)

    [call] = claude.fake.received
    assert call.model == CLAUDE_MODEL
    assert call.output_config == {"effort": CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": ELEMENT_CHOICE_SCHEMA}}
    lines = call.user.splitlines()
    [before] = [line for line in lines if line.startswith(BEFORE_MARK)]
    assert json.loads(before.removeprefix(BEFORE_MARK)) == {"role": "button", "name": "Save changes", "form": "/inventory"}
    listed = [json.loads(line) for line in lines if line.startswith("{")]
    assert all(set(entry) == {"id", "role", "name"} for entry in listed)
    assert [(entry["role"], entry["name"]) for entry in listed] == [
        *[("spinbutton", "Available for [name]")] * len(KUMO_DAY2["store"]),
        ("link", "Discard"),
        ("button", RENAMED),
    ]
    everything = claude.everything_sent()
    for absent in ("Sign out", "storefront", "Kumo", "/logout"):
        assert absent not in everything
    for row in KUMO_DAY2["store"]:
        assert row["sku"] not in everything and row["name"] not in everything
    for value in VAULT.values():
        assert value not in everything
    assert not re.search(r"\d", everything)
    assert sites.saved is False


def test_the_goal_of_the_step_is_sent_so_claude_knows_what_the_old_element_did(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch)
    claude = PickingClaude()

    repair(Session(claude), failed, not_found)

    lines = sent_lines(claude)
    assert lines[0] == f"Step goal: {SAVE_BUTTON.text}"
    assert "This step needs one element to click." in lines[1]


def test_a_confident_valid_pick_rebuilds_only_that_element_with_the_new_button(monkeypatch):
    sites, failed, not_found = renamed_failure(monkeypatch)
    claude = PickingClaude()
    session = Session(claude)

    found = repair(session, failed, not_found)

    assert isinstance(found, Recovered)
    assert (found.step_id, found.element, found.rung, found.escalated_from) == ("check-save", "save", "claude", ("script", "tree"))
    assert found.procedure.read.save_button == Target(SAVE_CSS, RENAMED, Hint("button", RENAMED, "/inventory"))
    recorded = kumo_procedure()
    assert (found.procedure.read.supplier, found.procedure.read.store, found.procedure.read.next_page) == (recorded.read.supplier, recorded.read.store, recorded.read.next_page)
    assert found.procedure.version == 1
    assert found.procedure.script == compile_read_script(found.procedure.read, failed.boundary)
    assert found.took_ms > not_found.took_ms
    assert found.snapshot is not None and found.snapshot.host == "127.0.0.1:3002"
    assert claude.calls == 1
    assert [call["outcome"] for call in session.session.usage["check-save"]] == ["chosen"]
    assert session.events == []
    assert commands_after_the_script(sites)[-1] == "close"
    assert sites.saved is False


def test_two_save_like_buttons_leave_claude_unsure_so_the_person_picks_one_of_the_two(monkeypatch):
    sites, failed, not_found = renamed_failure(monkeypatch, extra_store_buttons=(DECOY,))
    claude = PickingClaude()
    session = Session(claude, FakeDesk(("approved", "1016")))

    found = repair(session, failed, not_found)

    assert claude.calls == 1
    assert session.events == [
        {
            "type": "choice.requested",
            "choiceId": CHOICE_ID,
            "stepId": "check-save",
            "question": "Which button saves the changes now?",
            "before": "Save changes",
            "candidates": [{"ref": "1016", "role": "button", "name": RENAMED}, {"ref": "1100", "role": "button", "name": DECOY}],
        },
        {"type": "choice.answered", "choiceId": CHOICE_ID, "ref": "1016", "by": "You"},
    ]
    assert session.desk.paused == [{"approvalId": CHOICE_ID, "executionId": "exec_replay", "expiresInHours": 720, "timeout": 60}]
    assert (found.rung, found.escalated_from) == ("human", ("script", "tree", "claude"))
    assert found.procedure.read.save_button == Target(SAVE_CSS, RENAMED, Hint("button", RENAMED, "/inventory"))
    assert sites.saved is False


def test_the_person_can_pick_the_other_button_and_that_one_is_used(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch, extra_store_buttons=(DECOY,))

    found = repair(Session(PickingClaude(), FakeDesk(("approved", "1100"))), failed, not_found)

    assert found.procedure.read.save_button == Target(SAVE_CSS, DECOY, Hint("button", DECOY, "/inventory"))


def test_with_claude_off_for_the_client_the_person_is_asked_and_claude_is_never_called(monkeypatch, data_dir):
    write_settings(data_dir, {"kumo": {"useClaude": False}})
    _, failed, not_found = renamed_failure(monkeypatch)
    claude = PickingClaude()
    session = Session(claude, FakeDesk(("approved", "1016")))

    found = repair(session, failed, not_found)

    assert claude.calls == 0
    assert session.session.usage == {}
    assert session.events[0]["candidates"] == [{"ref": "1016", "role": "button", "name": RENAMED}]
    assert (found.rung, found.escalated_from) == ("human", ("script", "tree"))


def test_without_a_way_to_call_claude_the_person_is_asked(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch)
    session = Session(None, FakeDesk(("approved", "1016")))

    found = repair(session, failed, not_found)

    assert (found.rung, found.escalated_from) == ("human", ("script", "tree"))


def test_over_the_monthly_cap_claude_is_not_called_and_the_person_picks(monkeypatch, tmp_path):
    _, failed, not_found = renamed_failure(monkeypatch)
    claude = PickingClaude()
    session = Session(claude, FakeDesk(("approved", "1016")))
    meter = JobMeter("kumo", "exec_replay", ledger=CostLedger(tmp_path), cap=lambda: 0.01, estimated=False, wall=lambda: NOV_14)

    with metering(meter):
        found = repair(session, failed, not_found)

    assert claude.calls == 0
    assert (found.rung, found.escalated_from) == ("human", ("script", "tree"))


def test_two_unusable_answers_hand_the_step_to_the_person(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch)
    claude = FakeClaude()
    claude.reply_text("Press Update inventory.")
    claude.reply_choice("zz", "click")
    session = Session(claude, FakeDesk(("approved", "1016")))

    found = repair(session, failed, not_found)

    assert len(claude.received) == 2
    assert [call["outcome"] for call in session.session.usage["check-save"]] == ["not-json", "unknown-element"]
    assert (found.rung, found.escalated_from) == ("human", ("script", "tree", "claude"))


def test_a_confident_pick_whose_name_is_not_the_only_one_goes_to_the_person(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch, extra_store_buttons=(RENAMED,))
    claude = FakeClaude()
    listed = []

    async def pick_first_button(*args, **kwargs):
        listed.extend(json.loads(line) for line in kwargs["user"].splitlines() if line.startswith("{"))
        claude.reply_choice(next(entry["id"] for entry in listed if entry["role"] == "button"), "click")
        return await FakeClaude.__call__(claude, *args, **kwargs)

    session = Session(pick_first_button, FakeDesk(("approved", "1016")))

    found = repair(session, failed, not_found)

    assert session.events[0]["candidates"] == [{"ref": "1016", "role": "button", "name": RENAMED}, {"ref": "1100", "role": "button", "name": RENAMED}]
    assert found.rung == "human"


def test_none_of_these_stops_without_choosing_anything(monkeypatch):
    sites, failed, not_found = renamed_failure(monkeypatch, extra_store_buttons=(DECOY,))
    session = Session(PickingClaude(), FakeDesk(("rejected", "")))

    with pytest.raises(RepairStopped) as stopped:
        repair(session, failed, not_found)

    assert stopped.value.message == "You picked none of the buttons, so I stopped. Nothing was changed."
    assert stopped.value.problem == "none-chosen"
    assert session.events[-1] == {"type": "choice.answered", "choiceId": CHOICE_ID, "ref": None, "by": "You"}
    assert sites.saved is False


def test_a_pick_that_is_not_one_of_the_candidates_is_never_used(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch, extra_store_buttons=(DECOY,))
    session = Session(PickingClaude(), FakeDesk(("approved", "705")))

    with pytest.raises(RepairStopped) as stopped:
        repair(session, failed, not_found)

    assert stopped.value.problem == "choice-unreadable"
    assert stopped.value.message == "I couldn't tell which button you picked, so I stopped. Nothing was changed."
    assert [event["type"] for event in session.events] == ["choice.requested"]


def test_a_question_that_ends_without_an_answer_stops(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch, extra_store_buttons=(DECOY,))
    session = Session(PickingClaude(), FakeDesk("expired", statuses=("expired",)))

    with pytest.raises(RepairStopped) as stopped:
        repair(session, failed, not_found)

    assert stopped.value.problem == "choice-unanswered"
    assert stopped.value.message == "The question ended without an answer. Nothing was changed."


def test_stop_while_the_person_is_picking_ends_the_job_before_anything_is_saved(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch, extra_store_buttons=(DECOY,))
    session = Session(PickingClaude(), FakeDesk(asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        repair(session, failed, not_found)

    assert session.events[-1] == {"type": "job.failed", "reason": "stopped", "message": "Stopped before saving. Nothing was changed.", "stepId": "check-save"}
    assert session.desk.released == [CHOICE_ID]


def test_a_form_with_no_element_of_that_role_left_has_nothing_to_offer_and_hands_back_nothing(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch)
    gone = NotFound(not_found.step_id, not_found.element, Hint("button", "Save changes", "/nowhere"), 0, not_found.took_ms)
    claude = PickingClaude()
    session = Session(claude, FakeDesk(("approved", "1016")))

    assert repair(session, failed, gone) is None
    assert claude.calls == 0
    assert session.events == []


def test_without_repair_help_the_hook_hands_back_nothing(monkeypatch):
    _, failed, not_found = renamed_failure(monkeypatch)

    assert asyncio.run(replay.repair_failed_step(failed, not_found)) is None


def test_each_saved_element_has_the_goal_it_was_learned_with_and_a_plain_question():
    supplier_user, supplier_password, supplier_submit = sign_in_goals(SUPPLIER_PLACE)
    store_user, store_password, store_submit = sign_in_goals(STORE_PLACE)
    assert goal_for("sign-in", "user") == supplier_user
    assert goal_for("sign-in", "password") == supplier_password
    assert goal_for("sign-in", "submit") == supplier_submit
    assert goal_for("compare", "user") == store_user
    assert goal_for("compare", "password") == store_password
    assert goal_for("compare", "submit") == store_submit
    assert goal_for("read", "next") == NEXT_PAGE_NEEDED
    assert goal_for("check-save", "save") == SAVE_BUTTON
    assert question_for("save") == "Which button saves the changes now?"
    assert question_for("user") == "Which field takes the username now?"
    assert question_for("password") == "Which field takes the password now?"
    assert question_for("submit") == "Which button signs in now?"
    assert question_for("next") == "Which link goes to the next page now?"
