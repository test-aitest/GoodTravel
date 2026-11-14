import copy
import json
import os
from collections import deque
from dataclasses import dataclass
from itertools import count

from agentfield.cost_tracker import derive_provider, get_current_cost_tracker
from agentfield.execution_context import get_current_context
from agentfield.multimodal_response import MultimodalResponse, detect_multimodal_response
from agentfield.rate_limiter import RateLimitError
from agentfield.usage_routing import routing_provider

from clientops.claude_call import CLAUDE_MAX_TOKENS, CLAUDE_MODEL, NO_ELEMENT_ACTION

ANTHROPIC_PREFIX = "anthropic/"
FAKE_SIGNATURE = "fake-thinking-signature"
DEFAULT_INPUT_TOKENS = 5200
DEFAULT_OUTPUT_TOKENS = 640
VISIBLE_CHOICE_TOKENS = 50
APP_AI_RATE_LIMIT_RETRIES = 5

REFUSAL_EXPLANATIONS = {
    "cyber": "This request was declined because it could enable cyber harm.",
    "bio": "This request was declined because it could enable biological harm.",
    "frontier_llm": "This request was declined because it could assist the development of competing AI models.",
    "reasoning_extraction": "This request was declined because it asks the model to reproduce its internal reasoning.",
    "general_harms": "This request was declined because it conflicts with Anthropic's Usage Policy.",
    None: None,
}

ERROR_BODIES = {
    400: ("invalid_request_error", 'tool_choice: type "tool" and "any" are not supported for this model.'),
    401: ("authentication_error", "invalid x-api-key"),
    429: ("rate_limit_error", "This request would exceed the rate limit for your organization. Please try again later."),
    529: ("overloaded_error", "Overloaded"),
}

message_numbers = count(1)


class UnscriptedCall(AssertionError):
    pass


class UnsupportedCall(TypeError):
    pass


@dataclass(frozen=True)
class ReceivedCall:
    args: tuple
    system: str | None
    user: str | None
    model: str
    max_tokens: int | None
    output_config: dict | None
    extra: dict

    def text(self) -> str:
        parts = [str(arg) for arg in self.args] + [self.system or "", self.user or ""]
        parts.append(json.dumps(self.output_config, ensure_ascii=False, default=str))
        parts.append(json.dumps(self.extra, ensure_ascii=False, default=str))
        return "\n".join(parts)


@dataclass(frozen=True)
class ScriptedMessage:
    wire: dict


@dataclass(frozen=True)
class ScriptedFailure:
    status: int
    body: dict


def offline_litellm():
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    return litellm


def choice_text(element_id: str | None, action: str, confident: bool) -> str:
    return json.dumps(
        {"element_id": element_id, "action": action, "confident": confident},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def wire_usage(
    input_tokens: int,
    output_tokens: int,
    *,
    thinking_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> dict:
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_write_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation": {"ephemeral_5m_input_tokens": cache_write_tokens, "ephemeral_1h_input_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"thinking_tokens": thinking_tokens},
        "service_tier": "standard",
        "inference_geo": "global",
        "speed": "standard",
    }


def thought(usage: dict) -> bool:
    return (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0) > 0


def wire_message(text: str | None, stop_reason: str, stop_details: dict | None, usage: dict) -> dict:
    content = []
    if text is not None:
        thinking = [{"type": "thinking", "thinking": "", "signature": FAKE_SIGNATURE}] if thought(usage) else []
        content = [*thinking, {"type": "text", "text": text}]
    return {
        "id": f"msg_fake{next(message_numbers):020d}",
        "type": "message",
        "role": "assistant",
        "model": CLAUDE_MODEL.removeprefix(ANTHROPIC_PREFIX),
        "content": content,
        "container": None,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage,
        "diagnostics": None,
    }


def wire_error(status: int, message: str | None = None) -> dict:
    error_type, default_message = ERROR_BODIES[status]
    return {
        "type": "error",
        "error": {"type": error_type, "message": message or default_message},
        "request_id": f"req_fake{status}",
    }


def app_ai_response(wire: dict) -> MultimodalResponse:
    litellm = offline_litellm()
    import httpx
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    raw = AnthropicConfig().transform_parsed_response(
        completion_response=copy.deepcopy(wire),
        raw_response=httpx.Response(200, headers={"request-id": f"req_{wire['id']}", "content-type": "application/json"}),
        model_response=litellm.ModelResponse(),
    )
    return detect_multimodal_response(raw)


def app_ai_error(failure: ScriptedFailure, model: str) -> Exception:
    litellm = offline_litellm()
    body = json.dumps(failure.body)
    if failure.status == 429:
        last = f"litellm.RateLimitError: AnthropicException - {body}"
        return RateLimitError(
            f"Rate limit retries exhausted after {APP_AI_RATE_LIMIT_RETRIES} attempts. Last error: {last}"
        )
    if failure.status == 529:
        return litellm.InternalServerError(message=f"AnthropicError - {body}", model=model, llm_provider="anthropic")
    if failure.status == 401:
        return litellm.AuthenticationError(message=f"AnthropicException - {body}", model=model, llm_provider="anthropic")
    return litellm.BadRequestError(message=f"AnthropicException - {body}", model=model, llm_provider="anthropic")


