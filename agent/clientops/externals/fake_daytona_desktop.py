import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Final

from daytona import DaytonaError
from daytona.common.errors import SOURCE_DAEMON, create_daytona_error
from daytona_toolbox_api_client import AccessibilityTreeResponse, ComputerUseStartResponse, DaemonErrorCode

if TYPE_CHECKING:
    from clientops.externals.fake_daytona import FakeDaytona

ACCESSIBILITY_SAMPLES_DIR: Final = (
    Path(__file__).resolve().parents[2] / "tests" / "contract" / "external" / "daytona" / "accessibility"
)
READABLE_SCOPES: Final = ("all", "pid")
EDITABLE_STATE: Final = "editable"


def load_accessibility_sample(name: str) -> dict:
    return json.loads((ACCESSIBILITY_SAMPLES_DIR / name).read_text())


def walk(node: dict) -> Iterator[dict]:
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def daemon_error(prefix: str, status: int, code: DaemonErrorCode, message: str) -> DaytonaError:
    return create_daytona_error(f"{prefix}{message}", status_code=status, code=code.value, source=SOURCE_DAEMON)


def unavailable_error(prefix: str) -> DaytonaError:
    sample = load_accessibility_sample("error_a11y_unavailable.json")
    return create_daytona_error(
        f"{prefix}{sample['message']}", status_code=sample["statusCode"], code=sample["code"], source=sample["source"]
    )


class FakeDesktop:
    def __init__(self) -> None:
        self.started = False
        self.tree: dict = load_accessibility_sample("tree_all.json")["root"]
        self.truncate = False
        self.pids: dict[int, str] = {}
        self.values: dict[str, str] = {}

    def node(self, node_id: str) -> dict | None:
        return next((node for node in walk(self.tree) if node.get("id") == node_id), None)

    def application(self, pid: int | None) -> dict | None:
        name = self.pids.get(pid)
        if name is None:
            return None
        apps = (child for child in self.tree.get("children", []) if child.get("role") == "application")
        return next((app for app in apps if app.get("name") == name), None)


class FakeAccessibility:
    def __init__(self, cloud: "FakeDaytona", sandbox_id: str) -> None:
        self._cloud = cloud
        self._sandbox_id = sandbox_id

    def get_tree(
        self,
        scope: str | None = None,
        pid: int | None = None,
        max_depth: int | None = None,
        request_timeout: float | None = None,
    ) -> AccessibilityTreeResponse:
        if scope not in READABLE_SCOPES or max_depth is not None:
            raise ValueError("FakeAccessibility reads the whole tree of scope 'all' or 'pid' only")
        prefix = "Failed to get accessibility tree: "
        desktop = self._ready(prefix)
        root = desktop.tree if scope == "all" else desktop.application(pid)
        if root is None:
            raise daemon_error(prefix, 404, DaemonErrorCode.CodeNotFound, f"no accessible application with pid {pid}")
        self._cloud._record("accessibility.get_tree", self._sandbox_id, scope=scope, pid=pid, max_depth=max_depth)
        return AccessibilityTreeResponse.from_dict({"root": root, "truncated": desktop.truncate})

    def invoke_node(self, node_id: str, action: str | None = None, request_timeout: float | None = None) -> None:
        prefix = "Failed to invoke accessibility node: "
        node = self._node(node_id, prefix)
        actions = node.get("actions") or []
        if (action is None and not actions) or (action is not None and action not in actions):
            missing = f"action {action}" if action is not None else "action to invoke"
            raise daemon_error(prefix, 400, DaemonErrorCode.CodeBadRequest, f"node {node_id} has no {missing}")
        self._cloud._record("accessibility.invoke_node", self._sandbox_id, node_id=node_id, action=action)

    def set_node_value(self, node_id: str, value: str, request_timeout: float | None = None) -> None:
        prefix = "Failed to set accessibility node value: "
        node = self._node(node_id, prefix)
        if EDITABLE_STATE not in (node.get("states") or []):
            raise daemon_error(prefix, 400, DaemonErrorCode.CodeBadRequest, f"node {node_id} does not support value changes")
        self._cloud._record("accessibility.set_node_value", self._sandbox_id, node_id=node_id, value=value)
        self._cloud.desktop(self._sandbox_id).values[node_id] = value

    def _ready(self, prefix: str) -> FakeDesktop:
        self._cloud._require_running(self._sandbox_id, prefix)
        desktop = self._cloud.desktop(self._sandbox_id)
        if not desktop.started:
            raise unavailable_error(prefix)
        return desktop

    def _node(self, node_id: str, prefix: str) -> dict:
        node = self._ready(prefix).node(node_id)
        if node is None:
            raise daemon_error(prefix, 404, DaemonErrorCode.CodeNotFound, f"accessibility node {node_id} not found")
        return node


class FakeComputerUse:
    def __init__(self, cloud: "FakeDaytona", sandbox_id: str) -> None:
        self._cloud = cloud
        self._sandbox_id = sandbox_id
        self.accessibility = FakeAccessibility(cloud, sandbox_id)

    def start(self, request_timeout: float | None = None) -> ComputerUseStartResponse:
        self._cloud._require_running(self._sandbox_id, "Failed to start computer use: ")
        self._cloud.desktop(self._sandbox_id).started = True
        self._cloud._record("computer_use.start", self._sandbox_id)
        return ComputerUseStartResponse.from_dict(load_accessibility_sample("computer_use_start.json"))
