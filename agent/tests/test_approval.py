import asyncio
import json
from itertools import count

import pytest
from agentfield.exceptions import AgentFieldClientError, MemoryAccessError

from clientops.approval import (
    ANSWER_POLL_SECONDS,
    APPROVAL_EXPIRES_IN_HOURS,
    PENDING_PREFIX,
    UNDO_KEY,
    PendingWrite,
    see_approval_through,
    undo_last_sync,
    wait_for_answer,
)
from clientops.boundary import resolve_job
from clientops.diff import StockChange
from clientops.page import Locator
from clientops.records import EventNotes
from clientops.stock_sync import PreparedWrite
from approval_fakes import FOREVER, FakeDesk, MemoryStore, MemoryUnavailable, ticking_wall
from test_stock_sync import KUMO, data_dir, install_sites

__all__ = ["data_dir"]

STORE_SIGN_IN = (
    Locator("textbox", "Username", "/login"),
    Locator("textbox", "Password", "/login"),
    Locator("button", "Sign in", "/login"),
)
SAVE_BUTTON = Locator("button", "Save changes", "/inventory")
DECOY_BUTTON = Locator("button", "Save changes to all locations", "/inventory")
CHANGES = tuple(StockChange(**change) for change in KUMO["changes"])
FIRST_RUN_NOTES = 21
ASKED_AT = 1_794_636_000.0
APPROVAL_ID = "apr-exec-test"

AFTER_APPROVAL = [
    ("approval.answered", None),
    ("step.decided", "ask"),
    ("step.done", "ask"),
    ("step.started", "save"),
    ("page.seen", "save"),
    ("page.seen", "save"),
    ("write.done", "save"),
    ("verify.done", "save"),
    ("step.done", "save"),
]


def pending(save_button=SAVE_BUTTON, changes=CHANGES, client_id="kumo") -> PendingWrite:
    return PendingWrite(
        job_id="exec-test",
        run_id="run-test",
        workflow_id="run-test",
        client_id=client_id,
        approval_id=APPROVAL_ID,
        notes_written=FIRST_RUN_NOTES,
        asked_at=ASKED_AT,
        write=PreparedWrite(STORE_SIGN_IN, save_button, changes),
    )


def quarter_second_clock():
    ticks = count()
    return lambda: next(ticks) * 0.25


class Transcript:
    def __init__(self, start: int = FIRST_RUN_NOTES) -> None:
        self.sent: list[dict] = []
        self.notes = EventNotes(lambda message, tags: self.sent.append(json.loads(message)), start=start)

    @property
    def events(self) -> list[dict]:
        return [note["event"] for note in self.sent]

    def shape(self) -> list[tuple[str, str | None]]:
        return [(event["type"], event.get("stepId")) for event in self.events]


def see_through(desk, memory, job=None, *, transcript=None, resumed=False):
    transcript = transcript or Transcript()
    job = job or pending()
    result = asyncio.run(
        see_approval_through(
            desk,
            transcript.notes,
            resolve_job(job.client_id),
            job,
            memory,
            resumed=resumed,
            clock=quarter_second_clock(),
            wall=ticking_wall(ASKED_AT + 11.5),
        )
    )
    return result, transcript


def store_values(sites) -> dict[str, int]:
    return {row["sku"]: row["available"] for row in sites.store_rows}


