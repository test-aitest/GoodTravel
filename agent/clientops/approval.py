import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from agentfield.client import ApprovalResult
from agentfield.exceptions import AgentFieldClientError, MemoryAccessError

from .boundary import ClientBoundary
from .computers import ComputerError
from .diff import DuplicateSku, StockChange, StockRow
from .externals import MissingExternalKey
from .page import Element, Locator, Page, PageError, Snapshot, placeholder
from .plan import FILLABLE_ROLES, elapsed_ms
from .procedure import ProcedureDraft
from .records import EventNotes
from .stock_sync import (
    NOTHING_CHANGED,
    SIGNED_IN_SELECTOR,
    PreparedWrite,
    SignInFailed,
    TableError,
    at_sentence_start,
    counted,
    spoken,
    stock_rows,
)
from .verify import Mismatch, expected_after_write, verify_rows

Clock = Callable[[], float]
PageOpener = Callable[[], Page]

APPROVAL_EXPIRES_IN_HOURS = 24 * 30
ANSWER_POLL_SECONDS = 60
APPROVED = "approved"
EXPIRED = "expired"
STOPPED = "stopped"
PENDING = "pending"
APPROVER = "You"
RECORD_VERSION = 1
PENDING_PREFIX = "approvals/pending/"
UNDO_KEY = "stock-sync/undo"
SAVE_STATUS_SELECTOR = '[role="status"]'
SAVED_WORD = re.compile(r"\bsaved\b", re.IGNORECASE)

ASK_STEP = "ask"
SAVE_STEP = "save"
SIGN_IN_STEP = "sign-in"
UNDO_STEP = "undo"
UNDO_TITLE = "Undo stock sync"
UNDO_REQUEST = "Undo this sync"
KEEP_STEPS_OFF = "I stopped and didn't save the steps."

COULDNT_HOLD = f"I couldn't hold the job for your OK, so I stopped. {NOTHING_CHANGED}"
STOPPED_BEFORE_SAVING = f"Stopped before saving. {NOTHING_CHANGED}"
STOPPED_AFTER_SAVING = "Stopped after saving, before every row was checked. You can undo this sync."
NO_UNDO_COPY = f"I couldn't keep a copy of the current stock for undo, so I stopped. {NOTHING_CHANGED}"
STORE_CHANGED = f"The store admin changed after you approved, so I stopped. {NOTHING_CHANGED}"
FIELD_UNKNOWN = f"I couldn't tell which field holds a product's stock, so I stopped. {NOTHING_CHANGED}"
NOTHING_TO_UNDO = f"There's no sync to undo for this client. {NOTHING_CHANGED}"
UNDO_UNREADABLE = f"I couldn't read the copy from before the sync, so I stopped. {NOTHING_CHANGED}"
CHANGED_SINCE_SYNC = f"Some of these products changed after the sync, so I didn't put anything back. {NOTHING_CHANGED}"
NOT_APPROVED = {
    "rejected": f"The changes were turned down. {NOTHING_CHANGED}",
    "request_changes": f"Someone asked for different changes. {NOTHING_CHANGED}",
    EXPIRED: f"No one answered in time. {NOTHING_CHANGED}",
}
NOT_ANSWERED = f"The approval ended without an answer. {NOTHING_CHANGED}"


class ApprovalDesk(Protocol):
    async def pause(self, approval_request_id: str, *, expires_in_hours: int, timeout: float, execution_id: str) -> ApprovalResult: ...

    async def wait_for_resume(self, approval_request_id: str, *, execution_id: str, timeout: float) -> ApprovalResult: ...

    async def approval_status(self, execution_id: str) -> str: ...

    async def release(self, approval_request_id: str) -> None: ...

    def shutting_down(self) -> bool: ...


