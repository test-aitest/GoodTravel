import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import daytona_api_client
import daytona_toolbox_api_client

from clientops.externals.fake_ai import offline_litellm

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "tests" / "contract" / "external"

DAYTONA_SAMPLES = {
    "SnapshotDto": "daytona/snapshot.json",
    "ListSandboxesResponse": "daytona/list_sandboxes.json",
    "Sandbox": "daytona/sandbox.json",
    "ExecuteResponse": "daytona/execute_response_echo.json",
}
LISTED_KIND = "ListSandboxesResponse"
MESSAGE_SAMPLE = "anthropic/messages-choice.json"
APP_AI_SAMPLE = "anthropic/app-ai-returns.json"
APP_AI_CASE = "choice"
APP_AI_SAMPLE_LABEL = f"{APP_AI_SAMPLE} ({APP_AI_CASE})"
MAP_FIELDS = frozenset({"labels", "env"})
HIDDEN = "[hidden]"
WHOLE_RESPONSE = "(whole response)"
TYPED_ITEM_KEY = "type"
OPTIONAL_BLOCKS = frozenset({"thinking"})
NULLABLE_FIELDS = frozenset({"thinking_blocks"})


def load_sample(label: str) -> Any:
    if label == APP_AI_SAMPLE_LABEL:
        return json.loads((SAMPLES_DIR / APP_AI_SAMPLE).read_text())[APP_AI_CASE]["returns"]
    return json.loads((SAMPLES_DIR / label).read_text())


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


@dataclass(frozen=True)
class Difference:
    path: str
    sample: str | None
    real: str | None

    def describe(self) -> str:
        if self.real is None:
            return f"{self.path} is missing"
        if self.sample is None:
            return f"{self.path} is new ({self.real})"
        return f"{self.path} is {self.real}, not {self.sample}"


def child_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def is_typed_list(items: list) -> bool:
    return bool(items) and all(isinstance(item, dict) and isinstance(item.get(TYPED_ITEM_KEY), str) for item in items)


def first_of_each_type(items: list) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for item in items:
        found.setdefault(item[TYPED_ITEM_KEY], item)
    return found


def list_differences(sample: list, real: list, path: str) -> list[Difference]:
    if not sample or not real:
        return []
    if is_typed_list(sample) and is_typed_list(real):
        sample_blocks, real_blocks = first_of_each_type(sample), first_of_each_type(real)
        found: list[Difference] = []
        for kind, block in sample_blocks.items():
            label = f"{path}[{kind}]"
            if kind in real_blocks:
                found.extend(shape_differences(block, real_blocks[kind], label))
            elif kind not in OPTIONAL_BLOCKS:
                found.append(Difference(label, json_type(block), None))
        found.extend(
            Difference(f"{path}[{kind}]", None, json_type(block)) for kind, block in real_blocks.items() if kind not in sample_blocks
        )
        return found
    return shape_differences(sample[0], real[0], f"{path}[0]")


def object_differences(sample: dict, real: dict, path: str) -> list[Difference]:
    found: list[Difference] = []
    for key, value in sample.items():
        label = child_path(path, key)
        if key not in real:
            found.append(Difference(label, json_type(value), None))
        elif key in NULLABLE_FIELDS and (value is None or real[key] is None):
            continue
        elif key in MAP_FIELDS:
            if json_type(value) != json_type(real[key]):
                found.append(Difference(label, json_type(value), json_type(real[key])))
        else:
            found.extend(shape_differences(value, real[key], label))
    found.extend(Difference(child_path(path, key), None, json_type(value)) for key, value in real.items() if key not in sample)
    return found


def shape_differences(sample: Any, real: Any, path: str = "") -> list[Difference]:
    sample_type, real_type = json_type(sample), json_type(real)
    if sample_type != real_type:
        return [Difference(path or WHOLE_RESPONSE, sample_type, real_type)]
    if sample_type == "object":
        return object_differences(sample, real, path)
    if sample_type == "array":
        return list_differences(list(sample), list(real), path)
    return []


