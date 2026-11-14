import io
import json
import logging
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from daytona_toolbox_api_client import ExecuteResponse as WireExecuteResponse

import clientops.externals as externals
import clientops.setup_logins as setup_logins
from clientops.boundary import DATA_DIR_VARIABLE
from clientops.computer_image import PROJECT_ROOT
from clientops.computers import TIER_VARIABLE
from clientops.externals.fake_daytona import FakeDaytona
from clientops.setup_logins import main

CONTRACT = json.loads((Path(__file__).parent / "contract" / "setup.json").read_text())["logins"]
VALUES = CONTRACT["stdin"]
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


class LeakyUploadDaytona(FakeDaytona):
    def _record(self, method, sandbox_id, **arguments):
        if method == "upload_file":
            raise RuntimeError(f"upload failed for {arguments['content']!r}")
        super()._record(method, sandbox_id, **arguments)


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


def run(arguments: list[str], stdin_text: str) -> tuple[int, dict, str]:
    stdout = io.StringIO()
    code = main(arguments, io.StringIO(stdin_text), stdout)
    printed = stdout.getvalue()
    return code, json.loads(printed.strip().splitlines()[-1]), printed


def leaked(text: str, values=VALUES) -> list[str]:
    return [value for value in values.values() if isinstance(value, str) and value and value in text]


def test_logins_on_stdin_go_into_the_vault_and_only_their_names_come_out(disk, tmp_path):
    code, answer, printed = run([CONTRACT["clientId"]], json.dumps(VALUES))

    assert code == 0
    assert answer == CONTRACT["added"]
    assert printed.count("\n") == 1
    assert leaked(printed) == []
    vault = tmp_path / "computer" / "home" / "daytona" / ".clientops" / "vault.json"
    assert json.loads(vault.read_text()) == {"version": 1, "values": VALUES}
    assert stat.S_IMODE(vault.stat().st_mode) == 0o600


def test_the_values_reach_no_log_no_other_stream_and_no_command(disk, caplog, capsys):
    caplog.set_level(logging.DEBUG)

    code, _, printed = run(["kumo"], json.dumps(VALUES))

    captured = capsys.readouterr()
    commands = json.dumps([call.arguments.get("command") for call in disk.calls if call.method == "exec"])
    environments = json.dumps([call.arguments.get("env") for call in disk.calls if call.method == "exec"])
    assert code == 0
    assert leaked(printed + caplog.text + captured.out + captured.err + commands + environments) == []


@pytest.mark.parametrize(
    "stdin_text",
    ["", "{not json", "[]", json.dumps(["LP_STORE_PASSWORD", "secret-list-value-1"]), json.dumps("secret-text-value-2")],
)
def test_input_that_is_not_one_json_object_is_refused_without_echoing_it(disk, stdin_text):
    code, answer, printed = run(["kumo"], stdin_text)

    assert code == 2
    assert answer["ok"] is False
    assert "secret" not in printed
    assert [call for call in disk.calls if call.method in ("create", "upload_file", "exec")] == []


def test_input_larger_than_any_set_of_logins_is_refused(disk):
    code, answer, _ = run(["kumo"], json.dumps({"LP_STORE_PASSWORD": "x" * 70_000}))
    assert code == 2
    assert answer["ok"] is False
    assert disk.calls == []


@pytest.mark.parametrize("arguments", [[], ["kumo", "hinata"]])
def test_exactly_one_client_id_is_required(disk, arguments):
    code, answer, _ = run(arguments, json.dumps(VALUES))
    assert code == 2
    assert answer["ok"] is False
    assert disk.calls == []


def test_a_wrong_login_name_is_refused_by_name_and_its_value_is_never_shown(disk):
    sent = {"LP_OTHER_PASSWORD": "secret-other-value-3"}

    code, answer, printed = run(["kumo"], json.dumps(sent))

    assert code == 1
    assert answer["ok"] is False
    assert "LP_OTHER_PASSWORD" in answer["error"]
    assert leaked(printed, sent) == []
    assert [call for call in disk.calls if call.method == "upload_file"] == []


def test_an_unknown_client_is_refused_as_the_contract_says(disk):
    code, answer, _ = run(["acme"], json.dumps(VALUES))
    assert code == 1
    assert answer == CONTRACT["refused"]
    assert disk.calls == []


def test_sora_is_refused_before_any_computer_is_touched(disk):
    code, answer, printed = run(["sora"], json.dumps(VALUES))
    assert code == 1
    assert "Sora Outdoor" in answer["error"]
    assert leaked(printed) == []
    assert disk.calls == []


