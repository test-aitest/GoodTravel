import json
import stat
from pathlib import Path

import pytest

from clientops.computer_protocol import CommandResult, Computer
from clientops.computers import ComputerError, ComputerProblem
from clientops.local_computer import LOCAL_RUNNER_COMMAND, PROJECT_ROOT, LocalComputer


@pytest.fixture
def vault(tmp_path) -> Path:
    path = tmp_path / "vault.json"
    path.write_text(json.dumps({"version": 1, "values": {"LP_STORE_USERNAME": "demo-user"}}))
    path.chmod(0o600)
    return path


@pytest.fixture
def computer(tmp_path, vault) -> LocalComputer:
    return LocalComputer("kumo", vault_path=vault, workspace=tmp_path / "kumo")


def test_it_is_a_computer_for_one_client(computer):
    assert isinstance(computer, Computer)
    assert computer.client_id == "kumo"
    assert computer.kind == "local"


def test_the_local_runner_command_points_at_the_project_runner():
    assert LOCAL_RUNNER_COMMAND == f"bun {PROJECT_ROOT / 'sandbox' / 'runner' / 'main.ts'}"


def test_a_command_runs_in_the_workspace_and_returns_exit_code_and_output(computer, tmp_path):
    result = computer.run("pwd; echo to-stderr >&2; exit 3", timeout_seconds=10)
    assert result == CommandResult(exit_code=3, output=f"{(tmp_path / 'kumo').resolve()}\nto-stderr\n")


def test_the_runner_sees_its_vault_and_its_own_socket_but_none_of_this_shells_secrets(computer, vault, monkeypatch):
    monkeypatch.setenv("LP_STORE_PASSWORD", "from-the-developers-shell")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-for-the-computer")
    output = computer.run("env", timeout_seconds=10).output
    seen = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    assert seen["CLIENTOPS_VAULT"] == str(vault)
    assert seen["CLIENTOPS_RUNNER_SOCKET"].endswith(".sock")
    assert len(seen["CLIENTOPS_RUNNER_SOCKET"]) < 100
    assert "LP_STORE_PASSWORD" not in seen
    assert "ANTHROPIC_API_KEY" not in seen
    assert "from-the-developers-shell" not in output


def test_two_clients_never_share_a_runner(tmp_path, vault):
    kumo = LocalComputer("kumo", vault_path=vault, workspace=tmp_path / "kumo")
    hinata = LocalComputer("hinata", vault_path=vault, workspace=tmp_path / "hinata")
    sockets = {computer.run("echo $CLIENTOPS_RUNNER_SOCKET", timeout_seconds=10).output for computer in (kumo, hinata)}
    assert len(sockets) == 2


def test_extra_settings_reach_the_command_but_secrets_are_refused_like_on_daytona(computer):
    assert computer.run("echo $GREETING", timeout_seconds=10, env={"GREETING": "hello"}).output == "hello\n"
    with pytest.raises(ComputerError) as caught:
        computer.run("true", timeout_seconds=10, env={"LP_STORE_PASSWORD": "x"})
    assert caught.value.problem is ComputerProblem.SECRET_IN_ENV


def test_a_command_that_runs_too_long_is_a_timed_out_error(computer):
    with pytest.raises(ComputerError) as caught:
        computer.run("sleep 5", timeout_seconds=1)
    assert caught.value.problem is ComputerProblem.TIMED_OUT
    assert "1 seconds" in str(caught.value)


@pytest.mark.parametrize("timeout", [0, -1, 1.5, True])
def test_the_timeout_must_be_whole_seconds_above_zero(computer, timeout):
    with pytest.raises(ValueError):
        computer.run("true", timeout_seconds=timeout)


def test_upload_puts_the_file_under_the_workspace_at_the_remote_path(computer, tmp_path):
    computer.upload(b"console.log(1)", "/home/daytona/jobs/job.js")
    written = tmp_path / "kumo" / "files" / "home" / "daytona" / "jobs" / "job.js"
    assert written.read_bytes() == b"console.log(1)"
    assert stat.S_IMODE(written.stat().st_mode) & 0o077 == 0


def test_upload_refuses_paths_that_leave_the_workspace_and_text_instead_of_bytes(computer):
    with pytest.raises(ValueError):
        computer.upload(b"x", "/home/daytona/../../../etc/passwd")
    with pytest.raises(TypeError):
        computer.upload("x", "/home/daytona/a.txt")


def test_it_is_only_made_on_purpose_with_a_vault_and_a_workspace():
    with pytest.raises(TypeError):
        LocalComputer("kumo")


def test_the_runner_runs_saved_scripts_with_the_lightpanda_in_this_project(computer):
    output = computer.run("echo $CLIENTOPS_LIGHTPANDA", timeout_seconds=10).output.strip()
    assert output == str(PROJECT_ROOT / "vendor" / "lightpanda" / "lightpanda")
