import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from agentfield import Agent

from clientops.chat import SYSTEM_PROMPT, Decision, judged, read_catalog, read_message, route, user_prompt, well_formed
from clientops.check import TIMEOUT_RETRIES_VARIABLE, agentfield_server, message_usd, secret_values
from clientops.claude_call import chat_reply_call, claude_ai_config
from clientops.cost import claude_bill
from clientops.externals import ANTHROPIC_KEY_VARIABLE, claude_ai, externals_mode
from clientops.plan import finish_reason
from clientops.response_shapes import recording_anthropic, without_secrets

CONTRACT = Path(__file__).resolve().parents[1] / "tests" / "contract" / "chat_reply.json"
SPEND_LIMIT_USD = Decimal("0.60")
PROBE_NODE = "clientops-chat-live"
COMPARED = ("action", "facts", "clarify")
EXTRA_MESSAGES = {
    "extra-unknown-company": "How is Acme Corp doing?",
    "extra-supplier-is-in-scope": "Did the supplier's portal change?",
}


def say(entry: dict) -> None:
    print(without_secrets(json.dumps(entry, ensure_ascii=False), secret_values(os.environ)), flush=True)


def probe_agent() -> Agent:
    config = claude_ai_config().model_copy(update={"rate_limit_max_retries": 0, "retry_attempts": 0})
    return Agent(node_id=PROBE_NODE, agentfield_server=agentfield_server(os.environ), ai_config=config)


def raw_decision(text: str) -> tuple[Decision | None, Any]:
    try:
        reply = json.loads(text)
    except (TypeError, ValueError):
        return None, text
    if not isinstance(reply, dict):
        return None, reply
    facts = reply.get("facts")
    return Decision(str(reply.get("action")), tuple(facts) if isinstance(facts, list) else (), reply.get("clarify")), reply


def shown(decision: Decision | dict | None) -> dict | None:
    if decision is None:
        return None
    if isinstance(decision, Decision):
        return decision.to_json()
    return {key: decision[key] for key in COMPARED}


async def ask(ai: Any, example: dict) -> dict:
    given = example["input"]
    message = read_message(given["message"])
    catalog = read_catalog(given["client_id"], given["facts"])
    ids = tuple(fact.id for fact in catalog)
    call = chat_reply_call(system=SYSTEM_PROMPT, user=user_prompt(message, catalog))
    with recording_anthropic() as wire:
        response = await ai(**call)
    raw, parsed = raw_decision(response.text or "")
    usage = (wire.message or {}).get("usage") or {}
    request = wire.request or {}
    return {
        "name": example["name"],
        "message": message,
        "facts": list(ids),
        "contract": shown(example.get("output")),
        "contractDecidedBy": (example.get("output") or {}).get("decidedBy"),
        "rules": shown(route(message, ids)),
        "claudeRaw": parsed,
        "claude": shown(judged(response, catalog)),
        "wellFormed": raw is not None and well_formed(raw, ids),
        "finish": finish_reason(response),
        "stopReason": (wire.message or {}).get("stop_reason"),
        "contentTypes": [block.get("type") for block in (wire.message or {}).get("content") or []],
        "topLevel": sorted(wire.message or {}),
        "usageFields": sorted(usage),
        "model": (wire.message or {}).get("model"),
        "sentModel": request.get("model"),
        "sentOutputConfigMatches": request.get("output_config") == call["output_config"],
        "inputTokens": int(usage.get("input_tokens") or 0),
        "cacheReadTokens": int(usage.get("cache_read_input_tokens") or 0),
        "cacheWriteTokens": int(usage.get("cache_creation_input_tokens") or 0),
        "outputTokens": int(usage.get("output_tokens") or 0),
        "thinkingTokens": int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0),
        "usd": str(claude_bill(response).usd),
        "wireUsd": str(message_usd(usage)),
    }


async def main() -> int:
    if externals_mode() != "real" or not os.environ.get(ANTHROPIC_KEY_VARIABLE):
        print("This probe talks to the real Claude only: set ANTHROPIC_API_KEY and leave CLIENTOPS_EXTERNALS unset.", file=sys.stderr)
        return 1
    os.environ[TIMEOUT_RETRIES_VARIABLE] = "0"
    ai = claude_ai(probe_agent())
    chosen = set(sys.argv[1:])
    recorded = json.loads(CONTRACT.read_text())["examples"]
    extras = [
        {"name": name, "input": {**recorded[0]["input"], "message": message}, "output": None}
        for name, message in EXTRA_MESSAGES.items()
    ]
    examples = [example for example in [*recorded, *extras] if not chosen or example["name"] in chosen]
    spent = Decimal(0)
    calls = 0
    for example in examples:
        if spent >= SPEND_LIMIT_USD:
            say({"name": example["name"], "skipped": f"spend reached ${spent:.4f}"})
            continue
        calls += 1
        try:
            row = await ask(ai, example)
        except Exception as error:
            say({"name": example["name"], "error": f"{type(error).__name__}: {error}"[:400]})
            continue
        spent += Decimal(row["usd"])
        say(row)
    say({"calls": calls, "spentUsd": f"{spent:.4f}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
