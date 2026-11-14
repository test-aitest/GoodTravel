import json
import re
import shlex
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

from clientops.computer_protocol import CommandResult

TRAVEL = "http://127.0.0.1:3003"
PLACEHOLDER = re.compile(r"\$(LP_[A-Za-z0-9_]+)")
MASKED = "*****"
TRAVEL_ACCOUNTS = {"hinata": ("hinata-trips", "hinata-travel-demo"), "kumo": ("kumo-trips", "kumo-travel-demo")}
TRAVELERS = {
    "hinata": ("Aoi Nakamura", "aoi.nakamura@hinata-skincare.example"),
    "kumo": ("Ren Takeda", "ren.takeda@kumo-coffee.example"),
}
CLIENT_NAMES = {"hinata": "Hinata Skincare", "kumo": "Kumo Coffee Roasters"}
REFERENCE = "KQZTRA"
HOLD = "h-1"


PLAN_PREFIX = "const plan = "
SCRIPT_STEP_MS = {"sign-in": 180, "search": 60, "seats": 140}
CSS = re.compile(
    r'^(?:form\[action="(?P<form>[^"]*)"\] )?(?P<tag>[a-z]+)(?:\[name="(?P<name>[^"]*)"\])?(?P<password>\[type="password"\])?$'
)


