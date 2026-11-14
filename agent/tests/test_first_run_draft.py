import json

from clientops.procedure import ProcedureDraft
from picking_claude import PickingClaude
from procedure_fixtures import kumo_read_steps
from test_stock_sync import KUMO, VAULT, data_dir, install_sites, run

__all__ = ["data_dir"]


def test_the_waiting_first_run_hands_over_the_steps_it_used_as_a_draft(monkeypatch):
    install_sites(monkeypatch)

    result, _ = run(PickingClaude())

    draft = ProcedureDraft.from_json(result["procedure"])
    assert draft.read == kumo_read_steps()
    assert draft.read.supplier.landing == "/stock"
    assert draft.read.store.landing == "/inventory"


def test_the_expected_replay_time_is_the_time_to_the_approval_without_the_time_spent_asking_claude(monkeypatch):
    install_sites(monkeypatch)
    claude = PickingClaude()

    result, _ = run(claude)

    claude_ms = sum(call["tookMs"] for calls in result["claudeUsage"].values() for call in calls)
    draft = ProcedureDraft.from_json(result["procedure"])
    assert claude_ms > 0
    assert result["readyInMs"] > claude_ms
    assert draft.expected_replay_ms == result["readyInMs"] - claude_ms


def test_the_draft_carries_no_login_value(monkeypatch):
    install_sites(monkeypatch)

    result, _ = run(PickingClaude())

    written = json.dumps(result["procedure"])
    for value in VAULT.values():
        assert value not in written


def test_only_a_run_that_reaches_the_approval_hands_over_a_draft(monkeypatch):
    day = {**KUMO, "store": KUMO["supplier"][: len(KUMO["store"])], "changes": []}
    install_sites(monkeypatch, day=day)

    result, _ = run(PickingClaude())

    assert "procedure" not in result
