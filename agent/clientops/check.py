import argparse
import asyncio
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anthropic
from daytona import Daytona, DaytonaError, DaytonaNotFoundError, SandboxState
from daytona_api_client import SnapshotState

from clientops.boundary import resolve_job
from clientops.claude_call import CLAUDE_MODEL, claude_ai_config, element_choice_call
from clientops.computer_image import ImageSpec, load_image_spec
from clientops.computers import (
    SECRET_NAME,
    SECRET_VALUE_PREFIXES,
    SUPPLIER_URL,
    ComputerError,
    DaytonaComputer,
    daytona_problem,
    possessive,
)
from clientops.cost import CLAUDE_PRICES_PER_MILLION, MILLION, claude_bill
from clientops.externals import ANTHROPIC_KEY_VARIABLE, EXTERNALS_MODE_VARIABLE, daytona_client
from clientops.externals.fake_daytona import FakeDaytona
from clientops.page import Element
from clientops.plan import CHOICE_KEYS, SYSTEM_PROMPT, Goal, letter_id, user_prompt
from clientops.response_shapes import (
    APP_AI_SAMPLE_LABEL,
    MESSAGE_SAMPLE,
    RealResponses,
    app_ai_view,
    load_sample,
    recording_anthropic,
    recording_daytona,
    save_real_responses,
    shape_differences,
    without_secrets,
)

CHECK_CLIENT = "kumo"
DAYTONA_KEY_VARIABLE = "DAYTONA_API_KEY"
REQUIRED_KEYS = (ANTHROPIC_KEY_VARIABLE, DAYTONA_KEY_VARIABLE)
CLAUDE_PART = "claude"
PARTS = (CLAUDE_PART,)
CLAUDE_KEYS = (ANTHROPIC_KEY_VARIABLE,)
CLAUDE_SAMPLES = frozenset({MESSAGE_SAMPLE, APP_AI_SAMPLE_LABEL})
WEBHOOK_SECRET_VARIABLE = "AGENTFIELD_APPROVAL_WEBHOOK_SECRET"
TELEMETRY_VARIABLE = "AGENTFIELD_TELEMETRY_ENABLED"
TELEMETRY_ON_VALUES = frozenset({"true", "1"})
AGENTFIELD_SERVER_VARIABLE = "AGENTFIELD_SERVER"
AGENTFIELD_API_KEY_VARIABLE = "AGENTFIELD_API_KEY"
AGENTFIELD_DEFAULT_SERVER = "http://localhost:8080"
AGENTFIELD_CONTAINER = "clientops-agentfield"
AGENTFIELD_NODE = "clientops"
SETUP_STATUS = f"{AGENTFIELD_NODE}.setup_status"
CHECK_NODE = "clientops-check"
TIMEOUT_RETRIES_VARIABLE = "AGENTFIELD_AI_TIMEOUT_RETRIES"
MODEL_ID = CLAUDE_MODEL.removeprefix("anthropic/")
SHORTEST_SECRET = 8

AGAIN = "then run bun run check again."
RESTART_AGENTFIELD = f"Restart it with bun run agentfield:stop and bun run agentfield, {AGAIN}"

SIGN_IN_URL = f"{SUPPLIER_URL}/login"
SIGN_IN_PAGE = "Maruyama Supply's sign-in page"
TREE_COMMAND = f"lightpanda fetch --dump semantic_tree_text {SIGN_IN_URL} 2>/dev/null"
TREE_TIMEOUT_SECONDS = 60
TREE_ROOT = re.compile(r"\d+ RootWebArea\b")
OUTPUT_LIMIT = 200

CHECK_GOAL = Goal("Press the button that saves the changes to the stock form", "click")
CHECK_TARGET = "Save changes"
CHECK_PAGE = (
    Element(ref=1, role="link", name="Products", form=None),
    Element(ref=2, role="textbox", name="Available", form="Stock"),
    Element(ref=3, role="button", name="Discard", form="Stock"),
    Element(ref=4, role="button", name=CHECK_TARGET, form="Stock"),
    Element(ref=5, role="link", name="Sign out", form=None),
)