def test_an_approval_writes_the_twelve_numbers_from_our_diff_and_checks_every_row(monkeypatch):
    sites = install_sites(monkeypatch)
    memory = MemoryStore()
    desk = FakeDesk("approved")

    result, transcript = see_through(desk, memory)

    assert transcript.shape() == AFTER_APPROVAL
    assert [note["n"] for note in transcript.sent] == list(range(FIRST_RUN_NOTES + 1, FIRST_RUN_NOTES + 1 + len(AFTER_APPROVAL)))
    events = transcript.events
    assert events[0] == {"type": "approval.answered", "approvalId": APPROVAL_ID, "decision": "approved", "by": "You"}
    assert events[1] == {"type": "step.decided", "stepId": "ask", "rung": "human", "tookMs": 11500}
    assert events[2] == {"type": "step.done", "stepId": "ask", "summary": "You approved 12 changes", "tookMs": 11500}
    assert events[3] == {"type": "step.started", "stepId": "save", "activity": "Saving 12 changes"}
    assert {event["host"] for event in events if event["type"] == "page.seen"} == {"admin.kumo-coffee.example"}
    assert events[6] == {"type": "write.done", "stepId": "save", "written": 12}
    assert events[7] == {"type": "verify.done", "stepId": "save", "checked": 12, "matched": 12}
    assert events[8]["summary"] == "Saved and checked every row"
    assert events[8]["detail"] == "12 products"
    assert result == {"status": "done", "written": 12, "checked": 12, "matched": 12, "maySaveProcedure": True, "saveStatus": "Saved. 12 products updated."}
    after = store_values(sites)
    for change in CHANGES:
        assert after[change.sku] == change.supplier
    untouched = {row["sku"]: row["available"] for row in KUMO["store"] if row["sku"] not in {change.sku for change in CHANGES}}
    assert {sku: after[sku] for sku in untouched} == untouched
    assert sites.saves == ["Save changes"]


def test_the_numbers_typed_are_ours_and_logins_go_in_only_as_vault_placeholders(monkeypatch):
    sites = install_sites(monkeypatch)

    see_through(FakeDesk("approved"), MemoryStore())

    fills = [command for command in sites.commands if command["command"] == "fill"]
    assert [command["text"] for command in fills[:2]] == ["$LP_STORE_USERNAME", "$LP_STORE_PASSWORD"]
    typed = {command["target"]["name"]: command["text"] for command in fills[2:]}
    assert typed == {f"Available for {change.name}": str(change.supplier) for change in CHANGES}
    opened = [command["url"] for command in sites.commands if command["command"] == "open"]
    assert opened == ["http://127.0.0.1:3002/login"]
    assert sites.commands[-1] == {"command": "close"}


def test_the_previous_values_are_kept_for_undo_before_anything_is_written(monkeypatch):
    install_sites(monkeypatch)
    memory = MemoryStore()

    see_through(FakeDesk("approved"), memory)

    record = memory.values[UNDO_KEY]
    assert record["version"] == 1
    assert record["jobId"] == "exec-test"
    assert record["signIn"] == [locator.to_json() for locator in STORE_SIGN_IN]
    assert record["saveButton"] == SAVE_BUTTON.to_json()
    assert record["rows"] == [{"sku": change.sku, "name": change.name, "before": change.store, "after": change.supplier} for change in CHANGES]
    assert ("set", UNDO_KEY) in memory.calls
    stored = json.dumps(record)
    for value in ("kumo-admin", "kumo-store-demo", "LP_"):
        assert value not in stored


def test_the_pending_approval_is_kept_while_waiting_and_cleared_after(monkeypatch):
    install_sites(monkeypatch)
    memory = MemoryStore()
    desk = FakeDesk("approved")

    see_through(desk, memory)

    key = f"{PENDING_PREFIX}{APPROVAL_ID}"
    assert memory.calls[0] == ("set", key)
    assert PendingWrite.from_json(json.loads(json.dumps(pending().to_json()))) == pending()
    assert key not in memory.values
    assert desk.paused == [{"approvalId": APPROVAL_ID, "executionId": "exec-test", "expiresInHours": APPROVAL_EXPIRES_IN_HOURS, "timeout": ANSWER_POLL_SECONDS}]


