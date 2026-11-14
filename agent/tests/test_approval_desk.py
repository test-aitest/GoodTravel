import asyncio
from types import SimpleNamespace

from agentfield.agent import Agent, _PauseManager
from agentfield.client import ApprovalResult

import clientops.approval as approval
from clientops.approval import AgentDesk, wait_for_answer

JOB = "exec-1"
APPROVAL = "apr-exec-1"


class ControlPlane:
    def __init__(self) -> None:
        self.status = "pending"

    async def request_approval(self, **_: object) -> None:
        return None

    async def get_approval_status(self, execution_id: str) -> SimpleNamespace:
        return SimpleNamespace(status=self.status, response={"execution_id": execution_id})


class SdkAgent:
    pause = Agent.pause
    wait_for_resume = Agent.wait_for_resume

    def __init__(self) -> None:
        self.base_url = "http://127.0.0.1:8001"
        self.client = ControlPlane()
        self._pause_manager = _PauseManager()
        self._pause_clocks: dict = {}

    def note(self, *_: object, **__: object) -> None:
        return None


def test_waiting_again_after_a_quiet_minute_keeps_waiting_instead_of_ending_the_job():
    async def wait_three_times() -> list[str]:
        desk = AgentDesk(SdkAgent())
        first = await desk.pause(APPROVAL, expires_in_hours=72, timeout=0.01, execution_id=JOB)
        second = await desk.wait_for_resume(APPROVAL, execution_id=JOB, timeout=0.01)
        third = await desk.wait_for_resume(APPROVAL, execution_id=JOB, timeout=0.01)
        return [first.decision, second.decision, third.decision]

    assert asyncio.run(wait_three_times()) == ["expired", "expired", "expired"]


def test_an_ok_given_after_several_quiet_minutes_still_reaches_the_waiting_job(monkeypatch):
    monkeypatch.setattr(approval, "ANSWER_POLL_SECONDS", 0.02)
    agent = SdkAgent()

    async def answer_later() -> None:
        await asyncio.sleep(0.2)
        agent.client.status = "approved"
        await agent._pause_manager.resolve(APPROVAL, ApprovalResult(decision="approved", execution_id=JOB, approval_request_id=APPROVAL))

    async def wait() -> str:
        answering = asyncio.create_task(answer_later())
        answer = await wait_for_answer(AgentDesk(agent), APPROVAL, JOB)
        await answering
        return answer.decision

    assert asyncio.run(wait()) == "approved"