DONE = "done"
DECLINED = "declined"
CUT_OFF = "cut-off"
APP_AI_ENDINGS = {"stop": DONE, "content_filter": DECLINED, "length": CUT_OFF}
MESSAGE_ENDINGS = {"end_turn": DONE, "refusal": DECLINED, "max_tokens": CUT_OFF}
APP_AI = "AgentField app.ai"
SDK = "the Anthropic SDK directly"
AGREED = "with effort medium and the structured output"

FAILING_STATUSES = frozenset({"fail", "missing", "diff"})
EXECUTION_ENDED_BADLY = frozenset({"failed", "cancelled", "timeout"})
CONTROL_PLANE_TIMEOUT_SECONDS = 10
EXECUTION_WAIT_SECONDS = 60.0
EXECUTION_INTERVAL_SECONDS = 0.4
DOCKER_TIMEOUT_SECONDS = 30
WEBHOOK_SECRET_SET = "webhook-secret set"
TELEMETRY_PREFIX = "telemetry "
CONTAINER_TEMPLATE = (
    '{{range .Config.Env}}{{$pair := split . "="}}'
    f'{{{{if eq (index $pair 0) "{TELEMETRY_VARIABLE}"}}}}{TELEMETRY_PREFIX}{{{{index $pair 1}}}}{{{{println}}}}{{{{end}}}}'
    f'{{{{if eq (index $pair 0) "{WEBHOOK_SECRET_VARIABLE}"}}}}'
    f"{{{{if gt (len .) {len(WEBHOOK_SECRET_VARIABLE) + 1}}}}}{WEBHOOK_SECRET_SET}{{{{println}}}}{{{{end}}}}{{{{end}}}}"
    "{{end}}"
)


@dataclass(frozen=True)
class Asked:
    response: Any
    message: dict | None
    request: dict | None


Asker = Callable[[dict], Awaitable[Asked]]
Recorder = Callable[[RealResponses], AbstractContextManager[None]]


@dataclass(frozen=True)
class Reply:
    model: str | None
    ending: str | None
    text: str
    input_tokens: int
    output_tokens: int
    usd: Decimal | None

    def usage(self) -> str:
        cost = "cost unknown" if self.usd is None else f"${self.usd:.4f}"
        return f"{self.input_tokens} in, {self.output_tokens} out, {cost}"


@dataclass(frozen=True)
class ContainerSettings:
    telemetry: str | None
    webhook_secret_set: bool


class ContainerUnreadable(RuntimeError):
    pass


class ControlPlaneError(RuntimeError):
    pass


class ControlPlaneUnreachable(ControlPlaneError):
    pass


@dataclass(frozen=True)
class Execution:
    execution_id: str
    status: str
    duration_ms: int | None
    error: str | None


def docker(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=DOCKER_TIMEOUT_SECONDS)


def read_agentfield_container(
    run_docker: Callable[[list[str]], subprocess.CompletedProcess] = docker,
) -> ContainerSettings:
    try:
        finished = run_docker(["docker", "container", "inspect", AGENTFIELD_CONTAINER, "--format", CONTAINER_TEMPLATE])
    except FileNotFoundError:
        raise ContainerUnreadable("docker is not installed") from None
    except subprocess.TimeoutExpired:
        raise ContainerUnreadable(f"docker did not answer within {DOCKER_TIMEOUT_SECONDS} seconds") from None
    if finished.returncode != 0:
        raise ContainerUnreadable(one_line(finished.stderr) or f"docker exited with {finished.returncode}")
    lines = [line.strip() for line in finished.stdout.splitlines()]
    telemetry = next((line.removeprefix(TELEMETRY_PREFIX) for line in lines if line.startswith(TELEMETRY_PREFIX)), None)
    return ContainerSettings(telemetry=telemetry, webhook_secret_set=WEBHOOK_SECRET_SET in lines)


