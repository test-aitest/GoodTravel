import asyncio

import pytest

from approval_fakes import MemoryStore
from clientops.boundary import ClientNotSetUp, UnknownClient, resolve_job
from clientops.demo_reset import DEMO_RESET_VARIABLE, SITE_RESETS, demo_reset_enabled, reset_demo_client


def reset(client_id, *, enabled=True, memory=None, resolve=resolve_job):
    memory = memory if memory is not None else MemoryStore()
    sites: list[str] = []
    answer = asyncio.run(
        reset_demo_client(client_id, enabled=enabled, resolve=resolve, memory_for=lambda boundary: memory, reset_sites=lambda boundary: sites.append(boundary.client.id))
    )
    return answer, memory, sites


def test_with_demo_reset_off_nothing_is_touched():
    memory = MemoryStore()
    memory.values["stock-sync/procedure"] = {"version": 2}
    answer, memory, sites = reset("kumo", enabled=False, memory=memory)
    assert answer == {"clientId": "kumo", "ok": False, "problem": "not-enabled"}
    assert memory.values == {"stock-sync/procedure": {"version": 2}}
    assert sites == []


def test_it_forgets_everything_the_worker_learned_for_the_client_and_resets_its_demo_sites():
    memory = MemoryStore()
    memory.values.update({"stock-sync/procedure": {"version": 2}, "stock-sync/undo": {"rows": []}, "approvals/pending/apr-1": {}})
    answer, memory, sites = reset("kumo", memory=memory)
    assert answer == {"clientId": "kumo", "ok": True, "sites": "reset", "forgotten": 3}
    assert memory.values == {}
    assert sites == ["kumo"]


def test_a_client_without_a_computer_is_skipped():
    def not_set_up(client_id):
        raise ClientNotSetUp(client_id, "not set up")

    answer, _, sites = reset("sora", resolve=not_set_up)
    assert answer == {"clientId": "sora", "ok": True, "sites": "skipped", "forgotten": 0}
    assert sites == []


def test_an_unknown_client_is_refused():
    def unknown(client_id):
        raise UnknownClient(client_id, "unknown")

    answer, _, _ = reset("acme", resolve=unknown)
    assert answer == {"clientId": "acme", "ok": False, "problem": "unknown-client"}


@pytest.mark.parametrize(("value", "enabled"), [("on", True), ("", False), ("true", False), ("off", False)])
def test_demo_reset_is_on_only_when_asked_for_exactly(value, enabled):
    assert demo_reset_enabled({DEMO_RESET_VARIABLE: value}) is enabled
    assert demo_reset_enabled({}) is False


def test_the_sites_go_back_to_day_one_with_every_switch_off():
    assert SITE_RESETS == (
        ("/__control/switches", {"restyle": False, "renameButton": False, "decoyButton": False, "hiddenInstruction": False}),
        ("/__control/reset", None),
        ("/__control/day", {"day": 1}),
    )


def test_the_node_offers_the_reset_as_a_skill_that_stays_off_unless_the_worker_was_started_with_it(monkeypatch):
    import importlib

    from test_cost_node import as_agentfield_runs

    node = importlib.import_module("clientops.node")
    monkeypatch.delenv(DEMO_RESET_VARIABLE, raising=False)
    memory = MemoryStore()
    memory.values["stock-sync/procedure"] = {"version": 2}
    monkeypatch.setattr(node, "client_memory", lambda boundary, context: memory)

    answer = asyncio.run(as_agentfield_runs(node.reset_demo)(client_id="kumo"))

    assert answer == {"clientId": "kumo", "ok": False, "problem": "not-enabled"}
    assert memory.values == {"stock-sync/procedure": {"version": 2}}
