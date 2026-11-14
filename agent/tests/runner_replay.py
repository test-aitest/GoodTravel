import json
import shlex
from collections.abc import Mapping
from pathlib import Path

from clientops.computer_protocol import CommandResult

RUNNER_RECORDINGS = Path(__file__).parent / "contract" / "runner"


class UnrecordedRunnerCommand(LookupError):
    pass


class ReplayComputer:
    def __init__(self, recording: str = "kumo-session", client_id: str = "kumo") -> None:
        self.client_id = client_id
        data = json.loads((RUNNER_RECORDINGS / f"{recording}.json").read_text())
        self._exchanges = [dict(exchange, answered=False) for exchange in data["exchanges"]]
        self.runs: list[tuple[str, int, Mapping[str, str] | None]] = []
        self.uploads: list[tuple[bytes, str]] = []

    def run(self, command: str, *, timeout_seconds: int, env: Mapping[str, str] | None = None) -> CommandResult:
        self.runs.append((command, timeout_seconds, env))
        sent = json.loads(shlex.split(command)[-1])
        for exchange in self._exchanges:
            if not exchange["answered"] and exchange["command"] == sent:
                exchange["answered"] = True
                return CommandResult(exit_code=exchange["exitCode"], output=exchange["output"])
        raise UnrecordedRunnerCommand(f"{json.dumps(sent)} is not in the recording")

    def upload(self, content: bytes, remote_path: str) -> None:
        self.uploads.append((content, remote_path))


def recorded_output(index: int, recording: str = "kumo-session") -> dict:
    data = json.loads((RUNNER_RECORDINGS / f"{recording}.json").read_text())
    return json.loads(data["exchanges"][index]["output"])
