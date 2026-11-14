import asyncio
import importlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agentfield.execution_context import ExecutionContext, reset_execution_context, set_execution_context
from agentfield.types import AIConfig

from clientops import cost
from clientops.boundary import DATA_DIR_VARIABLE, known_clients, screen_request
from clientops.chat import SYSTEM_PROMPT, ChatInputError, route
from clientops.claude_call import (
    CHAT_ACTIONS,
    CHAT_REPLY_SCHEMA,
    CLARIFY_KINDS,
    CLAUDE_MAX_TOKENS,
    CLAUDE_MODEL,
    ModelNotAllowed,
    chat_reply_call,
    element_choice_call,
)
from clientops.cost import COSTS_DIRECTORY, month_of
from clientops.externals.fake_ai import FakeClaude
from clientops.externals.picking_ai import PickingClaude
from test_stock_sync import VAULT, write_settings

CONTRACTS = Path(__file__).parent / "contract"
FIXTURES = Path(__file__).parent / "fixtures"
RECORD_VARIABLE = "CLIENTOPS_RECORD_CONTRACTS"

CATALOG = [
    {"id": "last.result", "about": "What the last job did: rows written and checked, or why it stopped"},
    {"id": "last.changes", "about": "The products whose stock the last job changed, with the old and new counts"},
    {"id": "last.leftAlone", "about": "How many products the last job left alone, and why"},
    {"id": "last.steps", "about": "Who decided each step of the last job: saved steps, a search by name, Claude or you"},
    {"id": "last.timing", "about": "How long the last job took to get ready, compared with the first run"},
    {"id": "last.stepsChange", "about": "Whether a saved step changed in the last job: the old and new button, and the version"},
    {"id": "schedule", "about": "The daily run: whether it is on and at what time"},
    {"id": "last.cost", "about": "What Claude and the computer cost for the last job"},
    {"id": "last.ignored", "about": "Suspicious instructions on a page that the last job ignored"},
]
ALL_IDS = [fact["id"] for fact in CATALOG]
OTHER_CLIENT_WORDS = sorted({word for client in known_clients() if client.id != "kumo" for word in (client.name, client.short_name, client.nickname)})


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


def ask(message, facts=CATALOG, *, client_id="kumo", execution_id="exec_chat_1"):
    node = load_node()

    async def run():
        context = ExecutionContext(
            run_id=f"run_{execution_id}",
            execution_id=execution_id,
            agent_instance=node.app,
            reasoner_name="chat_reply",
            agent_node_id=node.NODE_ID,
            registered=True,
        )
        token = set_execution_context(context)
        try:
            return await node.chat_reply._original_func(client_id=client_id, message=message, facts=facts)
        finally:
            reset_execution_context(token)

    return asyncio.run(run())


def with_claude(monkeypatch, claude):
    monkeypatch.setattr(load_node(), "claude_ai", lambda agent: claude)
    return claude


def claude_says(fake, action, facts=(), clarify=None, **tokens):
    fake.reply_text(json.dumps({"action": action, "facts": list(facts), "clarify": clarify}), **tokens)


def reply(action, facts=(), clarify=None, *, decided_by="claude", calls=1, client_id="kumo"):
    return {"clientId": client_id, "action": action, "facts": list(facts), "clarify": clarify, "decidedBy": decided_by, "claudeCalls": calls}


def by_rules(message, facts=CATALOG, *, calls=0):
    decision = route(message, [fact["id"] for fact in facts])
    return reply(decision.action, decision.facts, decision.clarify, decided_by="rules", calls=calls)


def ledger(data_dir):
    folder = data_dir / COSTS_DIRECTORY
    if not folder.exists():
        return []
    return [json.loads(line) for path in sorted(folder.glob("*.jsonl")) for line in path.read_text().splitlines() if line.strip()]


def product_names():
    days = [json.loads(path.read_text()) for path in sorted(FIXTURES.glob("*-day*.json"))]
    return sorted({row["name"] for day in days for side in ("supplier", "store") for row in day[side]})


