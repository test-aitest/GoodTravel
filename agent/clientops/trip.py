import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date as calendar_date
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from agentfield.exceptions import AgentFieldClientError

from .approval import APPROVED, APPROVER, EXPIRED, ApprovalDesk, ClientMemory, failed, wait_for_answer
from .boundary import ClientBoundary, Site
from .claude_call import CLAUDE_MODEL, SEAT_REASONS, ModelNotAllowed, seat_suggestion_call
from .computers import ComputerError
from .cost import ClaudeBill, charge_claude, claude_bill, computer_in_use, job_meter
from .externals import MissingExternalKey
from .compile_script import compile_trip_search_script
from .page import Element, Locator, Page, PageError, ScriptResult, Tree, placeholder
from .procedure import NoStableSelector, target_for
from .trip_procedure import Decision, TripProcedure, announce_trip_procedure, keep_trip_procedure, saved_trip_procedure, seat_shape
from .plan import FINISHED, AiCall, ClaudeCall, budget_hand_off, elapsed_ms, finish_reason
from .records import EventNotes
from .settings import SettingsError, use_claude
from .stock_sync import LOGIN_PROBLEMS, PlanStep, SignInFailed, counted

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
PageOpener = Callable[[], Page]

SEARCH_TITLE = "Trip search"
BOOK_TITLE = "Trip booking"
FROM_CITY = "Tokyo"
NOTHING_BOOKED = "Nothing was booked."
HELD_NOT_BOOKED = "The seat is held but not booked."
STOPPED_BEFORE_BOOKING = f"Stopped before booking. {NOTHING_BOOKED}"
STOPPED_WHILE_HELD = f"Stopped before booking. {HELD_NOT_BOOKED}"
COULDNT_ASK = f"I couldn't hold the job for your OK, so I stopped. {HELD_NOT_BOOKED}"
TRIP_NOT_APPROVED = {
    "rejected": f"The booking was turned down. {HELD_NOT_BOOKED}",
    "request_changes": f"Someone asked for a different booking. {HELD_NOT_BOOKED}",
    EXPIRED: f"No one answered in time. {HELD_NOT_BOOKED}",
}
TRIP_NOT_ANSWERED = f"The approval ended without an answer. {HELD_NOT_BOOKED}"
MAYBE_BOOKED = "I pressed Confirm booking, so check the booking on the travel site before trying again."

CHOOSE_SEATS = "Choose seats for "
SEATS_ON = "Seats on "
TRIPS_TO = "Trips to "
REVIEW_HEADING = "Review your booking"
BOOKED_HEADING = "Booking confirmed"
SIGN_IN = (Locator("textbox", "Username"), Locator("textbox", "Password"), Locator("button", "Sign in"))
FULL_NAME = Locator("textbox", "Full name")
EMAIL = Locator("textbox", "Email")
HOLD_SEAT = Locator("button", "Hold seat")
CONFIRM_BOOKING = Locator("button", "Confirm booking")
TRAVELER_NAME = "LP_TRAVELER_NAME"
TRAVELER_EMAIL = "LP_TRAVELER_EMAIL"

LOOKS = 20
LOOK_SECONDS = 0.25
CLAUDE_WIRE_MODEL = CLAUDE_MODEL.removeprefix("anthropic/")
SUGGESTED_BY_CLAUDE = "claude"
SUGGESTED_BY_RULE = "rule"
LEFT_SIDE = "ABC"
RIGHT_SIDE = "DEF"
REPLY_KEYS = frozenset({"seat", "reason"})

TREE_LINE = re.compile(r"^\s*\d+\s+(?:\[i\]\s+)?(?:([A-Za-z]+)\s+)?'(.*)'\s*$")
CURRENCY = r"(JPY|USD|EUR|SGD|KRW)"
OPTION_LINE = re.compile(rf"\b(rail|flight)\s*·\s*(\d{{2}}:\d{{2}})\s*→\s*(\d{{2}}:\d{{2}})\s*·\s*{CURRENCY}\s*(\d[\d,]*)", re.IGNORECASE)
SEAT_NAME = re.compile(r"Seat (\d{1,2})([A-F]), (window|middle|aisle)(, exit row)?, (available|taken)")
TIMES = re.compile(r"\b(\d{2}:\d{2})\s*→\s*(\d{2}:\d{2})\b")
PRICE = re.compile(rf"\b{CURRENCY}\s*(\d[\d,]*)")
REFERENCE = re.compile(r"\bReference\s+([A-Z]{6})\b")
OPTION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
SEAT_ID = re.compile(r"(\d{1,2})([A-F])")
PLACE = re.compile(r"[A-Z][A-Za-z]{1,39}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
HOLD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

SEAT_SYSTEM_PROMPT = (
    "You suggest one seat for a business traveller on a train or a plane. "
    "You get the kind of ride, how many rows it has and its seat list. "
    "Each seat has an id, a row, a letter, its position (window, middle or aisle), whether it is in an exit row and whether it is available. "
    "Pick one available seat from the list and give one reason: "
    "window for a window seat, aisle for an aisle seat, quiet-front for a seat in the front third of the rows, "
    "exit-legroom for an exit row seat, together for a seat whose neighbour on the same side of the aisle is also available. "
    "The reason must be true of the seat you pick. Answer only with the seat id and the reason."
)


class TripPageUnexpected(RuntimeError):
    def __init__(self, problem: str, message: str) -> None:
        super().__init__(message)
        self.problem = problem


class BadTripInput(ValueError):
    pass


@dataclass(frozen=True)
class Seat:
    row: int
    letter: str
    position: str
    exit_row: bool
    available: bool
    name: str

    @property
    def id(self) -> str:
        return f"{self.row}{self.letter}"

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "position": self.position, "exitRow": self.exit_row, "available": self.available}

    def for_claude(self) -> dict[str, Any]:
        return {"id": self.id, "row": self.row, "letter": self.letter, "position": self.position, "exitRow": self.exit_row, "available": self.available}


