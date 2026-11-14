import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from daytona import (
    CodeLanguage,
    CreateSandboxFromSnapshotParams,
    CreateSnapshotParams,
    DaytonaBadRequestError,
    DaytonaConflictError,
    DaytonaError,
    DaytonaNotFoundError,
    ExecuteResponse,
    ExecutionArtifacts,
    ListSandboxesQuery,
    SandboxState,
)
from daytona.common.daytona import CODE_TOOLBOX_LANGUAGE_LABEL
from daytona.common.snapshot import Snapshot
from daytona_api_client import Sandbox as SandboxDto
from daytona_api_client import SandboxListItem, SignedPortPreviewUrl, SnapshotDto, SnapshotState
from daytona_toolbox_api_client import ExecuteResponse as WireExecuteResponse
from pydantic import ConfigDict, PrivateAttr

from clientops.externals.fake_daytona_desktop import FakeComputerUse, FakeDesktop

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "tests" / "contract" / "external" / "daytona"

DEFAULT_AUTO_STOP_MINUTES = 15
DEFAULT_SIGNED_PREVIEW_SECONDS = 60
LONGEST_SIGNED_PREVIEW_SECONDS = 86_400
UNSCRIPTED_COMMAND_RESPONSE = {"exitCode": 0, "result": ""}
FAKE_ID_NAMESPACE = uuid.UUID("0b6f5c8e-2d4a-4f1b-9c3e-7a5d1e0f8b26")
LIST_ITEM_WIRE_KEYS = frozenset(
    spec.serialization_alias or name
    for name, spec in SandboxListItem.model_fields.items()
    if name != "additional_properties"
)


def load_sample(name: str) -> dict:
    return json.loads((SAMPLES_DIR / name).read_text())


@dataclass
class FakeCall:
    method: str
    sandbox_id: str | None
    arguments: dict = field(default_factory=dict)


class FakeProcess:
    def __init__(self, sandbox: "FakeSandbox") -> None:
        self._sandbox = sandbox

    def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        cloud = self._sandbox._cloud
        cloud._require_running(self._sandbox.id, "Failed to execute command: ")
        cloud._record("exec", self._sandbox.id, command=command, cwd=cwd, env=env, timeout=timeout)
        wire = cloud._response_to(command)
        result = wire.result or ""
        return ExecuteResponse.model_construct(
            exit_code=wire.exit_code if wire.exit_code is not None else wire.additional_properties.get("code"),
            result=result,
            artifacts=ExecutionArtifacts(stdout=result, charts=[]),
            additional_properties=wire.additional_properties,
        )


class FakeFileSystem:
    def __init__(self, sandbox: "FakeSandbox") -> None:
        self._sandbox = sandbox

    def upload_file(self, src: str | bytes, dst: str, timeout: int = 30 * 60) -> None:
        cloud = self._sandbox._cloud
        cloud._require_running(self._sandbox.id, "Failed to upload files: ")
        content = src if isinstance(src, bytes) else Path(src).read_bytes()
        cloud._record("upload_file", self._sandbox.id, dst=dst, content=content)


