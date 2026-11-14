import os
import shlex
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Final

from .computer_protocol import CommandResult
from .computers import ComputerError, ComputerProblem, refuse_secrets

PROJECT_ROOT: Final = Path(__file__).resolve().parents[2]
LOCAL_RUNNER_COMMAND: Final = f"bun {shlex.quote(str(PROJECT_ROOT / 'sandbox' / 'runner' / 'main.ts'))}"
LOCAL_CDP_URL: Final = "ws://127.0.0.1:9222/"
LOCAL_LIGHTPANDA: Final = PROJECT_ROOT / "vendor" / "lightpanda" / "lightpanda"
PASSED_THROUGH: Final = ("PATH", "HOME", "TMPDIR", "LANG")


class LocalComputer:
    kind: Final = "local"

    def __init__(self, client_id: str, *, vault_path: Path, workspace: Path, cdp_url: str = LOCAL_CDP_URL) -> None:
        self.client_id = client_id
        self.vault_path = Path(vault_path)
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        socket = Path(tempfile.gettempdir()) / f"cr-{client_id}-{uuid.uuid4().hex[:8]}.sock"
        self._env = {
            **{name: os.environ[name] for name in PASSED_THROUGH if name in os.environ},
            "CLIENTOPS_VAULT": str(self.vault_path),
            "CLIENTOPS_RUNNER_SOCKET": str(socket),
            "LIGHTPANDA_CDP_WS": cdp_url,
            "CLIENTOPS_LIGHTPANDA": str(LOCAL_LIGHTPANDA),
        }

    def run(self, command: str, *, timeout_seconds: int, env: Mapping[str, str] | None = None) -> CommandResult:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise ValueError(f"timeout_seconds must be a whole number of seconds above 0, not {timeout_seconds!r}")
        if env:
            refuse_secrets(self.client_id, env)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                env={**self._env, **(env or {})},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise ComputerError(
                ComputerProblem.TIMED_OUT,
                self.client_id,
                f"Could not run a command on {self.client_id}'s local computer: It did not finish within {timeout_seconds} seconds.",
            ) from error
        return CommandResult(exit_code=completed.returncode, output=completed.stdout)

    def upload(self, content: bytes, remote_path: str) -> None:
        if not isinstance(content, bytes):
            raise TypeError(f"upload takes bytes, not {type(content).__name__}")
        parts = PurePosixPath(remote_path).parts
        if ".." in parts:
            raise ValueError(f"{remote_path!r} leaves the computer's files")
        target = self.workspace / "files" / PurePosixPath(*[part for part in parts if part != "/"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(mode=0o600, exist_ok=True)
        target.chmod(0o600)
        target.write_bytes(content)