@dataclass(frozen=True)
class Listed:
    name: str
    kind: str
    depart: str
    arrive: str
    currency: str
    price: int


@dataclass(frozen=True)
class TripOption:
    id: str
    listed: Listed
    seats: tuple[Seat, ...]

    def to_json(self, suggestion: "Suggestion | None") -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.listed.name,
            "kind": self.listed.kind,
            "depart": self.listed.depart,
            "arrive": self.listed.arrive,
            "currency": self.listed.currency,
            "price": self.listed.price,
            "seats": [seat.to_json() for seat in self.seats],
            "suggestion": None if suggestion is None else suggestion.to_json(),
        }


@dataclass(frozen=True)
class Suggestion:
    seat: str
    reason: str
    by: str

    def to_json(self) -> dict[str, str]:
        return {"seat": self.seat, "reason": self.reason, "by": self.by}


def trip_search_request(to: str, date: str) -> str:
    return f"Find trips from {FROM_CITY} to {to} on {date}"


def trip_book_request(option: str, seat: str) -> str:
    return f"Book seat {seat} on {option}"


def checked_place(to: Any) -> str:
    if not isinstance(to, str) or not PLACE.fullmatch(to.strip()):
        raise BadTripInput("a city name")
    return to.strip()


def checked_date(date: Any) -> str:
    if not isinstance(date, str) or not DATE.fullmatch(date.strip()):
        raise BadTripInput("a date like 2026-11-21")
    try:
        calendar_date.fromisoformat(date.strip())
    except ValueError:
        raise BadTripInput("a date like 2026-11-21") from None
    return date.strip()


def checked_option(option: Any) -> str:
    if not isinstance(option, str) or not OPTION_ID.fullmatch(option):
        raise BadTripInput("a ride id like nozomi-21")
    return option


def checked_seat(seat: Any) -> str:
    if not isinstance(seat, str) or not SEAT_ID.fullmatch(seat):
        raise BadTripInput("a seat like 7A")
    return seat


def tree_names(tree: Tree) -> list[tuple[str | None, str]]:
    found: list[tuple[str | None, str]] = []
    for line in tree.text.splitlines():
        match = TREE_LINE.match(line)
        if match:
            found.append((match.group(1), match.group(2).strip()))
    return found


def shows(tree: Tree, role: str, name: str) -> bool:
    return any(element.role == role and element.name == name for element in tree.elements) or (role, name) in tree_names(tree)


def heading_after(tree: Tree, prefix: str) -> str | None:
    return next((name.removeprefix(prefix) for role, name in tree_names(tree) if role == "heading" and name.startswith(prefix)), None)


def page_words(tree: Tree) -> str:
    return " ".join(name for _, name in tree_names(tree))


def query_value(url: str, key: str) -> str | None:
    values = parse_qs(urlsplit(url).query).get(key)
    return values[0] if values else None


def listed_options(tree: Tree) -> tuple[Listed, ...]:
    names = [name for _, name in tree_names(tree)]
    links = [element.name.removeprefix(CHOOSE_SEATS) for element in tree.elements if element.role == "link" and element.name.startswith(CHOOSE_SEATS)]
    listed: list[Listed] = []
    start = 0
    for name in links:
        end = next((index for index in range(start, len(names)) if names[index] == f"{CHOOSE_SEATS}{name}"), len(names))
        match = OPTION_LINE.search(" ".join(names[start:end]))
        if match is None:
            raise TripPageUnexpected("option-unreadable", f"I couldn't read the times and price of {name}")
        kind, depart, arrive, currency, price = match.groups()
        listed.append(Listed(name, kind.lower(), depart, arrive, currency.upper(), int(price.replace(",", ""))))
        start = end + 1
    if len({entry.name for entry in listed}) != len(listed):
        raise TripPageUnexpected("option-twice", "The same ride is listed twice")
    return tuple(listed)


def seats_on(tree: Tree) -> tuple[Seat, ...]:
    found: dict[str, Seat] = {}
    for name in [*(name for _, name in tree_names(tree)), *(element.name for element in tree.elements)]:
        match = SEAT_NAME.fullmatch(name.strip())
        if match is None:
            continue
        row, letter, position, exit_row, state = match.groups()
        seat = Seat(int(row), letter, position, exit_row is not None, state == "available", name.strip())
        found.setdefault(seat.id, seat)
    return tuple(sorted(found.values(), key=lambda seat: (seat.row, seat.letter)))


def neighbours(seat: Seat, seats: Iterable[Seat]) -> list[Seat]:
    side = LEFT_SIDE if seat.letter in LEFT_SIDE else RIGHT_SIDE
    index = side.index(seat.letter)
    beside = {side[place] for place in (index - 1, index + 1) if 0 <= place < len(side)}
    return [other for other in seats if other.row == seat.row and other.letter in beside]


