import asyncio
import importlib
import json

import httpx
import pytest
from agentfield.cancel import deregister_execution, register_execution_task
from agentfield.client import ApprovalRequestResponse, ApprovalResult, ApprovalStatusResponse
from agentfield.execution_context import ExecutionContext, reset_execution_context, set_execution_context

from clientops.approval import PENDING_PREFIX, UNDO_KEY, AgentDesk, PendingWrite, see_approval_through
from clientops.boundary import resolve_job
from clientops.records import EVENT_NOTE_TAG, EventNotes
from clientops.diff import StockChange
from clientops.page import Locator
from clientops.stock_sync import PreparedWrite
from approval_fakes import FOREVER, FakeDesk, MemoryStore
from picking_claude import PickingClaude
from test_stock_sync import FIRST_RUN_EVENTS, KUMO, REQUEST, data_dir, install_sites

__all__ = ["data_dir"]

AFTER_APPROVAL = 9


def load_node():
    return importlib.import_module("clientops.node")


class Seams:
    def __init__(self, monkeypatch, node, *answers, statuses=()) -> None:
        self.written: list[dict] = []
        self.desk = FakeDesk(*answers, statuses=statuses)
        self.memories: dict[str, MemoryStore] = {}
        self.claude = PickingClaude()
        self.reports: list[tuple[str, dict]] = []
        monkeypatch.setattr(node.app, "note", lambda message, tags=None: self.written.append(json.loads(message)))
        monkeypatch.setattr(node, "claude_ai", lambda agent: self.claude)
        monkeypatch.setattr(node, "approval_desk", lambda agent: self.desk)
        monkeypatch.setattr(node, "client_memory", lambda boundary, context: self.memories.setdefault(boundary.client.id, MemoryStore()))

        async def report(execution_id, payload):
            self.reports.append((execution_id, payload))

        monkeypatch.setattr(node, "report_finished", report)

    @property
    def events(self) -> list[dict]:
        return [note["event"] for note in self.written]


def in_execution(node, coroutine_function, *, execution_id="exec_kumo_1", run_id="run_kumo_1"):
    async def run():
        context = ExecutionContext(
            run_id=run_id,
            execution_id=execution_id,
            agent_instance=node.app,
            reasoner_name="run_job",
            agent_node_id=node.NODE_ID,
            registered=True,
        )
        token = set_execution_context(context)
        try:
            return await coroutine_function()
        finally:
            reset_execution_context(token)

    return asyncio.run(run())


def test_run_job_waits_at_the_approval_and_after_the_ok_saves_and_checks_every_row(monkeypatch):
    sites = install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")

    result = in_execution(node, lambda: node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))

    shape = [(event["type"], event.get("stepId")) for event in seams.events]
    assert shape[: len(FIRST_RUN_EVENTS)] == FIRST_RUN_EVENTS
    assert len(seams.events) == len(FIRST_RUN_EVENTS) + AFTER_APPROVAL + 1
    assert seams.events[-1]["type"] == "procedure.saved"
    assert [note["n"] for note in seams.written] == list(range(1, len(seams.written) + 1))
    job_id = seams.desk.paused[0]["executionId"]
    assert seams.desk.paused[0]["approvalId"] == f"apr-{job_id}"
    assert result == {
        "clientId": "kumo",
        "status": "done",
        "written": 12,
        "checked": 12,
        "matched": 12,
        "maySaveProcedure": True,
        "saveStatus": "Saved. 12 products updated.",
        "claudeCalls": 8,
        "claudeUsage": result["claudeUsage"],
        "events": len(seams.written),
    }
    assert {row["sku"]: row["available"] for row in sites.store_rows} == {row["sku"]: row["available"] for row in KUMO["supplier"] if row["sku"] in {r["sku"] for r in KUMO["store"]}}
    memory = seams.memories["kumo"]
    [held] = [data for key, data in memory.written if key.startswith(PENDING_PREFIX)]
    assert held["jobId"] == job_id
    assert held["runId"] == "run_kumo_1"
    assert held["notesWritten"] == len(FIRST_RUN_EVENTS)
    assert not any(key.startswith(PENDING_PREFIX) for key in memory.values)
    assert UNDO_KEY in memory.values