def test_the_decoy_save_button_is_caught_by_the_re_read_and_the_steps_are_not_kept(monkeypatch):
    sites = install_sites(monkeypatch, extra_store_buttons=("Save changes to all locations",))

    result, transcript = see_through(FakeDesk("approved"), MemoryStore(), pending(save_button=DECOY_BUTTON))

    changed = {change.sku: change.supplier for change in CHANGES}
    wrong_saved = sum(1 for value in changed.values() if value != 0)
    others = sum(1 for row in KUMO["store"] if row["sku"] not in changed and row["available"] != 0)
    events = transcript.events
    assert transcript.shape() == [*AFTER_APPROVAL[:-2], ("verify.done", "save"), ("job.failed", "save")]
    assert events[-2] == {"type": "verify.done", "stepId": "save", "checked": 12, "matched": 12 - wrong_saved}
    assert events[-1] == {
        "type": "job.failed",
        "reason": "verify-mismatch",
        "stepId": "save",
        "message": f"{wrong_saved} of 12 saved products don't match after saving, and {others} others changed too. I stopped and didn't save the steps.",
    }
    assert result["status"] == "failed"
    assert result["reason"] == "verify-mismatch"
    assert result["maySaveProcedure"] is False
    assert result["saveStatus"].startswith("Saved to all locations.")
    assert sites.saves == ["Save changes to all locations"]


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        ("rejected", "The changes were turned down. Nothing was changed."),
        ("request_changes", "Someone asked for different changes. Nothing was changed."),
    ],
)
def test_an_answer_other_than_approved_ends_the_job_without_writing(monkeypatch, decision, message):
    sites = install_sites(monkeypatch)
    memory = MemoryStore()

    result, transcript = see_through(FakeDesk(decision), memory)

    assert transcript.events == [{"type": "job.failed", "reason": "stopped", "stepId": "ask", "message": message}]
    assert result == {"status": "stopped", "decision": decision}
    assert sites.commands == []
    assert UNDO_KEY not in memory.values
    assert f"{PENDING_PREFIX}{APPROVAL_ID}" not in memory.values


def test_not_now_sends_nothing_so_the_job_keeps_waiting_until_an_answer_comes(monkeypatch):
    install_sites(monkeypatch)
    desk = FakeDesk("expired", "expired", "expired", "approved", statuses=("pending", "pending", "pending"))

    result, transcript = see_through(desk, MemoryStore())

    assert result["status"] == "done"
    assert len(desk.paused) == 1
    assert [resume["timeout"] for resume in desk.resumed] == [ANSWER_POLL_SECONDS] * 3
    assert desk.status_checks == ["exec-test"] * 3
    assert [event["type"] for event in transcript.events].count("job.failed") == 0


def test_an_answer_that_arrived_while_the_callback_was_missed_is_read_from_agentfield(monkeypatch):
    install_sites(monkeypatch)
    desk = FakeDesk("expired", statuses=("approved",))

    result, transcript = see_through(desk, MemoryStore())

    assert result["status"] == "done"
    assert desk.resumed == []
    assert transcript.events[0]["type"] == "approval.answered"


def test_stop_while_waiting_ends_the_job_as_stopped_and_releases_the_pause(monkeypatch):
    sites = install_sites(monkeypatch)
    memory = MemoryStore()
    desk = FakeDesk(FOREVER)
    transcript = Transcript()

    async def stop_while_waiting():
        task = asyncio.create_task(
            see_approval_through(desk, transcript.notes, resolve_job("kumo"), pending(), memory, clock=quarter_second_clock(), wall=ticking_wall())
        )
        await desk.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(stop_while_waiting())

    assert transcript.events == [{"type": "job.failed", "reason": "stopped", "stepId": "ask", "message": "Stopped before saving. Nothing was changed."}]
    assert desk.released == [APPROVAL_ID]
    assert f"{PENDING_PREFIX}{APPROVAL_ID}" not in memory.values
    assert sites.commands == []


def test_an_agent_shutting_down_leaves_the_job_waiting_for_the_next_start(monkeypatch):
    sites = install_sites(monkeypatch)
    memory = MemoryStore()
    desk = FakeDesk(FOREVER, shutting_down=True)
    transcript = Transcript()

    async def shut_down_while_waiting():
        task = asyncio.create_task(
            see_approval_through(desk, transcript.notes, resolve_job("kumo"), pending(), memory, clock=quarter_second_clock(), wall=ticking_wall())
        )
        await desk.waiting.wait()
        task.cancel()
        return await task

    result = asyncio.run(shut_down_while_waiting())

    assert result == {"status": "waiting", "approvalId": APPROVAL_ID, "resumable": True}
    assert transcript.events == []
    assert desk.released == []
    assert PendingWrite.from_json(memory.values[f"{PENDING_PREFIX}{APPROVAL_ID}"]) == pending()
    assert sites.commands == []


