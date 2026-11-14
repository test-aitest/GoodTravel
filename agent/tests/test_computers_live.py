import os

import pytest
from daytona import SandboxState

from clientops.computer_image import ensure_snapshot
from clientops.computers import CLIENT_LABEL, Computers, NetworkMode
from clientops.externals import daytona_client, externals_mode

LIVE_VARIABLE = "CLIENTOPS_LIVE_DAYTONA"
START_TARGET_SECONDS = 60
STORE_LOGIN = "http://127.0.0.1:3002/login"


def missing_for_live() -> str | None:
    if os.environ.get(LIVE_VARIABLE) != "1":
        return f"set {LIVE_VARIABLE}=1 to make real Daytona computers (it costs money and takes minutes)"
    if externals_mode() == "fake":
        return "CLIENTOPS_EXTERNALS=fake is set"
    if not os.environ.get("DAYTONA_API_KEY"):
        return "DAYTONA_API_KEY is not set"
    return None


@pytest.mark.skipif(missing_for_live() is not None, reason=str(missing_for_live()))
def test_two_clients_get_their_own_running_computer_with_lightpanda_reading_the_sites():
    daytona = daytona_client()
    ensure_snapshot(daytona, on_logs=print)
    computers = Computers(daytona)

    opened = {client: computers.open(client) for client in ("kumo", "hinata")}
    try:
        for client, computer in opened.items():
            print(f"{client}: {computer.id} created={computer.created} startup={computer.startup_seconds:.1f}s")
            assert computer.startup_seconds <= START_TARGET_SECONDS
            assert computer.sandbox.labels[CLIENT_LABEL] == client
            assert computer.sandbox.state is SandboxState.STARTED

            computer.start_sites()
            browser = computer.start_browser()
            assert browser.version == "1.0.4"

            tree = computer.run(f"lightpanda fetch --dump semantic_tree_text {STORE_LOGIN}", timeout_seconds=30)
            assert tree.exit_code == 0, tree.output
            assert "Store Admin" in tree.output

            outside = computer.run("curl -fsS --max-time 10 -o /dev/null https://example.com", timeout_seconds=20)
            assert outside.exit_code != 0, f"{client}'s computer reached example.com ({computer.network.mode})"
            if computer.network.mode is NetworkMode.PER_COMPUTER and computer.network.sandbox_allow_list:
                assert computer.sandbox.domain_allow_list == computer.network.domain_allow_list

        assert opened["kumo"].id != opened["hinata"].id
        assert computers.open("kumo").id == opened["kumo"].id
    finally:
        for computer in opened.values():
            computer.stop()

    for client in opened:
        assert computers.stop(client) is False
