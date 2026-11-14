import asyncio
import importlib

from clientops.boundary import resolve_job
from clientops.claude_call import ModelNotAllowed
from clientops.replay import run_replay
from clientops.repair import RepairHelp
from approval_fakes import FakeDesk, MemoryStore
from test_cost_node import as_agentfield_runs
from test_node_approval import Seams, in_execution
from test_replay import JOB_ID, KUMO_DAY2, REQUEST, Transcript, kumo_procedure, ticking_clock
from test_stock_sync import data_dir, install_sites

__all__ = ["data_dir"]

REFUSED = "This server's Claude setting isn't allowed (ClientOps only uses Claude Opus 5.5), so I stopped. Nothing was changed."


def refuse(*_args, **_kwargs):
    raise ModelNotAllowed("ClientOps only calls claude-opus-5-5, so the model 'gpt-x' is refused.")


def test_a_first_run_with_a_refused_model_stops_before_anything_and_says_why(monkeypatch):
    install_sites(monkeypatch)
    node = importlib.import_module("clientops.node")
    seams = Seams(monkeypatch, node, "approved")
    monkeypatch.setattr(node, "claude_ai", refuse)

    result = in_execution(node, lambda: as_agentfield_runs(node.run_job)(client_id="kumo", client_name="Kumo Coffee Roasters", request=REQUEST))

    assert [event["type"] for event in seams.events] == ["job.created", "job.failed"]
    assert seams.events[-1] == {"type": "job.failed", "reason": "error", "message": REFUSED}
    assert (result["status"], result["reason"]) == ("failed", "model_not_allowed")
    assert seams.desk.paused == []


def test_a_replay_that_needs_claude_with_a_refused_model_stops_at_that_step_and_says_why(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, save_label="Update inventory")
    transcript = Transcript()

    result = asyncio.run(
        run_replay(transcript.notes, resolve_job("kumo"), kumo_procedure(), request=REQUEST, job_id=JOB_ID, clock=ticking_clock(), repair=RepairHelp(None, FakeDesk(), JOB_ID, make_ai=refuse))
    )

    assert transcript.events[-1] == {"type": "job.failed", "reason": "error", "stepId": "check-save", "message": REFUSED}
    assert (result["status"], result["problem"]) == ("failed", "model-not-allowed")
    assert "approval.requested" not in [event["type"] for event in transcript.events]