class FakeSandbox(SandboxDto):
    env: dict[str, str] | None = None
    network_block_all: bool | None = None
    kvm: bool | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _cloud: "FakeDaytona" = PrivateAttr()
    _process: FakeProcess = PrivateAttr()
    _fs: FakeFileSystem = PrivateAttr()
    _computer_use: FakeComputerUse = PrivateAttr()

    @classmethod
    def _bound_to(cls, cloud: "FakeDaytona", record: SandboxDto | SandboxListItem) -> "FakeSandbox":
        sandbox = cls(**record.model_dump())
        sandbox._cloud = cloud
        sandbox._process = FakeProcess(sandbox)
        sandbox._fs = FakeFileSystem(sandbox)
        sandbox._computer_use = FakeComputerUse(cloud, sandbox.id)
        return sandbox

    @property
    def process(self) -> FakeProcess:
        return self._process

    @property
    def fs(self) -> FakeFileSystem:
        return self._fs

    @property
    def computer_use(self) -> FakeComputerUse:
        return self._computer_use

    def refresh_data(self, request_timeout: float | None = None) -> None:
        self._cloud._record("refresh_data", self.id)
        self._take(self._cloud._stored(self.id))

    def start(self, timeout: float | None = 60):
        self._cloud._set_state(self.id, SandboxState.STARTED)
        self._cloud._record("start", self.id)
        self._take(self._cloud._stored(self.id))

    def stop(self, timeout: float | None = 60, force: bool = False):
        self._cloud._set_state(self.id, SandboxState.STOPPED)
        self._cloud._record("stop", self.id, force=force)
        self._take(self._cloud._stored(self.id))

    def create_signed_preview_url(
        self,
        port: int,
        expires_in_seconds: int | None = None,
        request_timeout: float | None = None,
    ) -> SignedPortPreviewUrl:
        seconds = DEFAULT_SIGNED_PREVIEW_SECONDS if expires_in_seconds is None else expires_in_seconds
        if not 1 <= seconds <= LONGEST_SIGNED_PREVIEW_SECONDS:
            raise DaytonaBadRequestError(
                "Failed to create signed preview url: expiresInSeconds must be between 1 and 86400",
                status_code=400,
            )
        preview = self._cloud._signed_preview(self.id, port)
        self._cloud._record(
            "create_signed_preview_url", self.id, port=port, expires_in_seconds=seconds, token=preview.token
        )
        return preview

    def expire_signed_preview_url(self, port: int, token: str, request_timeout: float | None = None) -> None:
        self._cloud._record("expire_signed_preview_url", self.id, port=port, token=token)

    def _take(self, record: SandboxDto) -> None:
        for name in SandboxDto.model_fields:
            setattr(self, name, getattr(record, name))


class FakeSnapshotService:
    def __init__(self, cloud: "FakeDaytona") -> None:
        self._cloud = cloud
        self._snapshots: dict[str, dict] = {}
        self._build_failures: list[str] = []
        self._created = 0
        self._template = load_sample("snapshot.json")

    def fail_next_build(self, reason: str) -> None:
        self._build_failures.append(reason)

    def get(self, name: str) -> Snapshot:
        wire = self._find(name)
        if wire is None:
            raise DaytonaNotFoundError(f"Failed to get snapshot: Snapshot {name} not found", status_code=404)
        self._cloud._record("snapshot.get", None, name=name)
        return self._loaded(wire)

    def create(
        self,
        params: CreateSnapshotParams,
        *,
        on_logs: Callable[[str], None] | None = None,
        timeout: float | None = 0,
    ) -> Snapshot:
        if self._find(params.name) is not None:
            raise DaytonaConflictError(
                f"Failed to create snapshot: Snapshot with name {params.name} already exists",
                status_code=409,
            )
        image_name, dockerfile, context_paths = self._build_input(params)
        for path in context_paths:
            if not Path(path).exists():
                raise DaytonaError(f"Failed to create snapshot: Path does not exist: {path}")
        failure = self._build_failures.pop(0) if self._build_failures else None

        self._created += 1
        wire = deepcopy(self._template)
        resources = params.resources
        wire.update(
            {
                "id": str(uuid.uuid5(FAKE_ID_NAMESPACE, f"snapshot-{self._created}")),
                "name": params.name,
                "state": (SnapshotState.BUILD_FAILED if failure else SnapshotState.ACTIVE).value,
                "entrypoint": params.entrypoint,
                "errorReason": failure,
                "cpu": resources.cpu if resources and resources.cpu is not None else self._template["cpu"],
                "gpu": resources.gpu if resources and resources.gpu is not None else self._template["gpu"],
                "mem": resources.memory if resources and resources.memory is not None else self._template["mem"],
                "disk": resources.disk if resources and resources.disk is not None else self._template["disk"],
            }
        )
        if image_name is not None:
            wire["imageName"] = image_name
            wire.pop("buildInfo")
        else:
            wire["buildInfo"]["dockerfileContent"] = dockerfile
            wire["buildInfo"]["contextHashes"] = [
                hashlib.md5(Path(path).read_bytes()).hexdigest() for path in context_paths
            ]
        self._snapshots[wire["id"]] = wire
        self._cloud._record(
            "snapshot.create", None, params=params, dockerfile=dockerfile, context_paths=context_paths
        )
        if on_logs:
            on_logs(f"Creating snapshot {params.name} ({SnapshotState.PENDING.value})")
        if failure:
            raise DaytonaError(
                f"Failed to create snapshot: Failed to create snapshot {params.name}, reason: {failure}"
            )
        if on_logs:
            on_logs(f"Created snapshot {params.name} ({SnapshotState.ACTIVE.value})")
        return self._loaded(wire)

    def activate(self, snapshot: Snapshot | str) -> Snapshot:
        name = snapshot if isinstance(snapshot, str) else snapshot.id
        wire = self._find(name)
        if wire is None:
            raise DaytonaNotFoundError(f"Snapshot {name} not found", status_code=404)
        wire["state"] = SnapshotState.ACTIVE.value
        self._cloud._record("snapshot.activate", None, snapshot=name)
        return self._loaded(wire)

    def _set_state(self, name: str, state: SnapshotState) -> None:
        self._find(name)["state"] = state.value

    @staticmethod
    def _build_input(params: CreateSnapshotParams) -> tuple[str | None, str | None, list[str]]:
        if isinstance(params.image, str):
            return params.image, None, []
        image = params.image.entrypoint(params.entrypoint) if params.entrypoint else params.image
        return None, image.dockerfile(), [context.source_path for context in image._context_list]

    def _find(self, name_or_id: str) -> dict | None:
        for snapshot_id, wire in self._snapshots.items():
            if name_or_id in (snapshot_id, wire["name"]):
                return wire
        return None

    @staticmethod
    def _loaded(wire: dict) -> Snapshot:
        return Snapshot.from_dto(SnapshotDto.from_dict(deepcopy(wire)))


