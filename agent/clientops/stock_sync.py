import asyncio
import re
import secrets
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from .boundary import ClientBoundary, Site, known_clients
from .computers import ComputerError
from .diff import DuplicateSku, StockChange, StockDiff, StockRow, diff_stock
from .externals import MissingExternalKey
from .page import Locator, Page, PageError, Snapshot, TableRead, Tree, placeholder
from .plan import AiCall, Absent, Choice, ClaudeCall, ElementChooser, Goal, HandOff, HandOffReason, elapsed_ms
from .procedure import NoStableSelector, ProcedureDraft, ReadSteps, SignInSteps, target_for
from .records import EventNotes
from .settings import SettingsError, use_claude

Clock = Callable[[], float]

JOB_TITLE = "Supplier stock sync"
SIGNED_IN_SELECTOR = "table"
SUPPLIER_PLACE = "supplier portal"
STORE_PLACE = "store admin"
SKU_HEADERS = frozenset({"sku"})
NAME_HEADERS = frozenset({"product", "name", "item"})
AVAILABLE_HEADERS = frozenset({"available", "stock", "quantity", "qty", "on hand"})
NOTHING_CHANGED = "Nothing was changed."
NOTHING_TO_SAVE = "Nothing to save"
LOGIN_PROBLEMS = frozenset({"placeholder-unset", "vault-unreadable"})
COMMON_PAGE_SIZES = frozenset({10, 20, 25, 30, 50, 100})
SHOWING_COUNT = re.compile(r"(\d[\d,]*)\s*(?:[–—-]|to)\s*(\d[\d,]*)\s+of\s+(\d[\d,]*)", re.IGNORECASE)
PAGE_COUNT = re.compile(r"\bpage\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)

NEXT_PAGE = Goal(
    "Read the stock table across all its pages: the link or button that goes to the next page of the table.",
    "click",
    optional=True,
)
NEXT_PAGE_NEEDED = replace(NEXT_PAGE, optional=False)
SAVE_BUTTON = Goal("Save the stock form: the button that saves the changes in this form for this store only.", "click")


def sign_in_goals(place: str) -> tuple[Goal, Goal, Goal]:
    return (
        Goal(f"Sign in to the {place}: the field for the account ID or username.", "fill"),
        Goal(f"Sign in to the {place}: the password field.", "fill"),
        Goal(f"Sign in to the {place}: the button that sends the sign-in form.", "click"),
    )


def whole_number(text: str) -> int:
    return int(text.replace(",", ""))


def signs_of_more_pages(text: str, rows_on_page: int) -> bool:
    if rows_on_page in COMMON_PAGE_SIZES:
        return True
    if any(whole_number(last) < whole_number(total) for _, last, total in SHOWING_COUNT.findall(text)):
        return True
    return any(int(current) < int(total) for current, total in PAGE_COUNT.findall(text))


def compared_detail(diff: StockDiff) -> str:
    differing = len(diff.changes)
    if differing:
        return f"{counted(differing, 'product')} {'differs' if differing == 1 else 'differ'}"
    parts = ["No products differ"]
    if diff.missing_at_supplier:
        parts.append(f"{len(diff.missing_at_supplier)} only in the store")
    if diff.missing_in_store:
        parts.append(f"{len(diff.missing_in_store)} only at the supplier")
    return " · ".join(parts)


def spoken(site: Site) -> str:
    return site.name if site.name[:1].isupper() else f"the {site.name}"


def at_sentence_start(text: str) -> str:
    return text[:1].upper() + text[1:]


def counted(number: int, thing: str) -> str:
    return f"{number} {thing}" if number == 1 else f"{number} {thing}s"


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    title: str
    activity: str
    summary: str
    detail: str | None = None
    site: Site | None = None


def stock_sync_plan(supplier: Site, store: Site) -> tuple[PlanStep, ...]:
    return (
        PlanStep("sign-in", f"Sign in to {spoken(supplier)}", f"Signing in to {spoken(supplier)}", f"Signed in to {spoken(supplier)}", site=supplier),
        PlanStep("read", "Read today's stock", "Reading today's stock", "Read today's stock", site=supplier),
        PlanStep("compare", f"Compare with {spoken(store)}", f"Comparing with {spoken(store)}", f"Compared with {spoken(store)}", "Matching by SKU", store),
        PlanStep("check-save", "Check the save button", "Checking the save button", "Save button found", site=store),
        PlanStep("ask", "Ask you before saving", "Waiting for your OK", "You approved the changes"),
        PlanStep("save", "Save and check every row", "Saving the changes", "Saved and checked every row", site=store),
    )


@dataclass(frozen=True)
class PreparedWrite:
    sign_in: tuple[Locator, Locator, Locator]
    save_button: Locator
    changes: tuple[StockChange, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "signIn": [locator.to_json() for locator in self.sign_in],
            "saveButton": self.save_button.to_json(),
            "changes": [
                {"sku": change.sku, "name": change.name, "store": change.store, "supplier": change.supplier}
                for change in self.changes
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PreparedWrite":
        user, password, submit = (Locator.from_json(locator) for locator in data["signIn"])
        return cls(
            (user, password, submit),
            Locator.from_json(data["saveButton"]),
            tuple(StockChange(change["sku"], change["name"], int(change["store"]), int(change["supplier"])) for change in data["changes"]),
        )


class TableError(ValueError):
    pass


class ClaudeNotSetUp(RuntimeError):
    pass


class SignInFailed(RuntimeError):
    def __init__(self, site: Site) -> None:
        super().__init__(f"{site.name} did not accept the saved login")
        self.site = site


class JobStopped(Exception):
    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__(outcome.get("reason", "stopped"))
        self.outcome = outcome


def column_of(columns: Sequence[str], names: frozenset[str], what: str) -> int:
    for index, header in enumerate(columns):
        if header.strip().lower() in names:
            return index
    raise TableError(f"The table has no {what} column")


def stock_rows(table: TableRead) -> tuple[StockRow, ...]:
    sku = column_of(table.columns, SKU_HEADERS, "SKU")
    name = column_of(table.columns, NAME_HEADERS, "product")
    available = column_of(table.columns, AVAILABLE_HEADERS, "available stock")
    rows: list[StockRow] = []
    for cells in table.rows:
        try:
            rows.append(StockRow(cells[sku].strip(), cells[name].strip(), int(cells[available].strip())))
        except (IndexError, ValueError):
            raise TableError("A row of the table has no whole number of available stock") from None
    return tuple(rows)


async def hand_to_person(notes: EventNotes, step_id: str, hand_off: HandOff, tree: Tree) -> Choice | Absent | None:
    notes.emit("job.failed", reason="error", message=hand_off.message, stepId=step_id)
    return None


class FirstRun:
    def __init__(
        self,
        notes: EventNotes,
        boundary: ClientBoundary,
        ai: AiCall | None,
        *,
        request: str,
        job_id: str | None,
        clock: Clock,
    ) -> None:
        self.notes = notes
        self.boundary = boundary
        self.ai = ai
        self.request = request
        self.job_id = job_id
        self.clock = clock
        self.supplier = boundary.supplier
        self.store = boundary.store
        self.plan = {step.step_id: step for step in stock_sync_plan(self.supplier, self.store)}
        self.known: set[str] = {text for client in known_clients() for text in (client.name, client.short_name, client.nickname)}
        self.known.add(self.supplier.name)
        self.page: Page | None = None
        self.chooser: ElementChooser | None = None
        self.active: PlanStep | None = None
        self.usage: dict[str, list[dict[str, Any]]] = {}
        self.choices: dict[str, list[Choice | Absent]] = {}
        self.supplier_rows: tuple[StockRow, ...] = ()
        self.store_rows: tuple[StockRow, ...] = ()
        self.diff: StockDiff | None = None
        self.save_button: Locator | None = None
        self.started_at = 0.0
        self.landing: dict[str, str] = {}
        self.next_page: Choice | Absent | None = None

    @property
    def claude_calls(self) -> int:
        return sum(len(calls) for calls in self.usage.values())

    async def run(self) -> dict[str, Any]:
        self.started_at = self.clock()
        self.notes.emit(
            "job.created",
            clientName=self.boundary.client.name,
            title=JOB_TITLE,
            request=self.request,
            run={"kind": "learn"},
            plan=[{"stepId": step.step_id, "title": step.title} for step in self.plan.values()],
        )
        try:
            claude_on = use_claude(self.boundary.client.id)
            if claude_on and self.ai is None:
                raise ClaudeNotSetUp("Claude is on for this client but there is no way to call it")
            self.chooser = ElementChooser(self.ai, use_claude=claude_on, clock=self.clock)
            outcome = await self._steps()
        except JobStopped as stopped:
            outcome = stopped.outcome
        except (SettingsError, ClaudeNotSetUp, ComputerError, MissingExternalKey, PageError, SignInFailed, TableError, DuplicateSku) as error:
            outcome = self._failed(error)
        finally:
            await self._close()
        return {**outcome, "claudeCalls": self.claude_calls, "claudeUsage": self.usage}

    async def _steps(self) -> dict[str, Any]:
        await self._sign_in_step()
        await self._read_step()
        diff = await self._compare_step()
        if not diff.changes:
            return self._nothing_to_save()
        await self._check_save_step()
        return self._ask(diff)

    async def _sign_in_step(self) -> None:
        step = self.plan["sign-in"]
        started = self._start(step)
        opened = await asyncio.to_thread(self.boundary.job_computer)
        self.page = opened.page()
        await self._sign_in(step, started, self.supplier, SUPPLIER_PLACE)
        self._done(step, started)

    async def _read_step(self) -> None:
        step = self.plan["read"]
        started = self._start(step)
        tree = await self._on_page(self.page.tree, "full")
        this_page = await self._on_page(self.page.read_table)
        goal = NEXT_PAGE_NEEDED if signs_of_more_pages(tree.text, len(this_page.rows)) else NEXT_PAGE
        [next_page], rung, escalated = await self._decide(step, tree, (goal,))
        self._decided(step, started, rung, escalated)
        self.next_page = next_page
        if isinstance(next_page, Choice):
            table = await self._on_page(self.page.read_table, next=next_page.locator)
        else:
            table = this_page
        self._seen(step, await self._on_page(self.page.snapshot))
        self.supplier_rows = stock_rows(table)
        self._remember(self.supplier_rows)
        self._done(step, started, detail=counted(len(self.supplier_rows), "product"))

    async def _compare_step(self) -> StockDiff:
        step = self.plan["compare"]
        started = self._start(step)
        await self._sign_in(step, started, self.store, STORE_PLACE)
        self.store_rows = stock_rows(await self._on_page(self.page.read_table))
        self._remember(self.store_rows)
        self.diff = diff_stock(self.supplier_rows, self.store_rows)
        self._done(step, started, detail=compared_detail(self.diff))
        return self.diff

    async def _check_save_step(self) -> None:
        step = self.plan["check-save"]
        started = self._start(step)
        tree = await self._on_page(self.page.tree, snapshot=True)
        self._seen(step, tree.snapshot)
        [save], rung, escalated = await self._decide(step, tree, (SAVE_BUTTON,))
        self._decided(step, started, rung, escalated)
        try:
            await self._on_page(self.page.find, save.locator)
        except PageError as error:
            if error.code != "not-unique":
                raise
            save = await self._person(step, HandOff(SAVE_BUTTON, HandOffReason.NOT_UNIQUE, ()), tree)
        self.save_button = save.locator
        self.choices[step.step_id] = [save]
        self._done(step, started)

    def _ask(self, diff: StockDiff) -> dict[str, Any]:
        step = self.plan["ask"]
        self._start(step)
        approval_id = f"apr-{self.job_id or secrets.token_hex(6)}"
        changes = [{"sku": change.sku, "name": change.name, "store": change.store, "supplier": change.supplier} for change in diff.changes]
        self.notes.approval_requested(
            approval_id,
            step.step_id,
            f"Save {counted(len(changes), 'stock change')} to {spoken(self.store)}?",
            changes,
            undoable=True,
            only_in_store=len(diff.missing_at_supplier),
            only_at_supplier=len(diff.missing_in_store),
        )
        ready_in_ms = elapsed_ms(self.started_at, self.clock())
        user, password, submit = (choice.locator for choice in self.choices["compare"])
        prepared = PreparedWrite((user, password, submit), self.save_button, diff.changes)
        waiting: dict[str, Any] = {
            "status": "waiting",
            "approvalId": approval_id,
            "changes": len(changes),
            "soldOut": diff.sold_out,
            "readyInMs": ready_in_ms,
            "write": prepared.to_json(),
        }
        draft = self._draft(ready_in_ms)
        if draft is not None:
            waiting["procedure"] = draft.to_json()
        return waiting

    def _draft(self, ready_in_ms: int) -> ProcedureDraft | None:
        claude_ms = sum(call["tookMs"] for calls in self.usage.values() for call in calls)
        try:
            read = ReadSteps(
                supplier=self._sign_in_steps("supplier", "sign-in"),
                next_page=target_for(self.next_page.element) if isinstance(self.next_page, Choice) else None,
                store=self._sign_in_steps("store", "compare"),
                save_button=target_for(self.choices["check-save"][0].element),
            )
        except (NoStableSelector, KeyError, IndexError):
            return None
        return ProcedureDraft(read, max(0, ready_in_ms - claude_ms))

    def _sign_in_steps(self, site: str, step_id: str) -> SignInSteps:
        user, password, submit = (target_for(choice.element) for choice in self.choices[step_id][:3])
        return SignInSteps(site, self.landing[step_id], user, password, submit)

    def _nothing_to_save(self) -> dict[str, Any]:
        for step_id in ("check-save", "ask", "save"):
            self.notes.emit("step.done", stepId=step_id, summary=NOTHING_TO_SAVE, tookMs=0)
        return {"status": "done", "changes": 0}

    async def _sign_in(self, step: PlanStep, started: float, site: Site, place: str) -> None:
        opened = await self._on_page(self.page.open, site.sign_in_url, snapshot=True)
        self._seen(step, opened.snapshot)
        tree = await self._on_page(self.page.tree)
        (user, password, submit), rung, escalated = await self._decide(step, tree, sign_in_goals(place))
        self._decided(step, started, rung, escalated)
        username, password_name = site.logins
        await self._on_page(self.page.fill, user.element.ref, placeholder(username))
        await self._on_page(self.page.fill, password.element.ref, placeholder(password_name))
        try:
            landed = await self._on_page(self.page.click, submit.element.ref, wait_for=SIGNED_IN_SELECTOR, snapshot=True)
        except PageError as error:
            if error.code == "wait-failed":
                raise SignInFailed(site) from None
            raise
        self._seen(step, landed.snapshot)
        self.landing[step.step_id] = urlsplit(landed.page.url).path or "/"
        self.choices.setdefault(step.step_id, []).extend((user, password, submit))

    async def _decide(self, step: PlanStep, tree: Tree, goals: Iterable[Goal]) -> tuple[list[Choice | Absent], str, list[str]]:
        self._read(step, tree)
        choices: list[Choice | Absent] = []
        by_person = False
        claude_tried = False
        for goal in goals:
            result = await self.chooser.choose(tree, goal, self.known)
            self._count(step, result.calls)
            claude_tried = claude_tried or bool(result.calls)
            if isinstance(result, HandOff):
                result = await self._person(step, result, tree)
                by_person = True
            choices.append(result)
        if not by_person:
            return choices, "claude", []
        return choices, "human", ["claude"] if claude_tried else []

    async def _person(self, step: PlanStep, hand_off: HandOff, tree: Tree) -> Choice | Absent:
        choice = await hand_to_person(self.notes, step.step_id, hand_off, tree)
        if choice is None or (isinstance(choice, Absent) and not hand_off.goal.optional):
            raise JobStopped({"status": "failed", "reason": "needs_person", "stepId": step.step_id, "handOff": hand_off.reason.value})
        return choice

    def _count(self, step: PlanStep, calls: tuple[ClaudeCall, ...]) -> None:
        if calls:
            self.usage.setdefault(step.step_id, []).extend(call.to_json() for call in calls)

    def _remember(self, rows: Iterable[StockRow]) -> None:
        for row in rows:
            self.known.update((row.sku, row.name))

    def _start(self, step: PlanStep) -> float:
        self.active = step
        body: dict[str, Any] = {"stepId": step.step_id, "activity": step.activity}
        if step.detail:
            body["detail"] = step.detail
        self.notes.emit("step.started", **body)
        return self.clock()

    def _decided(self, step: PlanStep, started: float, rung: str, escalated_from: list[str]) -> None:
        body: dict[str, Any] = {"stepId": step.step_id, "rung": rung, "tookMs": elapsed_ms(started, self.clock())}
        if escalated_from:
            body["escalatedFrom"] = escalated_from
        self.notes.emit("step.decided", **body)

    def _done(self, step: PlanStep, started: float, *, detail: str | None = None) -> None:
        body: dict[str, Any] = {"stepId": step.step_id, "summary": step.summary}
        if detail:
            body["detail"] = detail
        self.notes.emit("step.done", **body, tookMs=elapsed_ms(started, self.clock()))
        self.active = None

    def _seen(self, step: PlanStep, snapshot: Snapshot | None) -> None:
        if snapshot is not None:
            self.notes.page_seen(step.step_id, self._shown_host(snapshot.host), snapshot.html)

    def _read(self, step: PlanStep, tree: Tree) -> None:
        host = self._shown_host(urlsplit(tree.page.url).netloc)
        self.notes.page_read(step.step_id, host, tree.text, [element.name for element in tree.elements])

    def _shown_host(self, host: str) -> str:
        for site in self.boundary.sites:
            if urlsplit(site.url).netloc == host:
                return site.host
        return host

    async def _on_page(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(method, *args, **kwargs)

    def _failed(self, error: Exception) -> dict[str, Any]:
        step = self.active
        message, problem = self._failure(error, step)
        body: dict[str, Any] = {"reason": "error", "message": message}
        outcome: dict[str, Any] = {"status": "failed", "reason": "error", "problem": problem}
        if step is not None:
            body["stepId"] = outcome["stepId"] = step.step_id
        self.notes.emit("job.failed", **body)
        return outcome

    def _failure(self, error: Exception, step: PlanStep | None) -> tuple[str, str]:
        site = step.site if step and step.site else self.supplier
        if isinstance(error, SettingsError):
            return f"This client's settings couldn't be read, so I stopped. {NOTHING_CHANGED}", "SettingsError"
        if isinstance(error, ClaudeNotSetUp):
            return f"Claude isn't set up on this server, so I stopped. {NOTHING_CHANGED}", "MissingExternalKey"
        if isinstance(error, MissingExternalKey):
            return f"I couldn't open this client's computer, so I stopped. {NOTHING_CHANGED}", "MissingExternalKey"
        if isinstance(error, ComputerError):
            if self.page is None:
                return f"I couldn't open this client's computer, so I stopped. {NOTHING_CHANGED}", error.problem.value
            return f"This client's computer stopped answering, so I stopped. {NOTHING_CHANGED}", error.problem.value
        if isinstance(error, SignInFailed):
            return f"I couldn't sign in to {spoken(error.site)} with the saved login, so I stopped. {NOTHING_CHANGED}", "wait-failed"
        if isinstance(error, PageError) and error.code in LOGIN_PROBLEMS:
            return f"The {site.name} login isn't saved on this client's computer, so I stopped. {NOTHING_CHANGED}", error.code
        if isinstance(error, PageError):
            return f"{at_sentence_start(spoken(site))} didn't respond as expected, so I stopped. {NOTHING_CHANGED}", error.code
        if isinstance(error, DuplicateSku) and error.side == "supplier list":
            site = self.supplier
        return f"I couldn't read the stock table on {spoken(site)}, so I stopped. {NOTHING_CHANGED}", type(error).__name__

    async def _close(self) -> None:
        if self.page is None:
            return
        try:
            await self._on_page(self.page.close)
        except (PageError, ComputerError):
            return


async def run_first_run(
    notes: EventNotes,
    boundary: ClientBoundary,
    ai: AiCall | None,
    *,
    request: str,
    job_id: str | None = None,
    clock: Clock = time.monotonic,
) -> dict[str, Any]:
    return await FirstRun(notes, boundary, ai, request=request, job_id=job_id, clock=clock).run()
