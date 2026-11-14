import asyncio
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from agentfield import Agent
from agentfield.agent_workflow import AgentWorkflow
from agentfield.cancel import deregister_execution, register_execution_task
from agentfield.exceptions import AgentFieldClientError, MemoryAccessError
from agentfield.execution_context import ExecutionContext, reset_execution_context, set_execution_context
from agentfield.memory import MemoryClient, MemoryInterface

from .approval import (
    PENDING_PREFIX,
    UNDO_REQUEST,
    AgentDesk,
    ApprovalDesk,
    ClientMemory,
    PendingWrite,
    forget,
    see_approval_through,
    undo_last_sync,
)
from .boundary import (
    Client,
    ClientBoundary,
    ClientNotSetUp,
    ComputerKind,
    UnknownClient,
    admit_job,
    computer_kind,
    data_directory,
    known_clients,
    resolve_job,
)
from .chat import reply_in_chat
from .claude_call import MODEL_REFUSED, ModelNotAllowed, claude_ai_config
from .plan import AiCall
from .computers import AUTO_STOP_MINUTES, ComputerError
from .cost import CostLedger, JobMeter, finish_job, open_job, suspend_job
from .demo_reset import demo_reset_enabled, reset_demo_client
from .externals import MissingExternalKey, claude_ai
from .procedure import ProcedureDraft, saved_procedure
from .records import EventNotes
from .refind import refind_saved_step
from .repair import RepairHelp
from .replay import replay_saved_steps, run_replay, writer_for
from .schedule import (
    ScheduleError,
    Scheduler,
    has_saved_steps,
    keep_schedule,
    read_schedule,
    scheduler_enabled,
    start_through_control_plane,
    waiting_job,
)
from .settings import claude_monthly_cap_usd
from .setup_skills import router as setup_router
from .stock_sync import NOTHING_CHANGED, PreparedWrite, run_first_run
from .trip import run_trip_book, run_trip_search, trip_book_request, trip_search_request
from .trip_talk import talk_about_trip

NODE_ID = "clientops"
JOB_REASONER = "run_job"
CHAT_REASONER = "chat_reply"
TRIP_TALK_REASONER = "trip_talk"
RESUMABLE_STATUSES = frozenset({"waiting", "running"})
RESUME_ATTEMPTS = 30
RESUME_RETRY_SECONDS = 2.0
REPORT_ATTEMPTS = 3
CONTROL_PLANE_TIMEOUT_SECONDS = 10


def node_settings() -> dict:
    return {
        "agentfield_server": os.environ.get("AGENTFIELD_SERVER", "http://localhost:8080"),
        "api_key": os.environ.get("AGENTFIELD_API_KEY") or None,
    }


def held_result(result: Any) -> bool:
    return isinstance(result, dict) and result.get("resumable") is True


def held_for_next_start(payload: dict[str, Any]) -> bool:
    return payload.get("status") == "succeeded" and held_result(payload.get("result"))


class HeldAwareWorkflow(AgentWorkflow):
    async def notify_call_complete(self, execution_id: str, workflow_id: str, result: Any, *args: Any, **kwargs: Any) -> None:
        if held_result(result):
            return
        await super().notify_call_complete(execution_id, workflow_id, result, *args, **kwargs)


class ClientOpsAgent(Agent):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.workflow_handler = HeldAwareWorkflow(self)

    async def _post_execution_status(self, callback_url: str, payload: dict[str, Any], execution_id: str, max_retries: int = 5) -> None:
        if held_for_next_start(payload):
            suspend_job(execution_id)
            return
        try:
            await finish_job(execution_id, payload)
        finally:
            await super()._post_execution_status(callback_url, payload, execution_id, max_retries)


app = ClientOpsAgent(node_id=NODE_ID, ai_config=claude_ai_config(), **node_settings())
app.include_router(setup_router)
resumed_jobs: set[asyncio.Task] = set()


def current_job_id() -> str | None:
    context = app.ctx
    return context.execution_id if context else None


def approval_desk(agent: Agent) -> ApprovalDesk:
    return AgentDesk(agent)


def client_memory(boundary: ClientBoundary, context: ExecutionContext) -> ClientMemory:
    return boundary.memory_in(MemoryInterface(MemoryClient(app.client, context, agent_node_id=app.node_id), None))


