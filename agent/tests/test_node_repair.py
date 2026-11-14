import importlib

from clientops.procedure import PROCEDURE_KEY, Procedure
from approval_fakes import MemoryStore
from test_cost_node import as_agentfield_runs
from test_node_approval import Seams, in_execution
from test_refind import SAVE_CSS
from test_replay import AFTER_APPROVAL, KUMO_DAY2, kumo_procedure
from test_replay_repair import ASKED_AT_CHECK_SAVE, CHANGE, DECOY, RENAMED, REPAIRED_AT_CHECK_SAVE
from test_stock_sync import REQUEST, data_dir, install_sites

__all__ = ["data_dir"]


def load_node():
    return importlib.import_module("clientops.node")


def shape(events):
    return [(event["type"], event.get("stepId")) for event in events]


def run_job(node):
    return in_execution(node, lambda: as_agentfield_runs(node.run_job)(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST), execution_id="exec_replay")


def test_run_job_repairs_a_renamed_save_button_with_claude_asks_with_the_change_and_saves_version_2(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    memory = seams.memories["kumo"] = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()

    result = run_job(node)

    assert shape(seams.events) == REPAIRED_AT_CHECK_SAVE + AFTER_APPROVAL + [("procedure.saved", None)]
    approval = next(event for event in seams.events if event["type"] == "approval.requested")
    assert approval["question"] == "Save 4 stock changes with the new button?"
    assert approval["procedureChange"] == CHANGE
    assert [paused["approvalId"] for paused in seams.desk.paused] == ["apr-exec_replay"]
    assert seams.claude.calls == 1
    assert result["status"] == "done" and result["matched"] == 4
    assert result["claudeCalls"] == 1
    stored = Procedure.from_json(memory.values[PROCEDURE_KEY])
    assert (stored.version, stored.read.save_button.css, stored.read.save_button.text) == (2, SAVE_CSS, RENAMED)
    assert sites.saves == [RENAMED]


def test_run_job_asks_the_person_between_two_save_like_buttons_then_asks_before_saving(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED, extra_store_buttons=(DECOY,))
    node = load_node()
    seams = Seams(monkeypatch, node, ("approved", "1016"), "approved")
    memory = seams.memories["kumo"] = MemoryStore()
    memory.values[PROCEDURE_KEY] = kumo_procedure().to_json()

    result = run_job(node)

    assert shape(seams.events) == ASKED_AT_CHECK_SAVE + AFTER_APPROVAL + [("procedure.saved", None)]
    assert [paused["approvalId"] for paused in seams.desk.paused] == ["cho-exec_replay-check-save", "apr-exec_replay"]
    assert {paused["executionId"] for paused in seams.desk.paused} == {"exec_replay"}
    assert result["status"] == "done" and result["matched"] == 4
    assert Procedure.from_json(memory.values[PROCEDURE_KEY]).version == 2
    assert sites.saves == [RENAMED]