class AgentDesk:
    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def pause(self, approval_request_id: str, *, expires_in_hours: int, timeout: float, execution_id: str) -> ApprovalResult:
        return await self.agent.pause(approval_request_id, expires_in_hours=expires_in_hours, timeout=timeout, execution_id=execution_id)

    async def wait_for_resume(self, approval_request_id: str, *, execution_id: str, timeout: float) -> ApprovalResult:
        answer = await self.agent.wait_for_resume(approval_request_id, execution_id=execution_id, timeout=timeout)
        if answer.decision == EXPIRED:
            await self.agent._pause_manager.resolve(approval_request_id, answer)
        return answer

    async def approval_status(self, execution_id: str) -> str:
        return (await self.agent.client.get_approval_status(execution_id)).status

    async def release(self, approval_request_id: str) -> None:
        stopped = ApprovalResult(decision=STOPPED, approval_request_id=approval_request_id)
        await self.agent._pause_manager.resolve(approval_request_id, stopped)

    def shutting_down(self) -> bool:
        return bool(getattr(self.agent, "_shutdown_requested", False) or getattr(self.agent, "_shutdown_cancelling", False))


class ClientMemory(Protocol):
    async def get(self, key: str, default: Any = None) -> Any: ...

    async def set(self, key: str, data: Any) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def list_keys(self) -> list[str]: ...


def pending_key(approval_id: str) -> str:
    return f"{PENDING_PREFIX}{approval_id}"


@dataclass(frozen=True)
class PendingWrite:
    job_id: str
    run_id: str
    workflow_id: str
    client_id: str
    approval_id: str
    notes_written: int
    asked_at: float
    write: PreparedWrite
    procedure: ProcedureDraft | None = None
    replay_version: int | None = None
    ready_in_ms: int | None = None

    def to_json(self) -> dict[str, Any]:
        learned = {} if self.procedure is None else {"procedure": self.procedure.to_json()}
        replayed = {} if self.replay_version is None else {"replayVersion": self.replay_version}
        ready = {} if self.ready_in_ms is None else {"readyInMs": self.ready_in_ms}
        return {
            "version": RECORD_VERSION,
            "jobId": self.job_id,
            "runId": self.run_id,
            "workflowId": self.workflow_id,
            "clientId": self.client_id,
            "approvalId": self.approval_id,
            "notesWritten": self.notes_written,
            "askedAt": self.asked_at,
            "write": self.write.to_json(),
            **learned,
            **replayed,
            **ready,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "PendingWrite":
        if data.get("version") != RECORD_VERSION:
            raise ValueError("A waiting job record must be version 1")
        return cls(
            job_id=str(data["jobId"]),
            run_id=str(data["runId"]),
            workflow_id=str(data["workflowId"]),
            client_id=str(data["clientId"]),
            approval_id=str(data["approvalId"]),
            notes_written=int(data["notesWritten"]),
            asked_at=float(data["askedAt"]),
            write=PreparedWrite.from_json(data["write"]),
            procedure=ProcedureDraft.from_json(data["procedure"]) if "procedure" in data else None,
            replay_version=int(data["replayVersion"]) if "replayVersion" in data else None,
            ready_in_ms=int(data["readyInMs"]) if "readyInMs" in data else None,
        )


@dataclass(frozen=True)
class UndoRow:
    sku: str
    name: str
    before: int
    after: int


@dataclass(frozen=True)
class UndoRecord:
    job_id: str
    saved_at: str
    sign_in: tuple[Locator, Locator, Locator]
    save_button: Locator
    rows: tuple[UndoRow, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "version": RECORD_VERSION,
            "jobId": self.job_id,
            "savedAt": self.saved_at,
            "signIn": [locator.to_json() for locator in self.sign_in],
            "saveButton": self.save_button.to_json(),
            "rows": [{"sku": row.sku, "name": row.name, "before": row.before, "after": row.after} for row in self.rows],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "UndoRecord":
        if data.get("version") != RECORD_VERSION:
            raise ValueError("An undo record must be version 1")
        user, password, submit = (Locator.from_json(locator) for locator in data["signIn"])
        return cls(
            job_id=str(data["jobId"]),
            saved_at=str(data["savedAt"]),
            sign_in=(user, password, submit),
            save_button=Locator.from_json(data["saveButton"]),
            rows=tuple(UndoRow(str(row["sku"]), str(row["name"]), int(row["before"]), int(row["after"])) for row in data["rows"]),
        )


class WriteStopped(Exception):
    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__(outcome.get("reason", "stopped"))
        self.outcome = outcome


async def wait_for_answer(desk: ApprovalDesk, approval_id: str, execution_id: str, *, resumed: bool = False) -> ApprovalResult:
    if resumed:
        answer = ApprovalResult(decision=EXPIRED, execution_id=execution_id, approval_request_id=approval_id)
    else:
        answer = await desk.pause(approval_id, expires_in_hours=APPROVAL_EXPIRES_IN_HOURS, timeout=ANSWER_POLL_SECONDS, execution_id=execution_id)
    while answer.decision == EXPIRED:
        try:
            held = await desk.approval_status(execution_id)
        except AgentFieldClientError:
            held = PENDING
        if held != PENDING:
            return ApprovalResult(decision=held, execution_id=execution_id, approval_request_id=approval_id)
        answer = await desk.wait_for_resume(approval_id, execution_id=execution_id, timeout=ANSWER_POLL_SECONDS)
    return answer


def mentions(text: str | None, phrase: str) -> bool:
    if not text or not phrase.strip():
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase.strip())}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def field_for(elements: Iterable[Element], row: StockRow, form: Any) -> Element | None:
    fillable = [element for element in elements if element.role in FILLABLE_ROLES and (not isinstance(form, str) or element.form == form)]
    for matches in (
        [element for element in fillable if mentions(element.name, row.sku) or mentions(element.field_name, row.sku)],
        [element for element in fillable if mentions(element.name, row.name)],
    ):
        if len(matches) == 1:
            found = matches[0]
            return found if found.value is None or found.value.strip() == str(row.available) else None
    return None


