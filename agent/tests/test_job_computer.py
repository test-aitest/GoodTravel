from pathlib import Path

import pytest

import clientops.boundary as boundary_module
import clientops.externals as externals
from clientops.boundary import (
    COMPUTERS_VARIABLE,
    DATA_DIR_VARIABLE,
    ComputerKind,
    computer_kind,
    local_computer_home,
    resolve_job,
)
from clientops.computers import TIER_VARIABLE, DaytonaComputer
from clientops.externals import MissingExternalKey
from clientops.local_computer import LOCAL_RUNNER_COMMAND, LocalComputer
from clientops.page import RUNNER_COMMAND, Page
from fake_sites import FakeSites, RunnerDaytona

KUMO_DAY = {"supplier": [], "store": []}


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    directory = tmp_path / "clientops-data"
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(directory))
    monkeypatch.delenv(COMPUTERS_VARIABLE, raising=False)
    monkeypatch.delenv(TIER_VARIABLE, raising=False)
    return directory


def test_the_sites_of_a_client_say_where_they_answer_inside_its_computer():
    kumo = resolve_job("kumo")
    assert [(site.host, site.url, site.sign_in_url) for site in kumo.sites] == [
        ("portal.maruyama-supply.example", "http://127.0.0.1:3001", "http://127.0.0.1:3001/login"),
        ("admin.kumo-coffee.example", "http://127.0.0.1:3002", "http://127.0.0.1:3002/login"),
    ]
    assert kumo.supplier.host == "portal.maruyama-supply.example"
    assert kumo.store.host == "admin.kumo-coffee.example"
    assert resolve_job("hinata").store.host == "admin.hinata-skincare.example"


def test_daytona_is_the_computer_unless_local_is_asked_for(monkeypatch):
    assert computer_kind() is ComputerKind.DAYTONA
    monkeypatch.setenv(COMPUTERS_VARIABLE, "daytona")
    assert computer_kind() is ComputerKind.DAYTONA
    monkeypatch.setenv(COMPUTERS_VARIABLE, "local")
    assert computer_kind() is ComputerKind.LOCAL


@pytest.mark.parametrize("value", ["Local", "fake", "lightpanda", "docker", " local"])
def test_an_unknown_computer_kind_is_an_error_not_a_quiet_fallback(monkeypatch, value):
    monkeypatch.setenv(COMPUTERS_VARIABLE, value)
    with pytest.raises(ValueError) as caught:
        computer_kind()
    assert COMPUTERS_VARIABLE in str(caught.value)


def test_a_local_computer_keeps_its_vault_and_files_in_the_data_directory_per_client(data_dir):
    assert local_computer_home("kumo") == data_dir.resolve() / "computers" / "kumo"
    assert local_computer_home("hinata") != local_computer_home("kumo")


def test_with_local_computers_the_job_gets_the_local_runner_and_never_touches_daytona(monkeypatch, data_dir):
    monkeypatch.setenv(COMPUTERS_VARIABLE, "local")
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "real")
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)

    opened = resolve_job("kumo").job_computer()

    assert isinstance(opened.computer, LocalComputer)
    assert opened.computer.client_id == "kumo"
    assert opened.computer.vault_path == local_computer_home("kumo") / "vault.json"
    assert opened.computer.workspace == local_computer_home("kumo") / "workspace"
    assert opened.runner_command == LOCAL_RUNNER_COMMAND
    assert isinstance(opened.page(), Page)


def test_by_default_the_job_gets_its_daytona_computer_with_browser_and_sites_started(monkeypatch):
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "fake")
    daytona = RunnerDaytona(FakeSites(KUMO_DAY, {}, {}))
    monkeypatch.setattr(externals, "shared_fake_daytona", lambda: daytona)

    opened = resolve_job("kumo").job_computer()

    assert isinstance(opened.computer, DaytonaComputer)
    assert opened.computer.client_id == "kumo"
    assert opened.runner_command == RUNNER_COMMAND
    assert len(daytona.other_commands) == 2
    assert "lightpanda serve" in daytona.other_commands[0]
    assert "sites/server.ts" in daytona.other_commands[1]


def test_by_default_without_a_daytona_key_the_job_cannot_open_a_computer(monkeypatch):
    monkeypatch.setenv("CLIENTOPS_EXTERNALS", "real")
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    with pytest.raises(MissingExternalKey):
        resolve_job("kumo").job_computer()


def test_only_the_boundary_makes_local_computers():
    package = Path(boundary_module.__file__).parent
    allowed = {package / "boundary.py"}
    offenders = [
        str(path.relative_to(package))
        for path in sorted(package.rglob("*.py"))
        if path not in allowed and "LocalComputer(" in path.read_text()
    ]
    assert offenders == []