class FakeClaude:
    def __init__(self, config_model: str = CLAUDE_MODEL, cost_tracker=None):
        self.config_model = config_model
        self.cost_tracker = cost_tracker
        self.received: list[ReceivedCall] = []
        self._script: deque = deque()

    @property
    def pending(self) -> int:
        return len(self._script)

    def reply_message(self, wire: dict) -> None:
        self._script.append(ScriptedMessage(copy.deepcopy(wire)))

    def reply_choice(
        self,
        element_id: str,
        action: str,
        confident: bool = True,
        *,
        input_tokens: int = DEFAULT_INPUT_TOKENS,
        output_tokens: int = DEFAULT_OUTPUT_TOKENS,
        thinking_tokens: int | None = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        self.reply_text(
            choice_text(element_id, action, confident),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )

    def reply_none(
        self,
        confident: bool = True,
        *,
        input_tokens: int = DEFAULT_INPUT_TOKENS,
        output_tokens: int = DEFAULT_OUTPUT_TOKENS,
        thinking_tokens: int | None = None,
    ) -> None:
        self.reply_text(
            choice_text(None, NO_ELEMENT_ACTION, confident),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
        )

    def reply_text(
        self,
        text: str,
        *,
        input_tokens: int = DEFAULT_INPUT_TOKENS,
        output_tokens: int = DEFAULT_OUTPUT_TOKENS,
        thinking_tokens: int | None = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        thinking = max(output_tokens - VISIBLE_CHOICE_TOKENS, 0) if thinking_tokens is None else thinking_tokens
        usage = wire_usage(
            input_tokens,
            output_tokens,
            thinking_tokens=thinking,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
        self.reply_message(wire_message(text, "end_turn", None, usage))

    def reply_refusal(self, category: str | None = "cyber", *, input_tokens: int = DEFAULT_INPUT_TOKENS) -> None:
        details = {"type": "refusal", "category": category, "explanation": REFUSAL_EXPLANATIONS[category]}
        self.reply_message(wire_message(None, "refusal", details, wire_usage(input_tokens, 0)))

    def reply_cut_off(
        self,
        partial_text: str,
        *,
        input_tokens: int = DEFAULT_INPUT_TOKENS,
        output_tokens: int = CLAUDE_MAX_TOKENS,
    ) -> None:
        usage = wire_usage(input_tokens, output_tokens, thinking_tokens=max(output_tokens - VISIBLE_CHOICE_TOKENS, 0))
        self.reply_message(wire_message(partial_text, "max_tokens", None, usage))

    def fail_with_status(self, status: int, message: str | None = None) -> None:
        self._script.append(ScriptedFailure(status, wire_error(status, message)))

    def fail_rate_limited(self) -> None:
        self.fail_with_status(429)

    def fail_overloaded(self) -> None:
        self.fail_with_status(529)

    def fail_invalid_request(self, message: str) -> None:
        self.fail_with_status(400, message)

    def fail_unauthorized(self) -> None:
        self.fail_with_status(401)

    def next_wire_message(self) -> dict:
        if not self._script or not isinstance(self._script[0], ScriptedMessage):
            raise UnscriptedCall("the next scripted reply is not a message")
        return copy.deepcopy(self._script[0].wire)

    def everything_sent(self) -> str:
        return "\n".join(call.text() for call in self.received)

    async def __call__(
        self,
        *args,
        system=None,
        user=None,
        schema=None,
        model=None,
        temperature=None,
        max_tokens=None,
        stream=None,
        response_format=None,
        context=None,
        memory_scope=None,
        **kwargs,
    ) -> MultimodalResponse:
        effective_model = model or self.config_model
        named = {
            "schema": schema,
            "temperature": temperature,
            "stream": stream,
            "response_format": response_format,
            "context": context,
            "memory_scope": memory_scope,
        }
        extra = {name: value for name, value in named.items() if value is not None}
        extra.update({name: value for name, value in kwargs.items() if name != "output_config"})
        self.received.append(
            ReceivedCall(args, system, user, effective_model, max_tokens, kwargs.get("output_config"), extra)
        )
        self._refuse_unsupported(schema, stream, response_format, kwargs)
        if "/" not in effective_model:
            raise ValueError(
                f"Invalid model spec: '{effective_model}'. Must include provider prefix, e.g. 'openai/gpt-4'."
            )
        if not effective_model.startswith(ANTHROPIC_PREFIX):
            raise UnsupportedCall(f"only the Anthropic provider is faked, not {effective_model!r}")
        if not self._script:
            raise UnscriptedCall("app.ai was called but no reply was scripted")
        scripted = self._script.popleft()
        if isinstance(scripted, ScriptedFailure):
            raise app_ai_error(scripted, effective_model.removeprefix(ANTHROPIC_PREFIX))
        wire = {**scripted.wire, "model": effective_model.removeprefix(ANTHROPIC_PREFIX)}
        response = app_ai_response(wire)
        self._record_usage(response, effective_model)
        return response

    def _refuse_unsupported(self, schema, stream, response_format, kwargs) -> None:
        if schema is not None:
            raise UnsupportedCall("schema hides stop_reason and usage; send output_config instead")
        if stream:
            raise UnsupportedCall("stream returns a stream, not a MultimodalResponse")
        if response_format is not None:
            raise UnsupportedCall("response_format is not part of the ClientOps call; send output_config instead")
        if "tools" in kwargs:
            raise UnsupportedCall("tools starts the app.ai tool loop, which ClientOps does not use")

    def _record_usage(self, response: MultimodalResponse, model: str) -> None:
        tracker = get_current_cost_tracker() or self.cost_tracker
        if tracker is None or not response.usage:
            return
        provider = derive_provider(model)
        context = get_current_context()
        usage = response.usage
        tracker.record(
            model=response.raw_response.model or model,
            provider=provider,
            routing_provider=routing_provider(provider, None),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=response.cost_usd,
            reasoner_name=context.reasoner_name if context else None,
            source="llm",
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_tokens", 0),
            cost_source=response.cost_source,
        )