def execution_context(execution_id: str, run_id: str, workflow_id: str | None = None, reasoner: str = JOB_REASONER) -> ExecutionContext:
    return ExecutionContext(
        run_id=run_id,
        execution_id=execution_id,
        agent_instance=app,
        reasoner_name=reasoner,
        agent_node_id=NODE_ID,
        workflow_id=workflow_id or run_id,
        registered=True,
    )


def this_job(reasoner: str = JOB_REASONER) -> ExecutionContext:
    context = app.ctx
    if context is not None:
        return context
    local = f"job-{secrets.token_hex(6)}"
    return execution_context(local, local, reasoner=reasoner)


def client_time_zone(client_id: str) -> ZoneInfo | None:
    try:
        return read_schedule(client_id).zone
    except ScheduleError:
        return None


def stop_client_computer(boundary: ClientBoundary) -> bool:
    try:
        return boundary.stop_computer()
    except (ComputerError, MissingExternalKey):
        return False


def cost_meter(boundary: ClientBoundary, job_id: str, *, resumed_since: float | None = None) -> JobMeter:
    client_id = boundary.client.id
    on_daytona = computer_kind() is ComputerKind.DAYTONA
    return JobMeter(
        client_id,
        job_id,
        ledger=CostLedger(data_directory()),
        cap=lambda: claude_monthly_cap_usd(client_id),
        estimated=not on_daytona,
        zone=client_time_zone(client_id),
        stop_computer=(lambda: stop_client_computer(boundary)) if on_daytona else None,
        auto_stop_seconds=AUTO_STOP_MINUTES * 60 if on_daytona else None,
        resumed_since=resumed_since,
    )


def claude_if_key() -> AiCall | None:
    try:
        return claude_ai(app)
    except MissingExternalKey:
        return None


def chat_meter(client_id: str, job_id: str) -> JobMeter:
    return JobMeter(
        client_id,
        job_id,
        ledger=CostLedger(data_directory()),
        cap=lambda: claude_monthly_cap_usd(client_id),
        estimated=False,
        zone=client_time_zone(client_id),
    )


@app.reasoner()
async def run_job(client_id: str, client_name: str, request: str) -> dict:
    notes = EventNotes(app.note)
    job_id = current_job_id()
    admission = admit_job(notes, client_id=client_id, request=request, job_id=job_id)
    if admission.boundary is None:
        return {"clientId": client_id, **admission.outcome, "events": notes.count}
    boundary = admission.boundary
    context = this_job()
    meter = open_job(cost_meter(boundary, context.execution_id), notes)
    memory = client_memory(boundary, context)
    procedure = await saved_procedure(memory, boundary)
    desk = approval_desk(app)
    if procedure is not None:
        first = await run_replay(notes, boundary, procedure, request=request, job_id=job_id, repair=RepairHelp(None, desk, context.execution_id, make_ai=claude_if_key))
    else:
        try:
            ai = claude_if_key()
        except ModelNotAllowed:
            notes.emit("job.created", clientName=boundary.client.name, title="Claude setting not allowed", request=request, run={"kind": "learn"}, plan=[])
            notes.emit("job.failed", reason="error", message=f"{MODEL_REFUSED} {NOTHING_CHANGED}")
            return {"clientId": client_id, "status": "failed", "reason": "model_not_allowed", "events": notes.count}
        first = await run_first_run(notes, boundary, ai, request=request, job_id=job_id)
        first.pop("readyInMs", None)
    meter.end_phase()
    write = first.pop("write", None)
    draft = first.pop("procedure", None)
    replayed = first.pop("replay", None)
    ready = {"readyInMs": first["readyInMs"]} if procedure is not None and "readyInMs" in first else {}
    if write is None:
        return {"clientId": client_id, **first, "events": notes.count}
    pending = PendingWrite(
        job_id=context.execution_id,
        run_id=context.run_id,
        workflow_id=context.workflow_id or context.run_id,
        client_id=boundary.client.id,
        approval_id=first["approvalId"],
        notes_written=notes.count,
        asked_at=time.time(),
        write=PreparedWrite.from_json(write),
        procedure=None if draft is None else ProcedureDraft.from_json(draft),
        replay_version=None if replayed is None else replayed["procedureVersion"],
        ready_in_ms=ready.get("readyInMs"),
    )
    writer = await writer_for(pending, boundary, memory)
    outcome = await see_approval_through(desk, notes, boundary, pending, memory, writer=writer)
    return {"clientId": client_id, **outcome, "claudeCalls": first["claudeCalls"], "claudeUsage": first["claudeUsage"], **ready, "events": notes.count}


