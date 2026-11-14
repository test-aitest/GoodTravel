import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from itertools import count
from pathlib import Path

import pytest
from websockets.sync.client import connect

from clientops.externals.fake_lightpanda import RECORDINGS, ScriptRun

PROJECT = Path(__file__).resolve().parents[2]
BINARY = PROJECT / "vendor" / "lightpanda" / "lightpanda"
CDP = os.environ.get("LIGHTPANDA_CDP", "http://127.0.0.1:9222").rstrip("/")
STORE = "http://127.0.0.1:3002"
LOGIN_KEYS = ("LP_STORE_USERNAME", "LP_STORE_PASSWORD")
HOW_TO_START = (
    "start the demo sites with `bun sandbox/sites/server.ts` and Lightpanda with `bun run lightpanda`, "
    "set LP_STORE_USERNAME and LP_STORE_PASSWORD to the store admin login, then run again"
)
ALL_SWITCHES_OFF = {"restyle": False, "renameButton": False, "decoyButton": False, "hiddenInstruction": False}


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read())


def post_json(url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read())


def reachable(url: str) -> bool:
    try:
        get_json(url)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return True


def missing_for_cdp() -> str | None:
    if not reachable(f"{CDP}/json/version"):
        return f"Lightpanda is not serving CDP at {CDP}"
    return missing_for_sites()


def missing_for_sites() -> str | None:
    if not reachable(f"{STORE}/__control"):
        return f"the demo store admin is not running at {STORE}"
    unset = [key for key in LOGIN_KEYS if not os.environ.get(key)]
    if unset:
        return f"{' and '.join(unset)} not set"
    return None


def missing_for_script() -> str | None:
    if not BINARY.exists():
        return f"no Lightpanda binary at {BINARY}"
    return missing_for_sites()


def recorded(name: str):
    return json.loads((RECORDINGS / f"{name}.json").read_text())


def recorded_result(name: str, method: str, params: dict, occurrence: int = 0):
    results = [
        call["response"]["result"]
        for call in recorded(name)["calls"]
        if call["request"]["method"] == method and call["request"]["params"] == params
    ]
    return results[occurrence]


def without_node_ids(tree: str) -> str:
    return re.sub(r"(?m)^(\s*)\d+ ", r"\1# ", tree)


def reset_sites():
    post_json(f"{STORE}/__control/reset")
    post_json(f"{STORE}/__control/switches", ALL_SWITCHES_OFF)


class Browser:
    def __init__(self, socket) -> None:
        self._socket = socket
        self._ids = count(1)
        self.session_id: str | None = None

    def send(self, method: str, params: dict | None = None, browser: bool = False):
        message = {"id": next(self._ids), "method": method, "params": params or {}}
        if self.session_id and not browser:
            message["sessionId"] = self.session_id
        self._socket.send(json.dumps(message))
        while True:
            reply = json.loads(self._socket.recv(timeout=15))
            if reply.get("id") == message["id"]:
                break
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply["result"]


@pytest.mark.skipif(missing_for_cdp() is not None, reason=f"{missing_for_cdp()}: {HOW_TO_START}")
def test_real_lightpanda_answers_like_the_recorded_store_save():
    reset_sites()
    with connect(get_json(f"{CDP}/json/version")["webSocketDebuggerUrl"], open_timeout=5, max_size=None) as socket:
        try:
            drive_store_save(Browser(socket))
        finally:
            reset_sites()


