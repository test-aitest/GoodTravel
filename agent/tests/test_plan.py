import asyncio
import json
import re
from itertools import count
from pathlib import Path

import pytest

from clientops.boundary import known_clients
from clientops.claude_call import CLAUDE_MAX_TOKENS, CLAUDE_MODEL, ELEMENT_CHOICE_SCHEMA
from clientops.externals.fake_ai import FakeClaude
from clientops.page import Element, Locator, PageInfo, Tree
from clientops.plan import (
    SYSTEM_PROMPT,
    Absent,
    Choice,
    ElementChooser,
    Goal,
    HandOff,
    HandOffReason,
    prompt_view,
)
from runner_replay import recorded_output

FIXTURES = Path(__file__).parent / "fixtures"
KUMO = json.loads((FIXTURES / "kumo-day1.json").read_text())
SUPPLIER_LOGIN = Tree.from_result(recorded_output(1))
SUPPLIER_STOCK = Tree.from_result(recorded_output(5))
STORE_INVENTORY = Tree.from_result(recorded_output(12))
LOGIN_VALUES = ("kumo-roasters", "kumo-supply-demo", "kumo-admin", "kumo-store-demo")

ACCOUNT_ID = Goal("Sign in to the supplier portal: the field for the account ID or username.", "fill")
PASSWORD = Goal("Sign in to the supplier portal: the password field.", "fill")
SIGN_IN = Goal("Sign in to the supplier portal: the button that sends the sign-in form.", "click")
NEXT_PAGE = Goal("Read the stock table across all its pages: the link or button that goes to the next page of the table.", "click")
SAVE = Goal("Save the stock form: the button that saves the changes in this form for this store only.", "click")
OPTIONAL_NEXT = Goal(NEXT_PAGE.text, "click", optional=True)


@pytest.fixture(autouse=True)
def offline_prices(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def client_names() -> set[str]:
    return {text for client in known_clients() for text in (client.name, client.short_name, client.nickname)}


def known_values() -> tuple[str, ...]:
    return (*client_names(), "Maruyama Supply")


def quarter_second_clock():
    ticks = count()
    return lambda: next(ticks) * 0.25


def chooser(fake=None, *, use_claude=True) -> ElementChooser:
    return ElementChooser(fake, use_claude=use_claude, clock=quarter_second_clock())


def choose(chooser_, tree: Tree, goal: Goal):
    return asyncio.run(chooser_.choose(tree, goal, known_values()))


def id_of(tree: Tree, role: str, name: str) -> str:
    view = prompt_view(tree, known_values())
    [found] = [entry["id"] for entry, element in zip(view, tree.elements) if element.role == role and element.name == name]
    return found


def tree_of(*elements: Element) -> Tree:
    return Tree(page=PageInfo(url="http://127.0.0.1:3002/inventory", title="Inventory"), mode="interactive", text="", elements=elements, cells=())


def test_each_element_gets_a_short_letter_id_with_no_digits_and_no_browser_numbers():
    view = prompt_view(STORE_INVENTORY, known_values())
    ids = [entry["id"] for entry in view]
    assert len(ids) == len(STORE_INVENTORY.elements) == 45
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[a-z]+", element_id) for element_id in ids)
    assert ids[:3] == ["a", "b", "c"]
    assert ids[26:28] == ["aa", "ab"]
    assert all(set(entry) == {"id", "role", "name", "form"} for entry in view)


def test_the_call_is_the_agreed_claude_call_with_the_goal_and_the_masked_elements():
    fake = FakeClaude()
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "textbox", "Account ID"), "fill")

    choose(chooser(fake), SUPPLIER_LOGIN, ACCOUNT_ID)

    [call] = fake.received
    assert call.model == CLAUDE_MODEL
    assert call.max_tokens == CLAUDE_MAX_TOKENS
    assert call.output_config == {"effort": "medium", "format": {"type": "json_schema", "schema": ELEMENT_CHOICE_SCHEMA}}
    assert call.system == SYSTEM_PROMPT
    assert ACCOUNT_ID.text in call.user
    lines = [json.loads(line) for line in call.user.splitlines() if line.startswith("{")]
    assert lines == prompt_view(SUPPLIER_LOGIN, known_values())
    assert {"id": "a", "role": "textbox", "name": "Account ID", "form": "/login"} in lines


