import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from agentfield.exceptions import MemoryAccessError

from .approval import ClientMemory
from .procedure import Target
from .records import EventNotes

TRIP_PROCEDURE_KEY = "trips/procedure"
TRIP_PROCEDURE_ID = "trip-search"
TRIP_RECORD_VERSION = 1
TRIP_STEP_IDS = ("sign-in", "search", "seats", "suggest")
SIGN_IN_NAMES = ("user", "password", "submit")
SUGGESTERS = frozenset({"claude", "rule"})


def seat_shape(seats: Any) -> str:
    listed = [[seat.id, seat.position, seat.exit_row, seat.available] for seat in seats]
    return hashlib.sha256(json.dumps(listed, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    shape: str
    seat: str
    reason: str
    by: str

    def to_json(self) -> dict[str, str]:
        return {"shape": self.shape, "seat": self.seat, "reason": self.reason, "by": self.by}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Decision":
        values = (data["shape"], data["seat"], data["reason"], data["by"])
        if not all(isinstance(value, str) and value for value in values) or data["by"] not in SUGGESTERS:
            raise ValueError(f"{data!r} is not a saved seat decision")
        return cls(*values)


@dataclass(frozen=True)
class TripProcedure:
    version: int
    landing: str
    user: Target
    password: Target
    submit: Target
    list_name: str
    link_prefix: str
    seats_heading: str
    expected_replay_ms: int
    decisions: Mapping[str, Decision] = field(default_factory=dict)

    def sign_in_elements(self) -> dict[str, Target]:
        return {"user": self.user, "password": self.password, "submit": self.submit}

    def with_decisions(self, decisions: Mapping[str, Decision]) -> "TripProcedure":
        return replace(self, decisions=dict(decisions))

    def to_json(self) -> dict[str, Any]:
        return {
            "version": TRIP_RECORD_VERSION,
            "procedureVersion": self.version,
            "landing": self.landing,
            "signIn": {name: target.to_json() for name, target in self.sign_in_elements().items()},
            "listName": self.list_name,
            "linkPrefix": self.link_prefix,
            "seatsHeading": self.seats_heading,
            "expectedReplayMs": self.expected_replay_ms,
            "decisions": {option: decision.to_json() for option, decision in self.decisions.items()},
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "TripProcedure":
        if data.get("version") != TRIP_RECORD_VERSION:
            raise ValueError("A saved trip procedure must be version 1")
        version, replay_ms = data["procedureVersion"], data["expectedReplayMs"]
        texts = (data["landing"], data["listName"], data["linkPrefix"], data["seatsHeading"])
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"{version!r} is not a procedure version")
        if isinstance(replay_ms, bool) or not isinstance(replay_ms, int) or replay_ms < 0:
            raise ValueError(f"{replay_ms!r} is not a replay time")
        if not all(isinstance(text, str) and text for text in texts) or not data["landing"].startswith("/"):
            raise ValueError("A saved trip procedure needs its landing page, list and headings")
        user, password, submit = (Target.from_json(data["signIn"][name]) for name in SIGN_IN_NAMES)
        decisions = {str(option): Decision.from_json(decision) for option, decision in data["decisions"].items()}
        return cls(version, data["landing"], user, password, submit, data["listName"], data["linkPrefix"], data["seatsHeading"], replay_ms, decisions)


async def saved_trip_procedure(memory: ClientMemory | None) -> TripProcedure | None:
    if memory is None:
        return None
    try:
        stored = await memory.get(TRIP_PROCEDURE_KEY)
    except MemoryAccessError:
        return None
    if stored is None:
        return None
    try:
        return TripProcedure.from_json(stored)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


async def keep_trip_procedure(memory: ClientMemory | None, procedure: TripProcedure) -> bool:
    if memory is None:
        return False
    try:
        await memory.set(TRIP_PROCEDURE_KEY, procedure.to_json())
    except MemoryAccessError:
        return False
    return True


def announce_trip_procedure(notes: EventNotes, procedure: TripProcedure) -> None:
    notes.emit(
        "procedure.saved",
        procedureId=TRIP_PROCEDURE_ID,
        version=procedure.version,
        stepCount=len(TRIP_STEP_IDS),
        expectedReplayMs=procedure.expected_replay_ms,
    )