def test_a_pause_agentfield_cannot_take_stops_the_job_before_anything_is_written(monkeypatch):
    sites = install_sites(monkeypatch)
    memory = MemoryStore()

    result, transcript = see_through(FakeDesk(AgentFieldClientError("Agent is not serving")), memory)

    assert transcript.events == [
        {"type": "job.failed", "reason": "error", "stepId": "ask", "message": "I couldn't hold the job for your OK, so I stopped. Nothing was changed."}
    ]
    assert result == {"status": "failed", "reason": "error", "stepId": "ask", "problem": "pause-unavailable"}
    assert f"{PENDING_PREFIX}{APPROVAL_ID}" not in memory.values
    assert sites.commands == []


def test_without_memory_for_the_waiting_job_it_does_not_pause_at_all(monkeypatch):
    install_sites(monkeypatch)
    desk = FakeDesk("approved")

    result, transcript = see_through(desk, MemoryStore(failing=("set",)))

    assert desk.paused == []
    assert transcript.events[-1]["message"] == "I couldn't hold the job for your OK, so I stopped. Nothing was changed."
    assert result["status"] == "failed"


def test_without_a_copy_for_undo_nothing_is_written(monkeypatch):
    sites = install_sites(monkeypatch)

    result, transcript = see_through(FakeDesk("approved"), MemoryStore(failing=(f"set:{UNDO_KEY}",)))

    assert transcript.events[-1] == {
        "type": "job.failed",
        "reason": "error",
        "stepId": "save",
        "message": "I couldn't keep a copy of the current stock for undo, so I stopped. Nothing was changed.",
    }
    assert result["status"] == "failed"
    assert sites.saved is False
    assert "write.done" not in [event["type"] for event in transcript.events]


def test_a_store_that_changed_after_the_approval_is_not_overwritten(monkeypatch):
    sites = install_sites(monkeypatch)
    sites.store_rows[0]["available"] += 1

    result, transcript = see_through(FakeDesk("approved"), MemoryStore())

    assert transcript.events[-1] == {
        "type": "job.failed",
        "reason": "error",
        "stepId": "save",
        "message": "The store admin changed after you approved, so I stopped. Nothing was changed.",
    }
    assert result["status"] == "failed"
    assert sites.saved is False


def test_fields_that_cannot_be_told_apart_are_never_guessed(monkeypatch):
    sites = install_sites(monkeypatch, unlabelled_fields=True)

    result, transcript = see_through(FakeDesk("approved"), MemoryStore())

    assert transcript.events[-1]["message"] == "I couldn't tell which field holds a product's stock, so I stopped. Nothing was changed."
    assert result["status"] == "failed"
    assert sites.saved is False
    assert [command for command in sites.commands if command["command"] == "fill"][2:] == []


def test_a_resumed_job_checks_agentfield_first_and_carries_on_numbering(monkeypatch):
    install_sites(monkeypatch)
    memory = MemoryStore()
    desk = FakeDesk(statuses=("approved",))

    result, transcript = see_through(desk, memory, resumed=True)

    assert result["status"] == "done"
    assert desk.paused == []
    assert transcript.sent[0]["n"] == FIRST_RUN_NOTES + 1
    assert ("set", f"{PENDING_PREFIX}{APPROVAL_ID}") not in memory.calls


def test_a_resumed_job_still_pending_waits_for_the_callback_without_asking_again(monkeypatch):
    install_sites(monkeypatch)
    desk = FakeDesk("approved", statuses=("pending",))

    result, _ = see_through(desk, MemoryStore(), resumed=True)

    assert result["status"] == "done"
    assert desk.paused == []
    assert len(desk.resumed) == 1


def test_wait_for_answer_returns_what_agentfield_holds_once_the_poll_says_it_is_no_longer_pending():
    desk = FakeDesk("expired", statuses=("rejected",))

    answer = asyncio.run(wait_for_answer(desk, APPROVAL_ID, "exec-test"))

    assert answer.decision == "rejected"