def test_run_job_turned_down_stops_without_touching_the_store(monkeypatch):
    sites = install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "rejected")

    result = in_execution(node, lambda: node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))

    assert seams.events[-1] == {"type": "job.failed", "reason": "stopped", "stepId": "ask", "message": "The changes were turned down. Nothing was changed."}
    assert result["status"] == "stopped"
    assert result["events"] == len(seams.written) == len(FIRST_RUN_EVENTS) + 1
    assert sites.saved is False


def test_run_job_without_a_claude_key_says_so_instead_of_pretending(monkeypatch):
    install_sites(monkeypatch)
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    node = load_node()
    written = []
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: written.append(json.loads(message)))
    monkeypatch.setattr(node, "client_memory", lambda boundary, context: MemoryStore())

    result = in_execution(node, lambda: node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))

    assert [note["event"]["type"] for note in written] == ["job.created", "job.failed"]
    assert written[-1]["event"]["message"] == "Claude isn't set up on this server, so I stopped. Nothing was changed."
    assert result["status"] == "failed"


def test_with_the_fake_externals_run_job_needs_no_keys_at_all(monkeypatch):
    install_sites(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    monkeypatch.setattr(node, "claude_ai", importlib.import_module("clientops.externals").claude_ai)

    result = in_execution(node, lambda: node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))

    assert result["status"] == "done"
    assert result["claudeCalls"] == 8


def test_undo_sync_is_its_own_job_and_puts_the_store_back(monkeypatch):
    sites = install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, "approved")
    in_execution(node, lambda: node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))
    seams.written.clear()

    result = in_execution(node, lambda: node.undo_sync(client_id="kumo"), execution_id="exec_kumo_undo", run_id="run_kumo_undo")

    assert seams.events[0]["type"] == "job.created"
    assert seams.events[0]["run"] == {"kind": "undo"}
    assert result == {"clientId": "kumo", "status": "done", "written": 12, "checked": 12, "matched": 12, "events": len(seams.written)}
    assert {row["sku"]: row["available"] for row in sites.store_rows} == {row["sku"]: row["available"] for row in KUMO["store"]}


def test_undo_sync_for_a_client_without_a_computer_says_so(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node)

    result = in_execution(node, lambda: node.undo_sync(client_id="sora"))

    assert [event["type"] for event in seams.events] == ["job.created", "job.failed"]
    assert result["status"] == "failed"


def waiting_record() -> PendingWrite:
    return PendingWrite(
        job_id="exec_kumo_1",
        run_id="run_kumo_1",
        workflow_id="run_kumo_1",
        client_id="kumo",
        approval_id="apr-exec_kumo_1",
        notes_written=len(FIRST_RUN_EVENTS),
        asked_at=1_794_636_000.0,
        write=PreparedWrite(
            (Locator("textbox", "Username", "/login"), Locator("textbox", "Password", "/login"), Locator("button", "Sign in", "/login")),
            Locator("button", "Save changes", "/inventory"),
            tuple(StockChange(**change) for change in KUMO["changes"]),
        ),
    )


