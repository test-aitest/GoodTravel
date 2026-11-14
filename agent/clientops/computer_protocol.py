from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str


@runtime_checkable
class Computer(Protocol):
    client_id: str

    def run(self, command: str, *, timeout_seconds: int, env: Mapping[str, str] | None = None) -> CommandResult: ...

    def upload(self, content: bytes, remote_path: str) -> None: ...
