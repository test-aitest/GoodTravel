import json
import os
import time
import urllib.error
import urllib.request

import pytest

from clientops.records import EVENT_NOTE_TAG

SERVER = os.environ.get("AGENTFIELD_SERVER", "http://localhost:8080").rstrip("/")
NOT_SET_UP_JOB = {"client_id": "sora", "client_name": "Sora Outdoor", "request": "Sync today's supplier stock to the store admin."}
HOW_TO_START = "start the control plane with `bun run agentfield` and the agent with `bun run agent`, then run again"


def request(method: str, path: str, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if os.environ.get("AGENTFIELD_API_KEY"):
        headers["X-API-Key"] = os.environ["AGENTFIELD_API_KEY"]
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(SERVER + path, data=data, headers=headers, method=method), timeout=5) as response:
        return response.status, json.loads(response.read() or b"null")


def clientops_is_ready():
    try:
        _, listing = request("GET", "/api/v1/nodes")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return any(node.get("id") == "clientops" and node.get("health_status") == "active" for node in listing.get("nodes", []))


def find_notes(value, execution_id):
    if isinstance(value, dict):
        if value.get("execution_id") == execution_id and isinstance(value.get("notes"), list):
            return value["notes"]
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        return None
    for item in values:
        found = find_notes(item, execution_id)
        if found is not None:
            return found
    return None


@pytest.mark.skipif(not clientops_is_ready(), reason=f"AgentField at {SERVER} has no active clientops node: {HOW_TO_START}")
def test_one_job_runs_end_to_end_through_agentfield():
    status, started = request("POST", "/api/v1/execute/async/clientops.run_job", {"input": NOT_SET_UP_JOB})
    assert status == 202
    execution_id = started["execution_id"]

    deadline = time.monotonic() + 30
    while True:
        _, execution = request("GET", f"/api/v1/executions/{execution_id}")
        if execution["status"] in {"succeeded", "failed", "cancelled", "timeout"}:
            break
        assert time.monotonic() < deadline, f"still {execution['status']} after 30 s"
        time.sleep(0.25)
    assert execution["status"] == "succeeded"
    assert execution["agent_node_id"] == "clientops"

    expected = execution["result"]["events"]
    deadline = time.monotonic() + 5
    while True:
        _, record = request("POST", "/api/v1/agentic/query", {"resource": "executions", "filters": {"execution_id": execution_id}})
        notes = find_notes(record, execution_id) or []
        events = sorted(
            (json.loads(note["message"]) for note in notes if EVENT_NOTE_TAG in (note.get("tags") or [])),
            key=lambda payload: payload["n"],
        )
        if len(events) >= expected or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    assert started["run_id"]
    assert [payload["n"] for payload in events] == list(range(1, expected + 1))
    assert [payload["event"]["type"] for payload in events] == ["job.created", "job.failed"]
    assert events[-1]["event"]["message"] == "Sora Outdoor is not set up yet. Set up its computer and logins first."
