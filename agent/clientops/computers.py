import json
import os
import re
import shlex
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from daytona import (
    CreateSandboxFromSnapshotParams,
    Daytona,
    DaytonaAuthenticationError,
    DaytonaBadRequestError,
    DaytonaConflictError,
    DaytonaConnectionError,
    DaytonaError,
    DaytonaForbiddenError,
    DaytonaNotFoundError,
    DaytonaQueueTimeoutError,
    DaytonaRateLimitError,
    DaytonaServiceUnavailableError,
    DaytonaTimeoutError,
    DaytonaUnprocessableEntityError,
    ListSandboxesQuery,
    Sandbox,
    SandboxState,
)

from clientops.computer_image import ImageSpec, load_image_spec
from clientops.computer_protocol import CommandResult
from clientops.externals import daytona_client
from clientops.externals.fake_daytona import FakeDaytona, FakeSandbox

CLIENT_LABEL = "clientops-client"
AUTO_STOP_MINUTES = 15
START_TIMEOUT_SECONDS = 60
STOP_TIMEOUT_SECONDS = 60
TIER_VARIABLE = "CLIENTOPS_DAYTONA_TIER"
SANDBOX_ALLOW_LIST_VARIABLE = "DAYTONA_SANDBOX_ALLOW_LIST"
DEFAULT_TIER = 1
FIRST_PER_COMPUTER_TIER = 3
KNOWN_TIERS = (1, 2, 3, 4)

SUPPLIER_PORTAL = "portal.maruyama-supply.example"
CLIENT_SITES: dict[str, tuple[str, ...]] = {
    "kumo": (SUPPLIER_PORTAL, "admin.kumo-coffee.example"),
    "hinata": (SUPPLIER_PORTAL, "admin.hinata-skincare.example"),
}
TRAVEL_SITE = "travel.hikari-travel.example"
CLIENT_TRIP_SITES: dict[str, tuple[str, ...]] = {
    "kumo": (TRAVEL_SITE,),
    "hinata": (TRAVEL_SITE,),
}
CLIENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

RUN_DIRECTORY = "/tmp/clientops"
LIGHTPANDA_PORT = 9222
LIGHTPANDA_VERSION_URL = f"http://127.0.0.1:{LIGHTPANDA_PORT}/json/version"
LIGHTPANDA_CDP_URL = f"ws://127.0.0.1:{LIGHTPANDA_PORT}/"
SUPPLIER_URL = "http://127.0.0.1:3001"
STORE_URL = "http://127.0.0.1:3002"
TRAVEL_URL = "http://127.0.0.1:3003"
SITES_SOURCE = "sandbox/sites"
WAIT_SECONDS = 15
NOT_ANSWERING_EXIT_CODE = 69
LOG_LINES = 20

SECRET_NAME = re.compile(
    r"^LP_|PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|(^|_)KEY$|CREDENTIAL|COOKIE|SESSION|AUTH",
    re.IGNORECASE,
)
SECRET_VALUE_PREFIXES = ("sk-", "dtn_", "ghp_", "github_pat_", "xoxb-", "xoxp-", "-----BEGIN")

BROKEN_STATES = frozenset(
    {SandboxState.ERROR, SandboxState.BUILD_FAILED, SandboxState.DESTROYED, SandboxState.DESTROYING}
)


class ComputerProblem(StrEnum):
    UNKNOWN_CLIENT = "unknown_client"
    MORE_THAN_ONE = "more_than_one"
    BROKEN = "broken"
    ALLOW_LIST_MISMATCH = "allow_list_mismatch"
    SECRET_IN_ENV = "secret_in_env"
    KEY_REFUSED = "key_refused"
    BUSY = "busy"
    TIMED_OUT = "timed_out"
    UNREACHABLE = "unreachable"
    NOT_FOUND = "not_found"
    REFUSED = "refused"
    BROWSER_NOT_ANSWERING = "browser_not_answering"
    SITES_NOT_ANSWERING = "sites_not_answering"
    FAILED = "failed"


class ComputerError(RuntimeError):
    def __init__(self, problem: ComputerProblem, client_id: str, message: str) -> None:
        super().__init__(message)
        self.problem = problem
        self.client_id = client_id


class NetworkMode(StrEnum):
    ORGANIZATION = "organization"
    PER_COMPUTER = "per-computer"


