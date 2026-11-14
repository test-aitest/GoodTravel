import importlib
import json
import stat
import subprocess
from pathlib import Path

import pytest
from daytona import DaytonaAuthenticationError, SandboxState
from daytona_toolbox_api_client import ExecuteResponse as WireExecuteResponse

import clientops.externals as externals
from clientops.boundary import DATA_DIR_VARIABLE, STORE_LOGINS, SUPPLIER_LOGINS, UnknownClient, put_logins, resolve_job
from clientops.computer_image import PROJECT_ROOT
from clientops.computers import CLIENT_LABEL, TIER_VARIABLE
from clientops.externals.fake_daytona import FakeDaytona
from clientops.setup_skills import prepare_computer, setup_status

CONTRACT = json.loads((Path(__file__).parent / "contract" / "setup.json").read_text())
ANSWERS = CONTRACT["answers"]
VALUES = CONTRACT["logins"]["stdin"]
COMPUTER_HOME = "/home/daytona"
RUNNER_IN_COMPUTER = "/opt/clientops/runner"
RUNNER_SOURCE = PROJECT_ROOT / "sandbox" / "runner"
TOUCHING = ("create", "start", "exec", "upload_file", "stop")


class DiskDaytona(FakeDaytona):
    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def local(self, text: str) -> str:
        return text.replace(RUNNER_IN_COMPUTER, str(RUNNER_SOURCE)).replace(COMPUTER_HOME, str(self.root / "home" / "daytona"))

    def _response_to(self, command: str) -> WireExecuteResponse:
        done = subprocess.run(["sh", "-c", self.local(command)], capture_output=True, text=True, timeout=60)
        return WireExecuteResponse.from_dict({"exitCode": done.returncode, "result": done.stdout + done.stderr})

    def _record(self, method: str, sandbox_id: str | None, **arguments) -> None:
        super()._record(method, sandbox_id, **arguments)
        if method == "upload_file":
            Path(self.local(arguments["dst"])).write_bytes(arguments["content"])


class RefusingDaytona(FakeDaytona):
    def create(self, params=None, *, timeout=60, on_snapshot_create_logs=None):
        raise DaytonaAuthenticationError("Failed to create sandbox: Unauthorized", status_code=401)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path / "clientops-data"))
    monkeypatch.delenv(TIER_VARIABLE, raising=False)
    monkeypatch.delenv("CLIENTOPS_COMPUTERS", raising=False)
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "fake")


@pytest.fixture
def use_daytona(monkeypatch):
    def install(daytona: FakeDaytona) -> FakeDaytona:
        monkeypatch.setattr(externals, "shared_fake_daytona", lambda: daytona)
        return daytona

    return install


@pytest.fixture
def disk(use_daytona, tmp_path):
    return use_daytona(DiskDaytona(tmp_path / "computer"))


def touched(daytona: FakeDaytona) -> list[str]:
    return [call.method for call in daytona.calls if call.method in TOUCHING]


def test_the_login_names_the_screen_asks_for_are_the_vaults():
    names = CONTRACT["loginNames"]
    assert (names["supplier"]["username"], names["supplier"]["password"]) == SUPPLIER_LOGINS
    assert (names["store"]["username"], names["store"]["password"]) == STORE_LOGINS


def test_the_setup_skills_are_registered_on_the_clientops_node():
    node = importlib.import_module("clientops.node")
    registered = {skill["id"] for skill in node.app.skills}
    assert set(CONTRACT["skills"]) <= registered


def test_status_of_a_client_without_a_computer_creates_and_starts_nothing(disk):
    assert setup_status("kumo") == ANSWERS["noComputer"]
    assert touched(disk) == []


def test_preparing_creates_the_clients_own_computer_once_and_reports_no_logins_yet(disk):
    assert prepare_computer("kumo") == ANSWERS["computerWithoutLogins"]
    assert prepare_computer("kumo") == ANSWERS["computerWithoutLogins"]

    creates = [call for call in disk.calls if call.method == "create"]
    assert len(creates) == 1
    assert creates[0].arguments["params"].labels[CLIENT_LABEL] == "kumo"
    assert setup_status("kumo") == ANSWERS["computerWithoutLogins"]


def test_preparing_one_client_leaves_the_other_without_a_computer(disk):
    prepare_computer("kumo")
    assert setup_status("hinata")["computer"] == "missing"


def test_after_the_logins_are_put_the_status_names_them_and_never_shows_a_value(disk):
    prepare_computer("kumo")
    put_logins("kumo", VALUES)

    answer = setup_status("kumo")

    assert answer == ANSWERS["computerWithLogins"]
    assert not [value for value in VALUES.values() if value in json.dumps(answer)]


