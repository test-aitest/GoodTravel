import asyncio
import copy
import importlib
import json

import pytest
from agentfield.execution_context import ExecutionContext, reset_execution_context, set_execution_context

from clientops import cost
from clientops.boundary import DATA_DIR_VARIABLE, known_client
from clientops.claude_call import CLAUDE_EFFORT, CLAUDE_MODEL, TRIP_TALK_ACTIONS, ModelNotAllowed
from clientops.cost import COSTS_DIRECTORY
from clientops.externals.fake_ai import FakeClaude
from clientops.externals.picking_ai import PickingClaude
from clientops.trip_talk import ASK_WHERE, GUIDE_INTROS, SYSTEM_PROMPT, TripTalkInputError, read_context, route
from test_stock_sync import VAULT, write_settings

CLIENT = "hinata"
OPTIONS = [
    {"id": "nozomi-7", "name": "Nozomi 7", "kind": "rail", "depart": "07:00", "arrive": "09:15", "price": 14170, "currency": "JPY"},
    {"id": "nozomi-21", "name": "Nozomi 21", "kind": "rail", "depart": "10:30", "arrive": "12:45", "price": 14170, "currency": "JPY"},
]
SUGGESTION = {"option": "nozomi-7", "seat": "12E", "reason": "window"}
BASE = {
    "month": "november",
    "scene": "where",
    "destination": None,
    "ranked": [
        {"destination": "kyoto", "score": 0.92, "reasons": ["Autumn leaves peak in November", "Client office nearby"]},
        {"destination": "seoul", "score": 0.61, "reasons": ["Short flight"]},
    ],
    "options": None,
    "suggestion": None,
    "seatPreference": None,
    "chosen": None,
    "history": [],
}


def context(**changes):
    built = copy.deepcopy(BASE)
    built.update(copy.deepcopy(changes))
    return built


RIDE = {"scene": "ride", "destination": "kyoto", "options": OPTIONS, "suggestion": SUGGESTION}
SEAT = {**RIDE, "scene": "seat", "chosen": {"option": "nozomi-7", "seat": None}, "seatPreference": {"position": "window", "exitRow": False}}


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    directory = tmp_path / "clientops-data"
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(directory))
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "fake")
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setattr(cost, "open_jobs", {})
    return directory


def load_node():
    return importlib.import_module("clientops.node")


def talk(said, state=None, *, client_id=CLIENT):
    node = load_node()

    async def run():
        execution = ExecutionContext(
            run_id="run_trip_talk",
            execution_id="exec_trip_talk",
            agent_instance=node.app,
            reasoner_name="trip_talk",
            agent_node_id=node.NODE_ID,
            registered=True,
        )
        token = set_execution_context(execution)
        try:
            return await node.trip_talk._original_func(client_id=client_id, said=said, context=context() if state is None else state)
        finally:
            reset_execution_context(token)

    return asyncio.run(run())


def with_claude(monkeypatch, claude):
    monkeypatch.setattr(load_node(), "claude_ai", lambda agent: claude)
    return claude


def claude_says(fake, **answer):
    fake.reply_text(json.dumps({"reply": "Sure.", "action": "none", "destination": None, "option": None, "seat": None, "speaker": "worker", **answer}))


def ledger(data_dir):
    folder = data_dir / COSTS_DIRECTORY
    if not folder.exists():
        return []
    return [json.loads(line) for path in sorted(folder.glob("*.jsonl")) for line in path.read_text().splitlines() if line.strip()]


def rules(said, state, calls=0):
    return {**route(said, read_context(state)).to_json(), "decidedBy": "rules", "claudeCalls": calls}