def trip_claude() -> AiCall | None:
    try:
        return claude_if_key()
    except ModelNotAllowed:
        return None


@app.reasoner()
async def trip_search(client_id: str, to: str, date: str) -> dict:
    notes = EventNotes(app.note)
    context = this_job("trip_search")
    admission = admit_job(notes, client_id=client_id, request=trip_search_request(str(to), str(date)), job_id=context.execution_id)
    if admission.boundary is None:
        return {"clientId": client_id, **admission.outcome, "events": notes.count}
    open_job(cost_meter(admission.boundary, context.execution_id), notes)
    outcome = await run_trip_search(notes, admission.boundary, trip_claude(), to=to, date=date, memory=client_memory(admission.boundary, context))
    return {"clientId": client_id, **outcome, "events": notes.count}


@app.reasoner()
async def trip_book(client_id: str, option: str, seat: str) -> dict:
    notes = EventNotes(app.note)
    context = this_job("trip_book")
    admission = admit_job(notes, client_id=client_id, request=trip_book_request(str(option), str(seat)), job_id=context.execution_id)
    if admission.boundary is None:
        return {"clientId": client_id, **admission.outcome, "events": notes.count}
    open_job(cost_meter(admission.boundary, context.execution_id), notes)
    outcome = await run_trip_book(notes, admission.boundary, approval_desk(app), option=option, seat=seat, job_id=context.execution_id)
    return {"clientId": client_id, **outcome, "events": notes.count}


@app.reasoner()
async def undo_sync(client_id: str) -> dict:
    notes = EventNotes(app.note)
    context = this_job("undo_sync")
    admission = admit_job(notes, client_id=client_id, request=UNDO_REQUEST, job_id=context.execution_id)
    if admission.boundary is None:
        return {"clientId": client_id, **admission.outcome, "events": notes.count}
    open_job(cost_meter(admission.boundary, context.execution_id), notes)
    outcome = await undo_last_sync(notes, admission.boundary, client_memory(admission.boundary, context))
    return {"clientId": client_id, **outcome, "events": notes.count}


@app.reasoner()
async def chat_reply(client_id: str, message: str, facts: list[dict[str, str]]) -> dict:
    context = this_job(CHAT_REASONER)
    return await reply_in_chat(
        client_id,
        message,
        facts,
        make_ai=claude_if_key,
        open_meter=lambda client: chat_meter(client, context.execution_id),
    )


@app.reasoner()
async def trip_talk(client_id: str, said: str, context: dict[str, Any]) -> dict:
    job = this_job(TRIP_TALK_REASONER)
    return await talk_about_trip(
        client_id,
        said,
        context,
        make_ai=claude_if_key,
        open_meter=lambda client: chat_meter(client, job.execution_id),
    )

@app.skill()
async def replay_script(client_id: str) -> dict:
    try:
        boundary = resolve_job(client_id)
    except (UnknownClient, ClientNotSetUp):
        return {"clientId": client_id, "ok": False, "problem": "not-set-up"}
    return await replay_saved_steps(boundary, client_memory(boundary, this_job("replay_script")))


@app.skill()
async def reset_demo(client_id: str) -> dict:
    return await reset_demo_client(client_id, enabled=demo_reset_enabled(), resolve=resolve_job, memory_for=lambda boundary: client_memory(boundary, this_job("reset_demo")))


@app.skill()
async def refind_by_tree(client_id: str, step_id: str) -> dict:
    try:
        boundary = resolve_job(client_id)
    except (UnknownClient, ClientNotSetUp):
        return {"clientId": client_id, "stepId": step_id, "found": False, "problem": "not-set-up"}
    return await refind_saved_step(boundary, client_memory(boundary, this_job("refind_by_tree")), step_id)


def control_plane_call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    settings = node_settings()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings["api_key"]:
        headers["X-API-Key"] = settings["api_key"]
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{settings['agentfield_server'].rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=CONTROL_PLANE_TIMEOUT_SECONDS) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        return error.code, None