def test_a_failed_upload_becomes_a_plain_error_without_the_values(use_daytona, capsys):
    use_daytona(LeakyUploadDaytona())

    code, answer, printed = run(["kumo"], json.dumps(VALUES))

    captured = capsys.readouterr()
    assert code == 1
    assert answer["ok"] is False
    assert leaked(printed + captured.out + captured.err) == []


def test_an_unexpected_crash_says_nothing_about_the_input(disk, monkeypatch, capsys):
    def crash(client_id, values):
        raise RuntimeError(f"crashed while holding {values!r}")

    monkeypatch.setattr(setup_logins, "put_logins", crash)

    code, answer, printed = run(["kumo"], json.dumps(VALUES))

    captured = capsys.readouterr()
    assert code == 1
    assert answer["ok"] is False
    assert leaked(printed + captured.out + captured.err + answer["error"]) == []


def test_the_module_runs_as_a_program_reading_stdin(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "clientops.setup_logins", "acme"],
        input=json.dumps(VALUES),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PROJECT_ROOT / "agent",
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), DATA_DIR_VARIABLE: str(tmp_path / "data")},
    )

    assert done.returncode == 1
    assert json.loads(done.stdout.strip().splitlines()[-1]) == CONTRACT["refused"]
    assert leaked(done.stdout + done.stderr) == []


@pytest.fixture
def local(monkeypatch, use_daytona, tmp_path):
    monkeypatch.setenv("CLIENTOPS_COMPUTERS", "local")
    daytona = use_daytona(FakeDaytona())
    return daytona, tmp_path / "clientops-data" / "computers"


def test_on_this_mac_the_logins_go_into_the_clients_local_vault_and_only_names_come_out(local, caplog, capsys):
    daytona, computers = local
    caplog.set_level(logging.DEBUG)

    code, answer, printed = run([CONTRACT["clientId"]], json.dumps(VALUES))

    captured = capsys.readouterr()
    vault = computers / "kumo" / "vault.json"
    assert code == 0
    assert answer == CONTRACT["added"]
    assert json.loads(vault.read_text()) == {"version": 1, "values": VALUES}
    assert stat.S_IMODE(vault.stat().st_mode) == 0o600
    assert stat.S_IMODE(vault.parent.stat().st_mode) == 0o700
    assert sorted(path.name for path in vault.parent.iterdir() if path.is_file()) == ["vault.json"]
    assert leaked(printed + caplog.text + captured.out + captured.err) == []
    assert daytona.calls == []


def test_on_this_mac_one_site_at_a_time_keeps_the_other_site(local):
    _, computers = local
    supplier_only = {name: VALUES[name] for name in ("LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD")}
    store_only = {name: VALUES[name] for name in ("LP_STORE_USERNAME", "LP_STORE_PASSWORD")}

    first = run(["kumo"], json.dumps(supplier_only))
    code, answer, _ = run(["kumo"], json.dumps(store_only))

    assert first[1] == {"ok": True, "present": ["LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD"], "missing": ["LP_STORE_USERNAME", "LP_STORE_PASSWORD"]}
    assert code == 0
    assert answer == CONTRACT["added"]
    assert json.loads((computers / "kumo" / "vault.json").read_text())["values"] == VALUES


def test_in_the_cloud_one_site_at_a_time_keeps_the_other_site(disk, tmp_path):
    supplier_only = {name: VALUES[name] for name in ("LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD")}
    store_only = {name: VALUES[name] for name in ("LP_STORE_USERNAME", "LP_STORE_PASSWORD")}

    run(["kumo"], json.dumps(supplier_only))
    code, answer, printed = run(["kumo"], json.dumps(store_only))

    vault = tmp_path / "computer" / "home" / "daytona" / ".clientops" / "vault.json"
    assert code == 0
    assert answer == CONTRACT["added"]
    assert json.loads(vault.read_text())["values"] == VALUES
    assert leaked(printed) == []


def test_on_this_mac_each_client_has_its_own_vault(local):
    _, computers = local
    run(["kumo"], json.dumps(VALUES))
    assert not (computers / "hinata" / "vault.json").exists()


def test_on_this_mac_sora_and_unknown_clients_get_no_folder(local):
    _, computers = local
    assert run(["sora"], json.dumps(VALUES))[0] == 1
    assert run(["acme"], json.dumps(VALUES))[1] == CONTRACT["refused"]
    assert not computers.exists()


def test_an_unknown_computer_switch_is_refused_without_writing_anything(monkeypatch, disk, tmp_path):
    monkeypatch.setenv("CLIENTOPS_COMPUTERS", "laptop")

    code, answer, printed = run(["kumo"], json.dumps(VALUES))

    assert code == 1
    assert answer["ok"] is False
    assert "CLIENTOPS_COMPUTERS" in answer["error"]
    assert leaked(printed) == []
    assert disk.calls == []
    assert not (tmp_path / "clientops-data" / "computers").exists()