RULES_TABLE = [
    ("Undo the last sync", "undo", [], None),
    ("Put it back please", "undo", [], None),
    ("Revert what you saved", "undo", [], None),
    ("Can you undo what you did?", "undo", [], None),
    ("Run it again", "run", [], None),
    ("Sync today's stock.", "run", [], None),
    ("Can you do it again?", "run", [], None),
    ("Please sync again and tell me what changed", "run", [], None),
    ("Run it every day at 18:00", "schedule", ["schedule"], None),
    ("Turn off the daily run", "schedule", ["schedule"], None),
    ("What time does it run?", "schedule", ["schedule"], None),
    ("How many products changed?", "answer", ["last.changes", "last.leftAlone"], None),
    ("What changed?", "answer", ["last.changes", "last.leftAlone"], None),
    ("Which products did you leave alone?", "answer", ["last.changes", "last.leftAlone"], None),
    ("Why did it press a different button?", "answer", ["last.stepsChange"], None),
    ("Who decided each step?", "answer", ["last.steps"], None),
    ("How long did it take?", "answer", ["last.timing"], None),
    ("Was it slow?", "answer", ["last.timing"], None),
    ("How much did Claude cost?", "answer", ["last.cost"], None),
    ("Anything suspicious on the pages?", "answer", ["last.ignored"], None),
    ("Did it work?", "answer", ["last.result"], None),
    ("Status?", "answer", ["last.result"], None),
    ("Did it run?", "answer", ["last.result"], None),
    ("Did the undo work?", "answer", ["last.result"], None),
    ("What changed and how long did it take?", "answer", ["last.changes", "last.leftAlone", "last.timing"], None),
    ("How long did it take and what did it cost?", "answer", ["last.timing", "last.cost"], None),
    ("", "clarify", [], "what-to-do"),
    ("   ", "clarify", [], "what-to-do"),
    ("hello", "clarify", [], "what-to-do"),
    ("the other one", "clarify", [], "what-to-do"),
    ("Write a poem about coffee", "cannot", [], None),
    ("What can you do for me?", "cannot", [], None),
    ("Book a meeting with the supplier tomorrow", "cannot", [], None),
]


@pytest.mark.parametrize(("message", "action", "facts", "clarify"), RULES_TABLE)
def test_the_rules_router_decides_from_english_keywords(message, action, facts, clarify):
    decision = route(message, ALL_IDS)
    assert (decision.action, list(decision.facts), decision.clarify) == (action, facts, clarify)


@pytest.mark.parametrize(
    ("message", "present", "action", "facts", "clarify"),
    [
        ("What changed?", ["last.changes"], "answer", ["last.changes"], None),
        ("What changed?", ["last.leftAlone", "schedule"], "answer", ["last.leftAlone"], None),
        ("What changed?", [], "clarify", [], "which-job"),
        ("Why did it press a different button?", ["last.steps"], "answer", ["last.steps"], None),
        ("Who decided each step?", ["last.stepsChange"], "answer", ["last.stepsChange"], None),
        ("How long did it take?", ["last.result", "last.changes"], "clarify", [], "which-job"),
        ("How much did Claude cost?", ["schedule"], "clarify", [], "which-job"),
        ("Did it run?", [], "clarify", [], "which-job"),
        ("What changed and how long did it take?", ["last.timing"], "answer", ["last.timing"], None),
        ("Run it every day at 18:00", [], "schedule", [], None),
        ("Run it again", [], "run", [], None),
        ("Undo the last sync", [], "undo", [], None),
    ],
)
def test_the_rules_only_name_facts_the_conversation_has(message, present, action, facts, clarify):
    decision = route(message, present)
    assert (decision.action, list(decision.facts), decision.clarify) == (action, facts, clarify)


def test_the_chat_actions_and_clarify_kinds_are_the_ones_the_screen_knows():
    assert CHAT_ACTIONS == ("answer", "run", "undo", "schedule", "clarify", "cannot")
    assert CLARIFY_KINDS == ("which-job", "what-to-do")


def test_claude_can_only_choose_an_action_fact_ids_and_a_clarify_kind():
    assert CHAT_REPLY_SCHEMA == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["answer", "run", "undo", "schedule", "clarify", "cannot"]},
            "facts": {"type": "array", "items": {"type": "string"}},
            "clarify": {"anyOf": [{"type": "string", "enum": ["which-job", "what-to-do"]}, {"type": "null"}]},
        },
        "required": ["action", "facts", "clarify"],
        "additionalProperties": False,
    }
    for forbidden in ("reply", "text", "message", "value", "values", "url", "reasoning"):
        assert forbidden not in CHAT_REPLY_SCHEMA["properties"]


