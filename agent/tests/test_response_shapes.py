import asyncio
import copy
import json
from types import SimpleNamespace

import daytona_api_client
import daytona_toolbox_api_client
import pytest

from anthropic_stub import AnthropicStub
from clientops.claude_call import CLAUDE_MAX_TOKENS, CLAUDE_MODEL, element_choice_call
from clientops.response_shapes import (
    APP_AI_SAMPLE_LABEL,
    DAYTONA_SAMPLES,
    MESSAGE_SAMPLE,
    RealResponses,
    load_sample,
    recording_anthropic,
    recording_daytona,
    save_real_responses,
    shape_differences,
)

TEST_KEY = "sk-ant-test-not-a-real-key"


def described(sample, real) -> list[str]:
    return [difference.describe() for difference in shape_differences(sample, real)]


def wire_reply(status: int, body: dict) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        reason="OK",
        getheaders=lambda: {"content-type": "application/json"},
    )


def test_every_sample_matches_itself():
    for name in [*DAYTONA_SAMPLES.values(), MESSAGE_SAMPLE, APP_AI_SAMPLE_LABEL]:
        assert shape_differences(load_sample(name), load_sample(name)) == []


def test_only_field_names_and_json_types_count_not_values():
    sandbox = load_sample("daytona/sandbox.json")
    real = copy.deepcopy(sandbox)
    real["id"] = "0c7f3a52-real-id"
    real["autoStopInterval"] = 15.0
    real["state"] = "stopped"
    assert shape_differences(sandbox, real) == []


def test_missing_new_and_changed_fields_are_named_with_their_path():
    sandbox = load_sample("daytona/sandbox.json")
    real = copy.deepcopy(sandbox)
    del real["cpu"]
    real["backupState"] = "None"
    real["autoStopInterval"] = "15"
    assert described(sandbox, real) == [
        "cpu is missing",
        "autoStopInterval is string, not number",
        "backupState is new (string)",
    ]


def test_null_in_place_of_a_value_is_a_difference():
    message = load_sample(MESSAGE_SAMPLE)
    real = copy.deepcopy(message)
    real["usage"]["cache_creation"] = None
    assert described(message, real) == ["usage.cache_creation is null, not object"]


def test_content_blocks_are_paired_by_their_type():
    message = load_sample(MESSAGE_SAMPLE)
    real = copy.deepcopy(message)
    real["content"] = [
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "{}", "citations": None},
    ]
    real["context_management"] = None
    assert described(message, real) == [
        "content[text].citations is new (null)",
        "content[redacted_thinking] is new (object)",
        "context_management is new (null)",
    ]


def test_a_reply_without_a_thinking_block_matches_because_claude_does_not_always_think():
    message = load_sample(MESSAGE_SAMPLE)
    real = copy.deepcopy(message)
    real["content"] = [block for block in real["content"] if block["type"] != "thinking"]
    assert shape_differences(message, real) == []
    assert shape_differences(message, load_sample("anthropic/messages-choice-without-thinking.json")) == []


def test_a_thinking_block_that_comes_is_still_compared_and_a_missing_text_block_still_counts():
    message = load_sample(MESSAGE_SAMPLE)
    real = copy.deepcopy(message)
    thinking = next(block for block in real["content"] if block["type"] == "thinking")
    real["content"] = [{**thinking, "summary": None}]
    assert described(message, real) == ["content[thinking].summary is new (null)", "content[text] is missing"]


def test_app_ai_thinking_blocks_may_be_null_when_claude_did_not_think_but_are_compared_when_both_come():
    returns = load_sample(APP_AI_SAMPLE_LABEL)
    real = copy.deepcopy(returns)
    real["provider_specific_fields"]["thinking_blocks"] = None
    assert shape_differences(returns, real) == []
    assert shape_differences(real, returns) == []
    real["provider_specific_fields"]["thinking_blocks"] = [{"type": "thinking", "thinking": "", "signature": "s", "extra": 1}]
    assert described(returns, real) == ["provider_specific_fields.thinking_blocks[thinking].extra is new (number)"]
    real["provider_specific_fields"]["citations"] = []
    assert "provider_specific_fields.citations is array, not null" in described(returns, real)


def test_list_items_are_compared_by_their_first_item_and_empty_lists_are_not_compared():
    listed = load_sample("daytona/list_sandboxes.json")
    real = copy.deepcopy(listed)
    real["items"][0]["cpu"] = "1"
    assert described(listed, real) == ["items[0].cpu is string, not number"]
    assert shape_differences(listed, {"items": [], "nextCursor": None}) == []


