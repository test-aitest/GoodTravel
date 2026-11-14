import importlib


def load_node():
    return importlib.import_module("clientops.node")


def test_the_node_is_clientops_with_run_job_undo_sync_chat_reply_the_two_job_skills_the_two_setup_skills_and_the_demo_reset():
    node = load_node()
    assert node.app.node_id == "clientops"
    assert [reasoner["id"] for reasoner in node.app.reasoners] == ["run_job", "trip_search", "trip_book", "undo_sync", "chat_reply", "trip_talk"]
    assert sorted(skill["id"] for skill in node.app.skills) == ["prepare_computer", "refind_by_tree", "replay_script", "reset_demo", "setup_status"]


def test_the_tree_refind_skill_answers_without_a_server_for_a_client_that_is_not_set_up():
    import asyncio

    node = load_node()
    assert asyncio.run(node.refind_by_tree(client_id="sora", step_id="check-save")) == {
        "clientId": "sora",
        "stepId": "check-save",
        "found": False,
        "problem": "not-set-up",
    }


def test_the_server_address_and_key_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("AGENTFIELD_SERVER", "http://127.0.0.1:18080")
    monkeypatch.setenv("AGENTFIELD_API_KEY", "test-key")
    node = load_node()
    settings = node.node_settings()
    assert settings == {"agentfield_server": "http://127.0.0.1:18080", "api_key": "test-key"}


def test_without_a_key_none_is_sent(monkeypatch):
    monkeypatch.delenv("AGENTFIELD_API_KEY", raising=False)
    monkeypatch.delenv("AGENTFIELD_SERVER", raising=False)
    node = load_node()
    assert node.node_settings() == {"agentfield_server": "http://localhost:8080", "api_key": None}


def test_run_job_returns_the_number_of_event_notes_it_wrote(monkeypatch, tmp_path):
    import asyncio
    import json
    from pathlib import Path

    import clientops.externals as externals
    from approval_fakes import FakeDesk, MemoryStore
    from fake_sites import FakeSites, RunnerDaytona
    from picking_claude import PickingClaude

    day = json.loads((Path(__file__).parent / "fixtures" / "kumo-day1.json").read_text())
    vault = {"LP_SUPPLIER_USERNAME": "u1", "LP_SUPPLIER_PASSWORD": "p1", "LP_STORE_USERNAME": "u2", "LP_STORE_PASSWORD": "p2"}
    daytona = RunnerDaytona(FakeSites(day, {"supplier": ("u1", "p1"), "store": ("u2", "p2")}, vault))
    monkeypatch.setenv("CLIENTOPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "fake")
    monkeypatch.delenv("CLIENTOPS_COMPUTERS", raising=False)
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setattr(externals, "shared_fake_daytona", lambda: daytona)

    node = load_node()
    written = []
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: written.append(json.loads(message)))
    monkeypatch.setattr(node, "claude_ai", lambda agent: PickingClaude())
    monkeypatch.setattr(node, "approval_desk", lambda agent: FakeDesk("approved"))
    monkeypatch.setattr(node, "client_memory", lambda boundary, context: MemoryStore())

    result = asyncio.run(node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request="Sync today's stock."))

    assert result["clientId"] == "kumo"
    assert result["status"] == "done"
    assert result["events"] == len(written) == 31
    assert [note["n"] for note in written] == list(range(1, 32))
    assert written[-1]["event"]["type"] == "procedure.saved"