class ScriptCheck(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def css_matches(css: str, line: "Line") -> bool:
    match = CSS.fullmatch(css)
    if match is None:
        return False
    if match["form"] is not None and line.form != match["form"]:
        return False
    if match["name"] is not None and line.field != match["name"]:
        return False
    if match["password"] and line.input_type != "password":
        return False
    return line.tag == match["tag"]


@dataclass(frozen=True)
class Ride:
    id: str
    name: str
    kind: str
    depart: str
    arrive: str
    price: int
    taken: frozenset[str]

    @property
    def rows(self) -> int:
        return 16 if self.kind == "rail" else 30

    @property
    def letters(self) -> str:
        return "ABCDE" if self.kind == "rail" else "ABCDEF"

    def position(self, letter: str) -> str:
        window = {"A", "E"} if self.kind == "rail" else {"A", "F"}
        aisle = {"C", "D"}
        return "window" if letter in window else "aisle" if letter in aisle else "middle"

    def exit_row(self, row: int) -> bool:
        return self.kind == "flight" and row in (12, 13)

    def seat_name(self, seat: str) -> str:
        row, letter = int(seat[:-1]), seat[-1]
        exit_part = ", exit row" if self.exit_row(row) else ""
        state = "taken" if seat in self.taken else "available"
        return f"Seat {seat}, {self.position(letter)}{exit_part}, {state}"

    def seats(self) -> list[str]:
        return [f"{row}{letter}" for row in range(1, self.rows + 1) for letter in self.letters]


CATALOG = {
    "Kyoto": (
        Ride("nozomi-21", "Nozomi 21", "rail", "08:00", "10:15", 14170, frozenset({"1A", "1E", "2A", "3E"})),
        Ride("nozomi-27", "Nozomi 27", "rail", "09:00", "11:12", 14170, frozenset({"1A", "2E"})),
    ),
    "Seoul": (
        Ride("ka-712", "Kestrel Air KA 712", "flight", "08:30", "11:05", 38400, frozenset({"1A", "1F"})),
        Ride("ns-104", "Northstar NS 104", "flight", "10:15", "12:50", 35900, frozenset({"2A"})),
    ),
    "Singapore": (
        Ride("ka-631", "Kestrel Air KA 631", "flight", "08:05", "14:35", 72800, frozenset({"1A", "1B", "1F", "2A", "2F", "3A", "12A", "12B", "12F", "13A", "13F", "14C", "14D"})),
        Ride("mr-12", "Meridian MR 12", "flight", "10:40", "17:20", 68500, frozenset({"1A", "1F", "2A"})),
    ),
}
RIDES = {ride.id: ride for rides in CATALOG.values() for ride in rides}


@dataclass
class Line:
    role: str | None
    name: str
    interactive: bool = False
    form: str | None = None
    tag: str | None = None
    depth: int = 1
    field: str | None = None
    input_type: str | None = None


@dataclass
class Shown:
    url: str
    title: str
    lines: list[Line] = field(default_factory=list)


class FakeTravel:
    def __init__(
        self,
        client_id: str = "hinata",
        *,
        vault: dict[str, str] | None = None,
        lagging: bool = True,
        refuse_login: bool = False,
        script_breaks_at: str | None = None,
    ) -> None:
        self.script_breaks_at = script_breaks_at
        self.scripts: list[dict] = []
        self.client_id = client_id
        username, password = TRAVEL_ACCOUNTS[client_id]
        traveler, email = TRAVELERS[client_id]
        self.vault = dict(vault) if vault is not None else {
            "LP_TRAVEL_USERNAME": username,
            "LP_TRAVEL_PASSWORD": password,
            "LP_TRAVELER_NAME": traveler,
            "LP_TRAVELER_EMAIL": email,
        }
        self.account = (username, "wrong" if refuse_login else password)
        self.lagging = lagging
        self.url = "about:blank"
        self.previous: str | None = None
        self.signed_in = False
        self.fields: dict[str, str] = {}
        self.held: dict[str, str] | None = None
        self.booked = False
        self.commands: list[dict] = []
        self.typed: list[str] = []
        self.clicked: list[str] = []

    def run(self, command: str, *, timeout_seconds: int, env=None) -> CommandResult:
        asked = json.loads(shlex.split(command)[-1])
        self.commands.append(asked)
        try:
            answer = self._answer(asked)
        except LookupError as error:
            answer = {"ok": False, "command": asked["command"], "error": {"code": str(error.args[0]), "message": str(error.args[1])}}
        return CommandResult(exit_code=0, output=json.dumps(self._scrubbed(answer), ensure_ascii=False) + "\n")

    def _scrubbed(self, answer: dict) -> dict:
        text = json.dumps(answer, ensure_ascii=False)
        for value in sorted(self.vault.values(), key=len, reverse=True):
            text = text.replace(value, MASKED)
        return json.loads(text)

    def _answer(self, asked: dict) -> dict:
        kind = asked["command"]
        if kind == "close":
            return {"ok": True, "command": "close"}
        if kind == "script":
            return self._script(asked["source"])
        if kind == "open":
            self._go(asked["url"])
            self.previous = None
            return {"ok": True, "command": "open", "page": self._info(self._shown(self.url))}
        if self.url == "about:blank":
            raise LookupError("no-page", "Open a page first")
        if kind == "tree":
            url = self.previous if self.lagging and self.previous is not None else self.url
            self.previous = None
            return self._tree(self._shown(url), asked.get("mode", "interactive"))
        shown = self._shown(self.url)
        line = self._find(shown, asked["target"])
        if kind == "fill":
            self.typed.append(asked["text"])
            missing = [name for name in PLACEHOLDER.findall(asked["text"]) if not self.vault.get(name)]
            if missing:
                raise LookupError("placeholder-unset", f"{', '.join(missing)} is not set")
            self.fields[line.name] = PLACEHOLDER.sub(lambda match: self.vault[match.group(1)], asked["text"])
            return {"ok": True, "command": "fill", "page": self._info(shown), "element": self._summary(shown, line)}
        if kind == "click":
            self.clicked.append(line.name)
            before = self.url
            self._click(line)
            self.previous = before if before != self.url else None
            return {"ok": True, "command": "click", "page": self._info(self._shown(before)), "element": self._summary(shown, line)}
        raise LookupError("bad-command", f"The fake travel site does not take {kind}")

    def _script(self, source: str) -> dict:
        first_line = source.split("\n", 1)[0]
        plan = json.loads(first_line[len(PLAN_PREFIX) : -1])
        self.scripts.append(plan)
        missing = sorted({name for name in PLACEHOLDER.findall(first_line) if not self.vault.get(name)})
        if missing:
            raise LookupError("placeholder-unset", f"{' and '.join(missing)} not in the vault on this computer")
        steps: list[dict] = []
        tables: dict[str, dict] = {}
        found: dict[str, Line] = {}
        for step in plan["steps"]:
            record = {"stepId": step["stepId"], "status": "failed", "tookMs": 0, "checks": 0, "pages": []}
            steps.append(record)
            try:
                if step["stepId"] == self.script_breaks_at:
                    raise ScriptCheck("not-found", f"the saved {step['stepId']} step found nothing")
                for op in step["ops"]:
                    self._script_op(op, found, tables, record)
            except ScriptCheck as failure:
                took = sum(entry["tookMs"] for entry in steps)
                return {
                    "ok": True,
                    "command": "script",
                    "outcome": "check-failed",
                    "tookMs": took,
                    "steps": steps,
                    "tables": tables,
                    "failure": {"stepId": step["stepId"], "kind": "check", "code": failure.code, "message": str(failure)},
                }
            record.update(status="done", tookMs=SCRIPT_STEP_MS[step["stepId"]])
        return {"ok": True, "command": "script", "outcome": "passed", "tookMs": sum(entry["tookMs"] for entry in steps), "steps": steps, "tables": tables, "result": {"said": {}}}

    def _script_op(self, op: dict, found: dict[str, Line], tables: dict[str, dict], record: dict) -> None:
        kind = op["op"]
        record["checks"] += 1
        if kind == "goto":
            self._go(op["url"])
        elif kind == "one":
            shown = self._shown(self.url)
            hits = [line for line in shown.lines if line.interactive and css_matches(op["css"], line) and (op["text"] is None or line.name == op["text"])]
            if len(hits) != 1:
                raise ScriptCheck("not-found" if not hits else "not-one", f"{op['css']} matched {len(hits)} elements, expected 1")
            found[op["as"]] = hits[0]
        elif kind == "fill":
            line = found[op["el"]]
            self.typed.append(op["value"])
            self.fields[line.name] = PLACEHOLDER.sub(lambda match: self.vault[match.group(1)], op["value"])
        elif kind == "click":
            self.clicked.append(found[op["el"]].name)
            self._click(found[op["el"]])
        elif kind == "path":
            if urlsplit(self.url).path != op["is"]:
                raise ScriptCheck(op["code"], f"the page is {urlsplit(self.url).path}, expected {op['is']}")
        elif kind == "options":
            shown = self._shown(self.url)
            if ("list", op["list"]) not in [(line.role, line.name) for line in shown.lines]:
                raise ScriptCheck("not-found", f"there is no list named {op['list']!r}")
            _, query = self._place(self.url)
            rows = [
                [ride.name, f"{ride.name} {ride.kind.capitalize()} · {ride.depart} → {ride.arrive} · JPY {ride.price:,} {op['link']}{ride.name}", f"/seats?{urlencode({'option': ride.id})}"]
                for ride in CATALOG.get(query.get("to", ""), ())
            ]
            tables[op["into"]] = {"columns": ["name", "text", "href"], "rows": rows, "pages": 1}
        elif kind == "seatMaps":
            rows = []
            for name, _, href in tables[op["from"]]["rows"]:
                self._go(op["base"] + href)
                shown = self._shown(self.url)
                if ("heading", op["heading"] + name) not in [(line.role, line.name) for line in shown.lines]:
                    raise ScriptCheck("wrong-page", f"the seat page is not {name}")
                rows += [[href, line.name] for line in shown.lines if line.role == "button" and line.name.startswith("Seat ")]
            tables[op["into"]] = {"columns": ["href", "name"], "rows": rows, "pages": len(tables[op["from"]]["rows"])}
        else:
            raise ScriptCheck("bad-op", f"the fake travel site does not run {kind}")

    def _go(self, url: str) -> None:
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != TRAVEL:
            raise LookupError("navigate-failed", f"Could not open {url}")
        self.url = url if self.signed_in or parts.path == "/login" else f"{TRAVEL}/login"

    def _click(self, line: Line) -> None:
        path, query = self._place(self.url)
        if line.name == "Sign in":
            if (self.fields.get("Username"), self.fields.get("Password")) == self.account:
                self.signed_in = True
                self.url = f"{TRAVEL}/search"
            return
        if line.name.startswith("Choose seats for "):
            ride = next(ride for ride in RIDES.values() if ride.name == line.name.removeprefix("Choose seats for "))
            self.url = f"{TRAVEL}/seats?{urlencode({'option': ride.id})}"
            return
        if line.name.startswith("Seat "):
            seat = line.name.split(",")[0].removeprefix("Seat ")
            self.url = f"{TRAVEL}/seats?{urlencode({'option': query['option'], 'seat': seat})}"
            return
        if line.name == "Hold seat":
            if not self.fields.get("Full name") or not self.fields.get("Email"):
                return
            self.held = {"option": query["option"], "seat": query["seat"], "name": self.fields["Full name"], "email": self.fields["Email"]}
            self.url = f"{TRAVEL}/review?{urlencode({'hold': HOLD})}"
            return
        if line.name == "Confirm booking":
            self.booked = True
            self.url = f"{TRAVEL}/booked?{urlencode({'ref': REFERENCE})}"
            return
        if line.name == "Sign out":
            self.signed_in = False
            self.url = f"{TRAVEL}/login"

    @staticmethod
    def _place(url: str) -> tuple[str, dict[str, str]]:
        parts = urlsplit(url)
        return parts.path, {key: values[0] for key, values in parse_qs(parts.query).items()}

    def _shown(self, url: str) -> Shown:
        path, query = self._place(url)
        if path == "/login":
            return Shown(url, "Sign in - Hikari Travel", [
                Line("heading", "Sign in to Hikari Travel"),
                Line(None, "form", form="/login"),
                Line("textbox", "Username", True, "/login", "input", 2, "username", "text"),
                Line("textbox", "Password", True, "/login", "input", 2, "password", "password"),
                Line("button", "Sign in", True, "/login", "button", 2),
            ])
        header = [Line(None, "Hikari Travel"), Line(None, f"Signed in as {CLIENT_NAMES[self.client_id]}"), Line("button", "Sign out", True, "/logout", "button")]
        if path == "/":
            return Shown(url, "Hikari Travel", [*header, Line("heading", "Where to?")])
        if path == "/search":
            rides = CATALOG.get(query.get("to", ""), ())
            lines = [*header, Line("heading", f"Trips to {query.get('to', '')}"), Line("list", "Options")]
            for ride in rides:
                lines += [
                    Line("heading", ride.name, depth=3),
                    Line(None, f"{ride.kind.capitalize()} · {ride.depart} → {ride.arrive} · JPY {ride.price:,}", depth=3),
                    Line("link", f"Choose seats for {ride.name}", True, None, "a", 3),
                ]
            return Shown(url, f"Trips to {query.get('to', '')} - Hikari Travel", lines)
        if path == "/seats":
            ride = RIDES[query["option"]]
            lines = [*header, Line("heading", f"Seats on {ride.name}"), Line("form", "Seat map")]
            for seat in ride.seats():
                name = ride.seat_name(seat)
                lines.append(Line("button", name, seat not in ride.taken, "/seats", "button", 2))
            if "seat" in query:
                lines += [
                    Line("region", "Passenger details"),
                    Line("textbox", "Full name", True, "/hold", "input", 2, "fullName", "text"),
                    Line("textbox", "Email", True, "/hold", "input", 2, "email", "email"),
                    Line("button", "Hold seat", True, "/hold", "button", 2),
                ]
            return Shown(url, f"Seats on {ride.name} - Hikari Travel", lines)
        if path == "/review" and self.held is not None:
            ride = RIDES[self.held["option"]]
            city = next(city for city, rides in CATALOG.items() if ride in rides)
            seat = self.held["seat"]
            seat_words = ride.seat_name(seat).removesuffix(", available").removesuffix(", taken")
            return Shown(url, "Review your booking - Hikari Travel", [
                *header,
                Line("heading", "Review your booking"),
                Line("region", "Booking summary"),
                Line(None, f"Tokyo → {city}", depth=2),
                Line(None, f"{ride.name} · {ride.kind.capitalize()} · Sat, 21 Nov 2026", depth=2),
                Line(None, f"{ride.depart} → {ride.arrive}", depth=2),
                Line(None, seat_words, depth=2),
                Line(None, "1 adult", depth=2),
                Line(None, f"Total JPY {ride.price:,}", depth=2),
                Line("button", "Confirm booking", True, "/confirm", "button", 2),
            ])
        if path == "/booked" and self.booked:
            return Shown(url, "Booking confirmed - Hikari Travel", [*header, Line("heading", "Booking confirmed"), Line(None, f"Reference {query['ref']}")])
        return Shown(url, "Not found - Hikari Travel", [*header, Line("heading", "Page not found")])

    def _refs(self, shown: Shown) -> list[tuple[int, Line]]:
        return [(index + 10, line) for index, line in enumerate(shown.lines)]

    def _find(self, shown: Shown, target: dict) -> Line:
        found = [line for _, line in self._refs(shown) if line.interactive and line.role == target["role"] and line.name == target["name"]]
        if not found:
            raise LookupError("not-found", f"No {target['role']} named {target['name']!r}")
        if len(found) > 1:
            raise LookupError("not-unique", f"More than one {target['role']} named {target['name']!r}")
        return found[0]

    def _summary(self, shown: Shown, line: Line) -> dict:
        ref = next(ref for ref, candidate in self._refs(shown) if candidate is line)
        return {"ref": ref, "role": line.role, "name": line.name, "form": line.form, "tag": line.tag}

    @staticmethod
    def _info(shown: Shown) -> dict:
        return {"url": shown.url, "title": shown.title, "status": 200}

    def _tree(self, shown: Shown, mode: str) -> dict:
        text = [f"1 RootWebArea '{shown.title}'"]
        elements = []
        for ref, line in self._refs(shown):
            if mode == "interactive" and not line.interactive:
                continue
            role = f"{line.role} " if line.role else ""
            marker = "[i] " if line.interactive else ""
            text.append(f"{' ' * line.depth}{ref} {marker}{role}'{line.name}'" if line.role or line.name != "form" else f"{' ' * line.depth}{ref} form")
            if line.interactive:
                element = {"ref": ref, "role": line.role, "name": line.name, "form": line.form, "tag": line.tag}
                if line.role == "textbox":
                    element["value"] = self.fields.get(line.name, "")
                if line.field is not None:
                    element["fieldName"] = line.field
                if line.input_type is not None:
                    element["inputType"] = line.input_type
                elements.append(element)
        return {"ok": True, "command": "tree", "page": self._info(shown), "mode": mode, "text": "\n".join(text) + "\n", "elements": elements, "cells": []}