async def execution_status(execution_id: str) -> str | None:
    status, body = await asyncio.to_thread(control_plane_call, "GET", f"/api/v1/executions/{quote(execution_id, safe='')}")
    if status == 404:
        return None
    if status >= 400 or not isinstance(body, dict):
        raise AgentFieldClientError(f"AgentField answered {status} for execution {execution_id}")
    return str(body.get("status", "")).strip().lower()


async def report_finished(execution_id: str, payload: dict[str, Any]) -> None:
    try:
        await finish_job(execution_id, payload)
    finally:
        await post_finished(execution_id, payload)


async def post_finished(execution_id: str, payload: dict[str, Any]) -> None:
    for attempt in range(REPORT_ATTEMPTS):
        try:
            status, _ = await asyncio.to_thread(control_plane_call, "POST", f"/api/v1/executions/{quote(execution_id, safe='')}/status", payload)
            if status < 500:
                return
        except OSError:
            pass
        await asyncio.sleep(2**attempt)


async def resume_job(boundary: ClientBoundary, pending: PendingWrite, memory: ClientMemory) -> None:
    token = set_execution_context(execution_context(pending.job_id, pending.run_id, pending.workflow_id))
    started = time.time()
    try:
        notes = EventNotes(app.note, start=pending.notes_written)
        open_job(cost_meter(boundary, pending.job_id, resumed_since=pending.asked_at), notes)
        writer = await writer_for(pending, boundary, memory)
        outcome = await see_approval_through(approval_desk(app), notes, boundary, pending, memory, resumed=True, writer=writer)
        if outcome.get("resumable"):
            return
        await report_finished(
            pending.job_id,
            {
                "status": "succeeded",
                "result": {"clientId": pending.client_id, **outcome, "events": notes.count},
                "duration_ms": round((time.time() - started) * 1000),
                "completed_at": datetime.now(UTC).isoformat(),
                "execution_id": pending.job_id,
                "reasoner": JOB_REASONER,
            },
        )
    finally:
        reset_execution_context(token)
        await deregister_execution(app, pending.job_id)


async def resume_waiting_jobs() -> list[asyncio.Task]:
    tasks: list[asyncio.Task] = []
    for client in known_clients():
        try:
            boundary = resolve_job(client.id)
        except ClientNotSetUp:
            continue
        memory = client_memory(boundary, execution_context("resume", "resume", reasoner="resume"))
        for key in await memory.list_keys():
            if not key.startswith(PENDING_PREFIX):
                continue
            try:
                pending = PendingWrite.from_json(await memory.get(key))
            except (KeyError, TypeError, ValueError):
                await forget(memory, key)
                continue
            if pending.client_id != boundary.client.id or await execution_status(pending.job_id) not in RESUMABLE_STATUSES:
                await forget(memory, key)
                continue
            task = asyncio.create_task(resume_job(boundary, pending, memory))
            await register_execution_task(app, pending.job_id, task)
            resumed_jobs.add(task)
            task.add_done_callback(resumed_jobs.discard)
            tasks.append(task)
    return tasks


async def resume_when_ready() -> None:
    for _ in range(RESUME_ATTEMPTS):
        try:
            await resume_waiting_jobs()
            return
        except (AgentFieldClientError, MemoryAccessError, OSError):
            await asyncio.sleep(RESUME_RETRY_SECONDS)


async def saved_steps_for(boundary: ClientBoundary) -> bool:
    return await has_saved_steps(client_memory(boundary, execution_context("schedule", "schedule", reasoner="schedule")), boundary)


async def waiting_job_for(boundary: ClientBoundary) -> str | None:
    return await waiting_job(client_memory(boundary, execution_context("schedule", "schedule", reasoner="schedule")), boundary, execution_status)


async def start_scheduled_job(client: Client, request: str) -> str:
    return await start_through_control_plane(control_plane_call, f"{NODE_ID}.{JOB_REASONER}", client, request)


scheduler = Scheduler(has_saved_steps=saved_steps_for, waiting_job=waiting_job_for, start=start_scheduled_job)


@asynccontextmanager
async def resume_on_start(_server: Any):
    looking = asyncio.create_task(resume_when_ready())
    ticking = asyncio.create_task(keep_schedule(scheduler)) if scheduler_enabled() else None
    try:
        yield
    finally:
        looking.cancel()
        if ticking is not None:
            ticking.cancel()


app.router.lifespan_context = resume_on_start