def test_the_chat_call_is_opus_5_5_at_medium_effort_with_the_chat_format():
    assert chat_reply_call("s", "u") == {
        "system": "s",
        "user": "u",
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": CHAT_REPLY_SCHEMA}},
    }


def test_the_node_registers_chat_reply_as_a_reasoner():
    assert "chat_reply" in [reasoner["id"] for reasoner in load_node().app.reasoners]


def test_a_valid_claude_answer_is_the_reply_and_is_marked_as_claudes(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"])

    assert ask("What changed?") == reply("answer", ["last.changes"])
    assert len(fake.received) == 1


def test_claude_may_ask_which_job_and_that_is_passed_on(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "clarify", [], "which-job")

    assert ask("And the one before that?") == reply("clarify", [], "which-job")


def test_claude_may_name_no_schedule_fact_when_the_conversation_has_none(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "schedule", [])

    assert ask("Stop the daily run", SHORT_CATALOG) == reply("schedule", [])


def test_the_reply_has_exactly_the_shape_the_screen_validates(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "schedule", ["schedule"])

    result = ask("Run it every day at 18:00")

    assert list(result) == ["clientId", "action", "facts", "clarify", "decidedBy", "claudeCalls"]
    assert isinstance(result["claudeCalls"], int) and not isinstance(result["claudeCalls"], bool)


INVALID_ANSWERS = {
    "not-json": "Show them the changes.",
    "a-written-reply": json.dumps({"action": "answer", "facts": ["last.changes"], "clarify": None, "reply": "Four products changed."}),
    "a-missing-key": json.dumps({"action": "answer", "facts": ["last.changes"]}),
    "an-unknown-fact": json.dumps({"action": "answer", "facts": ["last.prices"], "clarify": None}),
    "a-fact-the-conversation-lacks": json.dumps({"action": "answer", "facts": ["last.cost"], "clarify": None}),
    "an-unknown-action": json.dumps({"action": "delete", "facts": [], "clarify": None}),
    "an-action-in-capitals": json.dumps({"action": "ANSWER", "facts": ["last.changes"], "clarify": None}),
    "clarify-on-an-answer": json.dumps({"action": "answer", "facts": ["last.changes"], "clarify": "which-job"}),
    "clarify-without-a-kind": json.dumps({"action": "clarify", "facts": [], "clarify": None}),
    "an-unknown-clarify-kind": json.dumps({"action": "clarify", "facts": [], "clarify": "which-client"}),
    "an-answer-without-facts": json.dumps({"action": "answer", "facts": [], "clarify": None}),
    "cannot-with-facts": json.dumps({"action": "cannot", "facts": ["last.changes"], "clarify": None}),
    "clarify-with-facts": json.dumps({"action": "clarify", "facts": ["last.changes"], "clarify": "which-job"}),
    "run-with-facts": json.dumps({"action": "run", "facts": ["last.result"], "clarify": None}),
    "undo-with-facts": json.dumps({"action": "undo", "facts": ["last.changes"], "clarify": None}),
    "schedule-with-another-fact": json.dumps({"action": "schedule", "facts": ["last.result"], "clarify": None}),
    "a-fact-twice": json.dumps({"action": "answer", "facts": ["last.changes", "last.changes"], "clarify": None}),
    "facts-as-text": json.dumps({"action": "answer", "facts": "last.changes", "clarify": None}),
    "a-fact-that-is-a-number": json.dumps({"action": "answer", "facts": [4], "clarify": None}),
    "a-list": json.dumps(["answer", ["last.changes"], None]),
}
SHORT_CATALOG = [fact for fact in CATALOG if fact["id"] in ("last.result", "last.changes", "last.leftAlone")]


@pytest.mark.parametrize("case", sorted(INVALID_ANSWERS))
def test_an_answer_that_breaks_the_contract_falls_back_to_the_rules_and_still_counts_the_call(monkeypatch, data_dir, case):
    fake = with_claude(monkeypatch, FakeClaude())
    fake.reply_text(INVALID_ANSWERS[case], input_tokens=800, output_tokens=200)

    result = ask("What changed?", SHORT_CATALOG)

    assert result == by_rules("What changed?", SHORT_CATALOG, calls=1)
    assert result["facts"] == ["last.changes", "last.leftAlone"]
    assert [(entry["claudeCalls"], entry["claudeUsd"]) for entry in ledger(data_dir)] == [(1, 0.0072)]


def test_a_refusal_falls_back_to_the_rules_and_costs_nothing(monkeypatch, data_dir):
    fake = with_claude(monkeypatch, FakeClaude())
    fake.reply_refusal("cyber")

    assert ask("What changed?") == by_rules("What changed?", calls=1)
    assert [(entry["claudeCalls"], entry["claudeUsd"]) for entry in ledger(data_dir)] == [(1, 0.0)]


def test_a_cut_off_answer_falls_back_to_the_rules(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    fake.reply_cut_off('{"action":"answer","facts":["last.chan')

    assert ask("What changed?") == by_rules("What changed?", calls=1)


@pytest.mark.parametrize("failure", ["fail_overloaded", "fail_rate_limited", "fail_unauthorized"])
def test_when_claude_cannot_be_reached_the_rules_answer_and_the_try_is_counted_at_no_cost(monkeypatch, data_dir, failure):
    fake = with_claude(monkeypatch, FakeClaude())
    getattr(fake, failure)()

    assert ask("Run it again") == by_rules("Run it again", calls=1)
    assert [(entry["claudeCalls"], entry["claudeUsd"]) for entry in ledger(data_dir)] == [(1, 0.0)]


def test_over_the_monthly_cap_claude_is_not_asked_and_nothing_is_billed(monkeypatch, data_dir):
    write_settings(data_dir, {"kumo": {"claudeMonthlyCapUsd": 0}})
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"])

    assert ask("What changed?") == by_rules("What changed?")
    assert fake.received == []
    assert ledger(data_dir) == []


def test_chat_spending_counts_against_the_cap_so_the_next_message_goes_to_the_rules(monkeypatch, data_dir):
    write_settings(data_dir, {"kumo": {"claudeMonthlyCapUsd": 0.06}})
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"])
    claude_says(fake, "answer", ["last.timing"])

    first = ask("What changed?", execution_id="exec_chat_1")
    second = ask("How long did it take?", execution_id="exec_chat_2")

    assert first == reply("answer", ["last.changes"])
    assert second == by_rules("How long did it take?")
    assert len(fake.received) == 1
    assert [(entry["jobId"], entry["claudeUsd"]) for entry in ledger(data_dir)] == [("exec_chat_1", 0.0336)]


def test_when_this_months_spending_cannot_be_read_claude_is_not_asked(monkeypatch, data_dir):
    folder = data_dir / COSTS_DIRECTORY
    folder.mkdir(parents=True)
    (folder / f"{month_of(datetime.now(UTC))}.jsonl").write_text("not json\n")
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"])

    assert ask("What changed?") == by_rules("What changed?")
    assert fake.received == []


def test_a_cap_setting_that_cannot_be_read_keeps_claude_out(monkeypatch, data_dir):
    write_settings(data_dir, {"kumo": {"claudeMonthlyCapUsd": "ten"}})
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"])

    assert ask("What changed?") == by_rules("What changed?")
    assert fake.received == []


def test_with_claude_turned_off_for_the_client_the_rules_decide_and_claude_is_never_asked(monkeypatch, data_dir):
    write_settings(data_dir, {"kumo": {"useClaude": False}})
    fake = with_claude(monkeypatch, FakeClaude())

    for message, _, _, _ in RULES_TABLE:
        assert ask(message) == by_rules(message)
    assert fake.received == []
    assert ledger(data_dir) == []


def test_another_clients_switch_does_not_turn_claude_off_for_this_one(monkeypatch, data_dir):
    write_settings(data_dir, {"hinata": {"useClaude": False}})
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "run")

    assert ask("Run it again") == reply("run")


def test_settings_that_cannot_be_read_turn_claude_off(monkeypatch, data_dir):
    data_dir.mkdir(parents=True)
    (data_dir / "settings.json").write_text("{")
    fake = with_claude(monkeypatch, FakeClaude())

    assert ask("Run it again") == by_rules("Run it again")
    assert fake.received == []


def test_without_an_anthropic_key_the_rules_decide(monkeypatch, data_dir):
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "real")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_BASE"):
        monkeypatch.delenv(name, raising=False)

    assert ask("What changed?") == by_rules("What changed?")
    assert ledger(data_dir) == []


