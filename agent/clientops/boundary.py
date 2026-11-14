import json
import os
import re
import secrets
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from daytona import Daytona, SandboxState

from clientops.computer_image import PROJECT_ROOT, load_image_spec
from clientops.computer_protocol import Computer
from clientops.computers import (
    CLIENT_ID_PATTERN,
    CLIENT_LABEL,
    CLIENT_SITES,
    CLIENT_TRIP_SITES,
    STORE_URL,
    SUPPLIER_PORTAL,
    SUPPLIER_URL,
    TRAVEL_SITE,
    TRAVEL_URL,
    Computers,
    ComputerError,
    NetworkPolicy,
    network_policy,
)
from clientops.cost import computer_in_use
from clientops.externals.fake_daytona import FakeDaytona
from clientops.local_computer import LOCAL_RUNNER_COMMAND, LocalComputer
from clientops.page import RUNNER_COMMAND, Page
from clientops.records import EventNotes

CLIENTS_PATH = PROJECT_ROOT / "config" / "clients.json"
CLIENT_LIST_VERSION = 1

DATA_DIR_VARIABLE = "CLIENTOPS_DATA_DIR"
AUDIT_FILE = Path("audit") / "boundary.jsonl"

COMPUTERS_VARIABLE = "CLIENTOPS_COMPUTERS"
LOCAL_COMPUTERS_DIRECTORY = "computers"
LOCAL_VAULT_FILE = "vault.json"
LOCAL_WORKSPACE = "workspace"
SIGN_IN_PATH = "/login"

MEMORY_SCOPE = "actor"
MEMORY_SCOPE_PREFIX = f"{CLIENT_LABEL}:"

VAULT_DIRECTORY = "/home/daytona/.clientops"
VAULT_PATH = f"{VAULT_DIRECTORY}/vault.json"
VAULT_INCOMING_PATH = f"{VAULT_DIRECTORY}/vault.json.incoming"
VAULT_VERSION = 1
VAULT_TIMEOUT_SECONDS = 30
RUNNER_SOURCE = "sandbox/runner"

SUPPLIER_LOGINS = ("LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD")
STORE_LOGINS = ("LP_STORE_USERNAME", "LP_STORE_PASSWORD")
TRAVEL_LOGINS = ("LP_TRAVEL_USERNAME", "LP_TRAVEL_PASSWORD")
TRAVELER_VALUES = ("LP_TRAVELER_NAME", "LP_TRAVELER_EMAIL")
STORE_HOST_PREFIX = "admin."

LOGIN_WORDS = frozenset(
    {"login", "logins", "signin", "password", "passwords", "passcode", "credential", "credentials",
     "username", "usernames", "account", "accounts", "vault"}
)
LOGIN_PHRASES = frozenset({("log", "in"), ("log", "on"), ("sign", "in"), ("sign", "on")})
COMPUTER_WORDS = frozenset(
    {"computer", "computers", "machine", "machines", "pc", "sandbox", "sandboxes", "desktop", "browser", "workspace"}
)
FILES_WORDS = frozenset(
    {"file", "files", "document", "documents", "data", "records", "history", "memory", "procedure", "procedures",
     "steps", "script", "scripts", "notes", "export", "exports", "spreadsheet", "spreadsheets"}
)
STOP_WORDS = frozenset(
    {"and", "or", "but", "then", "to", "with", "using", "via", "than", "in", "on", "at", "into", "for", "from",
     "of", "as", "by", "so", "because", "while", "when", "if", "is", "are", "was", "were", "be", "not", "instead"}
)
CONNECTORS = frozenset({"of", "from", "for", "as", "by"})
FORWARD_REACH = 4
BACKWARD_REACH = 3
SENTENCE_END = re.compile(r"[.!?;\n]+(?=\s|$)|\n")
WORD = re.compile(r"[a-z0-9]+")

VAULT_CHECK_SCRIPT = """\
import {{ readVault }} from {vault_module};
const vault = readVault({vault_path});
console.log(JSON.stringify(vault.ok
  ? {{ ok: true, names: Object.keys(vault.values).filter((name) => vault.values[name] !== "") }}
  : {{ ok: false, problem: vault.problem }}));
"""

