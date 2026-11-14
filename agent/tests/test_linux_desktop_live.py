import os
import shlex
import time

import pytest
from daytona import CreateSandboxFromSnapshotParams

from clientops.elements import ElementError
from clientops.externals import daytona_client, externals_mode
from clientops.linux_desktop import LinuxDesktop
from clientops.procedure import Hint

LIVE_VARIABLE = "CLIENTOPS_LIVE_DAYTONA"
CHECK_LABEL = {"clientops-check": "desktop"}
DIALOG = "Kumo inventory"
FIELD = "Available for Ethiopia Guji 200g"
OUTPUT = "/tmp/clientops-desktop-check.out"
APPEAR_SECONDS = 20


def missing_for_live() -> str | None:
    if os.environ.get(LIVE_VARIABLE) != "1":
        return f"set {LIVE_VARIABLE}=1 to open a real Daytona desktop (it costs money and takes minutes)"
    if externals_mode() == "fake":
        return "CLIENTOPS_EXTERNALS=fake is set"
    if not os.environ.get("DAYTONA_API_KEY"):
        return "DAYTONA_API_KEY is not set"
    return None


def wait_for(read, seconds: float):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return read()
        except (ElementError, AssertionError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.mark.skipif(missing_for_live() is not None, reason=str(missing_for_live()))
def test_one_press_in_a_linux_desktop_app_with_the_same_hints():
    daytona = daytona_client()
    sandbox = daytona.create(CreateSandboxFromSnapshotParams(labels=CHECK_LABEL, auto_stop_interval=15), timeout=120)
    try:
        sandbox.computer_use.start()
        form = shlex.join(["zenity", "--forms", f"--title={DIALOG}", f"--add-entry={FIELD}"])
        launched = sandbox.process.exec(f"DISPLAY=:0 nohup {form} > {OUTPUT} 2>&1 < /dev/null & echo $!", timeout=10)
        pid = int(launched.result.strip().splitlines()[-1])
        desktop = LinuxDesktop(sandbox.computer_use.accessibility, client_id="desktop-check", pid=pid)

        wait_for(lambda: desktop.find(Hint("button", "OK", DIALOG)), APPEAR_SECONDS)
        desktop.set_value(Hint("textbox", FIELD, DIALOG), "6")
        desktop.press(Hint("button", "OK", DIALOG))

        def saved() -> str:
            output = sandbox.process.exec(f"cat {OUTPUT}", timeout=10).result
            assert output.strip() == "6", output
            return output

        wait_for(saved, 10)
    finally:
        sandbox.delete()