def test_labels_and_env_are_maps_whose_keys_are_data():
    sandbox = load_sample("daytona/sandbox.json")
    real = copy.deepcopy(sandbox)
    real["labels"] = {"clientops-client": "kumo", "team": "ops"}
    real["env"] = {"DAYTONA_SANDBOX_ID": "abc"}
    assert shape_differences(sandbox, real) == []
    real["labels"] = None
    assert described(sandbox, real) == ["labels is null, not object"]


def test_a_whole_response_of_another_type_is_one_difference():
    assert described({"items": []}, []) == ["(whole response) is array, not object"]


def test_daytona_responses_are_recorded_as_they_arrive_and_only_inside_the_block():
    sandbox = load_sample("daytona/sandbox.json")
    executed = {"exitCode": 0, "result": "1 RootWebArea 'Partner sign in - Maruyama Supply'\n"}
    responses = RealResponses()
    api = daytona_api_client.ApiClient()
    toolbox = daytona_toolbox_api_client.ApiClient()

    with recording_daytona(responses):
        parsed = api.response_deserialize(wire_reply(200, sandbox), {"200": "Sandbox"})
        toolbox.response_deserialize(wire_reply(200, executed), {"200": "ExecuteResponse"})
    api.response_deserialize(wire_reply(200, {**sandbox, "id": "after"}), {"200": "Sandbox"})

    assert parsed.data.id == sandbox["id"]
    assert responses.daytona_body("Sandbox") == sandbox
    assert responses.daytona_body("ExecuteResponse") == executed
    assert responses.daytona_body("SnapshotDto") is None


def test_daytona_errors_are_not_recorded_and_still_raise():
    responses = RealResponses()
    api = daytona_api_client.ApiClient()
    with recording_daytona(responses), pytest.raises(daytona_api_client.exceptions.ApiException):
        api.response_deserialize(wire_reply(404, {"message": "not found"}), {"200": "Sandbox"})
    assert responses.daytona_body("Sandbox") is None


def test_the_list_with_items_is_kept_over_a_later_empty_one():
    listed = load_sample("daytona/list_sandboxes.json")
    responses = RealResponses()
    responses.record_daytona("ListSandboxesResponse", {"items": [], "nextCursor": None})
    responses.record_daytona("ListSandboxesResponse", listed)
    responses.record_daytona("ListSandboxesResponse", {"items": [], "nextCursor": None})
    assert responses.daytona_body("ListSandboxesResponse") == listed


def test_the_anthropic_reply_and_the_request_litellm_sent_are_recorded(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_KEY)
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    import litellm

    call = element_choice_call("<system>", "<user>")
    reply = load_sample(MESSAGE_SAMPLE)
    with AnthropicStub() as stub:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", stub.url)
        stub.reply(200, reply)
        with recording_anthropic() as wire:
            asyncio.run(
                litellm.acompletion(
                    model=CLAUDE_MODEL,
                    messages=[{"role": "system", "content": call["system"]}, {"role": "user", "content": call["user"]}],
                    max_tokens=CLAUDE_MAX_TOKENS,
                    output_config=call["output_config"],
                    num_retries=0,
                )
            )
    assert wire.message == reply
    assert wire.request["model"] == "claude-opus-5-5"
    assert wire.request["output_config"] == call["output_config"]
    assert TEST_KEY not in json.dumps(wire.request)
    assert not any(type(callback).__name__ == "AnthropicRecorder" for callback in litellm.input_callback)


def test_saved_real_responses_mirror_the_sample_names_and_hide_env_values_and_secrets(tmp_path):
    responses = RealResponses()
    responses.record_daytona("Sandbox", {**load_sample("daytona/sandbox.json"), "env": {"SOME_TOKEN": "dtn_leaked_value"}})
    responses.message = {**load_sample(MESSAGE_SAMPLE), "id": "msg_contains_sk-ant-secret-value"}
    responses.app_ai = load_sample(APP_AI_SAMPLE_LABEL)

    saved = save_real_responses(responses, tmp_path / "run", secrets=("sk-ant-secret-value",))

    assert saved == tmp_path / "run"
    written = {path.relative_to(saved).as_posix(): path.read_text() for path in saved.rglob("*.json")}
    assert set(written) == {"daytona/sandbox.json", "anthropic/messages-choice.json", "anthropic/app-ai-returns.json"}
    assert json.loads(written["daytona/sandbox.json"])["env"] == {"SOME_TOKEN": "[hidden]"}
    assert "sk-ant-secret-value" not in written["anthropic/messages-choice.json"]
    assert json.loads(written["anthropic/app-ai-returns.json"]) == {"choice": {"returns": load_sample(APP_AI_SAMPLE_LABEL)}}
