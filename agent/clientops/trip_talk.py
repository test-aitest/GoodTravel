import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .boundary import UnknownClient, known_client
from .chat import names_another_client, plain, within_budget
from .claude_call import TRIP_DESTINATIONS, TRIP_SPEAKERS, TRIP_TALK_ACTIONS, WORKER_SPEAKER, ModelNotAllowed, trip_talk_call
from .cost import ClaudeBill, JobMeter, charge_claude, claude_bill, metering
from .plan import FINISHED, AiCall, finish_reason
from .settings import claude_allowed

ASK, SHOW_DESTINATIONS, SEARCH, SHOW_RIDES, CHOOSE_RIDE, SHOW_SEATS, CHOOSE_SEAT, SHOW_TICKET, NONE = TRIP_TALK_ACTIONS
DECIDED_BY_CLAUDE = "claude"
DECIDED_BY_RULES = "rules"

MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
SCENES = ("where", "ride", "seat", "hold", "booked")
KINDS = ("rail", "flight")
SPEAKERS = ("you", "worker")
POSITIONS = ("window", "aisle", "middle")
SEAT_REASONS = ("window", "aisle", "quiet-front", "exit-legroom", "together")

SAID_MAX_CHARS = 500
TEXT_MAX_CHARS = 300
HISTORY_MAX = 6
OPTIONS_MAX = 12
REASONS_MAX = 6
REPLY_MAX_WORDS = 30
GUIDE_MAX_WORDS = 40
REPLY_MAX_CHARS = 320

CONTEXT_KEYS = frozenset({"month", "scene", "destination", "ranked", "options", "suggestion", "seatPreference", "chosen", "history"})
RANKED_KEYS = frozenset({"destination", "score", "reasons"})
OPTION_KEYS = frozenset({"id", "name", "kind", "depart", "arrive", "price", "currency"})
SUGGESTION_KEYS = frozenset({"option", "seat", "reason"})
PREFERENCE_KEYS = frozenset({"position", "exitRow"})
CHOSEN_KEYS = frozenset({"option", "seat"})
TURN_KEYS = frozenset({"from", "text"})
REPLY_KEYS = frozenset({"reply", "action", "destination", "option", "seat", "speaker"})

OPTION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
SEAT_ID = re.compile(r"\d{1,2}[A-F]")
CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
CURRENCY = re.compile(r"[A-Z]{3}")
LABEL = re.compile(r"[A-Za-z][A-Za-z0-9 ,.'&/+()°%-]{0,59}")
RIDE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ,.'&/+()-]{0,79}")
PERSONAL = re.compile(r"[^\s@]+@[^\s@]+|https?://|\bpass(?:word|code)\b|\busername\b|\blogin\b", re.IGNORECASE)
CLAIMS_BOOKED = re.compile(r"\b(?:booked|confirmed|reserved)\b", re.IGNORECASE)
LIST_MARK = re.compile(r"(?:^|\n)\s*(?:[-*•]|\d+[.)])\s")

PLACE_WORDS = {destination: re.compile(rf"\b{destination}\b") for destination in TRIP_DESTINATIONS}
EARLIER = re.compile(r"\b(?:earlier|earliest|first|sooner|morning)\b")
LATER = re.compile(r"\b(?:later|latest|last|afternoon|evening)\b")
THAT_ONE = re.compile(r"\b(?:that one|this one|that ride|that train|that flight|sounds good|go with that|take it)\b")
SEAT_SAID = re.compile(r"\b(\d{1,2}) ?([a-f])\b")
WINDOW_OR_AISLE = re.compile(r"\b(window|aisle)\b")
AGREE = re.compile(r"\b(?:book|book it|yes|yeah|yep|ok|okay|sure|sounds good|go ahead|please do|perfect|great)\b")

