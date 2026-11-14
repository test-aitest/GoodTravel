import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .records import EventNotes

SkillCall = Callable[..., Awaitable[dict]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class StubStep:
    step_id: str
    title: str
    activity: str
    summary: str


STUB_PLAN = (
    StubStep("open-computer", "Open the client's computer", "Opening the client's computer", "Opened the client's computer"),
    StubStep("read-supplier", "Read the supplier portal", "Reading the supplier portal", "Read the supplier portal"),
    StubStep("read-store", "Read the store admin", "Reading the store admin", "Read the store admin"),
)


def elapsed_ms(start: float, end: float) -> int:
    return round((end - start) * 1000)


async def decide(call_skill: SkillCall, step_id: str) -> tuple[str, list[str]] | None:
    replay = await call_skill("replay_script", step_id=step_id)
    if replay.get("ok"):
        return "script", []
    refind = await call_skill("refind_by_tree", step_id=step_id)
    if refind.get("found"):
        return "tree", ["script"]
    return None


async def run_stub_job(
    notes: EventNotes,
    call_skill: SkillCall,
    *,
    client_name: str,
    request: str,
    clock: Clock = time.monotonic,
) -> dict:
    notes.emit(
        "job.created",
        clientName=client_name,
        title="Connection check",
        request=request,
        run={"kind": "learn"},
        plan=[{"stepId": step.step_id, "title": step.title} for step in STUB_PLAN],
    )
    for step in STUB_PLAN:
        started = clock()
        notes.emit("step.started", stepId=step.step_id, activity=step.activity)
        decision = await decide(call_skill, step.step_id)
        if decision is None:
            notes.emit(
                "job.failed",
                reason="error",
                message="The worker could not find this step on the page.",
                stepId=step.step_id,
            )
            return {"status": "failed", "stepId": step.step_id}
        rung, escalated_from = decision
        decided = {"stepId": step.step_id, "rung": rung, "tookMs": elapsed_ms(started, clock())}
        if escalated_from:
            decided["escalatedFrom"] = escalated_from
        notes.emit("step.decided", **decided)
        notes.emit("step.done", stepId=step.step_id, summary=step.summary, tookMs=elapsed_ms(started, clock()))
    return {"status": "done", "steps": len(STUB_PLAN)}