@dataclass(frozen=True)
class NetworkPolicy:
    client_id: str
    tier: int
    allow_list: tuple[str, ...]
    sandbox_allow_list: bool = False

    @property
    def mode(self) -> NetworkMode:
        return NetworkMode.PER_COMPUTER if self.tier >= FIRST_PER_COMPUTER_TIER else NetworkMode.ORGANIZATION

    @property
    def domain_allow_list(self) -> str:
        return ",".join(self.allow_list)

    @property
    def description(self) -> str:
        if self.mode is NetworkMode.PER_COMPUTER:
            return f"{possessive(self.client_id)} computer can reach only these sites: {', '.join(self.allow_list)}."
        return (
            "Outside access is restricted for the whole organization on Daytona Tier 1 and 2: "
            "computers reach only the services Daytona keeps open. "
            f"Per-computer allow lists are sent only when {SANDBOX_ALLOW_LIST_VARIABLE}=on, from Tier 3."
        )


@dataclass(frozen=True)
class BrowserStatus:
    version: str
    cdp_url: str


@dataclass(frozen=True)
class SitesStatus:
    supplier_url: str
    store_url: str
    travel_url: str = TRAVEL_URL


def possessive(client_id: str) -> str:
    return f"{client_id.capitalize()}'s"


def daytona_tier() -> int:
    value = os.environ.get(TIER_VARIABLE)
    if value is None or value == "":
        return DEFAULT_TIER
    if value not in {str(tier) for tier in KNOWN_TIERS}:
        raise ValueError(f"{TIER_VARIABLE} must be one of 1, 2, 3 or 4 (unset means 1), not {value!r}")
    return int(value)


def sandbox_allow_list_on() -> bool:
    return os.environ.get(SANDBOX_ALLOW_LIST_VARIABLE) == "on"


def network_policy(client_id: str, tier: int | None = None) -> NetworkPolicy:
    tier = daytona_tier() if tier is None else tier
    if tier not in KNOWN_TIERS:
        raise ValueError(f"{TIER_VARIABLE} must be one of 1, 2, 3 or 4, not {tier!r}")
    return NetworkPolicy(
        client_id=client_id,
        tier=tier,
        allow_list=CLIENT_SITES[known_client(client_id)],
        sandbox_allow_list=sandbox_allow_list_on(),
    )


def known_client(client_id: str) -> str:
    if not CLIENT_ID_PATTERN.fullmatch(client_id) or client_id not in CLIENT_SITES:
        raise ComputerError(
            ComputerProblem.UNKNOWN_CLIENT,
            client_id,
            f"There is no client {client_id!r}. Known clients: {', '.join(sorted(CLIENT_SITES))}.",
        )
    return client_id


def start_and_wait_command(start: str, check: str, log: str) -> str:
    attempts = WAIT_SECONDS * 4
    return (
        f"mkdir -p {RUN_DIRECTORY} && "
        f"{{ ({check}) > /dev/null 2>&1 || (({start}) > {log} 2>&1 < /dev/null &); }}; "
        f"for attempt in $(seq {attempts}); do ({check}) 2> /dev/null && exit 0; sleep 0.25; done; "
        f"tail -n {LOG_LINES} {log}; exit {NOT_ANSWERING_EXIT_CODE}"
    )


def daytona_problem(error: DaytonaError) -> tuple[ComputerProblem, str]:
    if isinstance(error, (DaytonaAuthenticationError, DaytonaForbiddenError)):
        return ComputerProblem.KEY_REFUSED, "Daytona refused the API key. Check DAYTONA_API_KEY in .env.local."
    if isinstance(error, (DaytonaRateLimitError, DaytonaQueueTimeoutError, DaytonaServiceUnavailableError)):
        return ComputerProblem.BUSY, "Daytona is busy right now. Try again in a minute."
    if isinstance(error, DaytonaConnectionError):
        return ComputerProblem.UNREACHABLE, "Daytona could not be reached. Check the network connection."
    if isinstance(error, DaytonaTimeoutError):
        return ComputerProblem.TIMED_OUT, "Daytona did not finish in time."
    if isinstance(error, DaytonaNotFoundError):
        return ComputerProblem.NOT_FOUND, "Daytona could not find it."
    if isinstance(error, (DaytonaBadRequestError, DaytonaConflictError, DaytonaUnprocessableEntityError)):
        return ComputerProblem.REFUSED, "Daytona refused the request."
    return ComputerProblem.FAILED, "Daytona reported an error."


@contextmanager
def daytona_errors(client_id: str, action: str, timeout_seconds: int | None = None) -> Iterator[None]:
    try:
        yield
    except DaytonaError as error:
        problem, explanation = daytona_problem(error)
        if problem is ComputerProblem.TIMED_OUT and timeout_seconds is not None:
            explanation = f"It did not finish within {timeout_seconds} seconds."
        if problem is ComputerProblem.NOT_FOUND and action == "create":
            explanation += " Build the computer snapshot first with `bun run computer:snapshot`."
        raise ComputerError(
            problem,
            client_id,
            f"Could not {action} {client_id}'s computer: {explanation} (Daytona: {error})",
        ) from error


