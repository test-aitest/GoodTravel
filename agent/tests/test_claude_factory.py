import asyncio
import json

import pytest

import clientops.externals as externals
from clientops.claude_call import element_choice_call
from clientops.externals import MissingExternalKey, claude_ai
from clientops.externals.picking_ai import PickingClaude
from clientops.plan import SYSTEM_PROMPT, Goal, user_prompt

SIGN_IN_PAGE = [
    {"id": "a", "role": "textbox", "name": "Account ID", "form": "/login"},
    {"id": "b", "role": "textbox", "name": "Password", "form": "/login"},
    {"id": "c", "role": "button", "name": "Sign in", "form": "/login"},
]


class StandIn:
    def __init__(self) -> None:
        self.ai = object()


def ask(ai, goal: Goal, view=SIGN_IN_PAGE):
    return asyncio.run(ai(**element_choice_call(system=SYSTEM_PROMPT, user=user_prompt(goal, view, None))))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def test_fake_mode_gives_a_claude_that_picks_by_name_from_what_it_received(monkeypatch):
    monkeypatch.setenv(externals.EXTERNALS_MODE_VARIABLE, "fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    ai = claude_ai(StandIn())

    assert isinstance(ai, PickingClaude)
    response = ask(ai, Goal("Sign in to the supplier portal: the password field.", "fill"))
    assert json.loads(response.text) == {"element_id": "b", "action": "fill", "confident": True}
    assert response.raw_response.choices[0].finish_reason == "stop"


def test_fake_mode_answers_none_when_a_step_that_may_be_missing_is_missing(monkeypatch):
    monkeypatch.setenv(externals.EXTERNALS_MODE_VARIABLE, "fake")

    response = ask(claude_ai(StandIn()), Goal("Read the table: the link or button that goes to the next page of the table.", "click", optional=True))

    assert json.loads(response.text) == {"element_id": None, "action": "none", "confident": True}


def test_each_job_gets_its_own_fake(monkeypatch):
    monkeypatch.setenv(externals.EXTERNALS_MODE_VARIABLE, "fake")
    app = StandIn()

    first, second = claude_ai(app), claude_ai(app)
    ask(first, Goal("Sign in to the store admin: the button that sends the sign-in form.", "click"))

    assert first is not second
    assert (first.calls, second.calls) == (1, 0)


def test_real_mode_with_a_key_uses_the_agents_own_ai(monkeypatch):
    monkeypatch.setenv(externals.EXTERNALS_MODE_VARIABLE, "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-used")
    app = StandIn()

    assert claude_ai(app) is app.ai


@pytest.mark.parametrize("mode", ["", "real"])
def test_real_mode_without_a_key_stops_instead_of_falling_back(monkeypatch, mode):
    monkeypatch.setenv(externals.EXTERNALS_MODE_VARIABLE, mode)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(MissingExternalKey) as raised:
        claude_ai(StandIn())

    assert "ANTHROPIC_API_KEY" in str(raised.value)
    assert "CLIENTOPS_EXTERNALS=fake" in str(raised.value)


def test_an_unknown_mode_is_an_error(monkeypatch):
    monkeypatch.setenv(externals.EXTERNALS_MODE_VARIABLE, "mock")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-used")

    with pytest.raises(ValueError):
        claude_ai(StandIn())


def test_the_test_helper_still_imports_the_same_picker():
    import picking_claude

    assert picking_claude.PickingClaude is PickingClaude
    assert picking_claude.pick is __import__("clientops.externals.picking_ai", fromlist=["pick"]).pick
