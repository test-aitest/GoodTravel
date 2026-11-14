import asyncio
from collections import deque
from typing import Any

from agentfield.client import ApprovalResult
from agentfield.exceptions import MemoryAccessError


class MemoryUnavailable(MemoryAccessError):
    pass


class MemoryStore:
    def __init__(self, *, failing: tuple[str, ...] = ()) -> None:
        self.values: dict[str, Any] = {}
        self.failing = set(failing)
        self.calls: list[tuple[str, str]] = []
        self.written: list[tuple[str, Any]] = []

    async def get(self, key: str, default: Any = None) -> Any:
        self.calls.append(("get", key))
        if "get" in self.failing:
            raise MemoryUnavailable("memory is down")
        return self.values.get(key, default)

    async def set(self, key: str, data: Any) -> None:
        self.calls.append(("set", key))
        if "set" in self.failing or f"set:{key}" in self.failing:
            raise MemoryUnavailable("memory is down")
        self.values[key] = data
        self.written.append((key, data))

    async def delete(self, key: str) -> None:
        self.calls.append(("delete", key))
        if "delete" in self.failing:
            raise MemoryUnavailable("memory is down")
        self.values.pop(key, None)

    async def list_keys(self) -> list[str]:
        self.calls.append(("list", ""))
        if "list" in self.failing:
            raise MemoryUnavailable("memory is down")
        return list(self.values)


class Forever:
    pass


FOREVER = Forever()


class FakeDesk:
    def __init__(self, *answers: Any, statuses: tuple[str, ...] = (), shutting_down: bool = False) -> None:
        self.answers = deque(answers)
        self.stopping_the_agent = shutting_down
        self.statuses = deque(statuses)
        self.paused: list[dict] = []
        self.resumed: list[dict] = []
        self.released: list[str] = []
        self.status_checks: list[str] = []
        self.waiting = asyncio.Event()

    async def pause(self, approval_request_id: str, *, expires_in_hours: int, timeout: float, execution_id: str) -> ApprovalResult:
        self.paused.append({"approvalId": approval_request_id, "executionId": execution_id, "expiresInHours": expires_in_hours, "timeout": timeout})
        return await self._next(approval_request_id, execution_id)

    async def wait_for_resume(self, approval_request_id: str, *, execution_id: str, timeout: float) -> ApprovalResult:
        self.resumed.append({"approvalId": approval_request_id, "executionId": execution_id, "timeout": timeout})
        return await self._next(approval_request_id, execution_id)

    async def approval_status(self, execution_id: str) -> str:
        self.status_checks.append(execution_id)
        return self.statuses.popleft() if self.statuses else "pending"

    async def release(self, approval_request_id: str) -> None:
        self.released.append(approval_request_id)

    def shutting_down(self) -> bool:
        return self.stopping_the_agent

    async def _next(self, approval_request_id: str, execution_id: str) -> ApprovalResult:
        self.waiting.set()
        answer = self.answers.popleft() if self.answers else FOREVER
        if isinstance(answer, Forever):
            await asyncio.Event().wait()
        if isinstance(answer, BaseException):
            raise answer
        decision, feedback = answer if isinstance(answer, tuple) else (answer, "")
        return ApprovalResult(decision=decision, feedback=feedback, execution_id=execution_id, approval_request_id=approval_request_id)


def ticking_wall(start: float = 1_794_636_000.0, step: float = 0.5):
    moments = iter(start + index * step for index in range(10_000))
    return lambda: next(moments)