UNDO_SHAPE = [
    ("job.created", None),
    ("step.started", "sign-in"),
    ("page.seen", "sign-in"),
    ("step.done", "sign-in"),
    ("step.started", "undo"),
    ("page.seen", "undo"),
    ("write.done", "undo"),
    ("verify.done", "undo"),
    ("step.done", "undo"),
]


def undo(memory, client_id="kumo"):
    transcript = Transcript(start=0)
    result = asyncio.run(undo_last_sync(transcript.notes, resolve_job(client_id), memory, clock=quarter_second_clock()))
    return result, transcript


def test_undo_puts_back_the_values_from_before_the_sync_and_checks_every_row(monkeypatch):
    sites = install_sites(monkeypatch)
    memory = MemoryStore()
    see_through(FakeDesk("approved"), memory)

    result, transcript = undo(memory)

    assert transcript.shape() == UNDO_SHAPE
    events = transcript.events
    assert events[0] == {
        "type": "job.created",
        "clientName": "Kumo Coffee Roasters",
        "title": "Undo stock sync",
        "request": "Undo this sync",
        "run": {"kind": "undo"},
        "plan": [
            {"stepId": "sign-in", "title": "Sign in to the store admin"},
            {"stepId": "undo", "title": "Put back the stock from before the sync"},
        ],
    }
    assert events[4] == {"type": "step.started", "stepId": "undo", "activity": "Putting back 12 products"}
    assert events[6] == {"type": "write.done", "stepId": "undo", "written": 12}
    assert events[7] == {"type": "verify.done", "stepId": "undo", "checked": 12, "matched": 12}
    assert events[8]["summary"] == "Put back and checked every row"
    assert events[8]["detail"] == "12 products"
    assert result == {"status": "done", "written": 12, "checked": 12, "matched": 12}
    assert store_values(sites) == {row["sku"]: row["available"] for row in KUMO["store"]}
    assert UNDO_KEY not in memory.values
    assert sites.saves == ["Save changes", "Save changes"]


def test_the_same_sync_cannot_be_undone_twice(monkeypatch):
    install_sites(monkeypatch)
    memory = MemoryStore()
    see_through(FakeDesk("approved"), memory)
    undo(memory)

    result, transcript = undo(memory)

    assert transcript.shape() == [("job.created", None), ("job.failed", None)]
    assert transcript.events[-1]["message"] == "There's no sync to undo for this client. Nothing was changed."
    assert result["status"] == "failed"


def test_undo_never_overwrites_products_that_changed_after_the_sync(monkeypatch):
    sites = install_sites(monkeypatch)
    memory = MemoryStore()
    see_through(FakeDesk("approved"), memory)
    changed_later = next(row for row in sites.store_rows if row["sku"] == CHANGES[0].sku)
    changed_later["available"] += 5
    saves_before = list(sites.saves)

    result, transcript = undo(memory)

    assert transcript.events[-1] == {
        "type": "job.failed",
        "reason": "error",
        "stepId": "undo",
        "message": "Some of these products changed after the sync, so I didn't put anything back. Nothing was changed.",
    }
    assert result["status"] == "failed"
    assert sites.saves == saves_before
    assert UNDO_KEY in memory.values


def test_undo_reads_its_copy_only_from_this_clients_memory(monkeypatch):
    install_sites(monkeypatch)
    kumo = MemoryStore()
    see_through(FakeDesk("approved"), kumo)

    result, transcript = undo(MemoryStore(), client_id="kumo")

    assert result["status"] == "failed"
    assert transcript.events[-1]["message"] == "There's no sync to undo for this client. Nothing was changed."


def test_memory_that_cannot_be_read_stops_the_undo_before_any_work(monkeypatch):
    sites = install_sites(monkeypatch)

    result, transcript = undo(MemoryStore(failing=("get",)))

    assert transcript.events[-1]["message"] == "I couldn't read the copy from before the sync, so I stopped. Nothing was changed."
    assert result["status"] == "failed"
    assert sites.commands == []


def test_the_fake_memory_fails_the_way_agentfield_memory_does():
    assert issubclass(MemoryUnavailable, MemoryAccessError)