RULES_TABLE = [
    ("I need to see the client in Kyoto next week", {}, ("search", "kyoto", None, None)),
    ("seoul please", {}, ("search", "seoul", None, None)),
    ("hello", {}, ("ask", None, None, None)),
    ("the earlier one", RIDE, ("choose-ride", "kyoto", "nozomi-7", None)),
    ("something later", RIDE, ("choose-ride", "kyoto", "nozomi-21", None)),
    ("that one", RIDE, ("choose-ride", "kyoto", "nozomi-7", None)),
    ("nozomi 21", RIDE, ("choose-ride", "kyoto", "nozomi-21", None)),
    ("yes", RIDE, ("choose-ride", "kyoto", "nozomi-7", None)),
    ("14 c", SEAT, ("choose-seat", "kyoto", "nozomi-7", "14C")),
    ("a window please", SEAT, ("choose-seat", "kyoto", "nozomi-7", "12E")),
    ("ok book it", SEAT, ("show-ticket", "kyoto", "nozomi-7", "12E")),
    ("yes", {**SEAT, "scene": "hold", "chosen": {"option": "nozomi-21", "seat": "3A"}}, ("show-ticket", "kyoto", "nozomi-21", "3A")),
    ("hmm", {"destination": "kyoto"}, ("search", "kyoto", None, None)),
    ("what about lunch", SEAT, ("ask", None, None, None)),
]


@pytest.mark.parametrize(("said", "changes", "expected"), RULES_TABLE)
def test_the_rules_choose_one_step_from_keywords(said, changes, expected):
    decision = route(said, read_context(context(**changes)))
    assert (decision.action, decision.destination, decision.option, decision.seat) == expected
    assert decision.reply and len(decision.reply.split()) <= (40 if decision.action == "search" else 30)
    assert decision.speaker == (decision.destination if decision.action == "search" else "worker")
    assert "booked" not in decision.reply.lower()


def test_the_rules_ask_where_and_when_when_nothing_is_clear():
    assert route("um", read_context(context())).reply == ASK_WHERE


def test_the_show_ticket_reply_says_to_press_and_hold():
    assert "press and hold" in route("yes book it", read_context(context(**SEAT))).reply.lower()


def test_the_node_registers_trip_talk_as_a_reasoner():
    assert "trip_talk" in [reasoner["id"] for reasoner in load_node().app.reasoners]


BAD_CONTEXTS = [
    {**BASE, "traveller": "Aiko"},
    {key: value for key, value in BASE.items() if key != "history"},
    context(month="smarch"),
    context(scene="checkout"),
    context(destination="paris"),
    context(ranked=[{"destination": "kyoto", "score": 1, "reasons": ["mail aiko@example.com"]}]),
    context(ranked=[{"destination": "kyoto", "score": 1, "reasons": ["Login with LP_TRAVEL_USERNAME"]}]),
    context(ranked=[{"destination": "kyoto", "score": -1, "reasons": []}]),
    context(ranked=[{"destination": "kyoto", "score": 1, "reasons": [], "owner": "Aiko"}]),
    context(options=[{**OPTIONS[0], "id": "Bad Id"}]),
    context(options=[{**OPTIONS[0], "depart": "7am"}]),
    context(options=[{**OPTIONS[0], "passenger": "Aiko"}]),
    context(options=OPTIONS, suggestion={"option": "ghost-1", "seat": "12E", "reason": "window"}),
    context(options=OPTIONS, suggestion={"option": "nozomi-7", "seat": "row 12", "reason": "window"}),
    context(options=OPTIONS, chosen={"option": "nozomi-7", "seat": "99Z"}),
    context(seatPreference={"position": "window", "exitRow": "yes"}),
    context(history=[{"from": "you", "text": "my email is aiko@example.com"}]),
    context(history=[{"from": "you", "text": "my password is hunter2"}]),
    context(history=[{"from": "agent", "text": "hi"}]),
    context(history=[{"from": "you", "text": "hi"}] * 7),
    "kyoto",
]


@pytest.mark.parametrize("state", BAD_CONTEXTS)
def test_a_context_outside_the_contract_or_with_anything_personal_is_refused(monkeypatch, state):
    claude = with_claude(monkeypatch, PickingClaude())
    with pytest.raises(TripTalkInputError):
        talk("kyoto", state)
    assert claude.calls == 0