def test_preparing_a_computer_that_already_holds_logins_keeps_them(disk):
    prepare_computer("kumo")
    put_logins("kumo", VALUES)
    assert prepare_computer("kumo") == ANSWERS["computerWithLogins"]


def test_status_of_a_stopped_computer_does_not_wake_it_and_does_not_know_the_logins(disk):
    prepare_computer("kumo")
    put_logins("kumo", VALUES)
    resolve_job("kumo").computer().stop()
    before = len(disk.calls)

    assert setup_status("kumo") == ANSWERS["computerAsleep"]
    assert [call.method for call in disk.calls[before:] if call.method in TOUCHING] == []


def test_a_recheck_may_wake_a_stopped_computer_to_read_the_names(disk):
    prepare_computer("kumo")
    put_logins("kumo", VALUES)
    resolve_job("kumo").computer().stop()

    assert setup_status("kumo", wake=True) == ANSWERS["computerWithLogins"]
    assert "start" in touched(disk)


def test_a_running_computer_is_read_without_waking_anything(disk):
    prepare_computer("kumo")
    put_logins("kumo", VALUES)
    before = len(disk.calls)

    assert setup_status("kumo") == ANSWERS["computerWithLogins"]
    assert [call.method for call in disk.calls[before:] if call.method in ("create", "start")] == []


def test_sora_cannot_have_a_computer_and_nothing_is_touched(disk):
    assert setup_status("sora") == ANSWERS["noSites"]
    assert prepare_computer("sora") == ANSWERS["noSites"]
    assert disk.calls == []


@pytest.mark.parametrize("skill", [setup_status, prepare_computer])
def test_an_unknown_client_is_an_error_before_daytona(disk, skill):
    with pytest.raises(UnknownClient):
        skill("acme")
    assert disk.calls == []


def test_daytona_refusing_the_key_is_reported_by_its_problem_instead_of_raised(use_daytona):
    use_daytona(RefusingDaytona())
    assert prepare_computer("kumo") == ANSWERS["daytonaRefusedTheKey"]


def test_a_broken_computer_is_reported_as_failed_without_starting_it(disk):
    prepare_computer("kumo")
    [sandbox_id] = {call.sandbox_id for call in disk.calls if call.method == "create"}
    disk._sandboxes[sandbox_id]["state"] = "error"
    before = len(disk.calls)

    assert setup_status("kumo") == {"clientId": "kumo", "computer": "failed", "problem": "broken"}
    assert [call.method for call in disk.calls[before:] if call.method in TOUCHING] == []


@pytest.fixture
def local(monkeypatch, use_daytona, tmp_path):
    monkeypatch.setenv("CLIENTOPS_COMPUTERS", "local")
    daytona = use_daytona(FakeDaytona())
    return daytona, tmp_path / "clientops-data" / "computers"


def test_on_this_mac_preparing_makes_the_clients_private_folder_and_daytona_is_never_used(local):
    daytona, computers = local
    assert setup_status("kumo") == ANSWERS["noComputer"]
    assert not computers.exists()

    assert prepare_computer("kumo") == ANSWERS["computerWithoutLogins"]

    assert stat.S_IMODE((computers / "kumo").stat().st_mode) == 0o700
    assert setup_status("kumo") == ANSWERS["computerWithoutLogins"]
    assert setup_status("hinata") == ANSWERS["noComputer"] | {"clientId": "hinata"}
    assert daytona.calls == []


def test_on_this_mac_the_status_names_the_logins_put_there(local):
    prepare_computer("kumo")
    put_logins("kumo", VALUES)
    answer = setup_status("kumo")
    assert answer == ANSWERS["computerWithLogins"]
    assert not [value for value in VALUES.values() if value in json.dumps(answer)]


def test_on_this_mac_sora_still_cannot_have_a_computer(local):
    daytona, computers = local
    assert prepare_computer("sora") == ANSWERS["noSites"]
    assert not computers.exists()
    assert daytona.calls == []


def test_ending_a_job_stops_only_that_clients_computer(disk):
    prepare_computer("kumo")
    prepare_computer("hinata")

    assert resolve_job("kumo").stop_computer() is True
    assert resolve_job("kumo").computer_state() is SandboxState.STOPPED
    assert resolve_job("hinata").computer_state() is SandboxState.STARTED
    assert resolve_job("kumo").stop_computer() is False


def test_ending_a_job_on_the_local_computer_leaves_the_mac_alone(disk, monkeypatch):
    monkeypatch.setenv("CLIENTOPS_COMPUTERS", "local")
    before = len(disk.calls)

    assert resolve_job("kumo").stop_computer() is False
    assert disk.calls[before:] == []