def mismatch_message(mismatches: Iterable[Mismatch], written: set[str], *, action: str, closing: str) -> str:
    listed = list(mismatches)
    wrong = sum(1 for mismatch in listed if mismatch.sku in written)
    others = len(listed) - wrong
    if wrong and others:
        return f"{wrong} of {len(written)} {action} don't match after saving, and {others} others changed too. {closing}"
    if wrong:
        return f"{wrong} of {len(written)} {action} don't match after saving. {closing}"
    return f"{counted(others, 'product')} I didn't change {'is' if others == 1 else 'are'} different after saving. {closing}"


class StoreSession:
    def __init__(self, notes: EventNotes, boundary: ClientBoundary, step_id: str, *, open_page: PageOpener | None) -> None:
        self.notes = notes
        self.boundary = boundary
        self.store = boundary.store
        self.step_id = step_id
        self._open_page = open_page or (lambda: boundary.job_computer().page())
        self.page: Page | None = None
        self.clicked_save = False

    async def call(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(method, *args, **kwargs)

    async def sign_in(self, sign_in: tuple[Locator, Locator, Locator]) -> None:
        self.page = await asyncio.to_thread(self._open_page)
        await self.call(self.page.open, self.store.sign_in_url)
        user, password, submit = sign_in
        username, password_name = self.store.logins
        await self.call(self.page.fill, user, placeholder(username))
        await self.call(self.page.fill, password, placeholder(password_name))
        try:
            landed = await self.call(self.page.click, submit, wait_for=SIGNED_IN_SELECTOR, snapshot=True)
        except PageError as error:
            if error.code == "wait-failed":
                raise SignInFailed(self.store) from None
            raise
        self.seen(landed.snapshot)

    async def rows(self) -> tuple[StockRow, ...]:
        return stock_rows(await self.call(self.page.read_table))

    async def fields(self, rows: Iterable[StockRow], form: Any) -> dict[str, Element] | None:
        tree = await self.call(self.page.tree)
        found: dict[str, Element] = {}
        for row in rows:
            element = field_for(tree.elements, row, form)
            if element is None:
                return None
            found[row.sku] = element
        return found

    async def fill_and_save(self, values: Mapping[str, int], fields: Mapping[str, Element], save_button: Locator) -> str | None:
        for sku, value in values.items():
            field = fields[sku]
            await self.call(self.page.fill, Locator(field.role, field.name, field.form), str(value))
        self.clicked_save = True
        try:
            saved = await self.call(self.page.click, save_button, wait_for=SAVE_STATUS_SELECTOR, snapshot=True)
        except PageError as error:
            if error.code != "wait-failed":
                raise
            return None
        self.seen(saved.snapshot)
        status = await self.call(self.page.wait, SAVE_STATUS_SELECTOR)
        return status if SAVED_WORD.search(status) else None

    def seen(self, snapshot: Snapshot | None) -> None:
        if snapshot is not None:
            self.notes.page_seen(self.step_id, self.store.host, snapshot.html)

    async def close(self) -> None:
        if self.page is None:
            return
        try:
            await self.call(self.page.close)
        except (PageError, ComputerError):
            return


def failed(notes: EventNotes, step_id: str | None, message: str, problem: str, reason: str = "error") -> dict[str, Any]:
    body: dict[str, Any] = {"reason": reason, "message": message}
    outcome: dict[str, Any] = {"status": "failed", "reason": reason}
    if step_id is not None:
        body["stepId"] = outcome["stepId"] = step_id
    notes.emit("job.failed", **body)
    return {**outcome, "problem": problem}


def page_failure(session: StoreSession, error: Exception) -> tuple[str, str]:
    after = " You can undo this sync." if session.clicked_save else f" {NOTHING_CHANGED}"
    if isinstance(error, SignInFailed):
        return f"I couldn't sign in to {spoken(error.site)} with the saved login, so I stopped.{after}", "wait-failed"
    if isinstance(error, MissingExternalKey):
        return f"I couldn't open this client's computer, so I stopped.{after}", "MissingExternalKey"
    if isinstance(error, ComputerError):
        return f"This client's computer stopped answering, so I stopped.{after}", error.problem.value
    if isinstance(error, PageError) and error.code in ("placeholder-unset", "vault-unreadable"):
        return f"The {session.store.name} login isn't saved on this client's computer, so I stopped.{after}", error.code
    if isinstance(error, PageError):
        return f"{at_sentence_start(spoken(session.store))} didn't respond as expected, so I stopped.{after}", error.code
    return f"I couldn't read the stock table on {spoken(session.store)}, so I stopped.{after}", type(error).__name__


WORK_ERRORS = (ComputerError, MissingExternalKey, PageError, SignInFailed, TableError, DuplicateSku)

Keeper = Callable[[dict[str, Any], Mapping[str, Element]], Awaitable[Any]]
Writer = Callable[[EventNotes, ClientBoundary, "PendingWrite", "ClientMemory"], Awaitable[dict[str, Any]]]


async def write_approved(
    notes: EventNotes,
    boundary: ClientBoundary,
    pending: PendingWrite,
    memory: ClientMemory,
    *,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
    wall: Clock = time.time,
    keep: Keeper | None = None,
) -> dict[str, Any]:
    write = pending.write
    started = clock()
    notes.emit("step.started", stepId=SAVE_STEP, activity=f"Saving {counted(len(write.changes), 'change')}")
    session = StoreSession(notes, boundary, SAVE_STEP, open_page=open_page)
    try:
        await session.sign_in(write.sign_in)
        before = await session.rows()
        current = {row.sku: row for row in before}
        if any(change.sku not in current or current[change.sku].available != change.store for change in write.changes):
            return failed(notes, SAVE_STEP, STORE_CHANGED, "store-changed")
        fields = await session.fields((current[change.sku] for change in write.changes), write.save_button.form)
        if fields is None:
            return failed(notes, SAVE_STEP, FIELD_UNKNOWN, "field-unknown")
        record = UndoRecord(
            job_id=pending.job_id,
            saved_at=datetime.fromtimestamp(wall(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            sign_in=write.sign_in,
            save_button=write.save_button,
            rows=tuple(UndoRow(change.sku, change.name, change.store, change.supplier) for change in write.changes),
        )
        try:
            await memory.set(UNDO_KEY, record.to_json())
        except MemoryAccessError:
            return failed(notes, SAVE_STEP, NO_UNDO_COPY, "undo-copy-unavailable")
        status = await session.fill_and_save({change.sku: change.supplier for change in write.changes}, fields, write.save_button)
        notes.emit("write.done", stepId=SAVE_STEP, written=len(write.changes))
        reread = await session.rows()
    except asyncio.CancelledError:
        notes.emit("job.failed", reason="stopped", message=STOPPED_AFTER_SAVING if session.clicked_save else STOPPED_BEFORE_SAVING, stepId=SAVE_STEP)
        raise
    except WORK_ERRORS as error:
        message, problem = page_failure(session, error)
        return failed(notes, SAVE_STEP, message, problem)
    finally:
        await session.close()
    outcome = checked(notes, SAVE_STEP, started, clock, before, write.changes, reread, status, closing=KEEP_STEPS_OFF, action="saved products", summary="Saved and checked every row")
    if keep is not None:
        await keep(outcome, fields)
    return outcome


def checked(
    notes: EventNotes,
    step_id: str,
    started: float,
    clock: Clock,
    before: Iterable[StockRow],
    changes: Iterable[StockChange],
    reread: Iterable[StockRow],
    status: str | None,
    *,
    closing: str,
    action: str,
    summary: str,
) -> dict[str, Any]:
    changes = tuple(changes)
    written = {change.sku for change in changes}
    verification = verify_rows(expected_after_write(before, changes), reread)
    wrong = sum(1 for mismatch in verification.mismatches if mismatch.sku in written)
    matched = len(written) - wrong
    notes.emit("verify.done", stepId=step_id, checked=len(written), matched=matched)
    result: dict[str, Any] = {"written": len(written), "checked": len(written), "matched": matched}
    if status is not None:
        result["saveStatus"] = status
    keeps_steps = {"maySaveProcedure": verification.may_save_procedure} if step_id == SAVE_STEP else {}
    if not verification.ok:
        message = mismatch_message(verification.mismatches, written, action=action, closing=closing)
        notes.emit("job.failed", reason="verify-mismatch", message=message, stepId=step_id)
        return {"status": "failed", "reason": "verify-mismatch", **result, **keeps_steps}
    notes.emit("step.done", stepId=step_id, summary=summary, detail=counted(len(written), "product"), tookMs=elapsed_ms(started, clock()))
    return {"status": "done", **result, **keeps_steps}


async def forget(memory: ClientMemory, key: str) -> None:
    try:
        await memory.delete(key)
    except MemoryAccessError:
        return


async def see_approval_through(
    desk: ApprovalDesk,
    notes: EventNotes,
    boundary: ClientBoundary,
    pending: PendingWrite,
    memory: ClientMemory,
    *,
    resumed: bool = False,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
    wall: Clock = time.time,
    writer: Writer | None = None,
) -> dict[str, Any]:
    key = pending_key(pending.approval_id)
    if not resumed:
        try:
            await memory.set(key, pending.to_json())
        except MemoryAccessError:
            return failed(notes, ASK_STEP, COULDNT_HOLD, "pending-unsaved")
    try:
        answer = await wait_for_answer(desk, pending.approval_id, pending.job_id, resumed=resumed)
    except asyncio.CancelledError:
        if desk.shutting_down():
            return {"status": "waiting", "approvalId": pending.approval_id, "resumable": True}
        notes.emit("job.failed", reason="stopped", message=STOPPED_BEFORE_SAVING, stepId=ASK_STEP)
        await desk.release(pending.approval_id)
        await forget(memory, key)
        raise
    except AgentFieldClientError:
        await forget(memory, key)
        return failed(notes, ASK_STEP, COULDNT_HOLD, "pause-unavailable")
    try:
        if answer.decision != APPROVED:
            notes.emit("job.failed", reason="stopped", message=NOT_APPROVED.get(answer.decision, NOT_ANSWERED), stepId=ASK_STEP)
            return {"status": "stopped", "decision": answer.decision}
        waited_ms = max(0, round((wall() - pending.asked_at) * 1000))
        body: dict[str, Any] = {"approvalId": pending.approval_id, "decision": APPROVED, "by": APPROVER}
        if answer.feedback and answer.feedback.strip():
            body["feedback"] = answer.feedback.strip()
        notes.emit("approval.answered", **body)
        notes.emit("step.decided", stepId=ASK_STEP, rung="human", tookMs=waited_ms)
        notes.emit("step.done", stepId=ASK_STEP, summary=f"You approved {counted(len(pending.write.changes), 'change')}", tookMs=waited_ms)
        if writer is not None:
            return await writer(notes, boundary, pending, memory)
        return await write_approved(notes, boundary, pending, memory, open_page=open_page, clock=clock, wall=wall)
    finally:
        await forget(memory, key)


async def undo_last_sync(
    notes: EventNotes,
    boundary: ClientBoundary,
    memory: ClientMemory,
    *,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
) -> dict[str, Any]:
    store = spoken(boundary.store)
    notes.emit(
        "job.created",
        clientName=boundary.client.name,
        title=UNDO_TITLE,
        request=UNDO_REQUEST,
        run={"kind": "undo"},
        plan=[
            {"stepId": SIGN_IN_STEP, "title": f"Sign in to {store}"},
            {"stepId": UNDO_STEP, "title": "Put back the stock from before the sync"},
        ],
    )
    try:
        stored = await memory.get(UNDO_KEY)
    except MemoryAccessError:
        return failed(notes, None, UNDO_UNREADABLE, "undo-unreadable")
    if stored is None:
        return failed(notes, None, NOTHING_TO_UNDO, "nothing-to-undo")
    try:
        record = UndoRecord.from_json(stored)
    except (KeyError, TypeError, ValueError):
        return failed(notes, None, UNDO_UNREADABLE, "undo-unreadable")
    session = StoreSession(notes, boundary, SIGN_IN_STEP, open_page=open_page)
    step_id = SIGN_IN_STEP
    try:
        started = clock()
        notes.emit("step.started", stepId=SIGN_IN_STEP, activity=f"Signing in to {store}")
        await session.sign_in(record.sign_in)
        notes.emit("step.done", stepId=SIGN_IN_STEP, summary=f"Signed in to {store}", tookMs=elapsed_ms(started, clock()))
        step_id = session.step_id = UNDO_STEP
        started = clock()
        notes.emit("step.started", stepId=UNDO_STEP, activity=f"Putting back {counted(len(record.rows), 'product')}")
        before = await session.rows()
        current = {row.sku: row for row in before}
        if any(row.sku not in current or current[row.sku].available != row.after for row in record.rows):
            return failed(notes, UNDO_STEP, CHANGED_SINCE_SYNC, "changed-since-sync")
        fields = await session.fields((current[row.sku] for row in record.rows), record.save_button.form)
        if fields is None:
            return failed(notes, UNDO_STEP, FIELD_UNKNOWN, "field-unknown")
        status = await session.fill_and_save({row.sku: row.before for row in record.rows}, fields, record.save_button)
        notes.emit("write.done", stepId=UNDO_STEP, written=len(record.rows))
        reread = await session.rows()
    except asyncio.CancelledError:
        notes.emit("job.failed", reason="stopped", message=STOPPED_AFTER_SAVING if session.clicked_save else STOPPED_BEFORE_SAVING, stepId=step_id)
        raise
    except WORK_ERRORS as error:
        message, problem = page_failure(session, error)
        return failed(notes, step_id, message, problem)
    finally:
        await session.close()
    putting_back = tuple(StockChange(row.sku, row.name, row.after, row.before) for row in record.rows)
    outcome = checked(notes, UNDO_STEP, started, clock, before, putting_back, reread, status, closing="I stopped.", action="products put back", summary="Put back and checked every row")
    outcome.pop("saveStatus", None)
    if outcome["status"] == "done":
        await forget(memory, UNDO_KEY)
    return outcome
