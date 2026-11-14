import asyncio

from clientops.boundary import resolve_job
from clientops.replay import run_replay
from clientops.repair import RepairHelp
from approval_fakes import FakeDesk
from picking_claude import PickingClaude
from test_replay import JOB_ID, KUMO_DAY2, REQUEST, Transcript, kumo_procedure
from test_replay_repair import DECOY, RENAMED
from test_stock_sync import data_dir, install_sites

__all__ = ["data_dir"]


def test_the_time_to_get_the_changes_ready_does_not_count_the_wait_for_your_pick(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, save_label=RENAMED, extra_store_buttons=(DECOY,))
    now = [1000.0]

    def clock():
        now[0] += 0.01
        return now[0]

    class SlowPick(FakeDesk):
        async def pause(self, approval_request_id, **kwargs):
            now[0] += 30.0
            return await super().pause(approval_request_id, **kwargs)

    transcript = Transcript()
    result = asyncio.run(
        run_replay(transcript.notes, resolve_job("kumo"), kumo_procedure(), request=REQUEST, job_id=JOB_ID, clock=clock, repair=RepairHelp(PickingClaude(), SlowPick(("approved", "1016")), JOB_ID))
    )

    assert result["status"] == "waiting"
    assert result["readyInMs"] < 5_000
