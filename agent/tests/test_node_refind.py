import asyncio
import importlib

from clientops.approval import PENDING_PREFIX
from clientops.procedure import PROCEDURE_KEY, Procedure
from approval_fakes import MemoryStore
from test_node_approval import Seams, in_execution
from test_refind import CLASS_SAVE, SAVE_CSS, recorded_with
from test_replay import AFTER_APPROVAL, DAY2_CHANGES, KUMO_DAY2
from test_replay_refind import REFOUND_AT_CHECK_SAVE, replay_of, waiting_after
from test_stock_sync import REQUEST, data_dir, install_sites

__all__ = ["data_dir"]


def load_node():
    return importlib.import_module("clientops.node")


def shape(events):
    return [(event["type"], event.get("stepId")) for event in events]


def test_run_job_finds_a_changed_save_button_by_name_asks_first_and_saves_version_2_after_a_full_match(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    memory = seams.memories["kumo"] = MemoryStore()
    memory.values[PROCEDURE_KEY] = recorded_with("check-save", "save", CLASS_SAVE).to_json()

    result = in_execution(node, lambda: node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))

    assert shape(seams.events) == REFOUND_AT_CHECK_SAVE + AFTER_APPROVAL + [("procedure.saved", None)]
    assert [(event["stepId"], event["rung"]) for event in seams.events if event["type"] == "step.decided"] == [
        ("sign-in", "script"),
        ("read", "script"),
        ("compare", "script"),
        ("check-save", "tree"),
        ("ask", "human"),
        ("save", "script"),
    ]
    assert next(event for event in seams.events if event["type"] == "approval.requested")["changes"] == DAY2_CHANGES
    assert len(seams.desk.paused) == 1
    assert seams.claude.calls == 0
    assert result["status"] == "done"
    assert result["claudeCalls"] == 0
    assert result["matched"] == 4
    stored = Procedure.from_json(memory.values[PROCEDURE_KEY])
    assert (stored.version, stored.read.save_button.css) == (2, SAVE_CSS)
    assert {row["sku"]: row["available"] for row in sites.store_rows} == {row["sku"]: row["available"] for row in KUMO_DAY2["supplier"]}


def test_a_waiting_job_with_rebuilt_steps_picked_up_after_a_restart_writes_with_them_and_saves_version_2(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    recorded = recorded_with("check-save", "save", CLASS_SAVE)
    result, transcript = replay_of(recorded)
    pending = waiting_after(result, transcript.notes.count)
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved", statuses=("pending",))
    memory = seams.memories["kumo"] = MemoryStore()
    memory.values[PROCEDURE_KEY] = recorded.to_json()
    memory.values[f"{PENDING_PREFIX}{pending.approval_id}"] = pending.to_json()

    async def execution_status(execution_id):
        return "waiting"

    monkeypatch.setattr(node, "execution_status", execution_status)

    async def resume():
        tasks = await node.resume_waiting_jobs()
        await asyncio.gather(*tasks)

    asyncio.run(resume())

    assert shape(seams.events) == AFTER_APPROVAL + [("procedure.saved", None)]
    assert seams.events[-1]["version"] == 2
    [save] = [op for op in sites.scripts[-1]["steps"][0]["ops"] if op["op"] == "one" and op["as"] == "save"]
    assert save["css"] == SAVE_CSS
    assert Procedure.from_json(memory.values[PROCEDURE_KEY]).version == 2
    assert seams.reports[0][1]["result"]["written"] == 4