ASK_WHERE = "Where would you like to go, and when?"
CHECKING = "Your worker is checking trains and flights now."
GUIDE_INTROS = {
    "kyoto": f"Hello from Kyoto! In autumn the maples glow red at Tofuku-ji and Arashiyama, and the temples are lovely in the evening. {CHECKING}",
    "seoul": f"Annyeong from Seoul! Walk the palace grounds of Gyeongbokgung and warm up with street food at Gwangjang Market. {CHECKING}",
    "singapore": f"Hello from Singapore! Watch Marina Bay light up at night and try the hawker food at Lau Pa Sat. {CHECKING}",
}

SYSTEM_PROMPT = (
    "You are a back-office worker talking with a business traveller by voice to plan one trip. "
    "You get a JSON object with what the person just said and the screen's state: the month, the scene, the destination if known, "
    "destinations ranked by the client's knowledge graph with reasons, the rides found, the suggested ride and seat, "
    "the traveller's usual seat preference, what was chosen, and the last few turns. "
    "Decide one next step and say one short reply. "
    "The reply is spoken English: warm, concise, at most thirty words, one or two sentences, never a list, never numbers the screen did not give. "
    "Use the knowledge reasons and the month when you suggest a destination. "
    "When the place, the date or the purpose is unclear, ask one short question with action ask, or show-destinations to show the ranked places. "
    "When the destination is known and options is null, use action search with that destination. "
    "When options exist and no ride is chosen, recommend one ride with its reason and wait for the person: use show-rides, or choose-ride with its option id when the person picked one. "
    "When a ride is chosen, recommend the suggested seat with its reason, mentioning the traveller's usual preference when it is given: use show-seats, or choose-seat with the seat id when the person picked a seat. "
    "When the person agrees to a seat, use show-ticket with the option and the seat and tell them to press and hold the ticket to book. "
    "Never say the trip is booked, confirmed or reserved: booking happens only when the person holds the ticket. "
    "Use only option ids from options and seat ids like 12C. Set destination, option and seat to null when the step does not need them. "
    "Use none when nothing should change on the screen. "
    "Each destination has its own local guide on screen. Set speaker to worker for every turn, except: "
    "when the action is search, and when the person asks about the destination city, the reply is spoken by that city's guide, "
    "so speaker is that city and destination is that city. "
    "The guide introduces one or two real, well-known charms of the city that fit the month and the purpose of the trip, "
    "then says in one short line that the worker is checking trains and flights now. A guide's reply may be up to forty words."
)


class TripTalkInputError(ValueError):
    pass


@dataclass(frozen=True)
class TripDecision:
    reply: str
    action: str
    destination: str | None = None
    option: str | None = None
    seat: str | None = None
    speaker: str = WORKER_SPEAKER

    def to_json(self) -> dict[str, Any]:
        return {"reply": self.reply, "action": self.action, "destination": self.destination, "option": self.option, "seat": self.seat, "speaker": self.speaker}


def refuse(message: str) -> TripTalkInputError:
    return TripTalkInputError(message)


def exact(entry: Any, keys: frozenset[str], what: str) -> dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != keys:
        raise refuse(f"The {what} must be an object with exactly {', '.join(sorted(keys))}.")
    return entry