def refuse_the_model(agent):
    raise ModelNotAllowed("ClientOps only calls claude-opus-5-5, so the model 'gpt-x' is refused.")


def test_a_refused_model_setting_is_never_called_and_the_reply_is_cannot_with_nothing_recorded(monkeypatch, data_dir):
    monkeypatch.setattr(load_node(), "claude_ai", refuse_the_model)

    assert ask("What changed?") == reply("cannot", decided_by="rules", calls=0)
    assert ask("Run it again") == reply("cannot", decided_by="rules", calls=0)
    assert ledger(data_dir) == []


def test_an_agent_set_to_another_model_answers_cannot_before_any_call(monkeypatch, data_dir):
    node = load_node()
    monkeypatch.setattr(node.app, "ai_config", AIConfig(model="openai/gpt-x"))
    picking = PickingClaude()
    monkeypatch.setattr("clientops.externals.picking_ai.PickingClaude", lambda: picking)

    assert ask("What changed?") == reply("cannot", decided_by="rules", calls=0)
    assert picking.calls == 0
    assert ledger(data_dir) == []


def test_a_model_refusal_raised_by_the_call_itself_is_not_swallowed_into_the_rules(monkeypatch, data_dir):
    async def refusing_call(**call):
        raise ModelNotAllowed("refused")

    with_claude(monkeypatch, refusing_call)

    assert ask("What changed?") == reply("cannot", decided_by="rules", calls=0)
    assert ledger(data_dir) == []