def refuse_secrets(client_id: str, env: Mapping[str, str]) -> None:
    for name, value in env.items():
        if SECRET_NAME.search(name) or str(value).startswith(SECRET_VALUE_PREFIXES):
            raise ComputerError(
                ComputerProblem.SECRET_IN_ENV,
                client_id,
                f"The setting {name} looks like a secret. Secrets never go to {client_id}'s computer through "
                "Daytona; logins are filled in inside the computer.",
            )


class DaytonaComputer:
    def __init__(
        self,
        client_id: str,
        sandbox: Sandbox | FakeSandbox,
        network: NetworkPolicy,
        image: ImageSpec,
        *,
        created: bool,
        startup_seconds: float,
    ) -> None:
        self.client_id = client_id
        self.sandbox = sandbox
        self.network = network
        self.created = created
        self.startup_seconds = startup_seconds
        self._image = image

    @property
    def id(self) -> str:
        return self.sandbox.id

    def run(self, command: str, *, timeout_seconds: int, env: Mapping[str, str] | None = None) -> CommandResult:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise ValueError(f"timeout_seconds must be a whole number of seconds above 0, not {timeout_seconds!r}")
        if env:
            refuse_secrets(self.client_id, env)
        with daytona_errors(self.client_id, "run a command on", timeout_seconds):
            response = self.sandbox.process.exec(command, env=dict(env) if env else None, timeout=timeout_seconds)
        if response.exit_code is None:
            raise ComputerError(
                ComputerProblem.FAILED,
                self.client_id,
                f"{possessive(self.client_id)} computer gave no exit code for {command!r}.",
            )
        return CommandResult(exit_code=response.exit_code, output=response.result)

    def upload(self, content: bytes, remote_path: str) -> None:
        if not isinstance(content, bytes):
            raise TypeError(f"upload takes bytes, not {type(content).__name__}")
        with daytona_errors(self.client_id, "upload a file to"):
            self.sandbox.fs.upload_file(content, remote_path)

    def browser_command(self) -> str:
        return start_and_wait_command(
            start=f"nohup lightpanda serve --host 127.0.0.1 --port {LIGHTPANDA_PORT}",
            check=f"curl -fsS --max-time 1 {LIGHTPANDA_VERSION_URL}",
            log=f"{RUN_DIRECTORY}/lightpanda.log",
        )

    def sites_command(self) -> str:
        server = f"{self._image.copied_to(SITES_SOURCE)}/server.ts"
        return start_and_wait_command(
            start=f"SITES_CLIENTS={shlex.quote(self.client_id)} nohup bun {server}",
            check=f"curl -fsS -o /dev/null --max-time 1 {SUPPLIER_URL}/login"
            f" && curl -fsS -o /dev/null --max-time 1 {STORE_URL}/login"
            f" && curl -fsS -o /dev/null --max-time 1 {TRAVEL_URL}/login",
            log=f"{RUN_DIRECTORY}/sites.log",
        )

    def start_browser(self) -> BrowserStatus:
        result = self.run(self.browser_command(), timeout_seconds=WAIT_SECONDS + 10)
        expected = self._image.lightpanda.version
        if result.exit_code != 0:
            raise self._not_answering(ComputerProblem.BROWSER_NOT_ANSWERING, "Lightpanda", result.output)
        try:
            answer = json.loads(result.output)
            version, cdp_url = answer["Lightpanda-Version"], answer["webSocketDebuggerUrl"]
        except (ValueError, KeyError, TypeError):
            raise self._not_answering(
                ComputerProblem.BROWSER_NOT_ANSWERING,
                "Lightpanda",
                f"Port {LIGHTPANDA_PORT} answered with something else: {result.output[:200]!r}",
            ) from None
        if version != expected:
            raise self._not_answering(
                ComputerProblem.BROWSER_NOT_ANSWERING,
                "Lightpanda",
                f"Lightpanda {version} answered, expected {expected}. Rebuild the computer snapshot.",
            )
        return BrowserStatus(version=version, cdp_url=cdp_url)

    def start_sites(self) -> SitesStatus:
        result = self.run(self.sites_command(), timeout_seconds=WAIT_SECONDS + 10)
        if result.exit_code != 0:
            raise self._not_answering(ComputerProblem.SITES_NOT_ANSWERING, "The demo sites", result.output)
        return SitesStatus(supplier_url=SUPPLIER_URL, store_url=STORE_URL, travel_url=TRAVEL_URL)

    def stop(self) -> None:
        with daytona_errors(self.client_id, "stop", STOP_TIMEOUT_SECONDS):
            self.sandbox.stop(timeout=STOP_TIMEOUT_SECONDS)

    def _not_answering(self, problem: ComputerProblem, what: str, details: str) -> ComputerError:
        return ComputerError(
            problem,
            self.client_id,
            f"{what} did not answer on {possessive(self.client_id)} computer within {WAIT_SECONDS} seconds. "
            f"Last output: {details.strip()}",
        )