VAULT_MERGE_SCRIPT = """\
import {{ readVault }} from {vault_module};
import {{ closeSync, fsyncSync, openSync, renameSync, rmSync, writeSync }} from "node:fs";
const vaultPath = {vault_path};
const incomingPath = {incoming_path};
const named = (vault) => vault.ok
  ? {{ ok: true, names: Object.keys(vault.values).filter((name) => vault.values[name] !== "") }}
  : {{ ok: false, problem: vault.problem }};
const incoming = readVault(incomingPath);
rmSync(incomingPath, {{ force: true }});
if (!incoming.ok) {{
  console.log(JSON.stringify(named(incoming)));
}} else {{
  const current = readVault(vaultPath);
  const values = {{ ...(current.ok ? current.values : {{}}), ...incoming.values }};
  const temporary = vaultPath + "." + process.pid + ".tmp";
  const file = openSync(temporary, "wx", 0o600);
  writeSync(file, JSON.stringify({{ version: {version}, values }}));
  fsyncSync(file);
  closeSync(file);
  renameSync(temporary, vaultPath);
  console.log(JSON.stringify(named(readVault(vaultPath))));
}}
"""


class ResourceKind(StrEnum):
    LOGIN = "login"
    COMPUTER = "computer"
    FILES = "files"


@dataclass(frozen=True)
class Resource:
    kind: ResourceKind
    site: str | None = None

    @property
    def label(self) -> str:
        if self.kind is ResourceKind.LOGIN and self.site:
            return f"{self.site} login"
        return self.kind.value


@dataclass(frozen=True)
class SiteKind:
    name: str
    words: frozenset[str]
    logins: tuple[str, ...]
    url: str


SUPPLIER = SiteKind("Maruyama Supply", frozenset({"maruyama", "supplier", "supply", "portal"}), SUPPLIER_LOGINS, SUPPLIER_URL)
STORE = SiteKind("store admin", frozenset({"store", "shop", "admin"}), STORE_LOGINS, STORE_URL)
SITE_KINDS = (SUPPLIER, STORE)
TRAVEL = SiteKind("Hikari Travel", frozenset({"hikari", "travel"}), TRAVEL_LOGINS, TRAVEL_URL)


@dataclass(frozen=True)
class Site:
    host: str
    name: str
    logins: tuple[str, ...]
    url: str

    @property
    def sign_in_url(self) -> str:
        return f"{self.url}{SIGN_IN_PATH}"


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    short_name: str
    nickname: str

    @property
    def aliases(self) -> tuple[tuple[str, ...], ...]:
        spelled = {tuple(WORD.findall(text.lower())) for text in (self.name, self.short_name, self.nickname, self.id)}
        return tuple(sorted((alias for alias in spelled if alias), key=len, reverse=True))


class BoundaryError(RuntimeError):
    def __init__(self, client_id: str, message: str) -> None:
        super().__init__(message)
        self.client_id = client_id


class UnknownClient(BoundaryError):
    pass


class ClientNotSetUp(BoundaryError):
    pass


class VaultError(BoundaryError):
    pass


@dataclass(frozen=True)
class Refusal:
    client_id: str
    owner: Client
    resource: Resource
    offers_own_account: bool

    def event_body(self, audit_id: str) -> dict:
        return {
            "resource": self.resource.label,
            "ownerClientId": self.owner.id,
            "ownerClientName": self.owner.name,
            "auditId": audit_id,
            "offersOwnAccount": self.offers_own_account,
        }


class BoundaryBlocked(BoundaryError):
    def __init__(self, refusal: Refusal, audit_id: str) -> None:
        super().__init__(
            refusal.client_id,
            f"{refusal.owner.name}'s {refusal.resource.label} is not available to this client. "
            f"Recorded as {audit_id}.",
        )
        self.refusal = refusal
        self.audit_id = audit_id


