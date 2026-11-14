import hashlib
import hmac
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from clientops.boundary import MEMORY_SCOPE, MEMORY_SCOPE_PREFIX
from clientops.procedure import PROCEDURE_KEY
from clientops.records import EVENT_NOTE_TAG
from test_first_run_integration import HOW_TO_START, KUMO, REQUEST, missing, put_local_vault, reset_sites
from test_integration import find_notes

SERVER = os.environ.get("AGENTFIELD_SERVER", "http://localhost:8080").rstrip("/")
SECRET = os.environ.get("AGENTFIELD_APPROVAL_WEBHOOK_SECRET", "")
AGENT_DIR = Path(__file__).resolve().parents[1]
AGENT_PORT = 8001
CALLBACK = f"http://host.docker.internal:{AGENT_PORT}"
RUN_WITH_SECRET = "bun --env-file=.env.local run test:agent tests/test_approval_agentfield.py"
RESTART_AGENTFIELD = "`bun run agentfield:stop`, then `bun run agentfield`"
TERMINAL = {"succeeded", "failed", "cancelled", "timeout"}


def call(method: str, path: str, body: dict | None = None, *, raw: bytes | None = None, headers: dict | None = None) -> tuple[int, object]:
    sent = {"Content-Type": "application/json", **(headers or {})}
    if os.environ.get("AGENTFIELD_API_KEY"):
        sent["X-API-Key"] = os.environ["AGENTFIELD_API_KEY"]
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(urllib.request.Request(SERVER + path, data=data, headers=sent, method=method), timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def signed_approval(approval_id: str) -> tuple[int, object]:
    body = json.dumps({"requestId": approval_id, "decision": "approved", "feedback": ""}, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return call("POST", "/api/v1/webhooks/approval-response", raw=body, headers={"X-Webhook-Signature": signature})


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def reason_to_skip() -> str | None:
    try:
        call("GET", "/api/v1/nodes")
    except (urllib.error.URLError, OSError):
        return f"AgentField is not running at {SERVER}: start it with `bun run agentfield`"
    if not SECRET:
        return f"AGENTFIELD_APPROVAL_WEBHOOK_SECRET is not set for this test: run `{RUN_WITH_SECRET}`"
    if missing() is not None:
        return f"{missing()}: {HOW_TO_START}"
    if port_in_use(AGENT_PORT):
        return f"something already listens on :{AGENT_PORT}; stop `bun run agent` first, because this test starts and restarts its own agent"
    status, _ = signed_approval(f"apr-probe-{secrets.token_hex(4)}")
    if status == 503:
        return f"AgentField was started without AGENTFIELD_APPROVAL_WEBHOOK_SECRET: run {RESTART_AGENTFIELD}"
    if status == 401:
        return f"AGENTFIELD_APPROVAL_WEBHOOK_SECRET differs between .env.local and AgentField: run {RESTART_AGENTFIELD}"
    return None


SKIP = reason_to_skip()
pytestmark = pytest.mark.skipif(SKIP is not None, reason=SKIP or "")


class AgentProcess:
    def __init__(self, data_dir: Path, log: Path) -> None:
        env = {
            **os.environ,
            "AGENTFIELD_SERVER": SERVER,
            "CLIENTOPS_EXTERNALS": "fake",
            "CLIENTOPS_COMPUTERS": "local",
            "CLIENTOPS_DATA_DIR": str(data_dir),
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "AGENT_CALLBACK_URL": CALLBACK,
            "CLIENTOPS_AGENT_PORT": str(AGENT_PORT),
        }
        env.pop("AGENTFIELD_APPROVAL_WEBHOOK_SECRET", None)
        env.pop("ANTHROPIC_API_KEY", None)
        self.log = log.open("ab")
        self.process = subprocess.Popen([sys.executable, "serve.py"], cwd=AGENT_DIR, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while not self.ready():
            assert self.process.poll() is None, f"the agent exited early; see {log}"
            assert time.monotonic() < deadline, f"the agent did not register within 60 s; see {log}"
            time.sleep(0.5)

    def ready(self) -> bool:
        if not port_in_use(AGENT_PORT):
            return False
        _, listing = call("GET", "/api/v1/nodes")
        return any(node.get("id") == "clientops" and node.get("health_status") == "active" for node in (listing or {}).get("nodes", []))

    def stop(self, how: int = signal.SIGTERM, wait_seconds: float = 90) -> None:
        if self.process.poll() is None:
            self.process.send_signal(how)
            try:
                self.process.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.log.close()


def forget_saved_steps(client_id: str) -> None:
    status, answer = call("POST", "/api/v1/memory/delete", {"key": PROCEDURE_KEY, "scope": MEMORY_SCOPE}, headers={"X-Actor-ID": f"{MEMORY_SCOPE_PREFIX}{client_id}"})
    assert status in (200, 204, 404), answer


@pytest.fixture
def world(tmp_path, monkeypatch):
    data_dir = tmp_path / "clientops-data"
    monkeypatch.setenv("CLIENTOPS_DATA_DIR", str(data_dir))
    put_local_vault("kumo")
    reset_sites()
    forget_saved_steps("kumo")
    agents: list[AgentProcess] = []

    def start() -> AgentProcess:
        agents.append(AgentProcess(data_dir, tmp_path / "agent.log"))
        return agents[-1]

    yield start
    for agent in agents:
        agent.stop(signal.SIGKILL)
    reset_sites()
    forget_saved_steps("kumo")


def start_job(reasoner: str, payload: dict) -> str:
    status, started = call("POST", f"/api/v1/execute/async/clientops.{reasoner}", {"input": payload})
    assert status == 202, started
    return started["execution_id"]


def execution(execution_id: str) -> dict:
    _, found = call("GET", f"/api/v1/executions/{execution_id}")
    return found


def events_of(execution_id: str) -> list[dict]:
    _, record = call("POST", "/api/v1/agentic/query", {"resource": "executions", "filters": {"execution_id": execution_id}})
    notes = find_notes(record, execution_id) or []
    payloads = sorted((json.loads(note["message"]) for note in notes if EVENT_NOTE_TAG in (note.get("tags") or [])), key=lambda payload: payload["n"])
    return payloads


def wait_until(check, seconds: float, what: str):
    deadline = time.monotonic() + seconds
    while True:
        found = check()
        if found:
            return found
        assert time.monotonic() < deadline, f"{what} did not happen within {seconds:g} s"
        time.sleep(0.5)


def waiting_for_approval(execution_id: str) -> str:
    def asked():
        payloads = events_of(execution_id)
        approval = next((payload["event"] for payload in payloads if payload["event"]["type"] == "approval.requested"), None)
        return approval["approvalId"] if approval and execution(execution_id)["status"] == "waiting" else None

    return wait_until(asked, 60, "the job waiting at the approval")


def finished(execution_id: str, seconds: float = 150) -> tuple[dict, list[dict]]:
    def ended():
        found = execution(execution_id)
        return found if found["status"] in TERMINAL else None

    done = wait_until(ended, seconds, "the job finishing")
    expected = (done.get("result") or {}).get("events")

    def every_note():
        found = events_of(execution_id)
        return found if found and (expected is None or len(found) >= expected) else None

    return done, wait_until(every_note, 10, "all notes arriving")


def after_approval(payloads: list[dict]) -> list[dict]:
    index = next(i for i, payload in enumerate(payloads) if payload["event"]["type"] == "approval.requested")
    return [payload["event"] for payload in payloads[index + 1 :]]


def test_a_signed_approval_lets_the_waiting_job_save_check_every_row_and_undo(world):
    world()
    job_id = start_job("run_job", {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": REQUEST})
    approval_id = waiting_for_approval(job_id)

    status, answer = signed_approval(approval_id)

    assert status == 200 and answer["status"] == "processed", answer
    done, payloads = finished(job_id)
    assert done["status"] == "succeeded"
    assert [payload["n"] for payload in payloads] == list(range(1, len(payloads) + 1))
    events = after_approval(payloads)
    assert [event["type"] for event in events] == ["approval.answered", "step.decided", "step.done", "step.started", "page.seen", "page.seen", "write.done", "verify.done", "step.done", "procedure.saved", "cost.recorded"]
    assert events[6] == {"type": "write.done", "stepId": "save", "written": 12}
    assert events[7] == {"type": "verify.done", "stepId": "save", "checked": 12, "matched": 12}

    undo_id = start_job("undo_sync", {"client_id": "kumo"})
    undone, undo_payloads = finished(undo_id)
    assert undone["status"] == "succeeded"
    assert [payload["event"] for payload in undo_payloads if payload["event"]["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "undo", "checked": 12, "matched": 12}]

    second = start_job("run_job", {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": REQUEST})
    waiting_for_approval(second)
    [approval] = [payload["event"] for payload in events_of(second) if payload["event"]["type"] == "approval.requested"]
    assert approval["changes"] == KUMO["changes"]
    status, _ = call("POST", f"/api/v1/executions/{second}/cancel", {"reason": "test cleanup"})
    assert status == 200


def test_stop_while_waiting_ends_the_job_and_a_late_approval_changes_nothing(world):
    world()
    job_id = start_job("run_job", {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": REQUEST})
    approval_id = waiting_for_approval(job_id)

    status, _ = call("POST", f"/api/v1/executions/{job_id}/cancel", {"reason": "Stopped from ClientOps"})

    assert status == 200
    done, payloads = finished(job_id, 30)
    assert done["status"] == "cancelled"
    stopped = wait_until(lambda: [p["event"] for p in events_of(job_id) if p["event"]["type"] == "job.failed"], 10, "the stop note")
    assert stopped[-1]["reason"] == "stopped"
    late_status, late = signed_approval(approval_id)
    assert late_status == 200 and late["status"] == "already_processed"
    assert all(payload["event"]["type"] != "write.done" for payload in events_of(job_id))


def test_the_waiting_job_survives_an_agent_restart_and_finishes_after_the_ok(world):
    first = world()
    job_id = start_job("run_job", {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": REQUEST})
    approval_id = waiting_for_approval(job_id)
    notes_before = len(events_of(job_id))

    first.stop(signal.SIGTERM)

    assert execution(job_id)["status"] == "waiting"
    world()
    status, answer = signed_approval(approval_id)
    assert status == 200 and answer["status"] == "processed", answer
    done, payloads = finished(job_id)
    assert done["status"] == "succeeded"
    assert [payload["n"] for payload in payloads] == list(range(1, len(payloads) + 1))
    assert len(payloads) > notes_before
    events = after_approval(payloads)
    assert [event for event in events if event["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "save", "checked": 12, "matched": 12}]


def test_a_waiting_job_survives_a_restart_and_an_ok_given_more_than_a_minute_later(world):
    first = world()
    job_id = start_job("run_job", {"client_id": "kumo", "client_name": "Kumo Coffee Roasters", "request": REQUEST})
    approval_id = waiting_for_approval(job_id)

    first.stop(signal.SIGTERM)
    world()
    time.sleep(75)

    assert execution(job_id)["status"] == "waiting"
    status, answer = signed_approval(approval_id)
    assert status == 200 and answer["status"] == "processed", answer
    done, payloads = finished(job_id)
    assert done["status"] == "succeeded"
    assert [payload["event"] for payload in payloads if payload["event"]["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "save", "checked": 12, "matched": 12}]