def test_after_a_restart_a_waiting_job_is_picked_up_again_and_finishes_on_the_ok(monkeypatch):
    sites = install_sites(monkeypatch)
    node = load_node()
    pending = waiting_record()
    seams = Seams(monkeypatch, node, "approved", statuses=("pending",))
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[f"{PENDING_PREFIX}{pending.approval_id}"] = pending.to_json()
    statuses = {"exec_kumo_1": "waiting"}

    async def execution_status(execution_id):
        return statuses.get(execution_id)

    monkeypatch.setattr(node, "execution_status", execution_status)

    async def restart():
        tasks = await node.resume_waiting_jobs()
        return await asyncio.gather(*tasks)

    asyncio.run(restart())

    assert [note["n"] for note in seams.written] == list(range(len(FIRST_RUN_EVENTS) + 1, len(FIRST_RUN_EVENTS) + 1 + AFTER_APPROVAL))
    assert seams.events[0]["type"] == "approval.answered"
    assert seams.desk.paused == []
    assert [report[0] for report in seams.reports] == ["exec_kumo_1"]
    status = seams.reports[0][1]
    assert status["status"] == "succeeded"
    assert status["result"]["events"] == len(FIRST_RUN_EVENTS) + AFTER_APPROVAL
    assert status["result"]["status"] == "done"
    assert status["execution_id"] == "exec_kumo_1"
    assert sites.saves == ["Save changes"]
    assert not any(key.startswith(PENDING_PREFIX) for key in seams.memories["kumo"].values)


def test_a_resumed_job_still_waiting_when_the_agent_stops_again_reports_nothing(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    pending = waiting_record()
    seams = Seams(monkeypatch, node, FOREVER, statuses=("pending",))
    seams.desk.stopping_the_agent = True
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[f"{PENDING_PREFIX}{pending.approval_id}"] = pending.to_json()

    async def execution_status(execution_id):
        return "waiting"

    monkeypatch.setattr(node, "execution_status", execution_status)

    async def restart_then_stop():
        [task] = await node.resume_waiting_jobs()
        await seams.desk.waiting.wait()
        task.cancel()
        await task

    asyncio.run(restart_then_stop())

    assert seams.reports == []
    assert seams.written == []
    assert f"{PENDING_PREFIX}{pending.approval_id}" in seams.memories["kumo"].values


@pytest.mark.parametrize("status", ["cancelled", "failed", "succeeded", "timeout", None])
def test_after_a_restart_a_job_that_already_ended_is_only_forgotten(monkeypatch, status):
    sites = install_sites(monkeypatch)
    node = load_node()
    pending = waiting_record()
    seams = Seams(monkeypatch, node, "approved")
    seams.memories["kumo"] = MemoryStore()
    seams.memories["kumo"].values[f"{PENDING_PREFIX}{pending.approval_id}"] = pending.to_json()

    async def execution_status(execution_id):
        return status

    monkeypatch.setattr(node, "execution_status", execution_status)

    tasks = asyncio.run(node.resume_waiting_jobs())

    assert tasks == []
    assert seams.written == []
    assert seams.reports == []
    assert sites.commands == []
    assert seams.memories["kumo"].values == {}


def test_the_node_starts_looking_for_waiting_jobs_when_it_starts():
    node = load_node()
    assert node.app.router.lifespan_context is node.resume_on_start


class FakePauseManager:
    def __init__(self) -> None:
        self.resolved: list[tuple[str, str]] = []

    async def resolve(self, approval_request_id, result):
        self.resolved.append((approval_request_id, result.decision))
        return False


class FakeClient:
    def __init__(self, status: str) -> None:
        self.status = status
        self.asked: list[str] = []

    async def get_approval_status(self, execution_id):
        self.asked.append(execution_id)
        return ApprovalStatusResponse(status=self.status)


class FakeAgent:
    def __init__(self) -> None:
        self._shutdown_requested = False
        self._shutdown_cancelling = False
        self.calls: list[tuple] = []
        self.client = FakeClient("pending")
        self._pause_manager = FakePauseManager()

    async def pause(self, approval_request_id, **kwargs):
        self.calls.append(("pause", approval_request_id, kwargs))
        return ApprovalResult(decision="approved")

    async def wait_for_resume(self, approval_request_id, **kwargs):
        self.calls.append(("wait_for_resume", approval_request_id, kwargs))
        return ApprovalResult(decision="approved")


def test_the_agentfield_desk_uses_the_durable_pause_and_releases_it_on_stop():
    agent = FakeAgent()
    desk = AgentDesk(agent)

    async def use():
        await desk.pause("apr-1", expires_in_hours=720, timeout=60, execution_id="exec-1")
        await desk.wait_for_resume("apr-1", execution_id="exec-1", timeout=60)
        held = await desk.approval_status("exec-1")
        await desk.release("apr-1")
        return held

    held = asyncio.run(use())

    assert agent.calls == [
        ("pause", "apr-1", {"expires_in_hours": 720, "timeout": 60, "execution_id": "exec-1"}),
        ("wait_for_resume", "apr-1", {"execution_id": "exec-1", "timeout": 60}),
    ]
    assert held == "pending"
    assert agent.client.asked == ["exec-1"]
    assert agent._pause_manager.resolved == [("apr-1", "stopped")]


@pytest.mark.parametrize("flag", ["_shutdown_requested", "_shutdown_cancelling"])
def test_the_agentfield_desk_tells_a_shutdown_from_a_stop(flag):
    agent = FakeAgent()
    desk = AgentDesk(agent)
    assert desk.shutting_down() is False

    setattr(agent, flag, True)

    assert desk.shutting_down() is True


class ControlPlaneStandIn:
    def __init__(self) -> None:
        self.requested: list[dict] = []

    async def request_approval(self, **kwargs):
        self.requested.append(kwargs)
        return ApprovalRequestResponse(approval_request_id=kwargs["approval_request_id"], approval_request_url="")


def agent_http(node):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=node.app), base_url="http://agent.test")