def reason_fits(seat: Seat, reason: str, seats: tuple[Seat, ...]) -> bool:
    if reason == "window":
        return seat.position == "window"
    if reason == "aisle":
        return seat.position == "aisle"
    if reason == "exit-legroom":
        return seat.exit_row
    if reason == "quiet-front":
        return seat.row <= max(1, max(other.row for other in seats) // 3)
    if reason == "together":
        return any(other.available for other in neighbours(seat, seats))
    return False


def rule_suggestion(seats: tuple[Seat, ...]) -> Suggestion | None:
    available = [seat for seat in seats if seat.available]
    for position in ("window", "aisle"):
        seat = next((seat for seat in available if seat.position == position), None)
        if seat is not None:
            return Suggestion(seat.id, position, SUGGESTED_BY_RULE)
    for seat in available:
        reason = next((reason for reason in SEAT_REASONS if reason_fits(seat, reason, seats)), None)
        if reason is not None:
            return Suggestion(seat.id, reason, SUGGESTED_BY_RULE)
    return None


def seat_prompt(kind: str, seats: tuple[Seat, ...]) -> str:
    asked = {"kind": kind, "rows": max(seat.row for seat in seats), "seats": [seat.for_claude() for seat in seats]}
    return json.dumps(asked, ensure_ascii=False, separators=(",", ":"))


def answered_model(response: Any) -> str | None:
    model = getattr(getattr(response, "raw_response", None), "model", None)
    return model if isinstance(model, str) else None


def judged_suggestion(response: Any, seats: tuple[Seat, ...]) -> tuple[str, Suggestion | None]:
    if finish_reason(response) != FINISHED:
        return "not-json", None
    if answered_model(response) not in (CLAUDE_MODEL, CLAUDE_WIRE_MODEL):
        return "model-refused", None
    try:
        reply = json.loads(response.text)
    except (TypeError, ValueError):
        return "not-json", None
    if not isinstance(reply, dict) or set(reply) != REPLY_KEYS or not all(isinstance(reply[key], str) for key in REPLY_KEYS):
        return "not-json", None
    seat = next((seat for seat in seats if seat.id == reply["seat"]), None)
    if seat is None or not seat.available:
        return "unknown-seat", None
    if reply["reason"] not in SEAT_REASONS or not reason_fits(seat, reply["reason"], seats):
        return "wrong-reason", None
    return "chosen", Suggestion(seat.id, reply["reason"], SUGGESTED_BY_CLAUDE)


def budget_allows() -> bool:
    try:
        return budget_hand_off() is None
    except SettingsError:
        return False


class SeatAdvisor:
    def __init__(self, ai: AiCall | None, *, claude_on: bool, clock: Clock) -> None:
        self._ai = ai
        self._claude_on = claude_on and ai is not None
        self._clock = clock
        self.calls: list[ClaudeCall] = []

    async def suggest(self, kind: str, seats: tuple[Seat, ...]) -> Suggestion | None:
        rule = rule_suggestion(seats)
        if rule is None or not self._claude_on or not budget_allows():
            return rule
        available = [seat.id for seat in seats if seat.available]
        started = self._clock()
        try:
            response = await self._ai(**seat_suggestion_call(system=SEAT_SYSTEM_PROMPT, user=seat_prompt(kind, seats), seat_ids=available))
        except ModelNotAllowed:
            self._claude_on = False
            return rule
        except Exception:
            charge_claude(ClaudeBill(calls=1))
            self.calls.append(ClaudeCall("unavailable", elapsed_ms(started, self._clock()), {}, None))
            return rule
        took_ms = elapsed_ms(started, self._clock())
        bill = claude_bill(response)
        charge_claude(bill)
        outcome, suggestion = judged_suggestion(response, seats)
        usage = {name: int(value) for name, value in (getattr(response, "usage", None) or {}).items()}
        self.calls.append(ClaudeCall(outcome, took_ms, usage, float(bill.usd)))
        if outcome == "model-refused":
            self._claude_on = False
        return suggestion or rule


@dataclass(frozen=True)
class SignInSeen:
    elements: tuple[Element | None, ...]
    landing: str


class TripSession:
    def __init__(self, notes: EventNotes, boundary: ClientBoundary, *, open_page: PageOpener | None, clock: Clock, sleep: Sleep) -> None:
        site = boundary.travel
        if site is None:
            raise TripPageUnexpected("no-travel-site", f"{boundary.client.name} has no travel site set up")
        self.notes = notes
        self.boundary = boundary
        self.site: Site = site
        self.clock = clock
        self.sleep = sleep
        self._open_page = open_page or (lambda: boundary.job_computer().page())
        self.page: Page | None = None
        self.active: PlanStep | None = None

    async def call(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(method, *args, **kwargs)

    async def opened(self) -> Page:
        if self.page is None:
            self.page = await asyncio.to_thread(self._open_page)
        return self.page

    async def look(self, ready: Callable[[Tree], bool]) -> Tree | None:
        page = await self.opened()
        for attempt in range(LOOKS):
            try:
                tree = await self.call(page.tree, "full")
            except PageError as error:
                if error.code in LOGIN_PROBLEMS:
                    raise
                tree = None
            if tree is not None and ready(tree):
                self.read(tree)
                return tree
            if attempt + 1 < LOOKS:
                await self.sleep(LOOK_SECONDS)
        return None

    async def sign_in(self, *, learn: bool = False) -> SignInSeen:
        page = await self.opened()
        await self.call(page.open, self.site.sign_in_url)
        form = await self.call(page.tree) if learn else None
        user, password, submit = SIGN_IN
        username, password_name = self.site.logins
        await self.call(page.fill, user, placeholder(username))
        await self.call(page.fill, password, placeholder(password_name))
        await self.call(page.click, submit)
        landed = await self.look(lambda tree: not shows(tree, "textbox", "Password"))
        if landed is None:
            raise SignInFailed(self.site)
        found = () if form is None else tuple(
            next((element for element in form.elements if element.role == locator.role and element.name == locator.name), None) for locator in SIGN_IN
        )
        return SignInSeen(found, urlsplit(landed.page.url).path or "/")

    async def open_and_look(self, url: str, ready: Callable[[Tree], bool], problem: str, message: str) -> Tree:
        page = await self.opened()
        await self.call(page.open, url)
        tree = await self.look(lambda tree: ready(tree) or shows(tree, "textbox", "Password"))
        if tree is None:
            raise TripPageUnexpected(problem, message)
        if not ready(tree):
            raise SignInFailed(self.site)
        return tree

    def read(self, tree: Tree) -> None:
        if self.active is not None:
            self.notes.page_read(self.active.step_id, self.site.host, tree.text, [element.name for element in tree.elements])

    def start(self, step: PlanStep) -> float:
        self.active = step
        self.notes.emit("step.started", stepId=step.step_id, activity=step.activity)
        return self.clock()

    def done(self, step: PlanStep, started: float, *, detail: str | None = None, took_ms: int | None = None) -> None:
        body: dict[str, Any] = {"stepId": step.step_id, "summary": step.summary}
        if detail:
            body["detail"] = detail
        self.notes.emit("step.done", **body, tookMs=elapsed_ms(started, self.clock()) if took_ms is None else took_ms)
        self.active = None

    async def close(self) -> None:
        if self.page is None:
            return
        try:
            await self.call(self.page.close)
        except (PageError, ComputerError):
            return


def trip_failure(error: Exception, site_name: str, closing: str) -> tuple[str, str]:
    if isinstance(error, SignInFailed):
        return f"I couldn't sign in to {site_name} with the saved login, so I stopped. {closing}", "wait-failed"
    if isinstance(error, MissingExternalKey):
        return f"I couldn't open this client's computer, so I stopped. {closing}", "MissingExternalKey"
    if isinstance(error, ComputerError):
        return f"This client's computer stopped answering, so I stopped. {closing}", error.problem.value
    if isinstance(error, PageError) and error.code in LOGIN_PROBLEMS:
        return f"The {site_name} login or the traveller's details aren't saved on this client's computer, so I stopped. {closing}", error.code
    if isinstance(error, PageError):
        return f"{site_name} didn't respond as expected, so I stopped. {closing}", error.code
    if isinstance(error, TripPageUnexpected):
        return f"{error} on {site_name}, so I stopped. {closing}", error.problem
    return f"{site_name} didn't respond as expected, so I stopped. {closing}", type(error).__name__


TRIP_ERRORS = (ComputerError, MissingExternalKey, PageError, SignInFailed, TripPageUnexpected)


def search_plan(site: str, to: str) -> tuple[PlanStep, ...]:
    return (
        PlanStep("sign-in", f"Sign in to {site}", f"Signing in to {site}", f"Signed in to {site}"),
        PlanStep("search", f"Find trips to {to}", f"Finding trips to {to}", f"Found trips to {to}"),
        PlanStep("seats", "Read the seats", "Reading the seats", "Read the seats"),
        PlanStep("suggest", "Suggest a seat", "Choosing a seat to suggest", "Suggested a seat"),
    )


def book_plan(site: str, seat: str) -> tuple[PlanStep, ...]:
    return (
        PlanStep("sign-in", f"Sign in to {site}", f"Signing in to {site}", f"Signed in to {site}"),
        PlanStep("seat", f"Choose seat {seat}", f"Choosing seat {seat}", f"Chose seat {seat}"),
        PlanStep("hold", "Hold the seat", "Holding the seat", "Held the seat"),
        PlanStep("ask", "Ask you before booking", "Waiting for your OK", "You approved the booking"),
        PlanStep("confirm", "Confirm the booking", "Confirming the booking", "Booked and checked the reference"),
    )


READ_STEPS = ("sign-in", "search", "seats")
SCRIPT_RUNG = "script"
TREE_RUNG = "tree"


def options_from(rows: Iterable[tuple[str, ...]]) -> list[tuple[str, Listed]]:
    found: list[tuple[str, Listed]] = []
    for row in rows:
        if len(row) != 3:
            raise TripPageUnexpected("option-unreadable", "A ride in the list was not readable")
        name, text, href = row
        match = OPTION_LINE.search(text)
        option_id = query_value(href, "option")
        if match is None or option_id is None or not OPTION_ID.fullmatch(option_id):
            raise TripPageUnexpected("option-unreadable", f"I couldn't read the times and price of {name}")
        kind, depart, arrive, currency, price = match.groups()
        found.append((option_id, Listed(name, kind.lower(), depart, arrive, currency.upper(), int(price.replace(",", "")))))
    if not found or len({option_id for option_id, _ in found}) != len(found):
        raise TripPageUnexpected("option-twice", "The rides were not listed once each")
    return found


def seats_from(rows: Iterable[tuple[str, ...]], href_of: dict[str, str]) -> dict[str, tuple[Seat, ...]]:
    grouped: dict[str, dict[str, Seat]] = {option_id: {} for option_id in href_of}
    by_href = {href: option_id for option_id, href in href_of.items()}
    for row in rows:
        if len(row) != 2 or row[0] not in by_href:
            raise TripPageUnexpected("seats-unreadable", "A seat was read from a page that was not in the list")
        match = SEAT_NAME.fullmatch(row[1].strip())
        if match is None:
            raise TripPageUnexpected("seats-unreadable", "A seat name was not readable")
        seat_row, letter, position, exit_row, state = match.groups()
        seat = Seat(int(seat_row), letter, position, exit_row is not None, state == "available", row[1].strip())
        grouped[by_href[row[0]]].setdefault(seat.id, seat)
    if any(not seats for seats in grouped.values()):
        raise TripPageUnexpected("seats-unreadable", "A ride showed no seats")
    return {option_id: tuple(sorted(seats.values(), key=lambda seat: (seat.row, seat.letter))) for option_id, seats in grouped.items()}


def passed(step: Any) -> bool:
    return step is not None and step.status == "done"


class TripSearch:
    def __init__(
        self,
        notes: EventNotes,
        boundary: ClientBoundary,
        ai: AiCall | None,
        *,
        to: Any,
        date: Any,
        memory: ClientMemory | None,
        open_page: PageOpener | None,
        clock: Clock,
        sleep: Sleep,
    ) -> None:
        self.notes = notes
        self.boundary = boundary
        self.ai = ai
        self.to = to
        self.date = date
        self.memory = memory
        self.open_page = open_page
        self.clock = clock
        self.sleep = sleep
        self.site_name = boundary.travel.name if boundary.travel is not None else "the travel site"
        shown_to = to.strip() if isinstance(to, str) and PLACE.fullmatch(to.strip()) else "the city"
        self.shown_date = date if isinstance(date, str) and DATE.fullmatch(date) else "the day"
        self.shown_to = shown_to
        self.plan = {step.step_id: step for step in search_plan(self.site_name, shown_to)}
        self.session: TripSession | None = None
        self.advisor: SeatAdvisor | None = None
        self.started: set[str] = set()
        self.replayed: set[str] = set()
        self.search_url = ""

    async def run(self) -> dict[str, Any]:
        saved = await saved_trip_procedure(self.memory)
        self.notes.emit(
            "job.created",
            clientName=self.boundary.client.name,
            title=SEARCH_TITLE,
            request=trip_search_request(self.shown_to, self.shown_date),
            run={"kind": "replay", "procedureVersion": saved.version} if saved is not None else {"kind": "learn"},
            plan=[{"stepId": step.step_id, "title": step.title} for step in self.plan.values()],
        )
        try:
            to, date = checked_place(self.to), checked_date(self.date)
        except BadTripInput as error:
            return failed(self.notes, None, f"I need {error} to search for trips, so I stopped. {NOTHING_BOOKED}", "bad-input")
        try:
            claude_on = use_claude(self.boundary.client.id)
        except SettingsError:
            claude_on = False
        self.advisor = SeatAdvisor(self.ai, claude_on=claude_on, clock=self.clock)
        learned: TripProcedure | None = None
        try:
            self.session = TripSession(self.notes, self.boundary, open_page=self.open_page, clock=self.clock, sleep=self.sleep)
            self.search_url = f"{self.session.site.url}/search?{urlencode({'from': FROM_CITY, 'to': to, 'date': date})}"
            options = await self._replay(saved) if saved is not None else None
            if options is None:
                options, learned = await self._learn(to, saved)
            suggestions, decisions = await self._suggest(options, saved)
        except TRIP_ERRORS as error:
            message, problem = trip_failure(error, self.site_name, NOTHING_BOOKED)
            step = self.session.active if self.session is not None else None
            return {**failed(self.notes, step.step_id if step else None, message, problem), **claude_used(self.advisor.calls)}
        except asyncio.CancelledError:
            step = self.session.active if self.session is not None else None
            body: dict[str, Any] = {"reason": "stopped", "message": STOPPED_BEFORE_BOOKING}
            if step is not None:
                body["stepId"] = step.step_id
            self.notes.emit("job.failed", **body)
            raise
        finally:
            if self.session is not None:
                await self.session.close()
        self.notes.emit(
            "trip.options",
            to=to,
            date=date,
            options=[option.to_json(suggestion) for option, suggestion in zip(options, suggestions, strict=True)],
        )
        kept = await self._keep(saved, learned, decisions)
        found: dict[str, Any] = {"status": "done", "options": len(options), **claude_used(self.advisor.calls)}
        if kept is not None:
            found["procedureVersion"] = kept.version
        return found

    def _begin(self, step: PlanStep) -> float:
        if step.step_id in self.started:
            self.session.active = step
            return self.clock()
        self.started.add(step.step_id)
        return self.session.start(step)

    async def _replay(self, saved: TripProcedure) -> list[TripOption] | None:
        session = self.session
        page = await session.opened()
        self._begin(self.plan["sign-in"])
        ran: ScriptResult = await session.call(page.run_script, compile_trip_search_script(saved, session.site, self.search_url))
        listed: list[tuple[str, Listed]] = []
        for step_id in READ_STEPS:
            step = self.plan[step_id]
            self._begin(step)
            script_step = ran.step(step_id)
            try:
                if not passed(script_step):
                    raise TripPageUnexpected("script-failed", f"The saved step {step_id} did not pass")
                if step_id == "search":
                    listed = options_from(ran.table("options").rows)
                if step_id == "seats":
                    seats = seats_from(ran.table("seats").rows, dict(self._hrefs(ran)))
            except (TripPageUnexpected, PageError):
                self.notes.emit("step.decided", stepId=step_id, rung=TREE_RUNG, tookMs=0, escalatedFrom=[SCRIPT_RUNG])
                return None
            self.notes.emit("step.decided", stepId=step_id, rung=SCRIPT_RUNG, tookMs=0)
            if step_id == "search":
                detail = counted(len(listed), "option")
            elif step_id == "seats":
                detail = counted(sum(len(found) for found in seats.values()), "seat")
            else:
                detail = None
            session.done(step, 0, detail=detail, took_ms=script_step.took_ms)
            self.replayed.add(step_id)
        return [TripOption(option_id, entry, seats[option_id]) for option_id, entry in listed]

    @staticmethod
    def _hrefs(ran: ScriptResult) -> list[tuple[str, str]]:
        return [(query_value(row[2], "option") or "", row[2]) for row in ran.table("options").rows if len(row) == 3]

    async def _learn(self, to: str, saved: TripProcedure | None) -> tuple[list[TripOption], TripProcedure | None]:
        session = self.session
        learn_started = self.clock()
        step = self.plan["sign-in"]
        quiet = "sign-in" in self.replayed
        started = self.clock() if quiet else self._begin(step)
        seen = await session.sign_in(learn=True)
        if not quiet:
            session.done(step, started)

        step = self.plan["search"]
        quiet = "search" in self.replayed
        started = self.clock() if quiet else self._begin(step)
        found = await session.open_and_look(
            self.search_url, lambda tree: heading_after(tree, TRIPS_TO) == to, "search-unreadable", f"I couldn't find the trips to {to}"
        )
        listed = listed_options(found)
        list_name = next((name for role, name in tree_names(found) if role == "list"), None)
        if not quiet:
            session.done(step, started, detail=counted(len(listed), "option"))

        step = self.plan["seats"]
        started = self._begin(step)
        options: list[TripOption] = []
        for entry in listed:
            await session.open_and_look(self.search_url, lambda tree: heading_after(tree, TRIPS_TO) == to, "search-unreadable", f"I couldn't find the trips to {to}")
            await session.call(session.page.click, Locator("link", f"{CHOOSE_SEATS}{entry.name}"))
            seat_page = await session.look(lambda tree: heading_after(tree, SEATS_ON) == entry.name)
            if seat_page is None:
                raise TripPageUnexpected("seats-unreadable", f"I couldn't open the seats on {entry.name}")
            option_id = query_value(seat_page.page.url, "option")
            seats = seats_on(seat_page)
            if option_id is None or not OPTION_ID.fullmatch(option_id) or not seats:
                raise TripPageUnexpected("seats-unreadable", f"I couldn't read the seats on {entry.name}")
            options.append(TripOption(option_id, entry, seats))
        session.done(step, started, detail=counted(sum(len(option.seats) for option in options), "seat"))
        replay_ms = elapsed_ms(learn_started, self.clock())
        return options, self._procedure(seen, list_name, saved, replay_ms)

    def _procedure(self, seen: SignInSeen, list_name: str | None, saved: TripProcedure | None, replay_ms: int) -> TripProcedure | None:
        if list_name is None or None in seen.elements or len(seen.elements) != 3:
            return None
        try:
            user, password, submit = (target_for(element) for element in seen.elements)
        except NoStableSelector:
            return None
        version = 1 if saved is None else saved.version + 1
        return TripProcedure(version, seen.landing, user, password, submit, list_name, CHOOSE_SEATS, SEATS_ON, replay_ms)

    async def _suggest(self, options: list[TripOption], saved: TripProcedure | None) -> tuple[list[Suggestion | None], dict[str, Decision]]:
        step = self.plan["suggest"]
        started = self._begin(step)
        decisions = dict(saved.decisions) if saved is not None else {}
        suggestions: list[Suggestion | None] = []
        reused = 0
        for option in options:
            shape = seat_shape(option.seats)
            kept = decisions.get(option.id)
            if kept is not None and kept.shape == shape:
                suggestions.append(Suggestion(kept.seat, kept.reason, kept.by))
                reused += 1
                continue
            suggestion = await self.advisor.suggest(option.listed.kind, option.seats)
            suggestions.append(suggestion)
            if suggestion is not None:
                decisions[option.id] = Decision(shape, suggestion.seat, suggestion.reason, suggestion.by)
        by = {suggestion.by for suggestion in suggestions if suggestion is not None}
        if options and reused == len(options):
            detail = "Same seats as last time"
        else:
            detail = "By Claude" if by == {SUGGESTED_BY_CLAUDE} else "By the seat rule" if by == {SUGGESTED_BY_RULE} else None
        self.session.done(step, started, detail=detail)
        return suggestions, decisions

    async def _keep(self, saved: TripProcedure | None, learned: TripProcedure | None, decisions: dict[str, Decision]) -> TripProcedure | None:
        if learned is not None:
            procedure = learned.with_decisions(decisions)
            if await keep_trip_procedure(self.memory, procedure):
                announce_trip_procedure(self.notes, procedure)
                return procedure
            return None
        if saved is None:
            return None
        if dict(saved.decisions) != decisions:
            await keep_trip_procedure(self.memory, saved.with_decisions(decisions))
        return saved


async def run_trip_search(
    notes: EventNotes,
    boundary: ClientBoundary,
    ai: AiCall | None,
    *,
    to: str,
    date: str,
    memory: ClientMemory | None = None,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    return await TripSearch(notes, boundary, ai, to=to, date=date, memory=memory, open_page=open_page, clock=clock, sleep=sleep).run()


def claude_used(calls: list[ClaudeCall]) -> dict[str, Any]:
    return {"claudeCalls": len(calls), "claudeUsage": {"suggest": [call.to_json() for call in calls]} if calls else {}}


@dataclass(frozen=True)
class Review:
    name: str
    hold: str | None
    depart: str | None
    arrive: str | None
    currency: str | None
    price: int | None

    def to_json(self, option: str, seat: str) -> dict[str, Any]:
        found: dict[str, Any] = {"option": option, "seat": seat, "name": self.name}
        for key, value in (("depart", self.depart), ("arrive", self.arrive), ("currency", self.currency), ("price", self.price), ("hold", self.hold)):
            if value is not None:
                found[key] = value
        return found


def review_of(tree: Tree, name: str, seat: str) -> Review:
    words = page_words(tree)
    if not re.search(rf"(?<![0-9A-Za-z]){re.escape(seat)}(?![0-9A-Za-z])", words) or name not in words:
        raise TripPageUnexpected("review-mismatch", f"The review didn't show seat {seat} on {name}")
    times = TIMES.search(words)
    price = PRICE.search(words)
    hold = query_value(tree.page.url, "hold")
    return Review(
        name=name,
        hold=hold if hold is not None and HOLD_ID.fullmatch(hold) else None,
        depart=times.group(1) if times else None,
        arrive=times.group(2) if times else None,
        currency=price.group(1) if price else None,
        price=int(price.group(2).replace(",", "")) if price else None,
    )


class TripBooking:
    def __init__(
        self,
        notes: EventNotes,
        boundary: ClientBoundary,
        desk: ApprovalDesk,
        *,
        option: str,
        seat: str,
        job_id: str,
        open_page: PageOpener | None,
        clock: Clock,
        wall: Clock,
        sleep: Sleep,
    ) -> None:
        self.notes = notes
        self.boundary = boundary
        self.desk = desk
        self.option = option
        self.seat = seat
        self.job_id = job_id
        self.open_page = open_page
        self.clock = clock
        self.wall = wall
        self.sleep = sleep
        self.site_name = boundary.travel.name if boundary.travel is not None else "the travel site"
        self.shown_seat = seat if isinstance(seat, str) and SEAT_ID.fullmatch(seat) else "the seat"
        self.shown_option = option if isinstance(option, str) and OPTION_ID.fullmatch(option) else "the ride"
        self.plan = {step.step_id: step for step in book_plan(self.site_name, self.shown_seat)}
        self.session: TripSession | None = None
        self.held = False
        self.confirming = False
        self.review_url: str | None = None

    @property
    def closing(self) -> str:
        if self.confirming:
            return MAYBE_BOOKED
        return HELD_NOT_BOOKED if self.held else NOTHING_BOOKED

    async def run(self) -> dict[str, Any]:
        self.notes.emit(
            "job.created",
            clientName=self.boundary.client.name,
            title=BOOK_TITLE,
            request=trip_book_request(self.shown_option, self.shown_seat),
            run={"kind": "learn"},
            plan=[{"stepId": step.step_id, "title": step.title} for step in self.plan.values()],
        )
        try:
            checked_option(self.option)
            checked_seat(self.seat)
        except BadTripInput as error:
            return failed(self.notes, None, f"I need {error} to book, so I stopped. {NOTHING_BOOKED}", "bad-input")
        try:
            self.session = TripSession(self.notes, self.boundary, open_page=self.open_page, clock=self.clock, sleep=self.sleep)
            review = await self._up_to_the_hold()
            answer = await self._ask(review)
            if answer is not None:
                return answer
            return await self._confirm()
        except TRIP_ERRORS as error:
            message, problem = trip_failure(error, self.site_name, self.closing)
            step = self.session.active if self.session is not None else None
            return failed(self.notes, step.step_id if step else None, message, problem)
        except asyncio.CancelledError:
            step = self.session.active if self.session is not None else None
            stopped = f"Stopped while booking. {MAYBE_BOOKED}" if self.confirming else STOPPED_WHILE_HELD if self.held else STOPPED_BEFORE_BOOKING
            body: dict[str, Any] = {"reason": "stopped", "message": stopped}
            if step is not None:
                body["stepId"] = step.step_id
            self.notes.emit("job.failed", **body)
            raise
        finally:
            if self.session is not None:
                await self.session.close()

    async def _up_to_the_hold(self) -> Review:
        session = self.session
        started = session.start(self.plan["sign-in"])
        await session.sign_in()
        session.done(self.plan["sign-in"], started)

        started = session.start(self.plan["seat"])
        name, chosen = await self._seat_page()
        await session.call(session.page.click, Locator("button", chosen.name))
        if await session.look(lambda tree: shows(tree, "textbox", FULL_NAME.name) and shows(tree, "button", HOLD_SEAT.name)) is None:
            raise TripPageUnexpected("hold-form-missing", f"Choosing seat {self.seat} didn't open the passenger form")
        session.done(self.plan["seat"], started, detail=name)

        started = session.start(self.plan["hold"])
        await session.call(session.page.fill, FULL_NAME, placeholder(TRAVELER_NAME))
        await session.call(session.page.fill, EMAIL, placeholder(TRAVELER_EMAIL))
        await session.call(session.page.click, HOLD_SEAT)
        reviewed = await session.look(lambda tree: heading_after(tree, REVIEW_HEADING) == "" and shows(tree, "button", CONFIRM_BOOKING.name))
        if reviewed is None:
            raise TripPageUnexpected("hold-failed", f"Seat {self.seat} couldn't be held")
        self.held = True
        self.review_url = reviewed.page.url
        review = review_of(reviewed, name, self.seat)
        self.notes.emit("trip.review", **review.to_json(self.option, self.seat))
        session.done(self.plan["hold"], started, detail=f"Seat {self.seat} on {name}")
        return review

    async def _seat_page(self) -> tuple[str, Seat]:
        session = self.session
        url = f"{session.site.url}/seats?{urlencode({'option': self.option})}"
        tree = await session.open_and_look(url, lambda tree: heading_after(tree, SEATS_ON) is not None, "seats-unreadable", f"I couldn't open the seats for {self.option}")
        name = heading_after(tree, SEATS_ON)
        chosen = next((seat for seat in seats_on(tree) if seat.id == self.seat), None)
        if chosen is None:
            raise TripPageUnexpected("no-such-seat", f"There's no seat {self.seat} on {name}")
        if not chosen.available:
            raise TripPageUnexpected("seat-taken", f"Seat {self.seat} on {name} is taken now")
        return name, chosen

    async def _ask(self, review: Review) -> dict[str, Any] | None:
        step = self.plan["ask"]
        self.session.start(step)
        approval_id = f"apr-{self.job_id}"
        asked_at = self.wall()
        self.notes.emit(
            "approval.requested",
            approvalId=approval_id,
            stepId=step.step_id,
            question=f"Book seat {self.seat} on {review.name}?",
            undoable=False,
            booking={"option": self.option, "seat": self.seat},
        )
        meter = job_meter()
        if meter is not None:
            meter.end_phase()
        try:
            answer = await wait_for_answer(self.desk, approval_id, self.job_id)
        except asyncio.CancelledError:
            await self.desk.release(approval_id)
            raise
        except AgentFieldClientError:
            return failed(self.notes, step.step_id, COULDNT_ASK, "pause-unavailable")
        if answer.decision != APPROVED:
            self.notes.emit("job.failed", reason="stopped", message=TRIP_NOT_APPROVED.get(answer.decision, TRIP_NOT_ANSWERED), stepId=step.step_id)
            self.session.active = None
            return {"status": "stopped", "decision": answer.decision, "held": True}
        waited_ms = max(0, round((self.wall() - asked_at) * 1000))
        body: dict[str, Any] = {"approvalId": approval_id, "decision": APPROVED, "by": APPROVER}
        if answer.feedback and answer.feedback.strip():
            body["feedback"] = answer.feedback.strip()
        self.notes.emit("approval.answered", **body)
        self.notes.emit("step.decided", stepId=step.step_id, rung="human", tookMs=waited_ms)
        self.notes.emit("step.done", stepId=step.step_id, summary=step.summary, tookMs=waited_ms)
        self.session.active = None
        return None

    async def _confirm(self) -> dict[str, Any]:
        session = self.session
        step = self.plan["confirm"]
        started = session.start(step)
        computer_in_use()
        on_review = await session.look(lambda tree: shows(tree, "button", CONFIRM_BOOKING.name))
        if on_review is None:
            await session.sign_in()
            await session.open_and_look(self.review_url, lambda tree: shows(tree, "button", CONFIRM_BOOKING.name), "review-gone", "The held booking couldn't be found again")
        self.confirming = True
        await session.call(session.page.click, CONFIRM_BOOKING)
        booked = await session.look(lambda tree: heading_after(tree, BOOKED_HEADING) == "" and REFERENCE.search(page_words(tree)) is not None)
        if booked is None:
            raise TripPageUnexpected("no-reference", "The booking didn't show a reference")
        reference = REFERENCE.search(page_words(booked)).group(1)
        shown = query_value(booked.page.url, "ref")
        if shown is not None and shown != reference:
            raise TripPageUnexpected("reference-mismatch", "The booking reference didn't match its page")
        self.notes.emit("trip.booked", option=self.option, seat=self.seat, reference=reference)
        session.done(step, started, detail=f"Reference {reference}")
        return {"status": "done", "reference": reference}


async def run_trip_book(
    notes: EventNotes,
    boundary: ClientBoundary,
    desk: ApprovalDesk,
    *,
    option: str,
    seat: str,
    job_id: str,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
    wall: Clock = time.time,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    return await TripBooking(
        notes, boundary, desk, option=option, seat=seat, job_id=job_id, open_page=open_page, clock=clock, wall=wall, sleep=sleep
    ).run()