@cache
def known_clients() -> tuple[Client, ...]:
    data = json.loads(CLIENTS_PATH.read_text())
    if data.get("version") != CLIENT_LIST_VERSION:
        raise ValueError(f"{CLIENTS_PATH} must be client list version {CLIENT_LIST_VERSION}")
    clients = tuple(
        Client(id=entry["id"], name=entry["name"], short_name=entry["shortName"], nickname=entry["nickname"])
        for entry in data["clients"]
    )
    ids = [client.id for client in clients]
    if not clients or len(set(ids)) != len(ids) or not all(CLIENT_ID_PATTERN.fullmatch(id) for id in ids):
        raise ValueError(f"{CLIENTS_PATH} must list clients once each, with lowercase ids")
    return clients


def known_client(client_id: str) -> Client:
    for client in known_clients():
        if client.id == client_id:
            return client
    names = ", ".join(sorted(client.id for client in known_clients()))
    raise UnknownClient(client_id, f"There is no client {client_id!r}. Known clients: {names}.")


def site_for(host: str) -> Site:
    kind = SUPPLIER if host == SUPPLIER_PORTAL else STORE if host.startswith(STORE_HOST_PREFIX) else TRAVEL if host == TRAVEL_SITE else None
    if kind is None:
        raise ValueError(f"{host} is neither the supplier portal, a store admin nor the travel site")
    return Site(host=host, name=kind.name, logins=kind.logins, url=kind.url)


def sites_of(client_id: str) -> tuple[Site, ...]:
    return tuple(site_for(host) for host in CLIENT_SITES.get(client_id, ()))


def trip_sites_of(client_id: str) -> tuple[Site, ...]:
    return tuple(site_for(host) for host in CLIENT_TRIP_SITES.get(client_id, ()))


def trip_vault_names(client_id: str) -> tuple[str, ...]:
    sites = trip_sites_of(client_id)
    return (*(name for site in sites for name in site.logins), *TRAVELER_VALUES) if sites else ()


def data_directory() -> Path:
    configured = os.environ.get(DATA_DIR_VARIABLE)
    if configured:
        directory = Path(configured).expanduser()
    else:
        directory = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "clientops"
    directory = directory.resolve()
    if directory.is_relative_to(PROJECT_ROOT):
        raise ValueError(
            f"{DATA_DIR_VARIABLE} must point outside the project ({PROJECT_ROOT}), "
            "so the audit log is never copied into a snapshot."
        )
    return directory


def audit_log_path() -> Path:
    return data_directory() / AUDIT_FILE


class ComputerKind(StrEnum):
    DAYTONA = "daytona"
    LOCAL = "local"


def computer_kind() -> ComputerKind:
    value = os.environ.get(COMPUTERS_VARIABLE, "")
    if value == "":
        return ComputerKind.DAYTONA
    try:
        return ComputerKind(value)
    except ValueError:
        raise ValueError(
            f"{COMPUTERS_VARIABLE} must be 'daytona' or 'local' (unset means daytona), not {value!r}"
        ) from None


def local_computer_home(client_id: str) -> Path:
    return data_directory() / LOCAL_COMPUTERS_DIRECTORY / client_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def random_suffix() -> str:
    return secrets.token_hex(3)


