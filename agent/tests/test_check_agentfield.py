import json
import subprocess
import threading
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clientops.check import (
    AGENTFIELD_CONTAINER,
    CONTAINER_TEMPLATE,
    SETUP_STATUS,
    ContainerSettings,
    ContainerUnreadable,
    ControlPlane,
    Services,
    read_agentfield_container,
    run,
)

KEYS = {"ANTHROPIC_API_KEY": "sk-ant-test-not-a-real-key", "DAYTONA_API_KEY": "dtn_test_not_a_real_key"}
WEBHOOK_SECRET = "whsec-test-not-real-1234"
WITH_SECRET = {**KEYS, "AGENTFIELD_APPROVAL_WEBHOOK_SECRET": WEBHOOK_SECRET}
STARTED = {
    "execution_id": "exec_check_1",
    "run_id": "run_check_1",
    "workflow_id": "run_check_1",
    "status": "queued",
    "target": "clientops.setup_status",
    "type": "reasoner",
    "created_at": "2026-11-14T02:00:00Z",
    "enqueued_at": "2026-11-14T02:00:00Z",
    "webhook_registered": False,
}
SUCCEEDED = {
    "execution_id": "exec_check_1",
    "run_id": "run_check_1",
    "agent_node_id": "clientops",
    "status": "succeeded",
    "result": {"clientId": "kumo", "computer": "ready", "logins": None},
    "started_at": "2026-11-14T02:00:00Z",
    "completed_at": "2026-11-14T02:00:00Z",
    "duration_ms": 412,
    "webhook_registered": False,
}
SETTINGS_OFF = ContainerSettings(telemetry="false", webhook_secret_set=True)
AGAIN = "then run bun run check again."
RESTART = "Restart it with bun run agentfield:stop and bun run agentfield, " + AGAIN


@dataclass(frozen=True)
class Received:
    method: str
    path: str
    headers: dict
    body: object


class ControlPlaneStub:
    def __init__(self):
        self.requests: list[Received] = []
        self._replies: dict[str, deque] = {"POST": deque(), "GET": deque()}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def reply(self, method: str, status: int, body: object) -> None:
        self._replies[method].append((status, body))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, method: str, body: object) -> None:
                headers = {name.lower(): value for name, value in self.headers.items()}
                stub.requests.append(Received(method, self.path, headers, body))
                status, reply = stub._replies[method].popleft()
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                self._answer("POST", json.loads(self.rfile.read(length)))

            def do_GET(self):
                self._answer("GET", None)

            def log_message(self, *args):
                return None

        return Handler


def unavailable(name: str):
    def fail():
        raise RuntimeError(f"{name} is not part of this test")

    return fail


@contextmanager
def nothing_recorded(responses):
    yield


def services(tmp_path, plane: ControlPlane) -> Services:
    return Services(
        app_ai=unavailable("app.ai"),
        sdk=unavailable("the Anthropic SDK"),
        daytona=unavailable("Daytona"),
        record_daytona=nothing_recorded,
        out_dir=lambda: tmp_path / "saved",
        agentfield=lambda: plane,
    )


def plane(url: str, settings: ContainerSettings = SETTINGS_OFF, **options) -> ControlPlane:
    def container() -> ContainerSettings:
        return settings

    return ControlPlane(url, container=container, interval_seconds=0.01, **options)


def first_lines(tmp_path, env: dict, chosen: ControlPlane, count: int = 3) -> tuple[int, list[str]]:
    printed: list[str] = []
    code = run(env, services(tmp_path, chosen), printed.append)
    return code, printed[:count]


def test_the_agentfield_lines_come_first_with_the_execution_id_telemetry_and_the_webhook_secret(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 200, SUCCEEDED)
        code, lines = first_lines(tmp_path, WITH_SECRET, plane(stub.url))
        received = stub.requests
    assert lines == [
        "ok      AgentField  af server ran clientops.setup_status for kumo as execution exec_check_1 (succeeded in 412 ms)",
        "ok      AgentField  telemetry is off (AGENTFIELD_TELEMETRY_ENABLED=false in the clientops-agentfield container)",
        "ok      AgentField  the approval webhook signing secret is set in .env.local and in the control plane",
    ]
    assert [(request.method, request.path, request.body) for request in received] == [
        ("POST", "/api/v1/execute/async/clientops.setup_status", {"input": {"client_id": "kumo"}}),
        ("GET", "/api/v1/executions/exec_check_1", None),
    ]
    assert SETUP_STATUS == "clientops.setup_status"
    assert code == 1


