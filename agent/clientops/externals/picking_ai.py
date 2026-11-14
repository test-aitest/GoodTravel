import json

from clientops.chat import route
from clientops.claude_call import CHAT_REPLY_SCHEMA
from clientops.externals.fake_ai import FakeClaude
from clientops.repair import BEFORE_MARK
from clientops.trip_talk import route as trip_route

PICKS = (
    ("account ID or username", "fill", "textbox", ("Account ID", "Username")),
    ("password field", "fill", "textbox", ("Password",)),
    ("sends the sign-in form", "click", "button", ("Sign in",)),
    ("next page", "click", "link", ("Next",)),
    ("saves the changes", "click", "button", ("Save changes",)),
)
MAY_BE_MISSING = "answer with element_id null and action none"
CHAT_INPUT_TOKENS = 900
CHAT_OUTPUT_TOKENS = 300


def listed_elements(user: str) -> list[dict]:
    return [json.loads(line) for line in user.splitlines() if line.startswith("{")]


def repair_pick(user: str) -> tuple[str, str, bool] | None:
    before = next((json.loads(line.removeprefix(BEFORE_MARK)) for line in user.splitlines() if line.startswith(BEFORE_MARK)), None)
    if before is None:
        return None
    action = "fill" if "one element to fill" in user else "click"
    same_role = [element["id"] for element in listed_elements(user) if element["role"] == before["role"]]
    if not same_role:
        raise AssertionError(f"no {before['role']} left in the form Claude received")
    return same_role[0], action, len(same_role) == 1


def pick(user: str) -> tuple[str | None, str]:
    repaired = repair_pick(user)
    if repaired is not None:
        return repaired[:2]
    elements = listed_elements(user)
    for phrase, action, role, names in PICKS:
        if phrase in user:
            found = [element["id"] for element in elements if element["role"] == role and element["name"] in names]
            if found:
                return found[0], action
            if MAY_BE_MISSING in user:
                return None, "none"
            raise AssertionError(f"no {role} named {names} in what Claude received")
    raise AssertionError(f"no pick for this goal: {user.splitlines()[0]}")


def chat_call(call: dict) -> bool:
    config = call.get("output_config") or {}
    return (config.get("format") or {}).get("schema") == CHAT_REPLY_SCHEMA


def seat_call(call: dict) -> bool:
    schema = ((call.get("output_config") or {}).get("format") or {}).get("schema") or {}
    return "seat" in (schema.get("properties") or {})


def seat_answer(user: str) -> str:
    free = [seat for seat in json.loads(user)["seats"] if seat["available"]]
    for position in ("window", "aisle"):
        seat = next((seat for seat in free if seat["position"] == position), None)
        if seat is not None:
            return json.dumps({"seat": seat["id"], "reason": position}, separators=(",", ":"))
    return json.dumps({"seat": free[0]["id"] if free else "", "reason": "together"}, separators=(",", ":"))


def trip_talk_call(call: dict) -> bool:
    schema = ((call.get("output_config") or {}).get("format") or {}).get("schema") or {}
    return "reply" in (schema.get("properties") or {})


def trip_talk_answer(user: str) -> str:
    asked = json.loads(user)
    said = asked.pop("said")
    return json.dumps(trip_route(said, asked).to_json(), ensure_ascii=False, separators=(",", ":"))


def chat_answer(user: str) -> str:
    asked = json.loads(user)
    decision = route(asked["message"], [fact["id"] for fact in asked["facts"]])
    return json.dumps(decision.to_json(), ensure_ascii=False, separators=(",", ":"))


class PickingClaude:
    def __init__(self, fake: FakeClaude | None = None, *, scripts: dict | None = None) -> None:
        self.fake = fake or FakeClaude()
        self.scripts = {phrase: list(steps) for phrase, steps in (scripts or {}).items()}

    async def __call__(self, *args, **kwargs):
        user = kwargs["user"]
        planned = next((steps for phrase, steps in self.scripts.items() if phrase in user and steps), None)
        if planned:
            planned.pop(0)(self.fake, user)
        elif chat_call(kwargs):
            self.fake.reply_text(chat_answer(user), input_tokens=CHAT_INPUT_TOKENS, output_tokens=CHAT_OUTPUT_TOKENS)
        elif trip_talk_call(kwargs):
            self.fake.reply_text(trip_talk_answer(user), input_tokens=CHAT_INPUT_TOKENS, output_tokens=CHAT_OUTPUT_TOKENS)
        elif seat_call(kwargs):
            self.fake.reply_text(seat_answer(user), input_tokens=CHAT_INPUT_TOKENS, output_tokens=CHAT_OUTPUT_TOKENS)
        else:
            self._reply_pick(user)
        return await self.fake(*args, **kwargs)

    def _reply_pick(self, user: str) -> None:
        repaired = repair_pick(user)
        if repaired is not None:
            element_id, action, confident = repaired
            self.fake.reply_choice(element_id, action, confident)
            return
        element_id, action = pick(user)
        if element_id is None:
            self.fake.reply_none()
        else:
            self.fake.reply_choice(element_id, action)

    @property
    def calls(self) -> int:
        return len(self.fake.received)

    def everything_sent(self) -> str:
        return self.fake.everything_sent()