@dataclass
class RealResponses:
    daytona: dict[str, list[Any]] = field(default_factory=dict)
    message: dict | None = None
    app_ai: dict | None = None

    def record_daytona(self, kind: str, body: Any) -> None:
        self.daytona.setdefault(kind, []).append(body)

    def daytona_body(self, kind: str) -> Any:
        bodies = self.daytona.get(kind) or []
        if kind == LISTED_KIND:
            listed = [body for body in bodies if isinstance(body, dict) and body.get("items")]
            bodies = listed or bodies
        return bodies[-1] if bodies else None

    def pairs(self) -> list[tuple[str, Any]]:
        found = [(label, self.daytona_body(kind)) for kind, label in DAYTONA_SAMPLES.items()]
        return [*found, (MESSAGE_SAMPLE, self.message), (APP_AI_SAMPLE_LABEL, self.app_ai)]


@contextmanager
def recording_daytona(responses: RealResponses) -> Iterator[None]:
    clients = (daytona_api_client.ApiClient, daytona_toolbox_api_client.ApiClient)
    originals = [client.response_deserialize for client in clients]

    def recorder(original):
        def response_deserialize(self, response_data, response_types_map=None):
            answer = original(self, response_data, response_types_map)
            kind = (response_types_map or {}).get(str(response_data.status))
            if kind in DAYTONA_SAMPLES:
                try:
                    responses.record_daytona(kind, json.loads(response_data.data))
                except ValueError:
                    pass
            return answer

        return response_deserialize

    for client, original in zip(clients, originals, strict=True):
        client.response_deserialize = recorder(original)
    try:
        yield
    finally:
        for client, original in zip(clients, originals, strict=True):
            client.response_deserialize = original


@dataclass
class AnthropicWire:
    request: dict | None = None
    message: dict | None = None

    def take(self, original_response: Any, sent: Any) -> None:
        if not isinstance(original_response, str):
            return
        try:
            message = json.loads(original_response)
        except ValueError:
            return
        if isinstance(message, dict):
            self.message = message
            self.request = json.loads(json.dumps(sent, default=str)) if isinstance(sent, dict) else None


@contextmanager
def recording_anthropic() -> Iterator[AnthropicWire]:
    litellm = offline_litellm()
    from litellm.integrations.custom_logger import CustomLogger

    wire = AnthropicWire()

    class AnthropicRecorder(CustomLogger):
        def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
            wire.take(kwargs.get("original_response"), (kwargs.get("additional_args") or {}).get("complete_input_dict"))

    recorder = AnthropicRecorder()
    litellm.input_callback.append(recorder)
    try:
        yield wire
    finally:
        litellm.input_callback.remove(recorder)


def app_ai_view(response: Any) -> dict:
    choice = response.raw_response.choices[0]
    return {
        "type": type(response).__name__,
        "text": response.text,
        "usage": response.usage,
        "cost_usd": response.cost_usd,
        "cost_source": response.cost_source,
        "model": response.raw_response.model,
        "finish_reason": choice.finish_reason,
        "refusal": getattr(choice.message, "refusal", None),
        "provider_specific_fields": choice.message.provider_specific_fields,
    }


def hide_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ({name: HIDDEN for name in item} if key == "env" and isinstance(item, dict) else hide_env(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [hide_env(item) for item in value]
    return value


def without_secrets(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, HIDDEN)
    return text


def saved_form(label: str, body: Any) -> tuple[str, Any]:
    if label == APP_AI_SAMPLE_LABEL:
        return APP_AI_SAMPLE, {APP_AI_CASE: {"returns": body}}
    return label, body


def save_real_responses(responses: RealResponses, directory: Path, *, secrets: Iterable[str] = ()) -> Path:
    hidden = tuple(secrets)
    for label, body in responses.pairs():
        if body is None:
            continue
        name, content = saved_form(label, hide_env(body))
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        target.write_text(without_secrets(text, hidden) + "\n")
    return directory