def test_the_execution_is_read_again_until_it_ends_and_a_404_means_not_stored_yet(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 404, {"error": "execution not found"})
        stub.reply("GET", 200, {**SUCCEEDED, "status": "running", "completed_at": None, "duration_ms": None})
        stub.reply("GET", 200, SUCCEEDED)
        _, [line, *_] = first_lines(tmp_path, WITH_SECRET, plane(stub.url))
        reads = [request for request in stub.requests if request.method == "GET"]
    assert line.startswith("ok      AgentField  af server ran clientops.setup_status for kumo as execution exec_check_1")
    assert len(reads) == 3


def test_an_execution_that_ends_badly_names_how_it_ended(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 200, {**SUCCEEDED, "status": "failed", "result": None, "error": "agent returned 500"})
        code, [line, *_] = first_lines(tmp_path, WITH_SECRET, plane(stub.url))
    assert code == 1
    assert line == (
        "fail    AgentField  af server started clientops.setup_status as execution exec_check_1, "
        "but it ended as failed: agent returned 500"
    )


def test_an_execution_that_does_not_end_in_time_is_named(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        for _ in range(50):
            stub.reply("GET", 200, {**SUCCEEDED, "status": "running"})
        _, [line, *_] = first_lines(tmp_path, WITH_SECRET, plane(stub.url, wait_seconds=0.05))
    assert line == (
        "fail    AgentField  af server started clientops.setup_status as execution exec_check_1, "
        "but it did not end within 0.05 seconds. Is the agent running (bun run agent)?"
    )


def test_a_refused_start_names_the_answer_and_the_agent_to_start(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 404, {"error": "agent node 'clientops' not found"})
        _, [line, *_] = first_lines(tmp_path, WITH_SECRET, plane(stub.url))
    assert line == (
        f"fail    AgentField  af server at {stub.url} refused clientops.setup_status "
        "(HTTP 404: agent node 'clientops' not found). Start the agent with bun run agent, " + AGAIN
    )


def test_an_unreachable_control_plane_says_how_to_start_it(tmp_path):
    _, [line, *_] = first_lines(tmp_path, WITH_SECRET, plane("http://127.0.0.1:9"))
    assert line.startswith("fail    AgentField  af server at http://127.0.0.1:9 could not be reached (")
    assert line.endswith("). Start it with bun run agentfield, " + AGAIN)


def test_the_api_key_goes_in_its_header_when_set(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 200, SUCCEEDED)
        first_lines(tmp_path, WITH_SECRET, plane(stub.url, api_key="af-key-not-real"))
        received = stub.requests
    assert {request.headers.get("x-api-key") for request in received} == {"af-key-not-real"}


@pytest.mark.parametrize(
    ("settings", "line"),
    [
        (
            ContainerSettings(telemetry="true", webhook_secret_set=True),
            "fail    AgentField  telemetry is on (AGENTFIELD_TELEMETRY_ENABLED=true in the clientops-agentfield container). "
            + RESTART,
        ),
        (
            ContainerSettings(telemetry="1", webhook_secret_set=True),
            "fail    AgentField  telemetry is on (AGENTFIELD_TELEMETRY_ENABLED=1 in the clientops-agentfield container). "
            + RESTART,
        ),
        (
            ContainerSettings(telemetry=None, webhook_secret_set=True),
            "fail    AgentField  telemetry is not turned off: AGENTFIELD_TELEMETRY_ENABLED is not set in the "
            "clientops-agentfield container. " + RESTART,
        ),
    ],
)
def test_telemetry_that_is_not_off_fails(tmp_path, settings, line):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 200, SUCCEEDED)
        code, lines = first_lines(tmp_path, WITH_SECRET, plane(stub.url, settings))
    assert code == 1
    assert lines[1] == line


