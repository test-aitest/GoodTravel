import asyncio
import json
from pathlib import Path

import pytest
from agentfield import Agent
from agentfield.cost_tracker import CostTracker, reset_current_cost_tracker, set_current_cost_tracker
from agentfield.types import AIConfig
from pydantic import BaseModel

from anthropic_stub import AnthropicStub
from clientops.claude_call import CHAT_REPLY_SCHEMA, CLAUDE_MAX_TOKENS, CLAUDE_MODEL, chat_reply_call, claude_ai_config, element_choice_call
from clientops.externals.fake_ai import FakeClaude, wire_error, wire_message, wire_usage

SAMPLES = Path(__file__).parent / "contract" / "external" / "anthropic"
TEST_KEY = "sk-ant-test-not-a-real-key"

pytestmark = pytest.mark.filterwarnings(
    "ignore:The `copy` method is deprecated:pydantic.warnings.PydanticDeprecatedSince20"
)

MESSAGE_FIELDS = {"id", "type", "role", "model", "content", "container", "stop_reason", "stop_sequence", "stop_details", "usage", "diagnostics"}
USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_creation",
    "output_tokens_details",
    "service_tier",
    "inference_geo",
    "speed",
}
STOP_REASONS = {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded"}
REFUSAL_CATEGORIES = {"cyber", "bio", "frontier_llm", "reasoning_extraction", "general_harms", None}
ERROR_TYPES = {
    "400": "invalid_request_error",
    "401": "authentication_error",
    "429": "rate_limit_error",
    "529": "overloaded_error",
}
MESSAGE_SAMPLES = {
    "choice": "messages-choice.json",
    "choice_without_thinking": "messages-choice-without-thinking.json",
    "none": "messages-none.json",
    "refusal": "messages-refusal.json",
    "max_tokens": "messages-max-tokens.json",
}


def sample(name):
    return json.loads((SAMPLES / name).read_text())


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    return type(value).__name__


def returned(response):
    choice = response.raw_response.choices[0]
    return {
        "type": type(response).__name__,
        "text": response.text,
        "usage": response.usage,
        "cost_usd": round(response.cost_usd, 10),
        "cost_source": response.cost_source,
        "model": response.raw_response.model,
        "finish_reason": choice.finish_reason,
        "refusal": getattr(choice.message, "refusal", None),
        "provider_specific_fields": choice.message.provider_specific_fields,
    }


def entries(tracker):
    return [{**entry, "cost_usd": round(entry["cost_usd"], 10)} for entry in tracker.serialize()["entries"]]


def raised(error):
    return {
        "exception": f"{type(error).__module__}.{type(error).__name__}",
        "status_code": getattr(error, "status_code", None),
    }


@pytest.fixture
def anthropic(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("AGENTFIELD_AI_TIMEOUT_RETRIES", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    with AnthropicStub() as stub:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", stub.url)
        yield stub


def real_app(config=None):
    settings = config or claude_ai_config().model_copy(update={"rate_limit_max_retries": 0})
    return Agent(node_id="contract", agentfield_server="http://127.0.0.1:9", ai_config=settings)


def call_real(app, tracker=None, **call):
    async def run():
        token = set_current_cost_tracker(tracker) if tracker is not None else None
        try:
            return await app.ai(**call)
        finally:
            if token is not None:
                reset_current_cost_tracker(token)

    return asyncio.run(run())


def test_the_message_samples_carry_exactly_the_documented_fields():
    for name in MESSAGE_SAMPLES.values():
        message = sample(name)
        assert set(message) == MESSAGE_FIELDS
        assert set(message["usage"]) == USAGE_FIELDS
        assert set(message["usage"]["cache_creation"]) == {"ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"}
        assert set(message["usage"]["output_tokens_details"]) == {"thinking_tokens"}
        assert message["type"] == "message" and message["role"] == "assistant"
        assert message["model"] == "claude-opus-5-5"
        assert message["stop_reason"] in STOP_REASONS
        assert {block["type"] for block in message["content"]} <= {"thinking", "redacted_thinking", "text"}
        thinking = message["usage"]["output_tokens_details"]["thinking_tokens"]
        assert 0 <= thinking <= message["usage"]["output_tokens"]


def test_a_thinking_block_comes_back_exactly_when_claude_thought():
    for name in MESSAGE_SAMPLES.values():
        message = sample(name)
        thought = message["usage"]["output_tokens_details"]["thinking_tokens"] > 0
        assert any(block["type"] == "thinking" for block in message["content"]) is thought, name


def test_the_fake_choice_without_thinking_is_the_reply_recorded_from_the_real_api_apart_from_the_id():
    fake = FakeClaude()
    fake.reply_choice("d", "click", input_tokens=701, output_tokens=21, thinking_tokens=0)
    built = fake.next_wire_message()
    expected = sample("messages-choice-without-thinking.json")
    for message in (built, expected):
        message["id"] = "msg"
    assert built == expected


def test_stop_details_are_null_unless_the_reply_is_a_refusal():
    assert sample("messages-choice.json")["stop_details"] is None
    assert sample("messages-max-tokens.json")["stop_details"] is None
    refusal = sample("messages-refusal.json")
    assert refusal["stop_reason"] == "refusal"
    assert refusal["content"] == []
    assert set(refusal["stop_details"]) == {"type", "category", "explanation"}
    assert refusal["stop_details"]["type"] == "refusal"
    assert refusal["stop_details"]["category"] in REFUSAL_CATEGORIES
    assert refusal["usage"]["output_tokens"] == 0


def test_the_choice_sample_answers_with_one_json_text_block_in_the_choice_shape():
    message = sample("messages-choice.json")
    texts = [block["text"] for block in message["content"] if block["type"] == "text"]
    assert len(texts) == 1
    choice = json.loads(texts[0])
    assert set(choice) == {"element_id", "action", "confident"}
    assert choice["action"] in {"click", "fill"}
    assert isinstance(choice["confident"], bool)


def test_the_none_sample_answers_that_the_element_is_not_on_this_page():
    message = sample("messages-none.json")
    assert message["stop_reason"] == "end_turn"
    [text] = [block["text"] for block in message["content"] if block["type"] == "text"]
    assert json.loads(text) == {"element_id": None, "action": "none", "confident": True}


def test_the_fake_none_reply_is_the_none_sample_apart_from_ids():
    fake = FakeClaude()
    fake.reply_none(input_tokens=900, output_tokens=420, thinking_tokens=390)
    built = fake.next_wire_message()
    expected = sample("messages-none.json")
    for message in (built, expected):
        message["id"] = "msg"
        message["content"][0]["signature"] = "signature"
    assert built == expected


def test_the_max_tokens_sample_is_a_cut_off_json_text():
    message = sample("messages-max-tokens.json")
    assert message["stop_reason"] == "max_tokens"
    assert message["usage"]["output_tokens"] == CLAUDE_MAX_TOKENS
    text = next(block["text"] for block in message["content"] if block["type"] == "text")
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_the_error_samples_have_the_documented_error_shape():
    errors = sample("errors.json")
    assert set(errors) == set(ERROR_TYPES)
    for status, body in errors.items():
        assert set(body) == {"type", "error", "request_id"}
        assert body["type"] == "error"
        assert set(body["error"]) == {"type", "message"}
        assert body["error"]["type"] == ERROR_TYPES[status]
        assert body["request_id"].startswith("req_")


def test_the_fake_builds_the_same_wire_shapes_as_the_samples():
    choice = wire_message(
        '{"element_id":"e12","action":"click","confident":true}',
        "end_turn",
        None,
        wire_usage(5200, 640, thinking_tokens=590),
    )
    refusal = wire_message(
        None,
        "refusal",
        {"type": "refusal", "category": "cyber", "explanation": "declined"},
        wire_usage(5200, 0),
    )
    cut = wire_message('{"element_id":"e1', "max_tokens", None, wire_usage(5200, CLAUDE_MAX_TOKENS, thinking_tokens=15988))
    unthought = wire_message('{"element_id":"d","action":"click","confident":true}', "end_turn", None, wire_usage(701, 21))
    assert shape(choice) == shape(sample("messages-choice.json"))
    assert shape(unthought) == shape(sample("messages-choice-without-thinking.json"))
    assert shape(refusal) == shape(sample("messages-refusal.json"))
    assert shape(cut) == shape(sample("messages-max-tokens.json"))
    for status, body in sample("errors.json").items():
        assert shape(wire_error(int(status))) == shape(body)
        assert wire_error(int(status))["error"]["type"] == body["error"]["type"]


def test_the_fake_choice_reply_is_the_choice_sample_apart_from_ids():
    fake = FakeClaude()
    fake.reply_choice("e12", "click", input_tokens=5200, output_tokens=640, thinking_tokens=590)
    built = fake.next_wire_message()
    expected = sample("messages-choice.json")
    for message in (built, expected):
        message["id"] = "msg"
        message["content"][0]["signature"] = "signature"
    assert built == expected


def test_litellm_prices_opus_5_5_at_the_published_rates(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    prices = litellm.model_cost["claude-opus-5-5"]
    assert prices["litellm_provider"] == "anthropic"
    assert prices["input_cost_per_token"] * 1e6 == pytest.approx(4)
    assert prices["output_cost_per_token"] * 1e6 == pytest.approx(20)
    assert prices["cache_read_input_token_cost"] * 1e6 == pytest.approx(0.2)
    assert prices["cache_creation_input_token_cost"] * 1e6 == pytest.approx(5)
    assert prices["cache_creation_input_token_cost_above_1hr"] * 1e6 == pytest.approx(8)
    assert prices["max_input_tokens"] == 1_000_000
    assert prices["max_output_tokens"] == 128_000
    assert prices["supports_native_structured_output"] is True
    assert prices["supports_forced_tool_use"] is False
    assert prices["thinking_always_on"] is True


def test_app_ai_sends_exactly_the_documented_request(anthropic):
    anthropic.reply(200, sample("messages-choice.json"))
    call_real(real_app(), **element_choice_call("<system>", "<user>"))
    expected = sample("request-element-choice.json")
    [request] = anthropic.requests
    assert request.path == expected["path"]
    assert request.body == expected["body"]
    for name, value in expected["headers"].items():
        assert request.headers[name] == (TEST_KEY if value == "<ANTHROPIC_API_KEY>" else value)
    assert "authorization" not in request.headers


def test_app_ai_sends_the_chat_reply_call_as_the_documented_request_with_the_chat_format(anthropic):
    anthropic.reply(200, sample("messages-choice.json"))
    call_real(real_app(), **chat_reply_call("<system>", "<user>"))
    expected = sample("request-element-choice.json")["body"]
    expected["output_config"]["format"]["schema"] = CHAT_REPLY_SCHEMA
    [request] = anthropic.requests
    assert request.body == expected


@pytest.mark.parametrize("case", sorted(MESSAGE_SAMPLES))
def test_app_ai_returns_what_the_fake_returns_and_what_the_sample_records(anthropic, case):
    wire = sample(MESSAGE_SAMPLES[case])
    anthropic.reply(200, wire)
    real_tracker = CostTracker()
    real = call_real(real_app(), real_tracker, **element_choice_call("s", "u"))

    fake = FakeClaude()
    fake.reply_message(wire)
    fake_tracker = CostTracker()
    token = set_current_cost_tracker(fake_tracker)
    try:
        faked = asyncio.run(fake(**element_choice_call("s", "u")))
    finally:
        reset_current_cost_tracker(token)

    recorded = sample("app-ai-returns.json")[case]
    assert returned(real) == returned(faked) == recorded["returns"]
    assert entries(real_tracker) == entries(fake_tracker) == [recorded["cost_tracker_entry"]]


@pytest.mark.parametrize("status", sorted(ERROR_TYPES))
def test_app_ai_raises_the_documented_error_and_the_fake_raises_the_same(anthropic, status):
    body = sample("errors.json")[status]
    anthropic.reply(int(status), body)
    with pytest.raises(Exception) as real:
        call_real(real_app(), **element_choice_call("s", "u"))

    fake = FakeClaude()
    fake.fail_with_status(int(status))
    with pytest.raises(Exception) as faked:
        asyncio.run(fake(**element_choice_call("s", "u")))

    expected = sample("app-ai-raises.json")[status]
    assert raised(real.value) == raised(faked.value) == {
        "exception": expected["exception"],
        "status_code": expected["status_code"],
    }
    assert expected["message_contains"] in str(real.value)
    assert expected["message_contains"] in str(faked.value)


def test_evidence_app_ai_with_schema_sends_the_deprecated_output_format_and_hides_refusals(anthropic):
    class Choice(BaseModel):
        element_id: str
        confident: bool

    anthropic.reply(200, sample("messages-refusal.json"), times=3)
    tracker = CostTracker()
    with pytest.raises(ValueError, match="Could not parse structured response"):
        call_real(
            real_app(),
            tracker,
            system="s",
            user="u",
            schema=Choice,
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            reasoning_effort="medium",
        )
    assert len(anthropic.requests) == 3
    first = anthropic.requests[0]
    assert "output_format" in first.body
    assert first.body["output_config"] == {"effort": "medium"}
    assert first.body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert first.headers["anthropic-beta"] == "structured-outputs-2025-11-13"
    assert "You must exactly adhere to the output schema" in first.body["system"][0]["text"]
    assert tracker.serialize()["total_input_tokens"] == 3 * sample("messages-refusal.json")["usage"]["input_tokens"]


def test_evidence_without_max_input_tokens_app_ai_silently_trims_a_long_tree(anthropic):
    tree = "\n".join(f'e{index} button "Action {index}"' for index in range(6000))
    anthropic.reply(200, sample("messages-choice.json"), times=2)
    default_settings = AIConfig(model=CLAUDE_MODEL, rate_limit_max_retries=0)
    call_real(real_app(default_settings), **element_choice_call("s", tree))
    call_real(real_app(), **element_choice_call("s", tree))
    trimmed, intact = (request.body["messages"][0]["content"][0]["text"] for request in anthropic.requests)
    assert len(trimmed) < len(tree) // 2
    assert intact == tree
