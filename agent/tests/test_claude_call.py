from clientops.claude_call import (
    CLAUDE_EFFORT,
    CLAUDE_MAX_INPUT_TOKENS,
    CLAUDE_MAX_TOKENS,
    CLAUDE_MODEL,
    ELEMENT_CHOICE_SCHEMA,
    claude_ai_config,
    element_choice_call,
)


def test_the_model_is_pinned_to_claude_opus_5_5_through_the_anthropic_provider():
    assert CLAUDE_MODEL == "anthropic/claude-opus-5-5"
    assert CLAUDE_EFFORT == "medium"


def test_the_call_is_the_system_and_user_text_with_medium_effort_and_the_choice_format():
    assert element_choice_call("Pick one element.", 'button "Save"') == {
        "system": "Pick one element.",
        "user": 'button "Save"',
        "model": "anthropic/claude-opus-5-5",
        "max_tokens": CLAUDE_MAX_TOKENS,
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": ELEMENT_CHOICE_SCHEMA},
        },
    }


def test_max_tokens_leaves_room_for_thinking_without_needing_streaming():
    assert CLAUDE_MAX_TOKENS == 16000


def test_claude_can_only_pick_an_element_or_say_it_is_not_there_an_action_and_whether_it_is_sure():
    assert ELEMENT_CHOICE_SCHEMA == {
        "type": "object",
        "properties": {
            "element_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "action": {"type": "string", "enum": ["click", "fill", "none"]},
            "confident": {"type": "boolean"},
        },
        "required": ["element_id", "action", "confident"],
        "additionalProperties": False,
    }


def test_the_only_union_is_the_element_or_nothing_and_no_field_is_optional():
    unions = [name for name, field in ELEMENT_CHOICE_SCHEMA["properties"].items() if "anyOf" in field]
    assert unions == ["element_id"]
    assert set(ELEMENT_CHOICE_SCHEMA["required"]) == set(ELEMENT_CHOICE_SCHEMA["properties"])


def test_the_choice_has_no_place_for_a_url_a_destination_a_value_or_reasoning():
    names = set(ELEMENT_CHOICE_SCHEMA["properties"])
    for forbidden in ("url", "href", "destination", "value", "text", "reasoning", "thinking"):
        assert forbidden not in names


def test_the_call_never_sends_what_opus_5_5_rejects_or_what_would_hide_the_stop_reason():
    call = element_choice_call("s", "u")
    for rejected in ("temperature", "top_p", "top_k", "thinking", "tool_choice", "tools"):
        assert rejected not in call
    for hiding in ("schema", "response_format", "reasoning_effort", "stream", "fallbacks"):
        assert hiding not in call


def test_the_agent_settings_stop_silent_trimming_and_never_fall_back_to_another_model():
    config = claude_ai_config()
    assert config.model == CLAUDE_MODEL
    assert config.max_input_tokens == CLAUDE_MAX_INPUT_TOKENS == 1_000_000
    assert config.fallback_models == []
    assert config.temperature is None
    assert config.top_p is None
    assert config.litellm_params == {}
