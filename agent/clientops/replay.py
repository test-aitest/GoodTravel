import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, NoReturn
from urllib.parse import urlsplit

from agentfield.exceptions import MemoryAccessError

from .approval import (
    KEEP_STEPS_OFF,
    NO_UNDO_COPY,
    SAVE_STEP,
    STOPPED_AFTER_SAVING,
    STOPPED_BEFORE_SAVING,
    STORE_CHANGED,
    UNDO_KEY,
    ClientMemory,
    PageOpener,
    PendingWrite,
    UndoRecord,
    UndoRow,
    Writer,
    checked,
    failed,
    write_approved,
)
from .boundary import ClientBoundary, Site
from .compile_script import compile_read_script, compile_read_script_from, compile_write_script
from .computers import ComputerError
from .diff import DuplicateSku, StockDiff, diff_stock
from .claude_call import MODEL_REFUSED, ModelNotAllowed
from .externals import MissingExternalKey
from .page import Page, PageError, ScriptResult, ScriptStep
from .plan import elapsed_ms
from .procedure import Procedure, ProcedureDraft, ReadSteps, keep_if_verified, keep_rebuilt, may_keep, note_replay_time, saved_procedure
from .records import EventNotes
from .refind import FailedStep, NotFound, Recovered, locator_of, refind_by_tree, step_targets
from .repair import CLAUDE_RUNG, HUMAN_RUNG, RepairHelp, RepairSession, RepairStopped, current_repair, noun_for, repair_step
from .stock_sync import (
    JOB_TITLE,
    NOTHING_CHANGED,
    NOTHING_TO_SAVE,
    PlanStep,
    PreparedWrite,
    TableError,
    at_sentence_start,
    compared_detail,
    counted,
    spoken,
    stock_rows,
    stock_sync_plan,
)

Clock = Callable[[], float]

SCRIPT_RUNG = "script"
READ_STEPS = ("sign-in", "read", "compare", "check-save")
SUPPLIER_STEPS = frozenset({"sign-in", "read"})
PREPARE_STEP = "save-prepare"
SUBMIT_STEP = "save-submit"
NOT_SIGNED_IN = "not-signed-in"
STORE_CHANGED_CODE = "store-changed"
LOGIN_PROBLEMS = frozenset({"placeholder-unset", "vault-unreadable"})
CHANGED_STEPS = "the saved steps changed while this job waited"


async def repair_failed_step(failed_step: FailedStep, not_found: NotFound) -> Recovered | None:
    session = current_repair.get()
    if session is None:
        return None
    return await repair_step(failed_step, not_found, session)


async def recover_failed_step(failed_step: FailedStep) -> Recovered | None:
    found = await refind_by_tree(failed_step)
    if isinstance(found, NotFound):
        return await repair_failed_step(failed_step, found)
    return found


class ReplayStopped(Exception):
    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__(outcome.get("problem", "stopped"))
        self.outcome = outcome


def doesnt_match(site: Site) -> str:
    return f"{at_sentence_start(spoken(site))} doesn't match the saved steps any more, so I stopped."


def script_failure_message(site: Site, code: str | None, kind: str, after: str) -> str:
    if code == NOT_SIGNED_IN:
        return f"I couldn't sign in to {spoken(site)} with the saved login, so I stopped.{after}"
    if code == STORE_CHANGED_CODE:
        return STORE_CHANGED
    if kind == "check":
        return f"{doesnt_match(site)}{after}"
    return f"{at_sentence_start(spoken(site))} didn't respond as expected, so I stopped.{after}"


def work_failure_message(site: Site, error: Exception, after: str) -> tuple[str, str]:
    if isinstance(error, ModelNotAllowed):
        return f"{MODEL_REFUSED}{after}", "model-not-allowed"
    if isinstance(error, MissingExternalKey):
        return f"I couldn't open this client's computer, so I stopped.{after}", "MissingExternalKey"
    if isinstance(error, ComputerError):
        return f"This client's computer stopped answering, so I stopped.{after}", error.problem.value
    if isinstance(error, PageError) and error.code in LOGIN_PROBLEMS:
        return f"The {site.name} login isn't saved on this client's computer, so I stopped.{after}", error.code
    if isinstance(error, PageError):
        return f"{at_sentence_start(spoken(site))} didn't respond as expected, so I stopped.{after}", error.code
    return f"I couldn't read the stock table on {spoken(site)}, so I stopped.{after}", type(error).__name__