def one_of(value: Any, allowed: tuple[str, ...], what: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise refuse(f"The {what} must be one of {', '.join(allowed)}.")
    return value


def matching(value: Any, pattern: re.Pattern[str], what: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise refuse(f"The {what} is not in the expected form.")
    return value


def impersonal(text: str, what: str) -> str:
    if PERSONAL.search(text):
        raise refuse(f"The {what} looks personal; emails, links and logins never go into a trip talk.")
    return text


def short_text(value: Any, what: str, limit: int = TEXT_MAX_CHARS) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise refuse(f"The {what} must be text of at most {limit} characters.")
    return impersonal(value, what)


def number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise refuse(f"The {what} must be a number that is zero or more.")
    return value


def read_ranked(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > len(TRIP_DESTINATIONS):
        raise refuse(f"The ranked list must have at most {len(TRIP_DESTINATIONS)} destinations.")
    ranked = []
    for entry in value:
        entry = exact(entry, RANKED_KEYS, "ranked destination")
        reasons = entry["reasons"]
        if not isinstance(reasons, list) or len(reasons) > REASONS_MAX:
            raise refuse(f"Each destination has at most {REASONS_MAX} reasons.")
        ranked.append(
            {
                "destination": one_of(entry["destination"], TRIP_DESTINATIONS, "ranked destination"),
                "score": number(entry["score"], "score"),
                "reasons": [impersonal(matching(reason, LABEL, "reason label"), "reason label") for reason in reasons],
            }
        )
    if len({entry["destination"] for entry in ranked}) != len(ranked):
        raise refuse("Each destination may be ranked only once.")
    return ranked


def read_options(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > OPTIONS_MAX:
        raise refuse(f"The options must be a list of at most {OPTIONS_MAX} rides.")
    options = []
    for entry in value:
        entry = exact(entry, OPTION_KEYS, "ride option")
        options.append(
            {
                "id": matching(entry["id"], OPTION_ID, "ride id"),
                "name": impersonal(matching(entry["name"], RIDE_NAME, "ride name"), "ride name"),
                "kind": one_of(entry["kind"], KINDS, "ride kind"),
                "depart": matching(entry["depart"], CLOCK, "departure time"),
                "arrive": matching(entry["arrive"], CLOCK, "arrival time"),
                "price": number(entry["price"], "price"),
                "currency": matching(entry["currency"], CURRENCY, "currency"),
            }
        )
    if len({option["id"] for option in options}) != len(options):
        raise refuse("Each ride id may be listed only once.")
    return options


def known_option(value: Any, options: list[dict[str, Any]] | None, what: str) -> str:
    option = matching(value, OPTION_ID, what)
    if options is not None and option not in {entry["id"] for entry in options}:
        raise refuse(f"The {what} is not one of the rides listed.")
    return option


def read_context(value: Any) -> dict[str, Any]:
    context = exact(value, CONTEXT_KEYS, "context")
    options = read_options(context["options"])
    suggestion = context["suggestion"]
    if suggestion is not None:
        suggestion = exact(suggestion, SUGGESTION_KEYS, "suggestion")
        suggestion = {
            "option": known_option(suggestion["option"], options, "suggested ride"),
            "seat": matching(suggestion["seat"], SEAT_ID, "suggested seat"),
            "reason": one_of(suggestion["reason"], SEAT_REASONS, "seat reason"),
        }
    preference = context["seatPreference"]
    if preference is not None:
        preference = exact(preference, PREFERENCE_KEYS, "seat preference")
        if not isinstance(preference["exitRow"], bool):
            raise refuse("The exit row preference must be true or false.")
        preference = {"position": one_of(preference["position"], POSITIONS, "seat position"), "exitRow": preference["exitRow"]}
    chosen = context["chosen"]
    if chosen is not None:
        chosen = exact(chosen, CHOSEN_KEYS, "choice")
        chosen = {"option": known_option(chosen["option"], options, "chosen ride"), "seat": matching(chosen["seat"], SEAT_ID, "chosen seat", nullable=True)}
    history = context["history"]
    if not isinstance(history, list) or len(history) > HISTORY_MAX:
        raise refuse(f"The history must be the last {HISTORY_MAX} turns at most.")
    turns = []
    for turn in history:
        turn = exact(turn, TURN_KEYS, "turn")
        turns.append({"from": one_of(turn["from"], SPEAKERS, "speaker"), "text": short_text(turn["text"], "turn")})
    return {
        "month": one_of(context["month"], MONTHS, "month"),
        "scene": one_of(context["scene"], SCENES, "scene"),
        "destination": one_of(context["destination"], TRIP_DESTINATIONS, "destination", nullable=True),
        "ranked": read_ranked(context["ranked"]),
        "options": options,
        "suggestion": suggestion,
        "seatPreference": preference,
        "chosen": chosen,
        "history": turns,
    }


def read_said(said: Any) -> str:
    return short_text(said, "message", SAID_MAX_CHARS)


def ride_by_clock(options: list[dict[str, Any]], latest: bool) -> dict[str, Any]:
    ordered = sorted(options, key=lambda option: option["depart"])
    return ordered[-1] if latest else ordered[0]


def named_ride(text: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((option for option in options if plain(option["name"]) in text), None)


def ride_said(text: str, context: dict[str, Any]) -> dict[str, Any] | None:
    options = context["options"]
    if not options:
        return None
    named = named_ride(text, options)
    if named is not None:
        return named
    if EARLIER.search(text):
        return ride_by_clock(options, latest=False)
    if LATER.search(text):
        return ride_by_clock(options, latest=True)
    if THAT_ONE.search(text) or (context["scene"] == "ride" and AGREE.search(text)):
        suggested = context["suggestion"]["option"] if context["suggestion"] else options[0]["id"]
        return next(option for option in options if option["id"] == suggested)
    return None


def ride_option(context: dict[str, Any]) -> str | None:
    if context["chosen"]:
        return context["chosen"]["option"]
    return context["suggestion"]["option"] if context["suggestion"] else None


def seat_said(text: str, context: dict[str, Any]) -> str | None:
    found = SEAT_SAID.search(text)
    if found:
        return f"{found.group(1)}{found.group(2).upper()}"
    if WINDOW_OR_AISLE.search(text) and context["suggestion"]:
        return context["suggestion"]["seat"]
    return None


def held_seat(context: dict[str, Any]) -> str | None:
    if context["chosen"] and context["chosen"]["seat"]:
        return context["chosen"]["seat"]
    if context["scene"] in ("seat", "hold") and context["suggestion"]:
        return context["suggestion"]["seat"]
    return None


def route(said: str, context: dict[str, Any]) -> TripDecision:
    text = plain(said)
    place = next((destination for destination, words in PLACE_WORDS.items() if words.search(text)), None)
    if place is not None:
        return TripDecision(GUIDE_INTROS[place], SEARCH, destination=place, speaker=place)
    seat = seat_said(text, context)
    option = ride_option(context)
    if seat is not None and option is not None and context["scene"] in ("seat", "hold"):
        return TripDecision(f"Seat {seat} it is. Say yes when you're happy with it.", CHOOSE_SEAT, destination=context["destination"], option=option, seat=seat)
    if context["scene"] in ("where", "ride"):
        ride = ride_said(text, context)
        if ride is not None:
            return TripDecision(f"Good choice, the {ride['name']} leaving at {ride['depart']}. Now let's pick a seat.", CHOOSE_RIDE, destination=context["destination"], option=ride["id"])
    held = held_seat(context)
    if held is not None and option is not None and AGREE.search(text):
        return TripDecision(f"Here's your ticket for seat {held}. Press and hold it to book.", SHOW_TICKET, destination=context["destination"], option=option, seat=held)
    if context["destination"] is not None and context["options"] is None and context["scene"] == "where":
        return TripDecision(GUIDE_INTROS[context["destination"]], SEARCH, destination=context["destination"], speaker=context["destination"])
    return TripDecision(ASK_WHERE, ASK)


def words_in(reply: str) -> int:
    return len(reply.split())


def well_formed(decision: TripDecision, context: dict[str, Any]) -> bool:
    reply = decision.reply.strip()
    if decision.speaker not in TRIP_SPEAKERS:
        return False
    guide = decision.speaker != WORKER_SPEAKER
    if guide and decision.speaker != decision.destination:
        return False
    if decision.action == SEARCH and not guide:
        return False
    limit = GUIDE_MAX_WORDS if guide else REPLY_MAX_WORDS
    if not reply or len(reply) > REPLY_MAX_CHARS or words_in(reply) > limit or LIST_MARK.search(reply):
        return False
    if PERSONAL.search(reply) or (context["scene"] != "booked" and CLAIMS_BOOKED.search(reply)):
        return False
    if decision.action not in TRIP_TALK_ACTIONS:
        return False
    if decision.destination is not None and decision.destination not in TRIP_DESTINATIONS:
        return False
    option_ids = {option["id"] for option in context["options"] or ()}
    if decision.option is not None and decision.option not in option_ids:
        return False
    if decision.seat is not None and not SEAT_ID.fullmatch(decision.seat):
        return False
    if decision.action == SEARCH:
        return decision.destination is not None
    if decision.action == CHOOSE_RIDE:
        return decision.option is not None
    if decision.action in (CHOOSE_SEAT, SHOW_TICKET):
        return decision.seat is not None and (decision.option is not None or context["chosen"] is not None)
    return True


def user_prompt(said: str, context: dict[str, Any]) -> str:
    return json.dumps({"said": said, **context}, ensure_ascii=False, separators=(",", ":"))


def judged(response: Any, context: dict[str, Any]) -> TripDecision | None:
    if finish_reason(response) != FINISHED:
        return None
    try:
        answer = json.loads(response.text)
    except (TypeError, ValueError):
        return None
    if not isinstance(answer, dict) or set(answer) != REPLY_KEYS:
        return None
    if not all(isinstance(answer[key], str) for key in ("reply", "action", "speaker")):
        return None
    if not all(answer[key] is None or isinstance(answer[key], str) for key in ("destination", "option", "seat")):
        return None
    decision = TripDecision(answer["reply"].strip(), answer["action"], answer["destination"], answer["option"], answer["seat"], answer["speaker"])
    return decision if well_formed(decision, context) else None


async def ask_claude(ai: AiCall, said: str, context: dict[str, Any], meter: JobMeter) -> tuple[TripDecision | None, int]:
    with metering(meter):
        if not within_budget():
            return None, 0
        option_ids = [option["id"] for option in context["options"] or ()]
        try:
            response = await ai(**trip_talk_call(system=SYSTEM_PROMPT, user=user_prompt(said, context), option_ids=option_ids))
        except ModelNotAllowed:
            raise
        except Exception:
            charge_claude(ClaudeBill(calls=1))
            meter.close()
            return None, 1
        charge_claude(claude_bill(response))
        meter.close()
    return judged(response, context), 1


def decision_json(decision: TripDecision, decided_by: str, claude_calls: int) -> dict[str, Any]:
    return {**decision.to_json(), "decidedBy": decided_by, "claudeCalls": claude_calls}


def by_rules(said: str, context: dict[str, Any], claude_calls: int = 0) -> dict[str, Any]:
    return decision_json(route(said, context), DECIDED_BY_RULES, claude_calls)


async def talk_about_trip(
    client_id: str,
    said: Any,
    context: Any,
    *,
    make_ai: Callable[[], AiCall | None],
    open_meter: Callable[[str], JobMeter],
    claude_on: Callable[[str], bool] = claude_allowed,
) -> dict[str, Any]:
    try:
        known_client(client_id)
    except UnknownClient:
        return decision_json(TripDecision("I can only plan trips for the clients I know.", NONE), DECIDED_BY_RULES, 0)
    text = read_said(said)
    state = read_context(context)
    if names_another_client(client_id, text):
        return decision_json(TripDecision("I can only plan this client's trip here.", NONE), DECIDED_BY_RULES, 0)
    if not text.strip() or not claude_on(client_id):
        return by_rules(text, state)
    try:
        ai = make_ai()
        if ai is None:
            return by_rules(text, state)
        decision, calls = await ask_claude(ai, text, state, open_meter(client_id))
    except ModelNotAllowed:
        return by_rules(text, state)
    if decision is None:
        return by_rules(text, state, calls)
    return decision_json(decision, DECIDED_BY_CLAUDE, calls)
