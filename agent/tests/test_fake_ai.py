import asyncio
import inspect
import json

import pytest
from agentfield import Agent
from agentfield.cost_tracker import CostTracker, reset_current_cost_tracker, set_current_cost_tracker
from agentfield.multimodal_response import MultimodalResponse
from agentfield.rate_limiter import RateLimitError
from pydantic import BaseModel

from clientops.claude_call import CLAUDE_MAX_TOKENS, CLAUDE_MODEL, element_choice_call
from clientops.externals.fake_ai import FakeClaude, UnscriptedCall, UnsupportedCall


@pytest.fixture(autouse=True)
def offline_prices(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def ask(fake, system="Pick the element to press.", user='button "Save"'):
    return asyncio.run(fake(**element_choice_call(system, user)))


def finish_reason(response):
    return response.raw_response.choices[0].finish_reason


def provider_fields(response):
    return response.raw_response.choices[0].message.provider_specific_fields


def test_the_fake_takes_exactly_the_arguments_of_app_ai():
    real = list(inspect.signature(Agent.ai).parameters.values())[1:]
    fake = list(inspect.signature(FakeClaude.__call__).parameters.values())[1:]
    assert [(p.name, p.kind, p.default) for p in fake] == [(p.name, p.kind, p.default) for p in real]


def test_a_valid_choice_comes_back_as_the_json_text_of_a_finished_reply():
    fake = FakeClaude()
    fake.reply_choice("e12", "click", confident=True)
    response = ask(fake)
    assert isinstance(response, MultimodalResponse)
    assert json.loads(response.text) == {"element_id": "e12", "action": "click", "confident": True}
    assert finish_reason(response) == "stop"
    assert response.raw_response.model == "claude-opus-5-5"
    assert provider_fields(response)["thinking_blocks"][0]["type"] == "thinking"


def test_usage_and_cost_follow_the_opus_5_5_prices():
    fake = FakeClaude()
    fake.reply_choice("e12", "fill", input_tokens=5200, output_tokens=640)
    response = ask(fake)
    assert response.usage == {
        "prompt_tokens": 5200,
        "completion_tokens": 640,
        "total_tokens": 5840,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    assert response.cost_usd == pytest.approx(5200 * 4 / 1e6 + 640 * 20 / 1e6)
    assert response.cost_source == "litellm"


def test_cached_input_is_counted_in_prompt_tokens_and_priced_as_cache_reads_and_writes():
    fake = FakeClaude()
    fake.reply_choice("e12", "click", input_tokens=400, cache_read_tokens=2000, cache_write_tokens=1000, output_tokens=120)
    response = ask(fake)
    assert response.usage["prompt_tokens"] == 3400
    assert response.usage["cache_read_tokens"] == 2000
    assert response.usage["cache_creation_tokens"] == 1000
    assert response.cost_usd == pytest.approx((400 * 4 + 1000 * 5 + 2000 * 0.2 + 120 * 20) / 1e6)


def test_a_choice_outside_the_tree_and_an_unsure_choice_come_back_as_given():
    fake = FakeClaude()
    fake.reply_choice("node-that-is-not-in-the-tree", "click")
    fake.reply_choice("e3", "fill", confident=False)
    outside = json.loads(ask(fake).text)
    unsure = json.loads(ask(fake).text)
    assert outside == {"element_id": "node-that-is-not-in-the-tree", "action": "click", "confident": True}
    assert unsure == {"element_id": "e3", "action": "fill", "confident": False}


def test_a_none_answer_says_the_element_is_not_on_this_page_sure_or_not():
    fake = FakeClaude()
    fake.reply_none()
    fake.reply_none(confident=False)
    sure = ask(fake)
    unsure = ask(fake)
    assert json.loads(sure.text) == {"element_id": None, "action": "none", "confident": True}
    assert json.loads(unsure.text) == {"element_id": None, "action": "none", "confident": False}
    assert finish_reason(sure) == "stop"


def test_text_that_is_not_json_is_returned_untouched():
    fake = FakeClaude()
    fake.reply_text("I would press the Save button.")
    response = ask(fake)
    assert response.text == "I would press the Save button."
    assert finish_reason(response) == "stop"
    with pytest.raises(json.JSONDecodeError):
        json.loads(response.text)


def test_a_refusal_has_no_text_and_carries_the_stop_details():
    fake = FakeClaude()
    fake.reply_refusal("cyber", input_tokens=5200)
    response = ask(fake)
    assert response.text == ""
    assert finish_reason(response) == "content_filter"
    fields = provider_fields(response)
    assert fields["stop_reason"] == "refusal"
    assert fields["stop_details"]["type"] == "refusal"
    assert fields["stop_details"]["category"] == "cyber"
    assert response.usage["completion_tokens"] == 0
    assert response.raw_response.choices[0].message.refusal == fields["stop_details"]["explanation"]


def test_a_refusal_without_a_named_category_has_null_details():
    fake = FakeClaude()
    fake.reply_refusal(None)
    fields = provider_fields(ask(fake))
    assert fields["stop_details"] == {"type": "refusal", "category": None, "explanation": None}


def test_a_reply_cut_by_max_tokens_is_partial_and_finishes_with_length():
    fake = FakeClaude()
    fake.reply_cut_off('{"element_id": "e1', output_tokens=CLAUDE_MAX_TOKENS)
    response = ask(fake)
    assert response.text == '{"element_id": "e1'
    assert finish_reason(response) == "length"
    assert response.usage["completion_tokens"] == CLAUDE_MAX_TOKENS


def test_a_rate_limit_surfaces_as_the_agentfield_error_left_after_its_retries():
    fake = FakeClaude()
    fake.fail_rate_limited()
    with pytest.raises(RateLimitError, match="Rate limit retries exhausted") as caught:
        ask(fake)
    assert "rate_limit_error" in str(caught.value)


def test_overload_a_bad_request_and_a_bad_key_surface_as_litellm_errors():
    import litellm

    fake = FakeClaude()
    fake.fail_overloaded()
    fake.fail_invalid_request('tool_choice: type "tool" and "any" are not supported for this model.')
    fake.fail_unauthorized()
    with pytest.raises(litellm.InternalServerError) as overloaded:
        ask(fake)
    assert "overloaded_error" in str(overloaded.value)
    with pytest.raises(litellm.BadRequestError) as bad_request:
        ask(fake)
    assert "invalid_request_error" in str(bad_request.value)
    with pytest.raises(litellm.AuthenticationError):
        ask(fake)


def test_replies_are_used_in_order_and_a_call_with_nothing_scripted_fails_loudly():
    fake = FakeClaude()
    fake.reply_choice("e1", "click")
    fake.reply_choice("e2", "fill")
    assert fake.pending == 2
    assert json.loads(ask(fake).text)["element_id"] == "e1"
    assert json.loads(ask(fake).text)["element_id"] == "e2"
    assert fake.pending == 0
    with pytest.raises(UnscriptedCall):
        ask(fake)


def test_every_call_is_recorded_with_what_was_sent_even_when_it_fails():
    fake = FakeClaude()
    fake.reply_choice("e1", "click")
    fake.fail_rate_limited()
    ask(fake, system="S1", user="U1")
    with pytest.raises(RateLimitError):
        ask(fake, system="S2", user="U2")
    assert [(call.system, call.user) for call in fake.received] == [("S1", "U1"), ("S2", "U2")]
    first = fake.received[0]
    assert first.model == CLAUDE_MODEL
    assert first.max_tokens == CLAUDE_MAX_TOKENS
    assert first.output_config == element_choice_call("S1", "U1")["output_config"]
    assert first.args == ()
    assert first.extra == {}


def test_everything_sent_covers_every_channel_so_secrets_and_values_can_be_searched():
    fake = FakeClaude()
    fake.reply_choice("e1", "fill")
    fake.reply_choice("e2", "click")
    ask(fake, system="Fill the stock field.", user='textbox "Stock"')
    asyncio.run(fake("positional hunter2", user="u", model=CLAUDE_MODEL, metadata={"note": "extra-secret"}))
    sent = fake.everything_sent()
    for expected in ("Fill the stock field.", 'textbox "Stock"', "positional hunter2", "extra-secret", "medium"):
        assert expected in sent
    assert "never-sent-password" not in sent


def test_without_a_model_the_configured_model_is_used():
    fake = FakeClaude()
    fake.reply_choice("e1", "click")
    asyncio.run(fake(system="s", user="u"))
    assert fake.received[0].model == CLAUDE_MODEL


def test_a_model_without_a_provider_prefix_fails_like_app_ai():
    fake = FakeClaude()
    fake.reply_choice("e1", "click")
    with pytest.raises(ValueError, match="Invalid model spec"):
        asyncio.run(fake(system="s", user="u", model="claude-opus-5-5"))
    assert fake.pending == 1


def test_calls_that_change_what_app_ai_returns_are_refused():
    class Choice(BaseModel):
        element_id: str

    fake = FakeClaude()
    fake.reply_choice("e1", "click")
    with pytest.raises(UnsupportedCall, match="schema"):
        asyncio.run(fake(system="s", user="u", schema=Choice))
    with pytest.raises(UnsupportedCall, match="stream"):
        asyncio.run(fake(system="s", user="u", stream=True))
    with pytest.raises(UnsupportedCall, match="tools"):
        asyncio.run(fake(system="s", user="u", tools=[]))
    with pytest.raises(UnsupportedCall, match="response_format"):
        asyncio.run(fake(system="s", user="u", response_format="json"))
    assert fake.pending == 1


def test_each_reply_is_recorded_in_the_current_cost_tracker_like_app_ai_does():
    fake = FakeClaude()
    fake.reply_choice("e1", "click", input_tokens=5200, output_tokens=640)
    tracker = CostTracker()
    token = set_current_cost_tracker(tracker)
    try:
        ask(fake)
    finally:
        reset_current_cost_tracker(token)
    assert tracker.serialize()["entries"] == [
        {
            "source": "llm",
            "provider": "anthropic",
            "model": "claude-opus-5-5",
            "harness": None,
            "reasoner": None,
            "input_tokens": 5200,
            "output_tokens": 640,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "total_tokens": 5840,
            "cost_usd": pytest.approx(0.0336),
            "cost_source": "litellm",
            "routing_provider": "anthropic",
        }
    ]


def test_the_fake_can_take_the_place_of_app_ai_on_the_node(monkeypatch):
    import clientops.node as node

    fake = FakeClaude()
    fake.reply_choice("e7", "click")
    monkeypatch.setattr(node.app, "ai", fake)
    response = asyncio.run(node.app.ai(**element_choice_call("s", "u")))
    assert json.loads(response.text)["element_id"] == "e7"