def test_a_good_answer_names_the_element_on_the_page_and_how_to_find_it_again():
    fake = FakeClaude()
    fake.reply_choice(id_of(STORE_INVENTORY, "button", "Save changes"), "click", input_tokens=1800, output_tokens=400)

    result = choose(chooser(fake), STORE_INVENTORY, SAVE)

    assert isinstance(result, Choice)
    assert result.element.ref == 1016
    assert result.action == "click"
    assert result.locator == Locator("button", "Save changes", "/inventory")
    [call] = result.calls
    assert call.outcome == "chosen"
    assert call.took_ms == 250
    assert call.usage["prompt_tokens"] == 1800
    assert call.usage["completion_tokens"] == 400
    assert call.cost_usd == pytest.approx((1800 * 4 + 400 * 20) / 1e6)


@pytest.mark.parametrize("action", ["FILL", "Fill", " fill "])
def test_the_action_is_compared_without_caring_about_letter_case(action):
    fake = FakeClaude()
    fake.reply_text(json.dumps({"element_id": id_of(SUPPLIER_LOGIN, "textbox", "Password"), "action": action, "confident": True}))
    result = choose(chooser(fake), SUPPLIER_LOGIN, PASSWORD)
    assert isinstance(result, Choice)
    assert result.element.ref == 18
    assert result.action == "fill"


def test_nothing_sent_to_claude_carries_a_number_a_sku_a_product_a_client_or_a_login():
    fake = FakeClaude()
    pages = [
        (SUPPLIER_LOGIN, ACCOUNT_ID, ("textbox", "Account ID")),
        (SUPPLIER_LOGIN, PASSWORD, ("textbox", "Password")),
        (SUPPLIER_LOGIN, SIGN_IN, ("button", "Sign in")),
        (SUPPLIER_STOCK, NEXT_PAGE, ("link", "Next")),
        (STORE_INVENTORY, SAVE, ("button", "Save changes")),
    ]
    filled_in = tree_of(
        Element(ref=15, role="textbox", name="Account ID", form="/login", value="kumo-roasters"),
        Element(ref=18, role="textbox", name="Password", form="/login", value="kumo-supply-demo"),
        Element(ref=19, role="button", name="Sign in", form="/login"),
        Element(ref=20, role="link", name="Kumo Coffee Roasters storefront", form=None),
        Element(ref=21, role="link", name="Questions? Call 0120-555-0199 or mail help@kumo-coffee.example", form=None),
    )
    pages.append((filled_in, SIGN_IN, ("button", "Sign in")))
    for tree, goal, (role, name) in pages:
        fake.reply_choice(id_of(tree, role, name), goal.action)
        assert isinstance(choose(chooser(fake), tree, goal), Choice)

    sent = fake.everything_sent()
    assert len(fake.received) == len(pages)
    assert not re.search(r"\d", sent)
    secrets = [*LOGIN_VALUES, *client_names(), "Maruyama", "help@", "kumo-coffee.example"]
    for row in (*KUMO["supplier"], *KUMO["store"]):
        secrets += [row["sku"], row["name"]]
    lowered = sent.lower()
    leaked = [secret for secret in secrets if secret.lower() in lowered]
    assert leaked == []