@pytest.mark.parametrize("said", ["send it to aiko@example.com", "x" * 501, 42])
def test_what_was_said_must_be_short_text_with_nothing_personal(monkeypatch, said):
    claude = with_claude(monkeypatch, PickingClaude())
    with pytest.raises(TripTalkInputError):
        talk(said)
    assert claude.calls == 0


@pytest.mark.parametrize(("said", "changes", "expected"), RULES_TABLE)
def test_the_runtime_fake_claude_answers_through_the_whole_claude_path(said, changes, expected):
    answer = talk(said, context(**changes))
    assert (answer["action"], answer["destination"], answer["option"], answer["seat"]) == expected
    assert (answer["decidedBy"], answer["claudeCalls"]) == ("claude", 1)


def test_each_claude_turn_is_billed_to_the_client(data_dir):
    talk("kyoto")
    entries = ledger(data_dir)
    assert [(entry["clientId"], entry["claudeCalls"]) for entry in entries] == [(CLIENT, 1)]


def test_a_valid_claude_answer_is_passed_on_as_claudes(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, reply="The Nozomi 7 gets you there before the leaves crowd in. Want it?", action="show-rides")
    answer = talk("which train", context(**RIDE))
    assert answer == {
        "reply": "The Nozomi 7 gets you there before the leaves crowd in. Want it?",
        "action": "show-rides",
        "destination": None,
        "option": None,
        "seat": None,
        "speaker": "worker",
        "decidedBy": "claude",
        "claudeCalls": 1,
    }


BROKEN_ANSWERS = [
    {"action": "choose-ride", "option": "ghost-9"},
    {"action": "choose-ride"},
    {"action": "search"},
    {"action": "fly-now"},
    {"action": "choose-seat", "option": "nozomi-7", "seat": "row twelve"},
    {"action": "show-ticket", "option": "nozomi-7", "seat": "12E", "reply": "Done, your seat is booked."},
    {"reply": " ".join(["word"] * 31)},
    {"reply": "- one\n- two"},
    {"reply": ""},
    {"destination": "paris"},
    {"action": "search", "destination": "kyoto", "reply": "Kyoto it is."},
    {"action": "search", "destination": "kyoto", "speaker": "seoul", "reply": "Annyeong!"},
    {"speaker": "kyoto", "reply": "Hello from Kyoto!"},
    {"speaker": "kyoto", "destination": "kyoto", "reply": " ".join(["word"] * 41)},
    {"speaker": "london", "destination": "kyoto"},
]


@pytest.mark.parametrize("broken", BROKEN_ANSWERS)
def test_an_answer_that_breaks_the_contract_falls_back_to_the_rules_and_counts_the_call(monkeypatch, broken):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, **broken)
    state = context(**RIDE)
    assert talk("that one", state) == rules("that one", state, calls=1)


