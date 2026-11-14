import json
import logging
import stat
import subprocess
from pathlib import Path

import pytest
from daytona_toolbox_api_client import ExecuteResponse as WireExecuteResponse

import clientops.externals as externals
from clientops.boundary import DATA_DIR_VARIABLE, LoginStatus, check_logins, put_logins
from clientops.computer_image import PROJECT_ROOT
from clientops.computers import TIER_VARIABLE
from clientops.externals.fake_daytona import FakeDaytona

SUPPLIER = {"LP_SUPPLIER_USERNAME": "merge-supply-user", "LP_SUPPLIER_PASSWORD": "merge-supply-pass-5Kp!"}
STORE = {"LP_STORE_USERNAME": "merge-store-user", "LP_STORE_PASSWORD": "merge-store-pass-2Vb#"}
NEW_STORE = {"LP_STORE_USERNAME": "merge-store-user-2", "LP_STORE_PASSWORD": "merge-store-pass-8Qz%"}
ALL_NAMES = ("LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD", "LP_STORE_USERNAME", "LP_STORE_PASSWORD")
COMPUTER_HOME = "/home/daytona"
RUNNER_IN_COMPUTER = "/opt/clientops/runner"
RUNNER_SOURCE = PROJECT_ROOT / "sandbox" / "runner"


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


@pytest.fixture(params=["daytona", "local"])
def computer(request, monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path / "clientops-data"))
    monkeypatch.delenv(TIER_VARIABLE, raising=False)
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "fake")
    daytona = DiskDaytona(tmp_path / "computer")
    monkeypatch.setattr(externals, "shared_fake_daytona", lambda: daytona)
    if request.param == "local":
        monkeypatch.setenv("CLIENTOPS_COMPUTERS", "local")
        folder = tmp_path / "clientops-data" / "computers" / "kumo"
    else:
        monkeypatch.delenv("CLIENTOPS_COMPUTERS", raising=False)
        folder = tmp_path / "computer" / "home" / "daytona" / ".clientops"
    return request.param, daytona, folder


def vault_values(folder: Path) -> dict:
    return json.loads((folder / "vault.json").read_text())["values"]


def secrets_in(text: str) -> list[str]:
    return [value for value in (*SUPPLIER.values(), *STORE.values(), *NEW_STORE.values()) if value in text]


def test_adding_one_site_keeps_the_other_sites_login(computer):
    _, _, folder = computer

    put_logins("kumo", SUPPLIER)
    status = put_logins("kumo", STORE)

    assert vault_values(folder) == SUPPLIER | STORE
    assert status == LoginStatus(client_id="kumo", present=ALL_NAMES, missing=(), problem=None)
    assert check_logins("kumo") == status


def test_replacing_one_sites_login_later_changes_only_that_site(computer):
    _, _, folder = computer
    put_logins("kumo", SUPPLIER | STORE)

    status = put_logins("kumo", NEW_STORE)

    assert vault_values(folder) == SUPPLIER | NEW_STORE
    assert status.present == ALL_NAMES


def test_the_merged_vault_is_600_in_a_700_folder_with_nothing_left_behind(computer):
    _, _, folder = computer
    put_logins("kumo", SUPPLIER)
    put_logins("kumo", STORE)

    vault = folder / "vault.json"
    assert stat.S_IMODE(vault.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert sorted(path.name for path in folder.iterdir() if path.is_file()) == ["vault.json"]


def test_a_vault_others_could_read_is_not_carried_over(computer):
    _, _, folder = computer
    put_logins("kumo", SUPPLIER)
    (folder / "vault.json").chmod(0o644)

    status = put_logins("kumo", STORE)

    assert vault_values(folder) == STORE
    assert stat.S_IMODE((folder / "vault.json").stat().st_mode) == 0o600
    assert status.present == ("LP_STORE_USERNAME", "LP_STORE_PASSWORD")


def test_a_broken_vault_is_replaced_by_the_new_login(computer):
    _, _, folder = computer
    put_logins("kumo", SUPPLIER)
    (folder / "vault.json").write_text("{not json")

    put_logins("kumo", STORE)

    assert vault_values(folder) == STORE


def test_merging_never_puts_a_value_in_a_command_a_log_or_an_answer(computer, caplog, capsys):
    mode, daytona, _ = computer
    caplog.set_level(logging.DEBUG)

    first = put_logins("kumo", SUPPLIER)
    second = put_logins("kumo", NEW_STORE)

    captured = capsys.readouterr()
    commands = json.dumps([call.arguments.get("command") for call in daytona.calls if call.method == "exec"])
    shown = "".join([caplog.text, captured.out, captured.err, commands, repr(first), repr(second), str(check_logins("kumo"))])
    assert secrets_in(shown) == []
    if mode == "local":
        assert daytona.calls == []