class FakeDaytona:
    def __init__(self) -> None:
        self.calls: list[FakeCall] = []
        self.snapshot = FakeSnapshotService(self)
        self._sandboxes: dict[str, dict] = {}
        self._desktops: dict[str, FakeDesktop] = {}
        self._responses: dict[str, WireExecuteResponse] = {}
        self._created = 0
        self._previews = 0
        self._sandbox_template = load_sample("sandbox.json")
        preview_template = load_sample("signed_port_preview_url.json")
        self._preview_domain = urlsplit(preview_template["url"]).hostname.split(".", 1)[1]
        self._preview_token_length = len(preview_template["token"])

    def script_command(self, command: str, response: dict) -> None:
        self._responses[command] = WireExecuteResponse.from_dict(response)

    def desktop(self, sandbox_id: str) -> FakeDesktop:
        if sandbox_id not in self._sandboxes:
            raise KeyError(f"FakeDaytona has no sandbox {sandbox_id}")
        if sandbox_id not in self._desktops:
            self._desktops[sandbox_id] = FakeDesktop()
        return self._desktops[sandbox_id]

    def create(
        self,
        params: CreateSandboxFromSnapshotParams | None = None,
        *,
        timeout: float = 60,
        on_snapshot_create_logs: Callable[[str], None] | None = None,
    ) -> FakeSandbox:
        if not params:
            params = CreateSandboxFromSnapshotParams(language=CodeLanguage.PYTHON)
        elif not params.language:
            params.language = CodeLanguage.PYTHON
        if not isinstance(params, CreateSandboxFromSnapshotParams):
            raise TypeError("FakeDaytona creates sandboxes from snapshots only")
        params.labels = params.labels or {}
        params.labels[CODE_TOOLBOX_LANGUAGE_LABEL] = str(params.language)
        self._check_create(params)

        self._created += 1
        sandbox_id = str(uuid.uuid5(FAKE_ID_NAMESPACE, f"sandbox-{self._created}"))
        wire = deepcopy(self._sandbox_template)
        wire.update(
            {
                "id": sandbox_id,
                "name": params.name or sandbox_id,
                "snapshot": params.snapshot,
                "env": dict(params.env_vars or {}),
                "labels": dict(params.labels),
                "public": bool(params.public),
                "networkBlockAll": bool(params.network_block_all),
                "networkAllowList": params.network_allow_list,
                "domainAllowList": params.domain_allow_list,
                "autoStopInterval": (
                    DEFAULT_AUTO_STOP_MINUTES if params.auto_stop_interval is None else params.auto_stop_interval
                ),
                "state": SandboxState.STARTED.value,
            }
        )
        self._sandboxes[sandbox_id] = wire
        self._record("create", sandbox_id, params=params)
        return FakeSandbox._bound_to(self, self._stored(sandbox_id))

    def get(self, sandbox_id_or_name: str, request_timeout: float | None = None) -> FakeSandbox:
        if not sandbox_id_or_name:
            raise DaytonaBadRequestError("Failed to get sandbox: sandbox_id_or_name is required")
        sandbox_id = self._resolve(sandbox_id_or_name)
        self._record("get", sandbox_id, sandbox_id_or_name=sandbox_id_or_name)
        return FakeSandbox._bound_to(self, self._stored(sandbox_id))

    def list(
        self,
        query: ListSandboxesQuery | None = None,
        request_timeout: float | None = None,
    ) -> Iterator[FakeSandbox]:
        query = query or ListSandboxesQuery()
        self._record("list", None, query=query)
        matching = [wire for wire in self._sandboxes.values() if self._matches(wire, query)]
        return iter([FakeSandbox._bound_to(self, self._listed(wire)) for wire in matching])

    def start(self, sandbox: FakeSandbox, timeout: float = 60) -> None:
        sandbox.start(timeout)

    def stop(self, sandbox: FakeSandbox, timeout: float = 60) -> None:
        sandbox.stop(timeout)

    def _check_create(self, params: CreateSandboxFromSnapshotParams) -> None:
        if params.auto_stop_interval is not None and params.auto_stop_interval < 0:
            raise DaytonaBadRequestError("Failed to create sandbox: auto_stop_interval must be a non-negative integer")
        if params.network_block_all and (params.network_allow_list or params.domain_allow_list):
            raise DaytonaBadRequestError(
                "Failed to create sandbox: networkBlockAll cannot be combined with networkAllowList or domainAllowList",
                status_code=400,
            )
        if params.name and any(wire["name"] == params.name for wire in self._sandboxes.values()):
            raise DaytonaConflictError(
                f"Failed to create sandbox: Sandbox with name {params.name} already exists",
                status_code=409,
            )

    def _resolve(self, sandbox_id_or_name: str) -> str:
        for sandbox_id, wire in self._sandboxes.items():
            if sandbox_id_or_name in (sandbox_id, wire["name"]):
                return sandbox_id
        raise DaytonaNotFoundError(
            f"Failed to get sandbox: Sandbox with ID or name {sandbox_id_or_name} not found",
            status_code=404,
        )

    @staticmethod
    def _matches(wire: dict, query: ListSandboxesQuery) -> bool:
        labels = query.labels or {}
        if any(wire["labels"].get(key) != value for key, value in labels.items()):
            return False
        return not query.states or SandboxState(wire["state"]) in query.states

    def _stored(self, sandbox_id: str) -> SandboxDto:
        return SandboxDto.from_dict(deepcopy(self._sandboxes[sandbox_id]))

    @staticmethod
    def _listed(wire: dict) -> SandboxListItem:
        return SandboxListItem.from_dict({key: deepcopy(value) for key, value in wire.items() if key in LIST_ITEM_WIRE_KEYS})

    def _set_state(self, sandbox_id: str, state: SandboxState) -> None:
        self._sandboxes[sandbox_id]["state"] = state.value

    def _require_running(self, sandbox_id: str, message_prefix: str) -> None:
        state = SandboxState(self._sandboxes[sandbox_id]["state"])
        if state is not SandboxState.STARTED:
            raise DaytonaError(f"{message_prefix}sandbox {sandbox_id} is {state.value}, not started")

    def _response_to(self, command: str) -> WireExecuteResponse:
        return self._responses.get(command) or WireExecuteResponse.from_dict(UNSCRIPTED_COMMAND_RESPONSE)

    def _signed_preview(self, sandbox_id: str, port: int) -> SignedPortPreviewUrl:
        self._previews += 1
        seed = f"{sandbox_id}:{port}:{self._previews}".encode()
        token = hashlib.sha256(seed).hexdigest()[: self._preview_token_length]
        return SignedPortPreviewUrl.from_dict(
            {
                "sandboxId": sandbox_id,
                "port": port,
                "token": token,
                "url": f"https://{port}-{token}.{self._preview_domain}",
            }
        )

    def _record(self, method: str, sandbox_id: str | None, **arguments) -> None:
        self.calls.append(FakeCall(method, sandbox_id, arguments))