class Computers:
    def __init__(
        self,
        daytona: Daytona | FakeDaytona | None = None,
        *,
        tier: int | None = None,
        image: ImageSpec | None = None,
    ) -> None:
        self._daytona = daytona if daytona is not None else daytona_client()
        self._tier = tier
        self._image = image or load_image_spec()

    def open(self, client_id: str) -> DaytonaComputer:
        started = time.monotonic()
        network = network_policy(known_client(client_id), self._tier)
        sandbox = self._find(client_id)
        created = sandbox is None
        if sandbox is None:
            sandbox = self._create(network)
        else:
            self._check_found(sandbox, network)
            self._start(client_id, sandbox)
        return DaytonaComputer(
            client_id,
            sandbox,
            network,
            self._image,
            created=created,
            startup_seconds=time.monotonic() - started,
        )

    def state(self, client_id: str) -> SandboxState | None:
        sandbox = self._find(known_client(client_id))
        return None if sandbox is None else sandbox.state

    def stop(self, client_id: str) -> bool:
        sandbox = self._find(known_client(client_id))
        if sandbox is None or sandbox.state is not SandboxState.STARTED:
            return False
        with daytona_errors(client_id, "stop", STOP_TIMEOUT_SECONDS):
            sandbox.stop(timeout=STOP_TIMEOUT_SECONDS)
        return True

    def _find(self, client_id: str) -> Sandbox | FakeSandbox | None:
        with daytona_errors(client_id, "find"):
            listed = list(self._daytona.list(ListSandboxesQuery(labels={CLIENT_LABEL: client_id})))
        mine = [sandbox for sandbox in listed if (sandbox.labels or {}).get(CLIENT_LABEL) == client_id]
        if len(mine) > 1:
            raise ComputerError(
                ComputerProblem.MORE_THAN_ONE,
                client_id,
                f"Found {len(mine)} computers labelled for {client_id}; there must be exactly one. "
                f"Delete the extra ones in Daytona: {', '.join(sandbox.id for sandbox in mine)}.",
            )
        return mine[0] if mine else None

    def _create(self, network: NetworkPolicy) -> Sandbox | FakeSandbox:
        params = CreateSandboxFromSnapshotParams(
            snapshot=self._image.snapshot,
            labels={CLIENT_LABEL: network.client_id},
            domain_allow_list=network.domain_allow_list if network.sandbox_allow_list else None,
            auto_stop_interval=AUTO_STOP_MINUTES,
        )
        with daytona_errors(network.client_id, "create", START_TIMEOUT_SECONDS):
            return self._daytona.create(params, timeout=START_TIMEOUT_SECONDS)

    def _check_found(self, sandbox: Sandbox | FakeSandbox, network: NetworkPolicy) -> None:
        if sandbox.state in BROKEN_STATES:
            reason = f": {sandbox.error_reason}" if sandbox.error_reason else ""
            raise ComputerError(
                ComputerProblem.BROKEN,
                network.client_id,
                f"{possessive(network.client_id)} computer is {sandbox.state.value}{reason}. "
                "Delete it in Daytona so a new one is made.",
            )
        if (
            network.sandbox_allow_list
            and network.mode is NetworkMode.PER_COMPUTER
            and sandbox.domain_allow_list != network.domain_allow_list
        ):
            raise ComputerError(
                ComputerProblem.ALLOW_LIST_MISMATCH,
                network.client_id,
                f"{possessive(network.client_id)} computer allows {sandbox.domain_allow_list or 'every site'}, "
                f"not only {network.domain_allow_list}. Delete it in Daytona so it is made again with its list.",
            )

    def _start(self, client_id: str, sandbox: Sandbox | FakeSandbox) -> None:
        if sandbox.state is SandboxState.STARTED:
            return
        with daytona_errors(client_id, "start", START_TIMEOUT_SECONDS):
            sandbox.start(timeout=START_TIMEOUT_SECONDS)