@dataclass(frozen=True)
class ControlPlane:
    url: str
    api_key: str | None = None
    container: Callable[[], ContainerSettings] = read_agentfield_container
    wait_seconds: float = EXECUTION_WAIT_SECONDS
    interval_seconds: float = EXECUTION_INTERVAL_SECONDS

    def run_skill(self, target: str, payload: dict) -> Execution:
        status, started = self._call("POST", f"/api/v1/execute/async/{target}", {"input": payload})
        if status >= 400 or not isinstance(started, dict) or not isinstance(started.get("execution_id"), str):
            raise ControlPlaneError(f"HTTP {status}: {error_text(started)}")
        execution_id = started["execution_id"]
        deadline = time.monotonic() + self.wait_seconds
        while True:
            status, execution = self._call("GET", f"/api/v1/executions/{quote(execution_id, safe='')}")
            if status != 404 and isinstance(execution, dict):
                ended = str(execution.get("status", "")).lower()
                if ended == "succeeded" or ended in EXECUTION_ENDED_BADLY:
                    error = execution.get("error") or execution.get("error_message")
                    return Execution(execution_id, ended, execution.get("duration_ms"), error)
            if time.monotonic() >= deadline:
                return Execution(execution_id, "", None, None)
            time.sleep(self.interval_seconds)

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=CONTROL_PLANE_TIMEOUT_SECONDS) as answer:
                return answer.status, parsed_json(answer.read())
        except urllib.error.HTTPError as error:
            return error.code, parsed_json(error.read())
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ControlPlaneUnreachable(str(reason)) from error


@dataclass(frozen=True)
class Services:
    app_ai: Callable[[], Asker]
    sdk: Callable[[], Asker]
    daytona: Callable[[], Daytona | FakeDaytona]
    record_daytona: Recorder
    out_dir: Callable[[], Path]
    agentfield: Callable[[], ControlPlane] | None = None


class Report:
    def __init__(self, say: Callable[[str], None], secrets: list[str]) -> None:
        self._say = say
        self._secrets = secrets
        self.failed = False

    @property
    def secrets(self) -> list[str]:
        return list(self._secrets)

    @property
    def code(self) -> int:
        return 1 if self.failed else 0

    def add(self, status: str, name: str, text: str) -> None:
        self.failed = self.failed or status in FAILING_STATUSES
        self._say(without_secrets(f"{status:<8}{name:<12}{text}", self._secrets))

    def guard(self, name: str, work: Callable[[], Any]) -> Any:
        try:
            return work()
        except Exception as error:
            self.add("fail", name, f"stopped unexpectedly: {described(error)}")
            return None


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def described(error: BaseException) -> str:
    return one_line(f"{type(error).__name__}: {error}")


def parsed_json(data: bytes) -> Any:
    try:
        return json.loads(data or b"null")
    except ValueError:
        return data.decode(errors="replace")


def error_text(body: Any) -> str:
    if isinstance(body, dict) and body.get("error"):
        return one_line(body["error"])
    return one_line(json.dumps(body) if not isinstance(body, str) else body)[:OUTPUT_LIMIT]


def secret_values(env: Mapping[str, str]) -> list[str]:
    return [
        value
        for name, value in env.items()
        if value and len(value) >= SHORTEST_SECRET and (SECRET_NAME.search(name) or value.startswith(SECRET_VALUE_PREFIXES))
    ]


def check_call() -> tuple[dict, dict[str, Element]]:
    view = [
        {"id": letter_id(index), "role": element.role, "name": element.name, "form": element.form}
        for index, element in enumerate(CHECK_PAGE)
    ]
    elements = {entry["id"]: element for entry, element in zip(view, CHECK_PAGE, strict=True)}
    return element_choice_call(SYSTEM_PROMPT, user_prompt(CHECK_GOAL, view, None)), elements


def app_ai_asker(ai: Callable[..., Awaitable[Any]]) -> Asker:
    async def ask(call: dict) -> Asked:
        with recording_anthropic() as wire:
            response = await ai(**call)
        return Asked(response, wire.message, wire.request)

    return ask


def sdk_arguments(call: dict) -> dict:
    return {
        "model": call["model"].removeprefix("anthropic/"),
        "max_tokens": call["max_tokens"],
        "system": call["system"],
        "messages": [{"role": "user", "content": call["user"]}],
        "output_config": call["output_config"],
    }


def sdk_asker(client: anthropic.Anthropic) -> Asker:
    async def ask(call: dict) -> Asked:
        raw = await asyncio.to_thread(client.messages.with_raw_response.create, **sdk_arguments(call))
        return Asked(raw.parse(), raw.json(), json.loads(raw.http_request.content))

    return ask


def app_ai_reply(response: Any) -> Reply:
    choice = response.raw_response.choices[0]
    usage = response.usage or {}
    return Reply(
        model=response.raw_response.model,
        ending=APP_AI_ENDINGS.get(choice.finish_reason, choice.finish_reason),
        text=response.text or "",
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        usd=claude_bill(response).usd,
    )