class AuditLog:
    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        suffix: Callable[[], str] = random_suffix,
    ) -> None:
        self.path = path or audit_log_path()
        self._clock = clock
        self._suffix = suffix

    def record(self, refusal: Refusal, *, stage: str, job_id: str | None = None) -> str:
        at = self._clock().astimezone(UTC)
        audit_id = f"audit-{refusal.client_id}-{at:%Y%m%d-%H%M%S}-{self._suffix()}"
        entry = {
            "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "auditId": audit_id,
            "clientId": refusal.client_id,
            "ownerClientId": refusal.owner.id,
            "resource": refusal.resource.label,
            "stage": stage,
        }
        if job_id:
            entry["jobId"] = job_id
        self._append((json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
        return audit_id

    def _append(self, line: bytes) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def refuse(
    refusal: Refusal,
    *,
    stage: str,
    notes: EventNotes | None = None,
    audit: AuditLog | None = None,
    job_id: str | None = None,
) -> str:
    audit_id = (audit or AuditLog()).record(refusal, stage=stage, job_id=job_id)
    if notes is not None:
        notes.emit("boundary.blocked", **refusal.event_body(audit_id))
    return audit_id


def offers_own_account(client_id: str, resource: Resource) -> bool:
    if resource.kind is not ResourceKind.LOGIN:
        return False
    own = sites_of(client_id)
    return bool(own) and (resource.site is None or any(site.name == resource.site for site in own))


def refusal_for(client_id: str, owner: Client, resource: Resource) -> Refusal:
    return Refusal(client_id, owner, resource, offers_own_account(client_id, resource))


@dataclass(frozen=True)
class VaultRef:
    client_id: str
    path: str
    placeholders: tuple[str, ...]

    def checked_names(self, values: Mapping[str, Any]) -> tuple[str, ...]:
        owner = known_client(self.client_id)
        if not values:
            raise VaultError(self.client_id, f"Give at least one of {owner.nickname}'s logins.")
        unknown = [name for name in values if name not in self.placeholders]
        if unknown:
            raise VaultError(
                self.client_id,
                f"{owner.nickname}'s vault has no {', '.join(map(str, unknown))}. "
                f"It takes {', '.join(self.placeholders)}.",
            )
        blank = [name for name, value in values.items() if not isinstance(value, str) or value == ""]
        if blank:
            raise VaultError(self.client_id, f"{', '.join(blank)} must be filled in with text.")
        return tuple(name for name in self.placeholders if name in values)


@dataclass(frozen=True)
class MemoryScope:
    scope: str
    scope_id: str

    def open(self, memory: Any) -> Any:
        return memory.actor(self.scope_id)


@dataclass(frozen=True)
class LoginStatus:
    client_id: str
    present: tuple[str, ...]
    missing: tuple[str, ...]
    problem: str | None

    @property
    def complete(self) -> bool:
        return self.problem is None and not self.missing


@dataclass(frozen=True)
class JobComputer:
    computer: Computer
    runner_command: str

    def page(self) -> Page:
        return Page(self.computer, runner_command=self.runner_command)


@dataclass(frozen=True)
class ClientBoundary:
    client: Client
    network: NetworkPolicy
    sites: tuple[Site, ...]
    vault: VaultRef
    memory: MemoryScope

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return self.network.allow_list

    @property
    def supplier(self) -> Site:
        return next(site for site in self.sites if site.name == SUPPLIER.name)

    @property
    def store(self) -> Site:
        return next(site for site in self.sites if site.name == STORE.name)

    @property
    def travel(self) -> Site | None:
        return next(iter(trip_sites_of(self.client.id)), None)

    @property
    def trip_names(self) -> tuple[str, ...]:
        return trip_vault_names(self.client.id)

    def job_computer(self) -> JobComputer:
        computer_in_use()
        if computer_kind() is ComputerKind.LOCAL:
            return JobComputer(self.local_computer(), LOCAL_RUNNER_COMMAND)
        opened = self.computer()
        opened.start_browser()
        opened.start_sites()
        return JobComputer(opened, RUNNER_COMMAND)

    def allows(self, host: str) -> bool:
        return host in self.allowed_hosts

    def computer(self, daytona: Daytona | FakeDaytona | None = None) -> Computer:
        opened = Computers(daytona).open(self.client.id)
        label = (opened.sandbox.labels or {}).get(CLIENT_LABEL)
        for owner_id in (opened.client_id, label):
            if owner_id != self.client.id:
                self.require_own(owner_id, Resource(ResourceKind.COMPUTER))
        return opened

    def local_computer(self) -> LocalComputer:
        home = local_computer_home(self.client.id)
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        home.chmod(0o700)
        return LocalComputer(self.client.id, vault_path=home / LOCAL_VAULT_FILE, workspace=home / LOCAL_WORKSPACE)

    def prepared_computer(self) -> Computer:
        if computer_kind() is ComputerKind.LOCAL:
            return self.local_computer()
        return self.computer()

    def computer_state(self, daytona: Daytona | FakeDaytona | None = None) -> SandboxState | None:
        if daytona is None and computer_kind() is ComputerKind.LOCAL:
            return SandboxState.STARTED if local_computer_home(self.client.id).is_dir() else None
        return Computers(daytona).state(self.client.id)

    def stop_computer(self) -> bool:
        if computer_kind() is ComputerKind.LOCAL:
            return False
        return Computers().stop(self.client.id)

    def memory_in(self, memory: Any) -> Any:
        return self.memory.open(memory)

    def require_own(
        self,
        owner_client_id: str,
        resource: Resource,
        *,
        notes: EventNotes | None = None,
        audit: AuditLog | None = None,
        job_id: str | None = None,
    ) -> None:
        if owner_client_id == self.client.id:
            return
        refusal = refusal_for(self.client.id, known_client(owner_client_id), resource)
        audit_id = refuse(refusal, stage="resolution", notes=notes, audit=audit, job_id=job_id)
        raise BoundaryBlocked(refusal, audit_id)


def resolve_job(client_id: str) -> ClientBoundary:
    client = known_client(client_id)
    if client.id not in CLIENT_SITES:
        raise ClientNotSetUp(client.id, f"{client.name} is not set up yet. Set up its computer and logins first.")
    sites = sites_of(client.id)
    return ClientBoundary(
        client=client,
        network=network_policy(client.id),
        sites=sites,
        vault=VaultRef(
            client_id=client.id,
            path=VAULT_PATH,
            placeholders=tuple(name for site in sites for name in site.logins),
        ),
        memory=MemoryScope(scope=MEMORY_SCOPE, scope_id=f"{MEMORY_SCOPE_PREFIX}{client.id}"),
    )


@dataclass(frozen=True)
class Mention:
    client: Client
    start: int
    end: int


def normalized(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"(\w)'s\b", r"\1", text)
    return re.sub(r"(\w)s'(?=\W|$)", r"\1s", text)


def mentions_in(words: list[str]) -> list[Mention]:
    found: list[Mention] = []
    index = 0
    while index < len(words):
        match = max(
            (
                Mention(client, index, index + len(alias))
                for client in known_clients()
                for alias in client.aliases
                if tuple(words[index : index + len(alias)]) == alias
            ),
            key=lambda mention: mention.end,
            default=None,
        )
        if match:
            found.append(match)
            index = match.end
        else:
            index += 1
    return found


def kind_at(words: list[str], index: int) -> tuple[ResourceKind, int] | None:
    if tuple(words[index : index + 2]) in LOGIN_PHRASES:
        return ResourceKind.LOGIN, index + 2
    word = words[index]
    for kind, vocabulary in (
        (ResourceKind.LOGIN, LOGIN_WORDS),
        (ResourceKind.COMPUTER, COMPUTER_WORDS),
        (ResourceKind.FILES, FILES_WORDS),
    ):
        if word in vocabulary:
            return kind, index + 1
    return None


def site_word(word: str) -> SiteKind | None:
    return next((kind for kind in SITE_KINDS if word in kind.words), None)


def forward(words: list[str], mention: Mention, starts: set[int]) -> tuple[Resource, int] | None:
    site: SiteKind | None = None
    index = mention.end
    while index < min(len(words), mention.end + FORWARD_REACH) and index not in starts:
        found = kind_at(words, index)
        if found:
            kind, _ = found
            return Resource(kind, site.name if site and kind is ResourceKind.LOGIN else None), index
        if words[index] in STOP_WORDS:
            break
        site = site or site_word(words[index])
        index += 1
    return (Resource(ResourceKind.LOGIN, site.name), index) if site else None


def backward(words: list[str], mention: Mention, starts: set[int], claimed: set[int]) -> Resource | None:
    connector = mention.start - 1
    if connector < 0 or words[connector] not in CONNECTORS:
        return None
    for start in range(connector - 1, max(-1, mention.start - 1 - BACKWARD_REACH), -1):
        if start in starts or start in claimed:
            return None
        found = kind_at(words, start)
        if found and found[1] <= connector:
            return Resource(found[0])
    return None


def screen_sentence(client: Client, sentence: str) -> Refusal | None:
    words = WORD.findall(normalized(sentence))
    mentions = mentions_in(words)
    starts = {mention.start for mention in mentions}
    claimed: set[int] = set()
    targets: dict[int, Resource] = {}
    for position, mention in enumerate(mentions):
        found = forward(words, mention, starts)
        if found:
            targets[position] = found[0]
            claimed.add(found[1])
    for position, mention in enumerate(mentions):
        if mention.client.id == client.id:
            continue
        resource = targets.get(position) or backward(words, mention, starts, claimed)
        if resource:
            return refusal_for(client.id, mention.client, resource)
    return None


def single_owner_hosts(client: Client) -> dict[str, Client]:
    owners: dict[str, list[str]] = {}
    for owner_id, hosts in CLIENT_SITES.items():
        for host in hosts:
            owners.setdefault(host, []).append(owner_id)
    own = set(CLIENT_SITES.get(client.id, ()))
    return {
        host: known_client(ids[0])
        for host, ids in owners.items()
        if len(ids) == 1 and ids[0] != client.id and host not in own
    }


def screen_request(client_id: str, request: str) -> Refusal | None:
    client = known_client(client_id)
    lowered = request.lower()
    for host, owner in single_owner_hosts(client).items():
        if host in lowered:
            return refusal_for(client.id, owner, Resource(ResourceKind.LOGIN, site_for(host).name))
    for sentence in SENTENCE_END.split(request):
        refusal = screen_sentence(client, sentence)
        if refusal:
            return refusal
    return None


@dataclass(frozen=True)
class Admission:
    boundary: ClientBoundary | None
    outcome: dict


def admit_job(
    notes: EventNotes,
    *,
    client_id: str,
    request: str,
    audit: AuditLog | None = None,
    job_id: str | None = None,
) -> Admission:
    client = known_client(client_id)
    refusal = screen_request(client.id, request)
    if refusal:
        notes.emit(
            "job.created",
            clientName=client.name,
            title=f"Another client's {refusal.resource.kind.value}",
            request=request,
            run={"kind": "learn"},
            plan=[],
        )
        audit_id = refuse(refusal, stage="request", notes=notes, audit=audit, job_id=job_id)
        return Admission(None, {"status": "blocked", "auditId": audit_id})
    try:
        boundary = resolve_job(client.id)
    except ClientNotSetUp as error:
        notes.emit("job.created", clientName=client.name, title="Client not set up", request=request, run={"kind": "learn"}, plan=[])
        notes.emit("job.failed", reason="error", message=str(error))
        return Admission(None, {"status": "failed", "reason": "not_set_up"})
    return Admission(boundary, {})


def vault_check_command(vault_path: str = VAULT_PATH, runner: str | None = None) -> str:
    runner = runner or load_image_spec().copied_to(RUNNER_SOURCE)
    script = VAULT_CHECK_SCRIPT.format(vault_module=json.dumps(f"{runner}/vault.ts"), vault_path=json.dumps(vault_path))
    return f"bun -e {shlex.quote(script)} 2> /dev/null"


def vault_prepare_command() -> str:
    directory = shlex.quote(VAULT_DIRECTORY)
    return f"umask 077 && mkdir -p {directory} && chmod 700 {directory}"


def vault_merge_command(
    vault_path: str = VAULT_PATH, incoming_path: str = VAULT_INCOMING_PATH, runner: str | None = None
) -> str:
    runner = runner or load_image_spec().copied_to(RUNNER_SOURCE)
    script = VAULT_MERGE_SCRIPT.format(
        vault_module=json.dumps(f"{runner}/vault.ts"),
        vault_path=json.dumps(vault_path),
        incoming_path=json.dumps(incoming_path),
        version=VAULT_VERSION,
    )
    incoming = shlex.quote(incoming_path)
    return f"chmod 600 {incoming} && bun -e {shlex.quote(script)} 2> /dev/null; merged=$?; rm -f {incoming}; exit $merged"


def checked_trip_names(boundary: ClientBoundary, values: Mapping[str, Any]) -> tuple[str, ...]:
    blank = [name for name, value in values.items() if not isinstance(value, str) or value == ""]
    if blank:
        raise VaultError(boundary.client.id, f"{', '.join(blank)} must be filled in with text.")
    return tuple(name for name in boundary.trip_names if name in values)


def reported_names(output: str) -> tuple[str, ...]:
    try:
        answer = json.loads(output.strip().splitlines()[-1])
        names = answer["names"] if answer["ok"] is True else []
    except (ValueError, IndexError, KeyError, TypeError):
        return ()
    return tuple(name for name in names if isinstance(name, str)) if isinstance(names, list) else ()


def login_status(vault: VaultRef, output: str) -> LoginStatus:
    try:
        answer = json.loads(output.strip().splitlines()[-1])
        ok = answer["ok"] is True
        reported = answer["names"] if ok else []
        problem = None if ok else str(answer["problem"])
        if not isinstance(reported, list):
            raise TypeError
    except (ValueError, IndexError, KeyError, TypeError):
        ok, reported, problem = False, [], "unreadable"
    present = tuple(name for name in vault.placeholders if ok and name in reported)
    missing = tuple(name for name in vault.placeholders if name not in present)
    return LoginStatus(client_id=vault.client_id, present=present, missing=missing, problem=problem)


def run_in(computer: Computer, boundary: ClientBoundary, command: str, failure: str) -> str:
    try:
        result = computer.run(command, timeout_seconds=VAULT_TIMEOUT_SECONDS)
    except ComputerError as error:
        raise VaultError(boundary.client.id, f"{failure} ({error.problem.value}).") from None
    if result.exit_code != 0:
        raise VaultError(boundary.client.id, f"{failure} (exit code {result.exit_code}).")
    return result.output


def local_vault_check_command(computer: LocalComputer) -> str:
    return vault_check_command(str(computer.vault_path), runner=str(PROJECT_ROOT / RUNNER_SOURCE))


def put_local_vault(boundary: ClientBoundary, content: bytes, failure: str) -> str:
    computer = boundary.local_computer()
    incoming = computer.vault_path.with_name(f"{computer.vault_path.name}.incoming")
    try:
        descriptor = os.open(incoming, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        incoming.chmod(0o600)
    except OSError:
        incoming.unlink(missing_ok=True)
        raise VaultError(boundary.client.id, f"{failure}.") from None
    merge = vault_merge_command(str(computer.vault_path), str(incoming), runner=str(PROJECT_ROOT / RUNNER_SOURCE))
    return run_in(computer, boundary, merge, failure)


def put_daytona_vault(boundary: ClientBoundary, content: bytes, failure: str) -> str:
    computer = boundary.computer()
    run_in(computer, boundary, vault_prepare_command(), failure)
    try:
        computer.upload(content, VAULT_INCOMING_PATH)
    except ComputerError as error:
        raise VaultError(boundary.client.id, f"{failure} ({error.problem.value}).") from None
    except Exception:
        raise VaultError(boundary.client.id, f"{failure}.") from None
    return run_in(computer, boundary, vault_merge_command(), failure)


def put_logins(client_id: str, values: Mapping[str, str]) -> LoginStatus:
    boundary = resolve_job(client_id)
    trip = {name: value for name, value in values.items() if name in boundary.trip_names}
    logins = {name: value for name, value in values.items() if name not in trip}
    names = (*(boundary.vault.checked_names(logins) if logins or not trip else ()), *checked_trip_names(boundary, trip))
    content = json.dumps({"version": VAULT_VERSION, "values": {name: values[name] for name in names}}).encode()
    failure = f"Could not put the logins into {boundary.client.nickname}'s computer"
    put = put_local_vault if computer_kind() is ComputerKind.LOCAL else put_daytona_vault
    output = put(boundary, content, failure)
    status = login_status(boundary.vault, output)
    kept = reported_names(output)
    lost = [name for name in names if name not in kept]
    if status.problem or lost:
        raise VaultError(
            boundary.client.id,
            f"{failure}: the vault did not check out afterwards ({status.problem or 'missing ' + ', '.join(lost)}).",
        )
    return status


def check_logins(client_id: str) -> LoginStatus:
    boundary = resolve_job(client_id)
    failure = f"Could not check {boundary.client.nickname}'s logins"
    if computer_kind() is ComputerKind.LOCAL:
        computer = boundary.local_computer()
        output = run_in(computer, boundary, local_vault_check_command(computer), failure)
    else:
        output = run_in(boundary.computer(), boundary, vault_check_command(), failure)
    return login_status(boundary.vault, output)