def site_for_logins(boundary: ClientBoundary, error: Exception) -> Site:
    missing = getattr(error, "missing", ())
    if any(name in boundary.store.logins for name in missing) and not any(name in boundary.supplier.logins for name in missing):
        return boundary.store
    return boundary.supplier


WORK_ERRORS = (ComputerError, MissingExternalKey, ModelNotAllowed, PageError, TableError, DuplicateSku)


def passed(step: ScriptStep | None) -> bool:
    return step is not None and step.status == "done"


class Replay:
    def __init__(
        self,
        notes: EventNotes,
        boundary: ClientBoundary,
        procedure: Procedure,
        *,
        request: str,
        job_id: str | None,
        clock: Clock,
        open_page: PageOpener | None,
        repair: RepairHelp | None = None,
    ) -> None:
        self.notes = notes
        self.boundary = boundary
        self.procedure = procedure
        self.repair = None if repair is None else RepairSession(repair, notes, job_id)
        self.renamed: dict[str, Any] | None = None
        self.request = request
        self.job_id = job_id
        self.clock = clock
        self._open_page = open_page or (lambda: boundary.job_computer().page())
        self.plan = {step.step_id: step for step in stock_sync_plan(boundary.supplier, boundary.store)}
        self.started_at = 0.0
        self.active: str | None = None
        self.working = procedure
        self.page: Page | None = None
        self.tables: dict[str, Any] = {}
        self.read_so_far: ScriptResult | None = None
        self.diff: StockDiff | None = None
        self.completed: list[str] = []

    def site_of(self, step_id: str | None) -> Site:
        return self.boundary.supplier if step_id in SUPPLIER_STEPS else self.boundary.store

    async def run(self) -> dict[str, Any]:
        self.started_at = self.clock()
        self.notes.emit(
            "job.created",
            clientName=self.boundary.client.name,
            title=JOB_TITLE,
            request=self.request,
            run={"kind": "replay", "procedureVersion": self.procedure.version},
            plan=[{"stepId": step.step_id, "title": step.title} for step in self.plan.values()],
        )
        repairing = current_repair.set(self.repair)
        try:
            outcome = await self._steps()
        except ReplayStopped as stopped:
            outcome = stopped.outcome
        except WORK_ERRORS as error:
            site = site_for_logins(self.boundary, error) if isinstance(error, PageError) and error.code in LOGIN_PROBLEMS else self.site_of(self.active)
            message, problem = work_failure_message(site, error, f" {NOTHING_CHANGED}")
            outcome = failed(self.notes, self.active, message, problem)
        finally:
            current_repair.reset(repairing)
        if self.repair is None:
            return {**outcome, "claudeCalls": 0, "claudeUsage": {}}
        return {**outcome, "claudeCalls": self.repair.claude_calls, "claudeUsage": self.repair.usage}

    async def _steps(self) -> dict[str, Any]:
        self._start(self.plan["sign-in"])
        ran = await self._run(compile_read_script(self.working.read, self.boundary))
        for step_id in READ_STEPS:
            step = self.plan[step_id]
            if step_id != "sign-in":
                self._start(step)
            script_step = ran.step(step_id)
            climbed_ms = 0
            if passed(script_step):
                self._decided(step_id, SCRIPT_RUNG, 0)
            else:
                recovered = await self._recover(ran)
                self._decided(step_id, recovered.rung, recovered.took_ms, recovered.escalated_from)
                if recovered.snapshot is not None:
                    self._seen(step_id, recovered.snapshot)
                self._note_rename(recovered)
                self.working = recovered.procedure
                ran = await self._run(compile_read_script_from(self.working.read, self.boundary, step_id))
                script_step = ran.step(step_id)
                if not passed(script_step):
                    self._stop(ran)
                climbed_ms = recovered.took_ms
            if script_step.pages:
                self._seen(step_id, script_step.pages[-1])
            self._done(self._found_new(step), climbed_ms + script_step.took_ms, self._renamed_detail(step_id) or self._detail(step_id))
            self.completed.append(step_id)
        if self.diff is None:
            self._stop(ran)
        if not self.diff.changes:
            for step_id in ("ask", "save"):
                self.notes.emit("step.done", stepId=step_id, summary=NOTHING_TO_SAVE, tookMs=0)
            return {"status": "done", "changes": 0}
        return self._ask(self.diff)

    async def _run(self, script: str) -> ScriptResult:
        if self.page is None:
            self.page = await asyncio.to_thread(self._open_page)
        ran: ScriptResult = await asyncio.to_thread(self.page.run_script, script)
        self.tables.update(ran.tables)
        self.read_so_far = replace(ran, tables=dict(self.tables))
        self.diff = self._diff()
        return ran

    def _diff(self) -> StockDiff | None:
        if self.read_so_far is None or "supplier" not in self.tables or "store" not in self.tables:
            return None
        return diff_stock(stock_rows(self.read_so_far.table("supplier")), stock_rows(self.read_so_far.table("store")))

    def _detail(self, step_id: str) -> str | None:
        if step_id == "read" and self.read_so_far is not None:
            return counted(len(self.read_so_far.table("supplier").rows), "product")
        if step_id == "compare" and self.diff is not None:
            return compared_detail(self.diff)
        return None

    def _failure_of(self, ran: ScriptResult) -> tuple[str | None, str, str | None, str]:
        failure = ran.failure
        step_id = failure.step_id if failure and failure.step_id in READ_STEPS else self.active
        if failure is None:
            return step_id, "step", None, "The script ended early"
        return step_id, failure.kind, failure.code, failure.message

    async def _recover(self, ran: ScriptResult) -> Recovered:
        step_id, kind, code, message = self._failure_of(ran)
        failed_step = FailedStep(
            step_id,
            kind,
            code,
            message,
            tuple(self.completed),
            self.working,
            self.boundary,
            self.read_so_far or ran,
            self._open_page,
            self.clock,
        )
        try:
            recovered = await recover_failed_step(failed_step)
        except RepairStopped as stopped:
            raise ReplayStopped(failed(self.notes, step_id, stopped.message, stopped.problem)) from None
        if recovered is None:
            self._stop(ran)
        return recovered

    def _note_rename(self, recovered: Recovered) -> None:
        if recovered.rung not in (CLAUDE_RUNG, HUMAN_RUNG) or self.renamed is not None:
            return
        before = step_targets(self.working.read, recovered.step_id)[recovered.element].hint
        after = step_targets(recovered.procedure.read, recovered.step_id)[recovered.element].hint
        self.renamed = {
            "stepId": recovered.step_id,
            "role": after.role,
            "change": {
                "stepId": recovered.step_id,
                "fromVersion": self.procedure.version,
                "toVersion": self.procedure.version + 1,
                "before": before.name,
                "after": after.name,
            },
        }

    def _found_new(self, step: PlanStep) -> PlanStep:
        if self.renamed is None or self.renamed["stepId"] != step.step_id:
            return step
        return replace(step, summary=f"Found the new {noun_for(self.renamed['role'])}")

    def _renamed_detail(self, step_id: str) -> str | None:
        if self.renamed is None or self.renamed["stepId"] != step_id:
            return None
        change = self.renamed["change"]
        return f"{change['before']} is now {change['after']}"

    def _stop(self, ran: ScriptResult) -> NoReturn:
        step_id, kind, code, _ = self._failure_of(ran)
        text = script_failure_message(self.site_of(step_id), code, kind, f" {NOTHING_CHANGED}")
        raise ReplayStopped(failed(self.notes, step_id, text, code or "script-failed"))

    def _ask(self, diff: StockDiff) -> dict[str, Any]:
        step = self.plan["ask"]
        self._start(step)
        approval_id = f"apr-{self.job_id or secrets.token_hex(6)}"
        changes = [{"sku": change.sku, "name": change.name, "store": change.store, "supplier": change.supplier} for change in diff.changes]
        question = f"Save {counted(len(changes), 'stock change')} to {spoken(self.boundary.store)}?"
        if self.renamed is not None:
            question = f"Save {counted(len(changes), 'stock change')} with the new {noun_for(self.renamed['role'])}?"
        self.notes.approval_requested(
            approval_id,
            step.step_id,
            question,
            changes,
            undoable=True,
            only_in_store=len(diff.missing_at_supplier),
            only_at_supplier=len(diff.missing_in_store),
            procedure_change=None if self.renamed is None else self.renamed["change"],
        )
        ready_in_ms = max(0, elapsed_ms(self.started_at, self.clock()) - (self.repair.waited_ms if self.repair else 0))
        store = self.working.read.store
        prepared = PreparedWrite(
            (locator_of(store.user.hint), locator_of(store.password.hint), locator_of(store.submit.hint)),
            locator_of(self.working.read.save_button.hint),
            diff.changes,
        )
        waiting: dict[str, Any] = {
            "status": "waiting",
            "approvalId": approval_id,
            "changes": len(changes),
            "soldOut": diff.sold_out,
            "readyInMs": ready_in_ms,
            "replay": {"procedureVersion": self.procedure.version},
            "write": prepared.to_json(),
        }
        if self.working.read != self.procedure.read:
            waiting["procedure"] = ProcedureDraft(self.working.read, self.procedure.expected_replay_ms).to_json()
        return waiting

    def _start(self, step: PlanStep) -> None:
        self.active = step.step_id
        body: dict[str, Any] = {"stepId": step.step_id, "activity": step.activity}
        if step.detail:
            body["detail"] = step.detail
        self.notes.emit("step.started", **body)

    def _decided(self, step_id: str, rung: str, took_ms: int, escalated_from: tuple[str, ...] = ()) -> None:
        body: dict[str, Any] = {"stepId": step_id, "rung": rung, "tookMs": took_ms}
        if escalated_from:
            body["escalatedFrom"] = list(escalated_from)
        self.notes.emit("step.decided", **body)

    def _done(self, step: PlanStep, took_ms: int, detail: str | None) -> None:
        body: dict[str, Any] = {"stepId": step.step_id, "summary": step.summary}
        if detail:
            body["detail"] = detail
        self.notes.emit("step.done", **body, tookMs=took_ms)
        self.active = None

    def _seen(self, step_id: str, snapshot: Any) -> None:
        for site in self.boundary.sites:
            if urlsplit(site.url).netloc == snapshot.host:
                self.notes.page_seen(step_id, site.host, snapshot.html)
                return
        self.notes.page_seen(step_id, snapshot.host, snapshot.html)


