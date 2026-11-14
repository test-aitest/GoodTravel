import asyncio
import importlib
import json
from pathlib import Path

from clientops.approval import PENDING_PREFIX, UNDO_KEY, PendingWrite
from clientops.procedure import PROCEDURE_KEY, Procedure
from approval_fakes import FakeDesk, MemoryStore
from picking_claude import PickingClaude
from test_node_approval import Seams, in_execution
from test_replay import AFTER_APPROVAL, DAY2_CHANGES, KUMO_DAY2, READ_EVENTS, kumo_procedure
from test_stock_sync import FIRST_RUN_EVENTS, KUMO, REQUEST, data_dir, install_sites

__all__ = ["data_dir"]

FIXTURES = Path(__file__).parent / "fixtures"


def load_node():
    return importlib.import_module("clientops.node")


def shape(events):
    return [(event["type"], event.get("stepId")) for event in events]


def run_job(node, execution_id="exec_kumo_1"):
    return in_execution(node, lambda: node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST), execution_id=execution_id)


def test_without_saved_steps_run_job_learns_and_after_a_full_match_saves_version_1(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")

    result = run_job(node)

    assert seams.events[0]["run"] == {"kind": "learn"}
    assert shape(seams.events)[: len(FIRST_RUN_EVENTS)] == FIRST_RUN_EVENTS
    assert seams.events[-1] == {"type": "procedure.saved", "procedureId": "kumo-stock-sync", "version": 1, "stepCount": 6, "expectedReplayMs": seams.events[-1]["expectedReplayMs"]}
    assert seams.events[-2]["type"] == "step.done" and seams.events[-2]["stepId"] == "save"
    assert result["status"] == "done"
    assert result["claudeCalls"] == 8
    stored = Procedure.from_json(seams.memories["kumo"].values[PROCEDURE_KEY])
    assert stored.version == 1
    assert stored.read.save_button.text == "Save changes"
    assert stored.field.target("KC-ETH-200", "Ethiopia Guji 200g").css == 'form[action="/inventory"] input[name="available-KC-ETH-200"]'


def test_with_saved_steps_run_job_replays_them_with_no_claude_and_still_asks_before_writing(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[PROCEDURE_KEY] = kumo_procedure().to_json()

    result = run_job(node)

    assert shape(seams.events) == READ_EVENTS + AFTER_APPROVAL
    assert seams.events[0]["run"] == {"kind": "replay", "procedureVersion": 1}
    assert {event["rung"] for event in seams.events if event["type"] == "step.decided" and event["stepId"] != "ask"} == {"script"}
    assert seams.claude.calls == 0
    assert len(seams.desk.paused) == 1
    approval = next(event for event in seams.events if event["type"] == "approval.requested")
    assert approval["changes"] == DAY2_CHANGES
    assert result["status"] == "done"
    assert result["written"] == result["checked"] == result["matched"] == 4
    assert result["claudeCalls"] == 0
    assert isinstance(result["readyInMs"], int) and result["readyInMs"] >= 0
    assert [note["n"] for note in seams.written] == list(range(1, len(seams.written) + 1))
    assert {row["sku"]: row["available"] for row in sites.store_rows} == {row["sku"]: row["available"] for row in KUMO_DAY2["supplier"]}
    assert "procedure.saved" not in {event["type"] for event in seams.events}
    assert UNDO_KEY in seams.memories["kumo"].values


def test_a_saved_procedure_for_one_client_is_not_used_for_another(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "rejected")
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[PROCEDURE_KEY] = {**kumo_procedure().to_json(), "clientId": "hinata"}

    run_job(node)

    assert seams.events[0]["run"] == {"kind": "learn"}


def test_the_first_run_then_day_two_runs_the_saved_steps_and_finds_its_four_changes(monkeypatch):
    sites = install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved", "approved")
    run_job(node)
    claude_calls_after_learning = seams.claude.calls
    day2 = KUMO_DAY2
    sites.supplier_rows = day2["supplier"]
    sites.store_rows = [dict(row) for row in day2["store"]]
    seams.written.clear()

    result = run_job(node, execution_id="exec_kumo_2")

    assert seams.events[0]["run"] == {"kind": "replay", "procedureVersion": 1}
    assert seams.claude.calls == claude_calls_after_learning
    approval = next(event for event in seams.events if event["type"] == "approval.requested")
    assert approval["changes"] == DAY2_CHANGES
    assert result["written"] == result["matched"] == 4


def test_a_waiting_replay_picked_up_after_a_restart_writes_with_the_saved_script(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved", statuses=("pending",))
    memory = seams.memories["kumo"] = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()
    from clientops.diff import StockChange
    from clientops.page import Locator
    from clientops.stock_sync import PreparedWrite

    pending = PendingWrite(
        job_id="exec_kumo_1",
        run_id="run_kumo_1",
        workflow_id="run_kumo_1",
        client_id="kumo",
        approval_id="apr-exec_kumo_1",
        notes_written=len(READ_EVENTS),
        asked_at=1_794_636_000.0,
        write=PreparedWrite(
            (Locator("textbox", "Username", "/login"), Locator("textbox", "Password", "/login"), Locator("button", "Sign in", "/login")),
            Locator("button", "Save changes", "/inventory"),
            tuple(StockChange(**change) for change in DAY2_CHANGES),
        ),
        replay_version=1,
    )
    memory.values[f"{PENDING_PREFIX}{pending.approval_id}"] = pending.to_json()

    async def execution_status(execution_id):
        return "waiting"

    monkeypatch.setattr(node, "execution_status", execution_status)

    async def resume():
        tasks = await node.resume_waiting_jobs()
        await asyncio.gather(*tasks)

    asyncio.run(resume())

    assert shape(seams.events) == AFTER_APPROVAL
    assert seams.written[0]["n"] == len(READ_EVENTS) + 1
    assert [command["command"] for command in sites.commands] == ["script"]
    assert sites.scripts[-1]["steps"][0]["stepId"] == "save-prepare"
    assert seams.reports[0][1]["result"]["written"] == 4


def test_a_waiting_replay_whose_saved_steps_changed_meanwhile_writes_nothing(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    memory = seams.memories["kumo"] = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure(version=2).to_json()
    from clientops.replay import writer_for
    from clientops.boundary import resolve_job
    from test_node_approval import waiting_record

    pending = PendingWrite(**{**waiting_record().__dict__, "replay_version": 1})
    writer = asyncio.run(writer_for(pending, resolve_job("kumo"), memory))
    from clientops.records import EventNotes

    sent: list[dict] = []
    outcome = asyncio.run(writer(EventNotes(lambda message, tags: sent.append(json.loads(message))), resolve_job("kumo"), pending, memory))

    assert outcome["problem"] == "procedure-changed"
    assert sent[-1]["event"]["message"] == "The saved steps changed while this job waited, so I stopped. Nothing was changed."
    assert sites.commands == []


def test_the_replay_script_skill_runs_the_saved_steps_for_one_client(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    node = load_node()
    seams = Seams(monkeypatch, node)
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[PROCEDURE_KEY] = kumo_procedure().to_json()

    found = asyncio.run(node.replay_script(client_id="kumo"))

    assert found["clientId"] == "kumo"
    assert found["ok"] is True
    assert found["procedureVersion"] == 1
    assert found["outcome"] == "passed"
    assert [step["stepId"] for step in found["steps"]] == ["sign-in", "read", "compare", "check-save"]
    assert all(step["status"] == "done" and step["checks"] > 0 for step in found["steps"])


def test_the_replay_script_skill_says_when_there_is_nothing_saved_or_no_such_client(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    Seams(monkeypatch, node)

    assert asyncio.run(node.replay_script(client_id="kumo")) == {"clientId": "kumo", "ok": False, "problem": "no-saved-steps"}
    assert asyncio.run(node.replay_script(client_id="sora")) == {"clientId": "sora", "ok": False, "problem": "not-set-up"}


def test_the_tree_refind_skill_finds_a_saved_steps_elements_again_by_role_name_and_form_and_saves_nothing(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, restyled=True)
    node = load_node()
    seams = Seams(monkeypatch, node)
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[PROCEDURE_KEY] = kumo_procedure().to_json()

    found = asyncio.run(node.refind_by_tree(client_id="kumo", step_id="compare"))

    assert (found["clientId"], found["stepId"], found["found"], found["procedureVersion"]) == ("kumo", "compare", True, 1)
    assert found["elements"]["user"] == {
        "css": 'form[action="/login"] input[name="f-username"]',
        "hint": {"role": "textbox", "name": "Username", "form": "/login"},
        "changed": True,
    }
    assert found["elements"]["password"]["changed"] is True
    assert found["elements"]["submit"] == {**kumo_procedure().read.store.submit.to_json(), "changed": False}
    assert isinstance(found["tookMs"], int)
    assert seams.memories["kumo"].values[PROCEDURE_KEY] == kumo_procedure().to_json()


def test_the_tree_refind_skill_says_which_element_it_could_not_find_or_why_it_could_not_look(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, save_label="Update inventory")
    node = load_node()
    seams = Seams(monkeypatch, node)

    assert asyncio.run(node.refind_by_tree(client_id="kumo", step_id="check-save")) == {
        "clientId": "kumo",
        "stepId": "check-save",
        "found": False,
        "problem": "no-saved-steps",
    }
    seams.memories["kumo"].values[PROCEDURE_KEY] = kumo_procedure().to_json()
    missing = asyncio.run(node.refind_by_tree(client_id="kumo", step_id="check-save"))
    assert {key: value for key, value in missing.items() if key != "tookMs"} == {
        "clientId": "kumo",
        "stepId": "check-save",
        "found": False,
        "element": "save",
        "count": 0,
    }
    assert asyncio.run(node.refind_by_tree(client_id="kumo", step_id="ask"))["problem"] == "no-elements"
    assert asyncio.run(node.refind_by_tree(client_id="sora", step_id="check-save"))["problem"] == "not-set-up"


def test_after_a_replay_that_matched_every_row_the_saved_estimate_is_that_replays_time(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[PROCEDURE_KEY] = kumo_procedure().to_json()

    result = run_job(node)

    stored = seams.memories["kumo"].values[PROCEDURE_KEY]
    assert stored["expectedReplayMs"] == result["readyInMs"]
    assert stored["procedureVersion"] == 1
    [held] = [data for key, data in seams.memories["kumo"].written if key.startswith(PENDING_PREFIX)]
    assert held["readyInMs"] == result["readyInMs"]