def drive_store_save(browser: Browser):
    context = browser.send("Target.createBrowserContext", browser=True)["browserContextId"]
    target = browser.send("Target.createTarget", {"url": "about:blank", "browserContextId": context}, browser=True)["targetId"]
    browser.session_id = browser.send("Target.attachToTarget", {"targetId": target, "flatten": True}, browser=True)["sessionId"]
    browser.send("Page.enable")
    browser.send("Page.navigate", {"url": f"{STORE}/login"})
    browser.send("LP.waitUntil", {"until": "load"})
    sign_in = {element.get("elementName") or element["name"]: element["backendNodeId"] for element in browser.send("LP.getInteractiveElements")["elements"]}
    assert browser.send("LP.fillNode", {"backendNodeId": sign_in["username"], "text": os.environ["LP_STORE_USERNAME"]}) == []
    assert browser.send("LP.fillNode", {"backendNodeId": sign_in["password"], "text": os.environ["LP_STORE_PASSWORD"]}) == []
    assert browser.send("LP.clickNode", {"backendNodeId": sign_in["Sign in"]}) == []
    browser.send("LP.waitForSelector", {"selector": 'form[action="/inventory"]'})
    assert browser.send("Target.getTargetInfo", {"targetId": target}, browser=True)["targetInfo"]["url"] == f"{STORE}/inventory"

    form = {"format": "text", "interactiveOnly": True}
    live_form = browser.send("LP.getSemanticTree", form)["semanticTree"]
    assert without_node_ids(live_form) == without_node_ids(recorded_result("store-save", "LP.getSemanticTree", form)["semanticTree"])

    elements = browser.send("LP.getInteractiveElements")["elements"]
    field = next(element for element in elements if element.get("elementName") == "available-KC-ETH-200")
    save = next(element for element in elements if element["role"] == "button" and element["name"] == "Save changes")
    recorded_field = next(
        element
        for element in recorded_result("store-save", "LP.getInteractiveElements", {}, occurrence=1)["elements"]
        if element.get("elementName") == "available-KC-ETH-200"
    )
    assert {key: value for key, value in field.items() if key != "backendNodeId"} == {
        key: value for key, value in recorded_field.items() if key != "backendNodeId"
    }

    assert browser.send("LP.fillNode", {"backendNodeId": field["backendNodeId"], "text": "6"}) == []
    assert browser.send("LP.getNodeDetails", {"backendNodeId": field["backendNodeId"]})["nodeDetails"]["value"] == "6"
    assert browser.send("LP.clickNode", {"backendNodeId": save["backendNodeId"]}) == []
    status = browser.send("LP.waitForSelector", {"selector": '[role="status"]'})["backendNodeId"]
    assert browser.send("Target.getTargetInfo", {"targetId": target}, browser=True)["targetInfo"]["url"] == f"{STORE}/inventory?saved=1"
    status_text = browser.send("LP.getSemanticTree", {"format": "text", "backendNodeId": status})["semanticTree"]
    assert without_node_ids(status_text) == "# status\n # 'Saved. 1 product updated.'\n"
    after_save = browser.send("LP.getSemanticTree", form)["semanticTree"]
    assert without_node_ids(after_save) == without_node_ids(recorded_result("store-save", "LP.getSemanticTree", form, occurrence=2)["semanticTree"])

    browser.send("Target.closeTarget", {"targetId": target}, browser=True)
    browser.send("Target.disposeBrowserContext", {"browserContextId": context}, browser=True)


def run_recorded_script(switches: dict) -> ScriptRun:
    reset_sites()
    if switches:
        post_json(f"{STORE}/__control/switches", switches)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env.update({key: os.environ[key] for key in LOGIN_KEYS})
    completed = subprocess.run(
        [str(BINARY), "run", str(RECORDINGS / "store-set-available.js")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return ScriptRun(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


@pytest.mark.skipif(missing_for_script() is not None, reason=f"{missing_for_script()}: {HOW_TO_START}")
def test_lightpanda_run_gives_the_recorded_output_and_exit_codes():
    runs = {run["scenario"]: run for run in recorded("pandascript-runs")["runs"]}
    try:
        for scenario in ("saved", "renamed-button", "restyled-with-decoy"):
            run = run_recorded_script(runs[scenario]["switches"])
            assert (run.exit_code, run.stdout, run.stderr) == (
                runs[scenario]["exitCode"],
                runs[scenario]["stdout"],
                runs[scenario]["stderr"],
            ), scenario
    finally:
        reset_sites()