async def run_replay(
    notes: EventNotes,
    boundary: ClientBoundary,
    procedure: Procedure,
    *,
    request: str,
    job_id: str | None = None,
    clock: Clock = time.monotonic,
    open_page: PageOpener | None = None,
    repair: RepairHelp | None = None,
) -> dict[str, Any]:
    return await Replay(notes, boundary, procedure, request=request, job_id=job_id, clock=clock, open_page=open_page, repair=repair).run()


async def put_back(memory: ClientMemory, previous: Any) -> None:
    try:
        if previous is None:
            await memory.delete(UNDO_KEY)
        else:
            await memory.set(UNDO_KEY, previous)
    except MemoryAccessError:
        return


async def write_with_script(
    notes: EventNotes,
    boundary: ClientBoundary,
    pending: PendingWrite,
    memory: ClientMemory,
    procedure: Procedure,
    *,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
    wall: Clock = time.time,
) -> dict[str, Any]:
    write = pending.write
    store = boundary.store
    started = clock()
    notes.emit("step.started", stepId=SAVE_STEP, activity=f"Saving {counted(len(write.changes), 'change')}")
    notes.emit("step.decided", stepId=SAVE_STEP, rung=SCRIPT_RUNG, tookMs=0)
    record = UndoRecord(
        job_id=pending.job_id,
        saved_at=datetime.fromtimestamp(wall(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sign_in=write.sign_in,
        save_button=write.save_button,
        rows=tuple(UndoRow(change.sku, change.name, change.store, change.supplier) for change in write.changes),
    )
    try:
        previous = await memory.get(UNDO_KEY)
        await memory.set(UNDO_KEY, record.to_json())
    except MemoryAccessError:
        return failed(notes, SAVE_STEP, NO_UNDO_COPY, "undo-copy-unavailable")
    opener = open_page or (lambda: boundary.job_computer().page())
    submitted = False
    try:
        script = compile_write_script(procedure, boundary, write.changes)
        page: Page = await asyncio.to_thread(opener)
        submitted = True
        ran: ScriptResult = await asyncio.to_thread(page.run_script, script)
    except asyncio.CancelledError:
        notes.emit("job.failed", reason="stopped", message=STOPPED_AFTER_SAVING if submitted else STOPPED_BEFORE_SAVING, stepId=SAVE_STEP)
        raise
    except WORK_ERRORS as error:
        await put_back(memory, previous)
        message, problem = work_failure_message(store, error, f" {NOTHING_CHANGED}")
        return failed(notes, SAVE_STEP, message, problem)
    prepared = ran.step(PREPARE_STEP)
    for step_id in (PREPARE_STEP, SUBMIT_STEP):
        found = ran.step(step_id)
        for snapshot in found.pages if found else ():
            notes.page_seen(SAVE_STEP, store.host, snapshot.html)
    if not ran.passed:
        clicked = prepared is not None and prepared.status == "done"
        if not clicked:
            await put_back(memory, previous)
        failure = ran.failure
        code = failure.code if failure else None
        after = " You can undo this sync." if clicked else f" {NOTHING_CHANGED}"
        message = script_failure_message(store, code, failure.kind if failure else "step", after)
        return failed(notes, SAVE_STEP, message, code or "script-failed")
    notes.emit("write.done", stepId=SAVE_STEP, written=len(write.changes))
    try:
        before, reread = stock_rows(ran.table("before")), stock_rows(ran.table("after"))
    except (TableError, PageError) as error:
        message, problem = work_failure_message(store, error, " You can undo this sync.")
        return failed(notes, SAVE_STEP, message, problem)
    outcome = checked(
        notes,
        SAVE_STEP,
        started,
        clock,
        before,
        write.changes,
        reread,
        ran.said("status"),
        closing=KEEP_STEPS_OFF,
        action="saved products",
        summary="Saved and checked every row",
    )
    if may_keep(outcome) and pending.ready_in_ms is not None:
        await note_replay_time(memory, boundary, version=procedure.version, ready_in_ms=pending.ready_in_ms)
    return outcome


def script_writer(
    procedure: Procedure,
    *,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
    wall: Clock = time.time,
) -> Writer:
    async def write(notes: EventNotes, boundary: ClientBoundary, pending: PendingWrite, memory: ClientMemory) -> dict[str, Any]:
        return await write_with_script(notes, boundary, pending, memory, procedure, open_page=open_page, clock=clock, wall=wall)

    return write


def learning_writer(*, open_page: PageOpener | None = None, clock: Clock = time.monotonic, wall: Clock = time.time) -> Writer:
    async def write(notes: EventNotes, boundary: ClientBoundary, pending: PendingWrite, memory: ClientMemory) -> dict[str, Any]:
        draft = pending.procedure

        async def keep(outcome: dict[str, Any], fields: Any) -> None:
            if draft is not None:
                await keep_if_verified(notes, boundary, memory, draft, outcome, fields, pending.write.changes, wall=wall)

        return await write_approved(notes, boundary, pending, memory, open_page=open_page, clock=clock, wall=wall, keep=keep)

    return write


def rebuilt_writer(
    saved: Procedure,
    read: ReadSteps,
    *,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
    wall: Clock = time.time,
) -> Writer:
    async def write(notes: EventNotes, boundary: ClientBoundary, pending: PendingWrite, memory: ClientMemory) -> dict[str, Any]:
        working = replace(saved, read=read)
        outcome = await write_with_script(notes, boundary, replace(pending, ready_in_ms=None), memory, working, open_page=open_page, clock=clock, wall=wall)
        await keep_rebuilt(notes, boundary, memory, working, outcome, wall=wall)
        return outcome

    return write


def changed_while_waiting() -> Writer:
    async def write(notes: EventNotes, boundary: ClientBoundary, pending: PendingWrite, memory: ClientMemory) -> dict[str, Any]:
        return failed(notes, SAVE_STEP, f"{at_sentence_start(CHANGED_STEPS)}, so I stopped. {NOTHING_CHANGED}", "procedure-changed")

    return write


async def writer_for(pending: PendingWrite, boundary: ClientBoundary, memory: ClientMemory) -> Writer:
    if pending.replay_version is None:
        return learning_writer()
    procedure = await saved_procedure(memory, boundary)
    if procedure is None or procedure.version != pending.replay_version:
        return changed_while_waiting()
    if pending.procedure is not None:
        return rebuilt_writer(procedure, pending.procedure.read)
    return script_writer(procedure)


async def replay_saved_steps(boundary: ClientBoundary, memory: ClientMemory, *, open_page: PageOpener | None = None) -> dict[str, Any]:
    client_id = boundary.client.id
    procedure = await saved_procedure(memory, boundary)
    if procedure is None:
        return {"clientId": client_id, "ok": False, "problem": "no-saved-steps"}
    opener = open_page or (lambda: boundary.job_computer().page())
    try:
        page: Page = await asyncio.to_thread(opener)
        ran: ScriptResult = await asyncio.to_thread(page.run_script, compile_read_script(procedure.read, boundary))
    except WORK_ERRORS as error:
        _, problem = work_failure_message(boundary.supplier, error, "")
        return {"clientId": client_id, "ok": False, "problem": problem}
    return {"clientId": client_id, "ok": ran.passed, "procedureVersion": procedure.version, **ran.to_json()}
