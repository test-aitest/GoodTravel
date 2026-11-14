import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from clientops.local_computer import LOCAL_RUNNER_COMMAND, PROJECT_ROOT, LocalComputer
from clientops.page import Locator, Page, PageError, ai_view, placeholder
from clientops.records import EventNotes

CDP = "http://127.0.0.1:9222"
SUPPLIER = "http://127.0.0.1:3001"
STORE = "http://127.0.0.1:3002"
ALL_SWITCHES_OFF = {"restyle": False, "renameButton": False, "decoyButton": False, "hiddenInstruction": False}
KUMO = json.loads((Path(__file__).parent / "fixtures" / "kumo-day1.json").read_text())
HOW_TO_START = "start Lightpanda with `bun run lightpanda` and the demo sites with `bun sandbox/sites/server.ts`, then run again"


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def missing() -> str | None:
    if not answers(f"{CDP}/json/version"):
        return f"Lightpanda is not serving CDP at {CDP}"
    if not (answers(f"{SUPPLIER}/__control") and answers(f"{STORE}/__control")):
        return "the demo sites are not running on 127.0.0.1:3001 and :3002"
    return None


pytestmark = pytest.mark.skipif(missing() is not None, reason=f"{missing()}: {HOW_TO_START}")


def control(site: str, path: str, body: dict | None = None) -> None:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{site}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=2):
        pass


def reset_sites() -> None:
    for site in (SUPPLIER, STORE):
        control(site, "/__control/switches", ALL_SWITCHES_OFF)
        control(site, "/__control/reset")


def demo_logins() -> dict[str, str]:
    printed = subprocess.run(
        ["bun", "-e", "import { accounts } from './sandbox/sites/catalog'; console.log(JSON.stringify(accounts))"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    accounts = {(account["client"], account["site"]): account for account in json.loads(printed)}
    return {
        "LP_SUPPLIER_USERNAME": accounts[("kumo", "supplier")]["username"],
        "LP_SUPPLIER_PASSWORD": accounts[("kumo", "supplier")]["password"],
        "LP_STORE_USERNAME": accounts[("kumo", "store")]["username"],
        "LP_STORE_PASSWORD": accounts[("kumo", "store")]["password"],
    }


@pytest.fixture
def logins() -> dict[str, str]:
    return demo_logins()


@pytest.fixture
def page(tmp_path, logins):
    vault = tmp_path / "vault.json"
    vault.write_text(json.dumps({"version": 1, "values": logins}))
    vault.chmod(0o600)
    computer = LocalComputer("kumo", vault_path=vault, workspace=tmp_path / "kumo")
    reset_sites()
    opened = Page(computer, runner_command=LOCAL_RUNNER_COMMAND)
    yield opened
    opened.close()
    reset_sites()
    socket = Path(computer.run("printf %s \"$CLIENTOPS_RUNNER_SOCKET\"", timeout_seconds=5).output)
    assert not socket.exists()


def sign_in(page: Page, site: str, user: str, password: str, wait_for: str) -> None:
    page.open(f"{site}/login")
    page.fill(Locator("textbox", user, "/login"), placeholder(f"LP_{'SUPPLIER' if site == SUPPLIER else 'STORE'}_USERNAME"))
    page.fill(Locator("textbox", password, "/login"), placeholder(f"LP_{'SUPPLIER' if site == SUPPLIER else 'STORE'}_PASSWORD"))
    page.click(Locator("button", "Sign in", "/login"), wait_for=wait_for)


def test_the_worker_reads_both_sites_saves_one_number_and_reads_it_back(page, logins):
    transcript: list[str] = []
    notes = EventNotes(lambda message, tags: transcript.append(message))

    first = page.open(f"{SUPPLIER}/login", snapshot=True)
    notes.page_seen("sign-in", first.snapshot.host, first.snapshot.html)
    sign_in(page, SUPPLIER, "Account ID", "Password", "table")
    stock = page.read_table(next=Locator("link", "Next", None))
    assert stock.pages == 3
    assert {row[0]: int(row[3]) for row in stock.rows} == {row["sku"]: row["available"] for row in KUMO["supplier"]}

    sign_in(page, STORE, "Username", "Password", 'form[action="/inventory"]')
    inventory = page.read_table()
    assert {row[0]: int(row[2]) for row in inventory.rows} == {row["sku"]: row["available"] for row in KUMO["store"]}

    view = ai_view(page.tree(), ("Kumo Coffee Roasters", "Kumo Coffee"))
    shown = json.dumps(view)
    assert not re.search(r"\d", " ".join(f"{element['name']} {element['form'] or ''}" for element in view))
    for row in KUMO["store"]:
        assert row["name"] not in shown
        assert row["sku"] not in shown
    assert [element["name"] for element in view].count("Available for [name]") == 42

    typed = page.fill(Locator("spinbutton", "Available for Ethiopia Guji 200g", "/inventory"), "6", snapshot=True)
    notes.page_seen("save", typed.snapshot.host, typed.snapshot.html)
    saved = page.click(Locator("button", "Save changes", "/inventory"), wait_for='[role="status"]')
    assert saved.page.url == f"{STORE}/inventory?saved=1"
    assert page.wait('[role="status"]') == "Saved. 1 product updated."
    after = page.read_table()
    assert {row[0]: row[2] for row in after.rows}["KC-ETH-200"] == "6"

    events = [json.loads(message)["event"] for message in transcript]
    assert [(event["type"], event["host"]) for event in events] == [("page.seen", "127.0.0.1:3001"), ("page.seen", "127.0.0.1:3002")]
    assert 'name="available-KC-ETH-200" value="6"' in events[1]["html"]
    for value in logins.values():
        assert value not in "\n".join(transcript)


def test_a_placeholder_missing_from_the_vault_is_refused_and_nothing_is_typed(page):
    page.open(f"{STORE}/login")
    with pytest.raises(PageError) as caught:
        page.fill(Locator("textbox", "Username", "/login"), placeholder("LP_HINATA_STORE_USERNAME"))
    assert caught.value.code == "placeholder-unset"
    assert caught.value.missing == ("LP_HINATA_STORE_USERNAME",)
    username = next(element for element in page.tree().elements if element.name == "Username")
    assert username.value == ""
