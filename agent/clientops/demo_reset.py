import asyncio
import json
import os
import shlex
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from .boundary import ClientBoundary, ClientNotSetUp, ComputerKind, UnknownClient, computer_kind
from .computers import STORE_URL, SUPPLIER_URL

DEMO_RESET_VARIABLE = "CLIENTOPS_DEMO_RESET"
SITE_RESETS: tuple[tuple[str, dict[str, Any] | None], ...] = (
    ("/__control/switches", {"restyle": False, "renameButton": False, "decoyButton": False, "hiddenInstruction": False}),
    ("/__control/reset", None),
    ("/__control/day", {"day": 1}),
)
SITE_TIMEOUT_SECONDS = 10


def demo_reset_enabled(env: Mapping[str, str] = os.environ) -> bool:
    return env.get(DEMO_RESET_VARIABLE) == "on"


def post_locally(url: str, body: dict[str, Any] | None) -> None:
    data = b"" if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="POST", headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=SITE_TIMEOUT_SECONDS):
        return


def curl_command(url: str, body: dict[str, Any] | None) -> str:
    data = "" if body is None else json.dumps(body)
    return f"curl -fsS -o /dev/null -X POST -H 'content-type: application/json' --data {shlex.quote(data)} {shlex.quote(url)}"


def reset_demo_sites(boundary: ClientBoundary) -> None:
    urls = [f"{site}{path}" for site in (SUPPLIER_URL, STORE_URL) for path, _ in SITE_RESETS]
    bodies = [body for _ in (SUPPLIER_URL, STORE_URL) for _, body in SITE_RESETS]
    if computer_kind() is ComputerKind.LOCAL:
        for url, body in zip(urls, bodies, strict=True):
            post_locally(url, body)
        return
    computer = boundary.computer()
    for url, body in zip(urls, bodies, strict=True):
        computer.run(curl_command(url, body), timeout_seconds=SITE_TIMEOUT_SECONDS)


async def reset_demo_client(
    client_id: str,
    *,
    enabled: bool,
    resolve: Callable[[str], ClientBoundary],
    memory_for: Callable[[ClientBoundary], Any],
    reset_sites: Callable[[ClientBoundary], None] = reset_demo_sites,
) -> dict[str, Any]:
    if not enabled:
        return {"clientId": client_id, "ok": False, "problem": "not-enabled"}
    try:
        boundary = resolve(client_id)
    except UnknownClient:
        return {"clientId": client_id, "ok": False, "problem": "unknown-client"}
    except ClientNotSetUp:
        return {"clientId": client_id, "ok": True, "sites": "skipped", "forgotten": 0}
    memory = memory_for(boundary)
    keys = await memory.list_keys()
    for key in keys:
        await memory.delete(key)
    await asyncio.to_thread(reset_sites, boundary)
    return {"clientId": client_id, "ok": True, "sites": "reset", "forgotten": len(keys)}