def test_a_refusal_or_a_failure_falls_back_to_the_rules(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    fake.reply_refusal()
    assert talk("kyoto") == rules("kyoto", context(), calls=1)
    fake.fail_overloaded()
    assert talk("kyoto") == rules("kyoto", context(), calls=1)


def test_with_claude_turned_off_for_the_client_the_rules_decide(monkeypatch, data_dir):
    write_settings(data_dir, {CLIENT: {"useClaude": False}})
    claude = with_claude(monkeypatch, PickingClaude())
    assert talk("kyoto") == rules("kyoto", context())
    assert claude.calls == 0


def test_over_the_monthly_cap_claude_is_not_asked(monkeypatch, data_dir):
    write_settings(data_dir, {CLIENT: {"claudeMonthlyCapUsd": 0}})
    claude = with_claude(monkeypatch, PickingClaude())
    assert talk("kyoto") == rules("kyoto", context())
    assert claude.calls == 0
    assert ledger(data_dir) == []


def test_a_model_that_is_not_pinned_is_never_called_and_the_rules_answer(monkeypatch, data_dir):
    def refuse_the_model(agent):
        raise ModelNotAllowed("refused")

    monkeypatch.setattr(load_node(), "claude_ai", refuse_the_model)
    assert talk("kyoto") == rules("kyoto", context())
    assert ledger(data_dir) == []


def test_without_an_anthropic_key_the_rules_decide(monkeypatch):
    monkeypatch.setattr(load_node(), "claude_if_key", lambda: None)
    assert talk("kyoto") == rules("kyoto", context())


def test_an_unknown_client_changes_nothing_and_never_reaches_claude(monkeypatch):
    claude = with_claude(monkeypatch, PickingClaude())
    answer = talk("kyoto", client_id="nobody")
    assert (answer["action"], answer["decidedBy"], answer["claudeCalls"]) == ("none", "rules", 0)
    assert claude.calls == 0


def test_the_call_is_opus_5_5_at_low_effort_for_a_quick_reply_with_only_the_listed_ride_ids(monkeypatch):
    claude = with_claude(monkeypatch, PickingClaude())
    talk("that one", context(**RIDE))
    call = claude.fake.received[0]
    assert call.model == CLAUDE_MODEL
    assert CLAUDE_EFFORT == "medium"
    assert call.output_config["effort"] == "low"
    schema = call.output_config["format"]["schema"]
    assert call.output_config["format"]["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["option"]["anyOf"][0]["enum"] == ["nozomi-7", "nozomi-21"]
    assert schema["properties"]["action"]["enum"] == list(TRIP_TALK_ACTIONS)


def test_the_prompt_never_lets_claude_claim_a_booking_and_says_how_to_book():
    assert "press and hold the ticket to book" in SYSTEM_PROMPT
    assert "Never say the trip is booked" in SYSTEM_PROMPT
    assert set(TRIP_TALK_ACTIONS) >= {"ask", "search", "choose-ride", "choose-seat", "show-ticket", "none"}


def test_nothing_personal_is_sent_to_claude(monkeypatch):
    claude = with_claude(monkeypatch, PickingClaude())
    talk("ok book it", context(**SEAT, history=[{"from": "worker", "text": "Seat 12E is a window seat."}]))
    sent = claude.everything_sent()
    client = known_client(CLIENT)
    for secret in [*VAULT.values(), *VAULT.keys(), client.name, "LP_TRAVEL_USERNAME", "LP_TRAVEL_PASSWORD", "@"]:
        assert secret not in sent
    user = json.loads(claude.fake.received[0].user)
    assert set(user) == {"said", *BASE.keys()}


@pytest.mark.parametrize("city", ["kyoto", "seoul", "singapore"])
def test_the_rules_let_the_city_guide_introduce_the_city_and_say_the_worker_is_checking(city):
    decision = route(f"let's go to {city}", read_context(context()))
    assert (decision.action, decision.destination, decision.speaker, decision.reply) == ("search", city, city, GUIDE_INTROS[city])
    assert len(decision.reply.split()) <= 40
    assert decision.reply.endswith("checking trains and flights now.")


def test_every_other_rules_turn_is_spoken_by_the_worker():
    for said, changes in [("the earlier one", RIDE), ("ok book it", SEAT), ("hello", {})]:
        assert route(said, read_context(context(**changes))).speaker == "worker"


def test_claude_may_let_a_city_guide_answer_a_question_about_the_city_in_up_to_forty_words(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    intro = "Hello from Singapore! In November Marina Bay glows at night and the hawker centres are buzzing after rain. " + "Your worker is checking flights now."
    claude_says(fake, reply=intro, action="search", destination="singapore", speaker="singapore")
    answer = talk("what is singapore like in november")
    assert (answer["speaker"], answer["action"], answer["destination"], answer["reply"], answer["decidedBy"]) == ("singapore", "search", "singapore", intro, "claude")


def test_the_prompt_tells_claude_when_the_city_guide_speaks():
    assert "that city's guide" in SYSTEM_PROMPT and "forty words" in SYSTEM_PROMPT
