import asyncio
import importlib
import json
from datetime import UTC, datetime

import pytest
from agentfield import Agent
from agentfield.execution_context import ExecutionContext, reset_execution_context, set_execution_context

from clientops import cost
from clientops.boundary import data_directory
from clientops.cost import CostLedger, month_of
from approval_fakes import FakeDesk, MemoryStore
from picking_claude import PickingClaude
from test_first_run_integration import HOW_TO_START, REQUEST, local_computers, logins, missing

__all__ = ["local_computers", "logins"]

pytestmark = pytest.mark.skipif(missing() is not None, reason=f"{missing()}: {HOW_TO_START}")


def test_a_real_job_on_the_local_computer_is_billed_once_with_its_time_marked_as_an_estimate(monkeypatch, logins):
    monkeypatch.setattr(cost, "open_jobs", {})
    node = importlib.import_module("clientops.node")
    written: list[dict] = []
    posted: list[dict] = []
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: written.append(json.loads(message)))
    monkeypatch.setattr(node, "claude_ai", lambda agent: PickingClaude())
    monkeypatch.setattr(node, "approval_desk", lambda agent: FakeDesk("approved"))
    monkeypatch.setattr(node, "client_memory", lambda boundary, context: MemoryStore())

    async def post(self, callback_url, payload, execution_id, max_retries=5):
        posted.append(json.loads(json.dumps(payload)))

    monkeypatch.setattr(Agent, "_post_execution_status", post)

    async def job():
        context = ExecutionContext(run_id="run_cost_1", execution_id="exec_cost_1", agent_instance=node.app, reasoner_name="run_job", agent_node_id=node.NODE_ID, registered=True)
        token = set_execution_context(context)
        try:
            result = await node.run_job._original_func(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST)
        finally:
            reset_execution_context(token)
        await node.app._post_execution_status("http://localhost:8001/callback", {"status": "succeeded", "result": result, "execution_id": "exec_cost_1"}, "exec_cost_1")
        return result

    result = asyncio.run(job())

    assert result["status"] == "done"
    assert [note["event"]["type"] for note in written].count("cost.recorded") == 1
    bill = written[-1]["event"]
    assert bill["type"] == "cost.recorded"
    assert bill["claudeCalls"] == result["claudeCalls"] == 8
    assert bill["claudeUsd"] == pytest.approx(sum(call["costUsd"] for calls in result["claudeUsage"].values() for call in calls))
    assert bill["computerSeconds"] >= 1
    assert bill["estimated"] is True
    assert posted[0]["result"]["events"] == len(written)
    [entry] = CostLedger(data_directory()).entries(month_of(datetime.now(UTC)))
    assert entry.job_id == "exec_cost_1"
    assert entry.cost.estimated is True