def message_usd(usage: Mapping[str, Any]) -> Decimal:
    prices = CLAUDE_PRICES_PER_MILLION
    written = int(usage.get("cache_creation_input_tokens") or 0)
    hour = min(int((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0), written)
    return (
        int(usage.get("input_tokens") or 0) * prices["input"]
        + (written - hour) * prices["cache_write_5m"]
        + hour * prices["cache_write_1h"]
        + int(usage.get("cache_read_input_tokens") or 0) * prices["cache_read"]
        + int(usage.get("output_tokens") or 0) * prices["output"]
    ) / MILLION


def message_reply(message: Mapping[str, Any]) -> Reply:
    usage = message.get("usage") or {}
    blocks = message.get("content") or []
    reason = message.get("stop_reason")
    return Reply(
        model=message.get("model"),
        ending=MESSAGE_ENDINGS.get(reason, reason),
        text="".join(block.get("text", "") for block in blocks if block.get("type") == "text"),
        input_tokens=sum(
            int(usage.get(name) or 0)
            for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        ),
        output_tokens=int(usage.get("output_tokens") or 0),
        usd=message_usd(usage),
    )


def answer_problem(reply: Reply, elements: Mapping[str, Element]) -> tuple[str | None, Element | None]:
    if reply.ending == DECLINED:
        return "declined the request", None
    if reply.ending == CUT_OFF:
        return "stopped at max_tokens before the JSON was complete", None
    if reply.ending != DONE:
        return f"stopped with {reply.ending}", None
    try:
        answer = json.loads(reply.text)
    except ValueError:
        return "did not answer with the agreed JSON", None
    if not isinstance(answer, dict) or set(answer) != CHOICE_KEYS or not isinstance(answer["confident"], bool):
        return "did not answer with the agreed JSON", None
    if not answer["confident"]:
        return "was not sure", None
    element = elements.get(answer["element_id"]) if isinstance(answer["element_id"], str) else None
    if element is None:
        return "answered with an element that is not on the page", None
    if element.name != CHECK_TARGET:
        return f'picked "{element.name}", not "{CHECK_TARGET}"', None
    if answer["action"] != CHECK_GOAL.action:
        return f'chose to {answer["action"]} "{CHECK_TARGET}", not to {CHECK_GOAL.action} it', None
    if reply.model != MODEL_ID:
        return f"came from {reply.model}, not {MODEL_ID}", None
    return None, element


def request_problem(request: Mapping[str, Any], call: dict) -> str | None:
    if request.get("model") != MODEL_ID:
        return f"sent model {request.get('model')}, not {MODEL_ID}"
    if request.get("output_config") != call["output_config"]:
        return f"sent output_config {json.dumps(request.get('output_config'))}, not effort medium with the JSON schema"
    return None


async def claude_check(services: Services, responses: RealResponses, report: Report) -> None:
    call, elements = check_call()
    try:
        asked = await services.app_ai()(call)
    except Exception as error:
        await sdk_check(services, call, elements, described(error), responses, report)
        return
    responses.message = asked.message
    responses.app_ai = app_ai_view(asked.response)
    reply = app_ai_reply(asked.response)
    sent_wrong = None if asked.request is None else request_problem(asked.request, call)
    problem, element = answer_problem(reply, elements)
    if sent_wrong:
        report.add("fail", "Claude", f"{APP_AI} {sent_wrong}")
    elif problem:
        report.add("fail", "Claude", f"{reply.model} answered through {APP_AI}, but {problem}")
    elif asked.request is None:
        report.add(
            "ok",
            "Claude",
            f'{reply.model} answered through {APP_AI}: picked "{element.name}" ({reply.usage()}). '
            "The request itself was not recorded, so effort and schema are unconfirmed",
        )
    else:
        report.add("ok", "Claude", f'{reply.model} answered through {APP_AI} {AGREED}: picked "{element.name}" ({reply.usage()})')


async def sdk_check(
    services: Services, call: dict, elements: Mapping[str, Element], failure: str, responses: RealResponses, report: Report
) -> None:
    first = f"{APP_AI} did not work ({failure})"
    try:
        asked = await services.sdk()(call)
    except Exception as error:
        report.add("fail", "Claude", f"{first}, and neither did {SDK} ({described(error)})")
        return
    responses.message = asked.message
    reply = message_reply(asked.message or {})
    sent_wrong = None if asked.request is None else request_problem(asked.request, call)
    problem, element = answer_problem(reply, elements)
    if sent_wrong:
        report.add("fail", "Claude", f"{first}; {SDK} {sent_wrong}")
    elif problem:
        report.add("fail", "Claude", f"{first}; through {SDK} {reply.model} answered, but {problem}")
    else:
        report.add(
            "fail",
            "Claude",
            f'{first}; {SDK} did: {reply.model} picked "{element.name}" {AGREED} ({reply.usage()})',
        )


def snapshot_problem(daytona: Daytona | FakeDaytona, spec: ImageSpec) -> str | None:
    name = spec.snapshot
    try:
        snapshot = daytona.snapshot.get(name)
    except DaytonaNotFoundError:
        return f"The computer snapshot {name} is not built yet. Build it with bun run computer:snapshot, {AGAIN}"
    except DaytonaError as error:
        _, explanation = daytona_problem(error)
        return f"Could not look up the computer snapshot {name}: {explanation} (Daytona: {error})"
    if snapshot.state is SnapshotState.ACTIVE:
        return None
    if snapshot.state is SnapshotState.INACTIVE:
        return f"The computer snapshot {name} is inactive. Run bun run computer:snapshot to wake or rebuild it, {AGAIN}"
    reason = f" ({snapshot.error_reason})" if snapshot.error_reason else ""
    return f"The computer snapshot {name} is {snapshot.state.value}{reason}. Wait for it or rebuild it with bun run computer:snapshot, {AGAIN}"


def daytona_check(services: Services, report: Report) -> DaytonaComputer | None:
    daytona = services.daytona()
    spec = load_image_spec()
    problem = snapshot_problem(daytona, spec)
    if problem:
        report.add("fail", "Daytona", problem)
        return None
    owner = possessive(CHECK_CLIENT)
    try:
        boundary = resolve_job(CHECK_CLIENT)
        computer = boundary.computer(daytona)
        computer.sandbox.refresh_data()
        state = boundary.computer_state(daytona)
    except ComputerError as error:
        report.add("fail", "Daytona", str(error))
        return None
    if state is not SandboxState.STARTED:
        shown = "gone" if state is None else state.value
        report.add("fail", "Daytona", f"{owner} computer {computer.id} is {shown} after it was opened, not started")
        return None
    made = "made" if computer.created else "found"
    report.add(
        "ok",
        "Daytona",
        f"{owner} computer {computer.id} was {made} in {computer.startup_seconds:.1f} s and is {state.value} (snapshot {spec.snapshot})",
    )
    return computer


def lightpanda_check(computer: DaytonaComputer | None, report: Report) -> None:
    owner = possessive(CHECK_CLIENT)
    if computer is None:
        report.add("skip", "Lightpanda", f"needs {owner} computer, which did not open")
        return
    try:
        computer.start_sites()
        browser = computer.start_browser()
        result = computer.run(TREE_COMMAND, timeout_seconds=TREE_TIMEOUT_SECONDS)
    except ComputerError as error:
        report.add("fail", "Lightpanda", str(error))
        return
    output = result.output.strip()
    where = f"Lightpanda in {owner} computer"
    if result.exit_code != 0:
        report.add(
            "fail", "Lightpanda", f"{where} could not read {SIGN_IN_PAGE} (exit code {result.exit_code}): {one_line(output)[:OUTPUT_LIMIT]}"
        )
        return
    lines = [line for line in result.output.splitlines() if line.strip()]
    if not lines or not TREE_ROOT.match(lines[0]):
        report.add("fail", "Lightpanda", f"{where} answered with something other than a semantic tree: {one_line(output)[:OUTPUT_LIMIT]}")
        return
    report.add(
        "ok",
        "Lightpanda",
        f"Lightpanda {browser.version} in {owner} computer read {SIGN_IN_PAGE}: {len(lines)} lines in its semantic tree",
    )


def shapes_check(
    responses: RealResponses, out_dir: Callable[[], Path], report: Report, samples: Collection[str] | None = None
) -> None:
    pairs = [(label, body) for label, body in responses.pairs() if samples is None or label in samples]
    recorded = [(label, body) for label, body in pairs if body is not None]
    missing = [label for label, body in pairs if body is None]
    if not recorded:
        report.add("skip", "Shapes", "nothing real was recorded, so nothing was compared")
        return
    directory = save_real_responses(responses, out_dir() / datetime.now(UTC).strftime("%Y%m%d-%H%M%S"), secrets=report.secrets)
    matching = 0
    for label, body in recorded:
        differences = shape_differences(load_sample(label), body)
        if differences:
            report.add("diff", "Shapes", f"{label}: {'; '.join(difference.describe() for difference in differences)}")
        else:
            matching += 1
    status = "ok" if matching == len(recorded) else "diff"
    text = f"{matching} of {len(recorded)} real responses match the contract samples field by field. Saved in {directory}"
    if missing:
        text += f". Not recorded: {', '.join(missing)}."
    report.add(status, "Shapes", text)


def execution_check(plane: ControlPlane, report: Report) -> None:
    hint = f"Start the agent with bun run agent, {AGAIN}"
    try:
        execution = plane.run_skill(SETUP_STATUS, {"client_id": CHECK_CLIENT})
    except ControlPlaneUnreachable as error:
        report.add("fail", "AgentField", f"af server at {plane.url} could not be reached ({error}). Start it with bun run agentfield, {AGAIN}")
        return
    except ControlPlaneError as error:
        report.add("fail", "AgentField", f"af server at {plane.url} refused {SETUP_STATUS} ({error}). {hint}")
        return
    started = f"af server started {SETUP_STATUS} as execution {execution.execution_id}, but it"
    if not execution.status:
        report.add("fail", "AgentField", f"{started} did not end within {plane.wait_seconds:g} seconds. Is the agent running (bun run agent)?")
        return
    if execution.status != "succeeded":
        reason = f": {one_line(execution.error)}" if execution.error else ""
        report.add("fail", "AgentField", f"{started} ended as {execution.status}{reason}")
        return
    took = "" if execution.duration_ms is None else f" in {execution.duration_ms} ms"
    report.add(
        "ok",
        "AgentField",
        f"af server ran {SETUP_STATUS} for {CHECK_CLIENT} as execution {execution.execution_id} ({execution.status}{took})",
    )


def telemetry_check(settings: ContainerSettings | ContainerUnreadable, report: Report) -> None:
    if isinstance(settings, ContainerUnreadable):
        report.add(
            "fail", "AgentField", f"could not read the {AGENTFIELD_CONTAINER} container ({settings}). Start it with bun run agentfield, {AGAIN}"
        )
    elif settings.telemetry is None:
        report.add(
            "fail",
            "AgentField",
            f"telemetry is not turned off: {TELEMETRY_VARIABLE} is not set in the {AGENTFIELD_CONTAINER} container. {RESTART_AGENTFIELD}",
        )
    elif settings.telemetry.lower() in TELEMETRY_ON_VALUES:
        report.add(
            "fail",
            "AgentField",
            f"telemetry is on ({TELEMETRY_VARIABLE}={settings.telemetry} in the {AGENTFIELD_CONTAINER} container). {RESTART_AGENTFIELD}",
        )
    else:
        report.add("ok", "AgentField", f"telemetry is off ({TELEMETRY_VARIABLE}={settings.telemetry} in the {AGENTFIELD_CONTAINER} container)")


def webhook_secret_check(env: Mapping[str, str], settings: ContainerSettings | ContainerUnreadable, report: Report) -> None:
    if not env.get(WEBHOOK_SECRET_VARIABLE):
        report.add(
            "missing",
            "AgentField",
            f"{WEBHOOK_SECRET_VARIABLE} is not set. Put it in .env.local, restart bun run agentfield, {AGAIN}",
        )
    elif isinstance(settings, ContainerUnreadable):
        report.add("ok", "AgentField", "the approval webhook signing secret is set in .env.local; the control plane's copy could not be read")
    elif not settings.webhook_secret_set:
        report.add(
            "fail",
            "AgentField",
            f"{WEBHOOK_SECRET_VARIABLE} is set in .env.local but not in the {AGENTFIELD_CONTAINER} container. {RESTART_AGENTFIELD}",
        )
    else:
        report.add("ok", "AgentField", "the approval webhook signing secret is set in .env.local and in the control plane")


def agentfield_check(env: Mapping[str, str], plane: ControlPlane, report: Report) -> None:
    report.guard("AgentField", lambda: execution_check(plane, report))
    try:
        settings: ContainerSettings | ContainerUnreadable = plane.container()
    except ContainerUnreadable as error:
        settings = error
    telemetry_check(settings, report)
    webhook_secret_check(env, settings, report)


def settings_problem(env: Mapping[str, str], required: Sequence[str] = REQUIRED_KEYS) -> tuple[str, str] | None:
    present = {name: bool(env.get(name)) for name in required}
    missing = [name for name, found in present.items() if not found]
    if missing:
        listed = ", ".join(f"{name} {'set' if found else 'missing'}" for name, found in present.items())
        them = "it" if len(missing) == 1 else "them"
        return "missing", f"{listed}. Put {them} in .env.local, {AGAIN}"
    mode = env.get(EXTERNALS_MODE_VARIABLE, "")
    if mode not in ("", "real"):
        return "fail", f"{EXTERNALS_MODE_VARIABLE} is {mode!r}, but the check talks to the real services only. Unset it, {AGAIN}"
    return None


def claude_part(services: Services, report: Report) -> int:
    responses = RealResponses()
    report.guard("Claude", lambda: asyncio.run(claude_check(services, responses, report)))
    report.guard("Shapes", lambda: shapes_check(responses, services.out_dir, report, CLAUDE_SAMPLES))
    return report.code


def run(env: Mapping[str, str], services: Services, say: Callable[[str], None], only: str | None = None) -> int:
    report = Report(say, secret_values(env))
    blocked = settings_problem(env, CLAUDE_KEYS if only == CLAUDE_PART else REQUIRED_KEYS)
    if blocked:
        status, text = blocked
        report.add(status, "Settings", text)
        return report.code
    if only == CLAUDE_PART:
        return claude_part(services, report)
    if services.agentfield is not None:
        plane = report.guard("AgentField", services.agentfield)
        if plane is not None:
            agentfield_check(env, plane, report)
    responses = RealResponses()
    report.guard("Claude", lambda: asyncio.run(claude_check(services, responses, report)))
    with services.record_daytona(responses):
        computer = report.guard("Daytona", lambda: daytona_check(services, report))
        report.guard("Lightpanda", lambda: lightpanda_check(computer, report))
    report.guard("Shapes", lambda: shapes_check(responses, services.out_dir, report))
    return report.code


def check_agent(env: Mapping[str, str]) -> Any:
    from agentfield import Agent

    config = claude_ai_config().model_copy(update={"rate_limit_max_retries": 0, "retry_attempts": 0})
    return Agent(node_id=CHECK_NODE, agentfield_server=agentfield_server(env), ai_config=config)


def agentfield_server(env: Mapping[str, str]) -> str:
    return env.get(AGENTFIELD_SERVER_VARIABLE) or AGENTFIELD_DEFAULT_SERVER


def checked_out_dir() -> Path:
    from clientops.boundary import data_directory

    return data_directory() / "check"


def real_services(env: Mapping[str, str]) -> Services:
    return Services(
        app_ai=lambda: app_ai_asker(check_agent(env).ai),
        sdk=lambda: sdk_asker(anthropic.Anthropic(api_key=env[ANTHROPIC_KEY_VARIABLE], max_retries=0)),
        daytona=daytona_client,
        record_daytona=recording_daytona,
        out_dir=checked_out_dir,
        agentfield=lambda: ControlPlane(agentfield_server(env), api_key=env.get(AGENTFIELD_API_KEY_VARIABLE) or None),
    )


def chosen_part(arguments: Sequence[str] | None) -> str | None:
    parser = argparse.ArgumentParser(prog="python -m clientops.check")
    parser.add_argument("--only", choices=PARTS)
    return parser.parse_args(arguments).only


def main(arguments: Sequence[str] | None = None) -> None:
    only = chosen_part(arguments)
    os.environ[TIMEOUT_RETRIES_VARIABLE] = "0"
    raise SystemExit(run(os.environ, real_services(os.environ), lambda line: print(line, flush=True), only))


if __name__ == "__main__":
    main()