def test_the_real_sdk_pause_is_answered_through_the_agents_own_approval_webhook(monkeypatch):
    node = load_node()
    control_plane = ControlPlaneStandIn()
    monkeypatch.setattr(node.app.client, "request_approval", control_plane.request_approval)
    monkeypatch.setattr(node.app, "base_url", "http://host.docker.internal:8001")
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: None)

    async def approve_through_the_webhook():
        desk = AgentDesk(node.app)
        waiting = asyncio.create_task(desk.pause("apr-exec-x", expires_in_hours=720, timeout=5, execution_id="exec-x"))
        while not control_plane.requested:
            await asyncio.sleep(0.01)
        async with agent_http(node) as http:
            response = await http.post("/webhooks/approval", json={"execution_id": "exec-x", "decision": "approved", "approval_request_id": "apr-exec-x", "feedback": ""})
        return response.json(), await waiting

    received, answer = asyncio.run(approve_through_the_webhook())

    assert received == {"status": "received", "resolved": True}
    assert answer.decision == "approved"
    assert control_plane.requested == [
        {
            "execution_id": "exec-x",
            "approval_request_id": "apr-exec-x",
            "approval_request_url": "",
            "callback_url": "http://host.docker.internal:8001/webhooks/approval",
            "expires_in_hours": 720,
        }
    ]


def test_stop_through_the_sdk_cancel_route_ends_the_waiting_job_and_releases_the_pause(monkeypatch):
    sites = install_sites(monkeypatch)
    node = load_node()
    control_plane = ControlPlaneStandIn()
    written: list[dict] = []
    monkeypatch.setattr(node.app.client, "request_approval", control_plane.request_approval)
    monkeypatch.setattr(node.app, "base_url", "http://host.docker.internal:8001")
    monkeypatch.setattr(node.app, "note", lambda message, tags=None: written.append(json.loads(message)) if tags == [EVENT_NOTE_TAG] else None)
    memory = MemoryStore()
    pending = waiting_record()

    async def stop_through_the_cancel_route():
        notes = EventNotes(node.app.note, start=pending.notes_written)
        task = asyncio.create_task(see_approval_through(AgentDesk(node.app), notes, resolve_job("kumo"), pending, memory))
        await register_execution_task(node.app, pending.job_id, task)
        while not control_plane.requested:
            await asyncio.sleep(0.01)
        async with agent_http(node) as http:
            response = await http.post(f"/_internal/executions/{pending.job_id}/cancel")
        outcome = await asyncio.gather(task, return_exceptions=True)
        await deregister_execution(node.app, pending.job_id)
        return response.json(), outcome

    cancelled, outcome = asyncio.run(stop_through_the_cancel_route())

    assert cancelled == {"cancelled": True, "execution_id": pending.job_id}
    assert isinstance(outcome[0], asyncio.CancelledError)
    assert [note["event"] for note in written] == [{"type": "job.failed", "reason": "stopped", "stepId": "ask", "message": "Stopped before saving. Nothing was changed."}]
    assert node.app._pause_manager._pending == {}
    assert node.app._pause_manager._exec_to_request == {}
    assert memory.values == {}
    assert sites.commands == []