@pytest.mark.parametrize(
    ("reply", "problem"),
    [
        ({"element_id": "zz", "action": "click", "confident": True}, "unknown-element"),
        ({"element_id": "http://evil.example/steal", "action": "click", "confident": True}, "unknown-element"),
        ({"element_id": "19", "action": "click", "confident": True}, "unknown-element"),
        ({"element_id": "c", "action": "click", "confident": True, "url": "http://evil.example"}, "not-json"),
        ({"element_id": "c", "action": "click", "confident": True, "text": "hunter2"}, "not-json"),
        ({"element_id": "c", "action": "click"}, "not-json"),
        ({"element_id": "c", "action": "submit", "confident": True}, "not-json"),
        ({"element_id": "c", "action": "click", "confident": "yes"}, "not-json"),
        ({"element_id": ["c"], "action": "click", "confident": True}, "not-json"),
        ({"element_id": "c", "action": "fill", "confident": True}, "wrong-action"),
        ({"element_id": "a", "action": "click", "confident": True}, "role-mismatch"),
        ("I would press the Sign in button.", "not-json"),
        ("[]", "not-json"),
    ],
)
def test_an_answer_that_cannot_be_used_is_asked_once_more_and_the_second_answer_is_used(reply, problem):
    fake = FakeClaude()
    fake.reply_text(reply if isinstance(reply, str) else json.dumps(reply))
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click")

    result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, Choice)
    assert result.element.ref == 19
    assert [call.outcome for call in result.calls] == [problem, "chosen"]
    first, second = fake.received
    assert first.user != second.user
    assert "previous answer could not be used" in second.user
    assert first.system == second.system == SYSTEM_PROMPT


def test_a_refusal_a_cut_off_reply_and_an_error_count_as_unusable_answers():
    for script, problem in (
        (lambda fake: fake.reply_refusal("cyber"), "declined"),
        (lambda fake: fake.reply_cut_off('{"element_id": "c'), "cut-off"),
        (lambda fake: fake.fail_rate_limited(), "unavailable"),
        (lambda fake: fake.fail_overloaded(), "unavailable"),
        (lambda fake: fake.fail_unauthorized(), "unavailable"),
    ):
        fake = FakeClaude()
        script(fake)
        fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click")
        result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)
        assert isinstance(result, Choice), problem
        assert [call.outcome for call in result.calls] == [problem, "chosen"]


def test_two_unusable_answers_hand_the_step_to_a_person_without_a_third_call():
    fake = FakeClaude()
    fake.reply_choice("zz", "click")
    fake.reply_text("Sorry, I cannot tell.")
    fake.reply_choice("c", "click")

    result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.NO_USABLE_ANSWER
    assert result.goal == SIGN_IN
    assert [call.outcome for call in result.calls] == ["unknown-element", "not-json"]
    assert len(fake.received) == 2
    assert fake.pending == 1


def test_an_answer_that_is_not_confident_goes_straight_to_a_person():
    fake = FakeClaude()
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click", confident=False)
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click")

    result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.UNSURE
    assert [call.outcome for call in result.calls] == ["unsure"]
    assert fake.pending == 1


def test_an_unsure_second_answer_also_goes_to_a_person():
    fake = FakeClaude()
    fake.reply_text("no idea")
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click", confident=False)

    result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.UNSURE
    assert [call.outcome for call in result.calls] == ["not-json", "unsure"]


