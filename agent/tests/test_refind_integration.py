import re

import pytest

import clientops.replay as replay
from clientops.procedure import PROCEDURE_KEY, Hint, Procedure
from clientops.refind import NotFound
from test_first_run_integration import HOW_TO_START, STORE, SUPPLIER, control, local_computers, logins, missing, runner_sockets
from test_replay_integration import KUMO_DAY2, Seams, day

__all__ = ["local_computers", "logins"]

pytestmark = pytest.mark.skipif(missing() is not None, reason=f"{missing()}: {HOW_TO_START}")

DOESNT_MATCH = "The store admin doesn't match the saved steps any more, so I stopped. Nothing was changed."
FIRST_RUN_SELECTORS = {
    "user": 'form[action="/login"] input[name="username"]',
    "password": 'form[action="/login"] input[name="password"][type="password"]',
    "save": 'form[action="/inventory"] button[name="action"]',
}
RESTYLED_NAME = re.compile(r'\[name="f[0-9a-z]{6}"\]')


def restyle() -> None:
    for site in (SUPPLIER, STORE):
        control(site, "/__control/switches", {"restyle": True})


def decided(seams: Seams) -> list[tuple[str, str, list[str] | None]]:
    return [(event["stepId"], event["rung"], event.get("escalatedFrom")) for event in seams.events if event["type"] == "step.decided"]


def test_a_restyled_store_breaks_the_saved_script_the_tree_finds_each_element_by_name_and_version_2_is_saved_after_the_ok(monkeypatch, logins):
    sockets_before = runner_sockets()
    seams = Seams(monkeypatch, "approved", "approved")
    seams.run_job()
    version_1 = Procedure.from_json(seams.memory.values[PROCEDURE_KEY])
    assert (version_1.read.store.user.css, version_1.read.store.password.css, version_1.read.save_button.css) == tuple(FIRST_RUN_SELECTORS.values())

    day(2)
    restyle()
    refound = seams.run_job()

    events = seams.events
    assert decided(seams) == [
        ("sign-in", "script", None),
        ("read", "script", None),
        ("compare", "tree", ["script"]),
        ("check-save", "tree", ["script"]),
        ("ask", "human", None),
        ("save", "script", None),
    ]
    [approval] = [event for event in events if event["type"] == "approval.requested"]
    assert approval["changes"] == KUMO_DAY2["changes"]
    assert [event for event in events if event["type"] == "verify.done"] == [{"type": "verify.done", "stepId": "save", "checked": 4, "matched": 4}]
    assert events[-1] == {"type": "procedure.saved", "procedureId": "kumo-stock-sync", "version": 2, "stepCount": 6, "expectedReplayMs": events[-1]["expectedReplayMs"]}
    assert refound["status"] == "done" and refound["claudeCalls"] == 0
    assert seams.claude.calls == 8
    version_2 = Procedure.from_json(seams.memory.values[PROCEDURE_KEY])
    assert version_2.version == 2
    for css in (version_2.read.store.user.css, version_2.read.store.password.css, version_2.read.save_button.css):
        assert RESTYLED_NAME.search(css), css
    assert version_2.read.save_button.text == "Save changes"
    assert version_2.read.store.submit == version_1.read.store.submit
    assert version_2.read.supplier == version_1.read.supplier
    climbed = {event["stepId"]: event["tookMs"] for event in events if event["type"] == "step.decided" and event["rung"] == "tree"}
    ready_ms = seams.ready_in_ms()

    again = seams.run_job()

    assert again["status"] == "done" and again["changes"] == 0
    assert seams.events[0]["run"] == {"kind": "replay", "procedureVersion": 2}
    assert {rung for _, rung, _ in decided(seams)} == {"script"}
    everything = "\n".join(message for _, message in seams.transcript)
    for value in logins.values():
        assert value not in everything
    assert not re.search(r"\d", seams.claude.everything_sent())
    assert runner_sockets() - sockets_before == set()
    print(f"\nfound again by the tree: compare {climbed['compare']} ms, check-save {climbed['check-save']} ms; ready for approval in {ready_ms} ms (readyInMs {refound['readyInMs']} ms)")


def test_a_renamed_save_button_is_not_found_by_the_tree_and_is_handed_to_the_hook_for_claude(monkeypatch, logins):
    seams = Seams(monkeypatch, "approved")
    seams.run_job()
    day(2)
    control(STORE, "/__control/switches", {"renameButton": True})
    handed: list[NotFound] = []

    async def repair(failed, not_found):
        handed.append(not_found)
        return None

    monkeypatch.setattr(replay, "repair_failed_step", repair)

    result = seams.run_job()

    [not_found] = handed
    assert (not_found.step_id, not_found.element, not_found.hint, not_found.count) == ("check-save", "save", Hint("button", "Save changes", "/inventory"), 0)
    assert seams.events[-1] == {"type": "job.failed", "reason": "error", "stepId": "check-save", "message": DOESNT_MATCH}
    assert result["problem"] == "wrong-name"
    assert "approval.requested" not in {event["type"] for event in seams.events}
    assert Procedure.from_json(seams.memory.values[PROCEDURE_KEY]).version == 1
