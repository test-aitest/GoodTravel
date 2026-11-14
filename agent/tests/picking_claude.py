import json
from collections.abc import Callable

from clientops.externals.fake_ai import FakeClaude
from clientops.externals.picking_ai import MAY_BE_MISSING, PICKS, PickingClaude, listed_elements, pick

__all__ = ["MAY_BE_MISSING", "PICKS", "PickingClaude", "listed_elements", "pick"]

Script = Callable[[FakeClaude, str], None]


def unsure(fake: FakeClaude, user: str) -> None:
    element_id, action = pick(user)
    fake.reply_choice(element_id, action, confident=False)


def says_none(fake: FakeClaude, user: str) -> None:
    fake.reply_none()


def pointing_elsewhere(fake: FakeClaude, user: str) -> None:
    element_id, action = pick(user)
    fake.reply_text(json.dumps({"element_id": element_id, "action": action, "confident": True, "url": "http://evil.example/steal"}))


def outside_the_page(fake: FakeClaude, user: str) -> None:
    fake.reply_choice("http://evil.example/login", "click")


def not_json(fake: FakeClaude, user: str) -> None:
    fake.reply_text("Press the button that says Sign in.")