def test_with_claude_turned_off_nothing_is_sent_and_the_step_goes_to_a_person():
    fake = FakeClaude()
    fake.reply_choice("c", "click")

    result = choose(chooser(fake, use_claude=False), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.CLAUDE_OFF
    assert result.calls == ()
    assert fake.received == []


def test_turning_claude_off_needs_no_claude_at_all():
    result = choose(chooser(None, use_claude=False), SUPPLIER_LOGIN, SIGN_IN)
    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.CLAUDE_OFF


def test_claude_on_without_a_way_to_call_it_is_a_programming_error():
    with pytest.raises(ValueError):
        ElementChooser(None, use_claude=True)


@pytest.mark.parametrize("reason", list(HandOffReason))
def test_what_the_person_is_told_is_short_plain_and_says_nothing_was_changed(reason):
    hand_off = HandOff(goal=SIGN_IN, reason=reason, calls=())
    message = hand_off.message
    sentences = [part for part in re.split(r"(?<=[.!?])\s+", message.strip()) if part]
    assert 1 <= len(sentences) <= 2
    assert message.endswith("Nothing was changed.")
    assert not re.search(r"\d", message)
    assert "element" not in message.lower()
    assert "json" not in message.lower()


def test_each_reason_is_told_apart():
    messages = {HandOff(goal=SIGN_IN, reason=reason, calls=()).message for reason in HandOffReason}
    assert len(messages) == len(HandOffReason)


def test_a_call_record_is_plain_data_for_the_cost_report():
    fake = FakeClaude()
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "textbox", "Account ID"), "fill", input_tokens=900, output_tokens=300)
    result = choose(chooser(fake), SUPPLIER_LOGIN, ACCOUNT_ID)
    [call] = result.calls
    record = call.to_json()
    assert record == {
        "outcome": "chosen",
        "tookMs": 250,
        "usage": {"prompt_tokens": 900, "completion_tokens": 300, "total_tokens": 1200, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        "costUsd": pytest.approx((900 * 4 + 300 * 20) / 1e6),
    }
    json.dumps(record)


def test_goals_need_their_element_unless_they_say_it_may_be_missing():
    assert SIGN_IN.optional is False
    assert OPTIONAL_NEXT.optional is True


def test_an_optional_element_that_is_not_on_the_page_is_a_confident_none():
    fake = FakeClaude()
    fake.reply_none(input_tokens=700, output_tokens=200)

    result = choose(chooser(fake), SUPPLIER_LOGIN, OPTIONAL_NEXT)

    assert isinstance(result, Absent)
    assert result.goal == OPTIONAL_NEXT
    [call] = result.calls
    assert call.outcome == "none"
    assert call.usage["prompt_tokens"] == 700


def test_an_optional_element_that_is_there_is_still_chosen():
    fake = FakeClaude()
    fake.reply_choice(id_of(SUPPLIER_STOCK, "link", "Next"), "click")
    result = choose(chooser(fake), SUPPLIER_STOCK, OPTIONAL_NEXT)
    assert isinstance(result, Choice)
    assert result.locator == Locator("link", "Next", None)


def test_a_none_claude_is_not_sure_about_still_goes_to_a_person():
    fake = FakeClaude()
    fake.reply_none(confident=False)
    result = choose(chooser(fake), SUPPLIER_LOGIN, OPTIONAL_NEXT)
    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.UNSURE
    assert [call.outcome for call in result.calls] == ["unsure"]


def test_none_for_an_element_the_step_needs_is_asked_once_more():
    fake = FakeClaude()
    fake.reply_none()
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click")

    result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, Choice)
    assert [call.outcome for call in result.calls] == ["required-element", "chosen"]
    assert "previous answer could not be used" in fake.received[1].user


def test_none_twice_for_an_element_the_step_needs_goes_to_a_person():
    fake = FakeClaude()
    fake.reply_none()
    fake.reply_none()

    result = choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    assert isinstance(result, HandOff)
    assert result.reason is HandOffReason.NO_USABLE_ANSWER
    assert [call.outcome for call in result.calls] == ["required-element", "required-element"]


@pytest.mark.parametrize(
    "reply",
    [
        {"element_id": None, "action": "click", "confident": True},
        {"element_id": None, "action": "fill", "confident": True},
        {"element_id": "c", "action": "none", "confident": True},
        {"element_id": "", "action": "none", "confident": True},
    ],
)
def test_nothing_and_an_element_cannot_be_mixed(reply):
    fake = FakeClaude()
    fake.reply_text(json.dumps(reply))
    fake.reply_none()

    result = choose(chooser(fake), SUPPLIER_STOCK, OPTIONAL_NEXT)

    assert isinstance(result, Absent)
    assert [call.outcome for call in result.calls] == ["not-json", "none"]


def test_claude_is_told_it_may_answer_none_only_when_the_step_allows_it():
    fake = FakeClaude()
    fake.reply_none()
    fake.reply_choice(id_of(SUPPLIER_LOGIN, "button", "Sign in"), "click")

    choose(chooser(fake), SUPPLIER_LOGIN, OPTIONAL_NEXT)
    choose(chooser(fake), SUPPLIER_LOGIN, SIGN_IN)

    optional, required = (call.user for call in fake.received)
    offer = "answer with element_id null and action none"
    assert offer in optional
    assert offer not in required
    assert "must be one of the elements in the list" in required
    assert "element_id null and action none" in SYSTEM_PROMPT