def test_a_blank_message_asks_what_to_do_without_calling_claude(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())

    assert ask("  ") == reply("clarify", [], "what-to-do", decided_by="rules", calls=0)
    assert fake.received == []


def test_claude_sees_only_the_message_and_the_fact_catalog(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"])

    ask("What changed in the last sync?")

    [call] = fake.received
    assert call.args == ()
    assert call.system == SYSTEM_PROMPT
    assert json.loads(call.user) == {"message": "What changed in the last sync?", "facts": CATALOG}
    assert call.model == CLAUDE_MODEL
    assert call.max_tokens == CLAUDE_MAX_TOKENS
    assert call.output_config == {"effort": "medium", "format": {"type": "json_schema", "schema": CHAT_REPLY_SCHEMA}}
    assert call.extra == {}


def test_the_prompt_keeps_claude_on_this_one_client_and_makes_another_company_cannot():
    assert "This conversation is about that one client only: its stock, its supplier's portal and its store. " in SYSTEM_PROMPT
    assert "cannot: the message asks for something this worker does not do, or asks about another company or client. " in SYSTEM_PROMPT


def test_nothing_sent_to_claude_carries_a_number_the_person_did_not_type(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"])
    claude_says(fake, "schedule", ["schedule"])

    ask("What changed?", execution_id="exec_chat_1")
    assert re.findall(r"\d+", fake.everything_sent()) == []

    ask("Run it every day at 18", execution_id="exec_chat_2")
    assert re.findall(r"\d+", fake.everything_sent()) == ["18"]


def test_nothing_sent_to_claude_names_another_client_a_product_or_a_login(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    for message, _, _, _ in RULES_TABLE:
        if message.strip():
            claude_says(fake, "cannot")
            ask(message)

    sent = fake.everything_sent()
    assert len(fake.received) == len([message for message, _, _, _ in RULES_TABLE if message.strip()])
    for word in [*OTHER_CLIENT_WORDS, *product_names(), *VAULT, *VAULT.values(), "maruyama", "kumo-coffee.example"]:
        assert word.lower() not in sent.lower()


@pytest.mark.parametrize(
    "message",
    [
        "Use Hinata's login for the store.",
        "Sign in with Hinata Skincare's account",
        "Open admin.hinata-skincare.example and sync it",
    ],
)
def test_a_request_for_another_clients_things_goes_on_as_a_job_for_the_boundary_to_refuse_and_never_reaches_claude(monkeypatch, data_dir, message):
    fake = with_claude(monkeypatch, FakeClaude())

    result = ask(message)

    assert screen_request("kumo", message) is not None
    assert result == reply("run", decided_by="rules", calls=0)
    assert fake.received == []
    assert ledger(data_dir) == []
    for word in OTHER_CLIENT_WORDS:
        assert word.lower() not in json.dumps(result).lower()


@pytest.mark.parametrize("message", ["Do the same as you did for Hinata", "How is Sora Outdoor doing?"])
def test_a_message_that_only_names_another_client_is_cannot_by_the_rules_and_never_reaches_claude(monkeypatch, data_dir, message):
    fake = with_claude(monkeypatch, FakeClaude())

    result = ask(message)

    assert screen_request("kumo", message) is None
    assert result == reply("cannot", decided_by="rules", calls=0)
    assert fake.received == []
    assert ledger(data_dir) == []
    for word in OTHER_CLIENT_WORDS:
        assert word.lower() not in json.dumps(result).lower()


def test_the_clients_own_name_is_fine(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "run")

    assert ask("Run Kumo Coffee's sync again") == reply("run")


def test_an_unknown_client_gets_cannot_without_naming_the_known_ones(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())

    result = ask("What changed?", client_id="acme")

    assert result == reply("cannot", decided_by="rules", calls=0, client_id="acme")
    assert fake.received == []


def test_a_client_that_is_not_set_up_can_still_chat(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "clarify", [], "which-job")

    assert ask("What changed?", [], client_id="sora") == reply("clarify", [], "which-job", client_id="sora")


@pytest.mark.parametrize(
    ("message", "facts"),
    [
        ("x" * 2001, CATALOG),
        (None, CATALOG),
        (["What changed?"], CATALOG),
        ("What changed?", {"last.changes": "changes"}),
        ("What changed?", [{"id": "last.changes"}]),
        ("What changed?", [{"id": "last.changes", "about": "Changed products", "value": "4"}]),
        ("What changed?", [{"id": "last.changes", "about": "Changes: 4 products"}]),
        ("What changed?", [{"id": "last.changes", "about": "Changes: ４ products"}]),
        ("What changed?", [{"id": "last.changes", "about": "Hinata Skincare's changes"}]),
        ("What changed?", [{"id": "last changes", "about": "Changed products"}]),
        ("What changed?", [{"id": "step2", "about": "The second step"}]),
        ("What changed?", [{"id": "last.changes", "about": "  "}]),
        ("What changed?", [{"id": "last.changes", "about": "a" * 201}]),
        ("What changed?", [{"id": "last.changes", "about": "Changed products"}, {"id": "last.changes", "about": "Changed products again"}]),
        ("What changed?", [{"id": f"fact.{'a' * index}", "about": "Something"} for index in range(1, 34)]),
    ],
)
def test_input_outside_the_contract_is_refused_before_anything_is_decided(monkeypatch, message, facts):
    fake = with_claude(monkeypatch, FakeClaude())

    with pytest.raises(ChatInputError):
        ask(message, facts)
    assert fake.received == []


def test_a_message_of_exactly_two_thousand_characters_is_taken(monkeypatch):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "cannot")

    assert ask("y" * 2000) == reply("cannot")


def test_each_claude_call_is_billed_once_to_the_client_at_its_real_price(monkeypatch, data_dir):
    fake = with_claude(monkeypatch, FakeClaude())
    claude_says(fake, "answer", ["last.changes"], input_tokens=800, output_tokens=200)
    claude_says(fake, "run", input_tokens=800, output_tokens=200)

    ask("What changed?", execution_id="exec_chat_1")
    ask("Run it again", execution_id="exec_chat_2")
    write_settings(data_dir, {"kumo": {"useClaude": False}})
    ask("Undo the last sync", execution_id="exec_chat_3")

    assert [
        (entry["jobId"], entry["clientId"], entry["claudeCalls"], entry["inputTokens"], entry["outputTokens"], entry["claudeUsd"], entry["computerSeconds"])
        for entry in ledger(data_dir)
    ] == [("exec_chat_1", "kumo", 1, 800, 200, 0.0072, 0), ("exec_chat_2", "kumo", 1, 800, 200, 0.0072, 0)]


def test_the_runtime_fake_claude_answers_chat_through_the_whole_claude_path(data_dir):
    for message, _, _, _ in RULES_TABLE:
        if not message.strip():
            continue
        decision = route(message, ALL_IDS)
        assert ask(message) == reply(decision.action, decision.facts, decision.clarify)

    entries = ledger(data_dir)
    assert len(entries) == len([message for message, _, _, _ in RULES_TABLE if message.strip()])
    assert {(entry["claudeCalls"], entry["inputTokens"], entry["outputTokens"], entry["claudeUsd"]) for entry in entries} == {(1, 900, 300, 0.0096)}


def test_the_runtime_fake_claude_still_picks_elements_by_name():
    picking = PickingClaude()
    user = "\n".join(
        [
            "Step goal: Click the button that sends the sign-in form.",
            "This step needs one element to click. The element must be one of the elements in the list.",
            "Elements on the page, one JSON object per line:",
            '{"id":"a","role":"textbox","name":"Account ID","form":"login"}',
            '{"id":"b","role":"button","name":"Sign in","form":"login"}',
        ]
    )
    response = asyncio.run(picking(**element_choice_call("s", user)))

    assert json.loads(response.text) == {"element_id": "b", "action": "click", "confident": True}


CONTRACT_EXAMPLES = [
    ("answer-what-changed", {}, "What changed?", CATALOG),
    ("answer-why-the-button-changed", {}, "Why did it press a different button?", CATALOG),
    ("answer-two-questions-in-the-order-asked", {}, "What changed and how long did it take?", CATALOG),
    ("run-again", {}, "Run it again", CATALOG),
    ("undo", {}, "Undo the last sync", CATALOG),
    ("schedule", {}, "Run it every day at 18:00", CATALOG),
    ("clarify-which-job", {}, "What changed?", []),
    ("clarify-what-to-do", {}, "hello", CATALOG),
    ("cannot-unrelated", {}, "Write a poem about coffee", CATALOG),
    ("run-another-clients-login-so-the-boundary-refuses-it", {}, "Use Hinata's login for the store.", CATALOG),
    ("cannot-another-client-named", {}, "How is Sora Outdoor doing?", CATALOG),
    ("rules-when-claude-is-off", {"useClaude": False}, "What changed?", CATALOG),
    ("rules-over-the-monthly-cap", {"claudeMonthlyCapUsd": 0}, "How long did it take?", CATALOG),
]


def produce_contract(data_dir) -> dict:
    examples = []
    for index, (name, settings, message, facts) in enumerate(CONTRACT_EXAMPLES, start=1):
        write_settings(data_dir, {"kumo": settings})
        example = {"name": name}
        if settings:
            example["settings"] = settings
        example["input"] = {"client_id": "kumo", "message": message, "facts": facts}
        example["output"] = ask(message, facts, execution_id=f"exec_chat_{index}")
        examples.append(example)
    return {"reasoner": "clientops.chat_reply", "examples": examples}


def test_the_chat_replies_are_exactly_the_contract_the_screen_reads(data_dir):
    produced = produce_contract(data_dir)
    path = CONTRACTS / "chat_reply.json"
    if os.environ.get(RECORD_VARIABLE) == "1":
        path.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(path.read_text())


def test_the_contract_covers_every_action_both_deciders_and_both_clarify_kinds():
    examples = json.loads((CONTRACTS / "chat_reply.json").read_text())["examples"]
    outputs = [example["output"] for example in examples]
    assert {output["action"] for output in outputs} == set(CHAT_ACTIONS)
    assert {output["decidedBy"] for output in outputs} == {"claude", "rules"}
    assert {output["clarify"] for output in outputs} == {None, *CLARIFY_KINDS}
    for example in examples:
        given = [fact["id"] for fact in example["input"]["facts"]]
        assert set(example["output"]["facts"]) <= set(given)
        assert re.search(r"\d", json.dumps(example["input"]["facts"])) is None