def test_a_webhook_secret_missing_from_env_local_is_named(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 200, SUCCEEDED)
        code, lines = first_lines(tmp_path, KEYS, plane(stub.url, ContainerSettings("false", False)))
    assert code == 1
    assert lines[2] == (
        "missing AgentField  AGENTFIELD_APPROVAL_WEBHOOK_SECRET is not set. "
        "Put it in .env.local, restart bun run agentfield, " + AGAIN
    )


def test_a_webhook_secret_the_control_plane_did_not_get_is_named(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 200, SUCCEEDED)
        code, lines = first_lines(tmp_path, WITH_SECRET, plane(stub.url, ContainerSettings("false", False)))
    assert code == 1
    assert lines[2] == (
        "fail    AgentField  AGENTFIELD_APPROVAL_WEBHOOK_SECRET is set in .env.local but not in the "
        "clientops-agentfield container. " + RESTART
    )


def test_an_unreadable_container_fails_the_telemetry_line_and_the_secret_is_judged_from_env_local(tmp_path):
    def gone() -> ContainerSettings:
        raise ContainerUnreadable("Error response from daemon: No such container: clientops-agentfield")

    with ControlPlaneStub() as stub:
        stub.reply("POST", 202, STARTED)
        stub.reply("GET", 200, SUCCEEDED)
        code, lines = first_lines(tmp_path, WITH_SECRET, ControlPlane(stub.url, container=gone))
    assert code == 1
    assert lines[1:] == [
        "fail    AgentField  could not read the clientops-agentfield container "
        "(Error response from daemon: No such container: clientops-agentfield). "
        "Start it with bun run agentfield, " + AGAIN,
        "ok      AgentField  the approval webhook signing secret is set in .env.local; "
        "the control plane's copy could not be read",
    ]


def test_no_secret_value_from_the_control_plane_is_printed(tmp_path):
    with ControlPlaneStub() as stub:
        stub.reply("POST", 400, {"error": f"bad signature with {WEBHOOK_SECRET}"})
        _, lines = first_lines(tmp_path, WITH_SECRET, plane(stub.url))
    assert WEBHOOK_SECRET not in "\n".join(lines)
    assert "[hidden]" in lines[0]


def test_without_a_control_plane_no_agentfield_line_is_printed(tmp_path):
    chosen = Services(
        app_ai=unavailable("app.ai"),
        sdk=unavailable("the Anthropic SDK"),
        daytona=unavailable("Daytona"),
        record_daytona=nothing_recorded,
        out_dir=lambda: tmp_path / "saved",
    )
    printed: list[str] = []
    run(WITH_SECRET, chosen, printed.append)
    assert not [line for line in printed if "AgentField  " in line[:20]]


def test_the_container_is_read_with_docker_inspect_and_only_two_facts_come_back():
    calls = []

    def docker(arguments):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="webhook-secret set\ntelemetry false\n\n", stderr="")

    assert read_agentfield_container(docker) == ContainerSettings(telemetry="false", webhook_secret_set=True)
    assert calls == [["docker", "container", "inspect", AGENTFIELD_CONTAINER, "--format", CONTAINER_TEMPLATE]]
    assert AGENTFIELD_CONTAINER == "clientops-agentfield"
    assert CONTAINER_TEMPLATE.count("index $pair 1") == 1


def test_a_container_without_either_variable_reads_as_unset():
    def docker(arguments):
        return subprocess.CompletedProcess(arguments, 0, stdout="\n", stderr="")

    assert read_agentfield_container(docker) == ContainerSettings(telemetry=None, webhook_secret_set=False)


def test_a_docker_failure_is_unreadable_with_its_message():
    def docker(arguments):
        return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="Error response from daemon: No such container: clientops-agentfield\n")

    with pytest.raises(ContainerUnreadable, match="No such container: clientops-agentfield"):
        read_agentfield_container(docker)


def test_docker_that_is_not_installed_is_unreadable():
    def docker(arguments):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    with pytest.raises(ContainerUnreadable, match="docker is not installed"):
        read_agentfield_container(docker)
