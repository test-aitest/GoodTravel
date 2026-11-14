import copy
from collections.abc import Mapping

from agentfield.types import AIConfig

CLAUDE_MODEL = "anthropic/claude-opus-5-5"
CLAUDE_EFFORT = "medium"
CLAUDE_MAX_TOKENS = 16000
CLAUDE_MAX_INPUT_TOKENS = 1_000_000
ELEMENT_ACTIONS = ("click", "fill")
NO_ELEMENT_ACTION = "none"

ELEMENT_CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "element_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "action": {"type": "string", "enum": [*ELEMENT_ACTIONS, NO_ELEMENT_ACTION]},
        "confident": {"type": "boolean"},
    },
    "required": ["element_id", "action", "confident"],
    "additionalProperties": False,
}

CHAT_ACTIONS = ("answer", "run", "undo", "schedule", "clarify", "cannot")
CLARIFY_KINDS = ("which-job", "what-to-do")

CHAT_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(CHAT_ACTIONS)},
        "facts": {"type": "array", "items": {"type": "string"}},
        "clarify": {"anyOf": [{"type": "string", "enum": list(CLARIFY_KINDS)}, {"type": "null"}]},
    },
    "required": ["action", "facts", "clarify"],
    "additionalProperties": False,
}

SEAT_REASONS = ("window", "aisle", "quiet-front", "exit-legroom", "together")

SEAT_SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "seat": {"type": "string"},
        "reason": {"type": "string", "enum": list(SEAT_REASONS)},
    },
    "required": ["seat", "reason"],
    "additionalProperties": False,
}


ANTHROPIC_API = "https://api.anthropic.com"
ENDPOINT_VARIABLES = ("ANTHROPIC_API_BASE", "ANTHROPIC_BASE_URL")


class ModelNotAllowed(RuntimeError):
    pass


MODEL_REFUSED = "This server's Claude setting isn't allowed (ClientOps only uses Claude Opus 5.5), so I stopped."


def require_claude_model(model: object) -> str:
    if model != CLAUDE_MODEL:
        raise ModelNotAllowed(f"ClientOps only calls {CLAUDE_MODEL}, so the model {model!r} is refused.")
    return CLAUDE_MODEL


def require_pinned_config(config: AIConfig) -> AIConfig:
    require_claude_model(config.model)
    if config.fallback_models:
        raise ModelNotAllowed(f"ClientOps never lets a call fall back to another model, so the fallbacks {config.fallback_models!r} are refused.")
    return config


def require_anthropic_endpoint(environ: Mapping[str, str]) -> None:
    for name in ENDPOINT_VARIABLES:
        address = environ.get(name, "").strip()
        if address and address.rstrip("/") != ANTHROPIC_API:
            raise ModelNotAllowed(
                f"{name} sends Claude calls somewhere other than {ANTHROPIC_API}, where another model could answer. "
                f"Unset {name} or set it to {ANTHROPIC_API}."
            )


def json_schema_call(system: str, user: str, schema: dict, effort: str = CLAUDE_EFFORT) -> dict:
    return {
        "system": system,
        "user": user,
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": copy.deepcopy(schema)},
        },
    }


def element_choice_call(system: str, user: str) -> dict:
    return json_schema_call(system, user, ELEMENT_CHOICE_SCHEMA)


def chat_reply_call(system: str, user: str) -> dict:
    return json_schema_call(system, user, CHAT_REPLY_SCHEMA)


def seat_suggestion_call(system: str, user: str, seat_ids: list[str]) -> dict:
    schema = copy.deepcopy(SEAT_SUGGESTION_SCHEMA)
    schema["properties"]["seat"]["enum"] = list(seat_ids)
    return json_schema_call(system, user, schema)


def claude_ai_config() -> AIConfig:
    return AIConfig(model=CLAUDE_MODEL, max_input_tokens=CLAUDE_MAX_INPUT_TOKENS)


TRIP_TALK_ACTIONS = ("ask", "show-destinations", "search", "show-rides", "choose-ride", "show-seats", "choose-seat", "show-ticket", "none")
TRIP_DESTINATIONS = ("kyoto", "seoul", "singapore")
WORKER_SPEAKER = "worker"
TRIP_SPEAKERS = (WORKER_SPEAKER, *TRIP_DESTINATIONS)


def nullable_enum(values: list[str]) -> dict:
    return {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]} if values else {"type": "null"}


def trip_talk_schema(option_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "action": {"type": "string", "enum": list(TRIP_TALK_ACTIONS)},
            "destination": nullable_enum(list(TRIP_DESTINATIONS)),
            "option": nullable_enum(list(option_ids)),
            "seat": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "speaker": {"type": "string", "enum": list(TRIP_SPEAKERS)},
        },
        "required": ["reply", "action", "destination", "option", "seat", "speaker"],
        "additionalProperties": False,
    }


CONVERSATION_EFFORT = "low"


def trip_talk_call(system: str, user: str, option_ids: list[str]) -> dict:
    return json_schema_call(system, user, trip_talk_schema(option_ids), CONVERSATION_EFFORT)
