import os
from functools import cache
from typing import Any, Literal

from daytona import Daytona, DaytonaConfig

from clientops.claude_call import require_anthropic_endpoint, require_pinned_config
from clientops.externals.fake_daytona import FakeDaytona

EXTERNALS_MODE_VARIABLE = "CLIENTOPS_EXTERNALS"
DAYTONA_DEFAULT_API_URL = "https://app.daytona.io/api"
ANTHROPIC_KEY_VARIABLE = "ANTHROPIC_API_KEY"

ExternalsMode = Literal["real", "fake"]


class MissingExternalKey(RuntimeError):
    pass


def externals_mode() -> ExternalsMode:
    value = os.environ.get(EXTERNALS_MODE_VARIABLE, "")
    if value in ("", "real"):
        return "real"
    if value == "fake":
        return "fake"
    raise ValueError(f"{EXTERNALS_MODE_VARIABLE} must be 'fake' or 'real' (unset means real), not {value!r}")


@cache
def shared_fake_daytona() -> FakeDaytona:
    return FakeDaytona()


def daytona_client() -> Daytona | FakeDaytona:
    if externals_mode() == "fake":
        return shared_fake_daytona()
    api_key = os.environ.get("DAYTONA_API_KEY", "")
    if not api_key:
        raise MissingExternalKey(
            "DAYTONA_API_KEY is not set. Put it in .env.local, "
            f"or set {EXTERNALS_MODE_VARIABLE}=fake to use the recorded Daytona samples."
        )
    api_url = os.environ.get("DAYTONA_API_URL") or DAYTONA_DEFAULT_API_URL
    return Daytona(DaytonaConfig(api_key=api_key, api_url=api_url))


def require_pinned_claude(app: Any) -> None:
    config = getattr(app, "ai_config", None)
    if config is not None:
        require_pinned_config(config)
    if externals_mode() == "real":
        require_anthropic_endpoint(os.environ)


def claude_ai(app: Any) -> Any:
    require_pinned_claude(app)
    if externals_mode() == "fake":
        from clientops.externals.picking_ai import PickingClaude

        return PickingClaude()
    if not os.environ.get(ANTHROPIC_KEY_VARIABLE, ""):
        raise MissingExternalKey(
            f"{ANTHROPIC_KEY_VARIABLE} is not set. Put it in .env.local, "
            f"or set {EXTERNALS_MODE_VARIABLE}=fake to use the fake Claude that picks elements by name."
        )
    return app.ai