class StatusPosts:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def __call__(self, method, url, **kwargs):
        self.sent.append((url, kwargs.get("json")))
        return httpx.Response(200, json={"status": "ok"})


def held_payload(execution_id: str) -> dict:
    return {
        "status": "succeeded",
        "result": {"clientId": "kumo", "status": "waiting", "approvalId": f"apr-{execution_id}", "resumable": True, "events": 21},
        "execution_id": execution_id,
        "reasoner": "run_job",
    }


def test_a_job_held_for_the_next_start_is_never_reported_to_agentfield_as_finished(monkeypatch):
    node = load_node()
    posts = StatusPosts()
    monkeypatch.setattr(node.app.client, "_async_request", posts)

    asyncio.run(node.app._post_execution_status("http://localhost:8080/api/v1/executions/exec-held/status", held_payload("exec-held"), "exec-held"))

    assert posts.sent == []


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "succeeded", "result": {"clientId": "kumo", "status": "done", "events": 30}},
        {"status": "succeeded", "result": {"clientId": "kumo", "status": "stopped", "events": 22}},
        {"status": "cancelled", "error": "cancelled_by_control_plane"},
        {"status": "failed", "error": "boom"},
    ],
)
def test_every_other_ending_is_still_reported(monkeypatch, payload):
    node = load_node()
    posts = StatusPosts()
    monkeypatch.setattr(node.app.client, "_async_request", posts)

    asyncio.run(node.app._post_execution_status("http://localhost:8080/api/v1/executions/exec-1/status", {**payload, "execution_id": "exec-1"}, "exec-1"))

    assert [url for url, _ in posts.sent] == ["http://localhost:8080/api/v1/executions/exec-1/status"]


def test_run_job_held_by_a_shutdown_returns_the_result_that_is_never_reported(monkeypatch):
    install_sites(monkeypatch)
    node = load_node()
    seams = Seams(monkeypatch, node, FOREVER)
    seams.desk.stopping_the_agent = True

    async def shut_down_while_waiting():
        task = asyncio.create_task(node.run_job(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))
        await seams.desk.waiting.wait()
        task.cancel()
        return await task

    result = asyncio.run(shut_down_while_waiting())

    assert result["status"] == "waiting"
    assert result["resumable"] is True
    assert node.held_for_next_start({"status": "succeeded", "result": result}) is True


def workflow_context(node, execution_id: str) -> ExecutionContext:
    return ExecutionContext(run_id="run-held", execution_id=execution_id, agent_instance=node.app, reasoner_name="run_job", agent_node_id=node.NODE_ID, registered=True)


def test_a_held_job_is_not_reported_as_finished_through_the_workflow_events_either(monkeypatch):
    node = load_node()
    posts = StatusPosts()
    monkeypatch.setattr(node.app.client, "_async_request", posts)
    held = held_payload("exec-held")["result"]

    asyncio.run(node.app.workflow_handler.notify_call_complete("exec-held", "run-held", held, 1200, workflow_context(node, "exec-held")))

    assert posts.sent == []


def test_a_finished_job_is_still_reported_through_the_workflow_events(monkeypatch):
    node = load_node()
    posts = StatusPosts()
    monkeypatch.setattr(node.app.client, "_async_request", posts)
    done = {"clientId": "kumo", "status": "done", "events": 30}

    asyncio.run(node.app.workflow_handler.notify_call_complete("exec-done", "run-done", done, 1200, workflow_context(node, "exec-done")))

    assert [url for url, _ in posts.sent] == ["http://localhost:8080/api/v1/workflow/executions/events"]
