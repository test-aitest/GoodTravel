import json
import shutil
import subprocess

import pytest
from daytona import (
    CreateSandboxFromSnapshotParams,
    DaytonaAuthenticationError,
    DaytonaBadRequestError,
    DaytonaConnectionError,
    DaytonaError,
    DaytonaForbiddenError,
    DaytonaNotFoundError,
    DaytonaProcessExecutionTimeoutError,
    DaytonaQueueTimeoutError,
    DaytonaRateLimitError,
    DaytonaServiceUnavailableError,
    DaytonaTimeoutError,
    ListSandboxesQuery,
    SandboxState,
)

import clientops.computers as computers_module
from clientops.computer_protocol import CommandResult, Computer
from clientops.computers import (
    AUTO_STOP_MINUTES,
    CLIENT_LABEL,
    LIGHTPANDA_CDP_URL,
    SANDBOX_ALLOW_LIST_VARIABLE,
    TIER_VARIABLE,
    Computers,
    ComputerError,
    ComputerProblem,
    DaytonaComputer,
    NetworkMode,
    network_policy,
)
from clientops.externals import MissingExternalKey
from clientops.externals.fake_daytona import SAMPLES_DIR, FakeDaytona

KUMO_SITES = ("portal.maruyama-supply.example", "admin.kumo-coffee.example")
HINATA_SITES = ("portal.maruyama-supply.example", "admin.hinata-skincare.example")
LIGHTPANDA_VERSION_ANSWER = json.dumps(
    {
        "Browser": "Lightpanda/1.0",
        "Protocol-Version": "1.3",
        "User-Agent": "Lightpanda/1.0",
        "Lightpanda-Version": "1.0.4",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/",
    }
)


def sample(name: str) -> dict:
    return json.loads((SAMPLES_DIR / name).read_text())


def calls(daytona: FakeDaytona, method: str):
    return [call for call in daytona.calls if call.method == method]


def created_params(daytona: FakeDaytona) -> list[CreateSandboxFromSnapshotParams]:
    return [call.arguments["params"] for call in calls(daytona, "create")]


@pytest.fixture(autouse=True)
def no_tier_setting(monkeypatch):
    monkeypatch.delenv(TIER_VARIABLE, raising=False)
    monkeypatch.delenv(SANDBOX_ALLOW_LIST_VARIABLE, raising=False)


@pytest.fixture
def daytona():
    return FakeDaytona()


@pytest.fixture
def computers(daytona):
    return Computers(daytona)


class LabelBlindDaytona(FakeDaytona):
    def list(self, query=None, request_timeout=None):
        return super().list(None, request_timeout)


class FailingCreateDaytona(FakeDaytona):
    def __init__(self, error: DaytonaError):
        super().__init__()
        self.error = error

    def create(self, params=None, *, timeout=60, on_snapshot_create_logs=None):
        raise self.error


def test_the_first_open_creates_the_clients_computer_from_the_snapshot(daytona, computers):
    computer = computers.open("kumo")

    [params] = created_params(daytona)
    assert params.snapshot == "clientops-computer-3"
    assert params.labels == {CLIENT_LABEL: "kumo", "code-toolbox-language": "python"}
    assert computer.created is True
    assert computer.client_id == "kumo"
    assert computer.sandbox.state is SandboxState.STARTED


def test_the_label_key_is_the_temporary_clientops_client():
    assert CLIENT_LABEL == "clientops-client"


def test_opening_again_reuses_the_computer_found_by_its_label(daytona, computers):
    first = computers.open("kumo")
    second = computers.open("kumo")

    assert len(calls(daytona, "create")) == 1
    assert second.id == first.id
    assert second.created is False
    assert calls(daytona, "list")[-1].arguments["query"] == ListSandboxesQuery(labels={CLIENT_LABEL: "kumo"})


def test_a_fresh_computers_object_finds_the_computer_made_before_a_restart(daytona):
    made = Computers(daytona).open("kumo")
    found = Computers(daytona).open("kumo")
    assert found.id == made.id
    assert len(calls(daytona, "create")) == 1


def test_each_client_gets_exactly_one_computer_of_its_own(daytona, computers):
    kumo = computers.open("kumo")
    hinata = computers.open("hinata")
    computers.open("kumo")
    computers.open("hinata")

    assert kumo.id != hinata.id
    assert [params.labels[CLIENT_LABEL] for params in created_params(daytona)] == ["kumo", "hinata"]


def test_another_clients_computer_is_never_handed_out_even_if_the_label_filter_lets_it_through():
    daytona = LabelBlindDaytona()
    computers = Computers(daytona)
    kumo = computers.open("kumo")

    hinata = computers.open("hinata")

    assert hinata.id != kumo.id
    assert hinata.sandbox.labels[CLIENT_LABEL] == "hinata"
    assert [params.labels[CLIENT_LABEL] for params in created_params(daytona)] == ["kumo", "hinata"]
    assert computers.open("kumo").id == kumo.id


def test_two_computers_with_one_clients_label_is_an_error_not_a_guess(daytona, computers):
    for _ in range(2):
        daytona.create(CreateSandboxFromSnapshotParams(snapshot="clientops-computer-3", labels={CLIENT_LABEL: "kumo"}))

    with pytest.raises(ComputerError) as caught:
        computers.open("kumo")

    assert caught.value.problem is ComputerProblem.MORE_THAN_ONE
    assert "2 computers" in str(caught.value)
    assert calls(daytona, "create")[2:] == []


def test_a_stopped_computer_is_started_again_instead_of_making_another(daytona, computers):
    made = computers.open("kumo")
    made.stop()

    reopened = computers.open("kumo")

    assert reopened.id == made.id
    assert reopened.sandbox.state is SandboxState.STARTED
    assert len(calls(daytona, "create")) == 1
    assert [call.method for call in daytona.calls][-1] == "start"


def test_an_archived_computer_is_started_again(daytona, computers):
    made = computers.open("kumo")
    daytona._set_state(made.id, SandboxState.ARCHIVED)

    assert computers.open("kumo").sandbox.state is SandboxState.STARTED
    assert len(calls(daytona, "start")) == 1


def test_a_computer_in_error_is_reported_with_its_reason_and_not_used(daytona, computers):
    made = computers.open("kumo")
    daytona._set_state(made.id, SandboxState.ERROR)
    daytona._sandboxes[made.id]["errorReason"] = "runner lost"

    with pytest.raises(ComputerError) as caught:
        computers.open("kumo")

    assert caught.value.problem is ComputerProblem.BROKEN
    assert "runner lost" in str(caught.value)
    assert calls(daytona, "start") == []


@pytest.mark.parametrize("client_id", ["sora-unknown", "Kumo", "kumo; reboot", "", "../kumo"])
def test_an_unknown_or_malformed_client_never_reaches_daytona(daytona, computers, client_id):
    with pytest.raises(ComputerError) as caught:
        computers.open(client_id)
    assert caught.value.problem is ComputerProblem.UNKNOWN_CLIENT
    assert daytona.calls == []


def test_each_clients_allow_list_is_its_own_sites_only():
    assert network_policy("kumo").allow_list == KUMO_SITES
    assert network_policy("hinata").allow_list == HINATA_SITES
    assert "admin.hinata-skincare.example" not in network_policy("kumo").allow_list
    assert "admin.kumo-coffee.example" not in network_policy("hinata").allow_list


def test_the_allow_list_is_sent_at_creation_as_daytona_expects_it(monkeypatch, daytona, computers):
    monkeypatch.setenv(SANDBOX_ALLOW_LIST_VARIABLE, "on")
    computers.open("kumo")
    computers.open("hinata")
    assert [params.domain_allow_list for params in created_params(daytona)] == [
        "portal.maruyama-supply.example,admin.kumo-coffee.example",
        "portal.maruyama-supply.example,admin.hinata-skincare.example",
    ]
    assert all(params.network_allow_list is None and not params.network_block_all for params in created_params(daytona))


@pytest.mark.parametrize("setting", [None, "", "off", "ON", "1", "true", " on"])
def test_unless_the_operator_turns_sandbox_lists_on_no_allow_list_is_sent_at_creation(monkeypatch, daytona, setting):
    if setting is not None:
        monkeypatch.setenv(SANDBOX_ALLOW_LIST_VARIABLE, setting)

    Computers(daytona, tier=3).open("kumo")

    [params] = created_params(daytona)
    assert params.domain_allow_list is None
    assert params.network_allow_list is None and not params.network_block_all
    assert params.labels[CLIENT_LABEL] == "kumo"


def test_an_organization_that_refuses_sandbox_lists_still_gets_its_computer(daytona):
    class OrganizationListOnly(FakeDaytona):
        def create(self, params=None, *, timeout=60, on_snapshot_create_logs=None):
            if params is not None and params.domain_allow_list is not None:
                raise DaytonaBadRequestError(
                    "Failed to create sandbox: Network access is restricted and cannot be overridden at the "
                    "sandbox level. Remove domainAllowList from the request."
                )
            return super().create(params, timeout=timeout, on_snapshot_create_logs=on_snapshot_create_logs)

    restricted = OrganizationListOnly()

    computer = Computers(restricted).open("kumo")

    assert computer.created is True
    assert computer.sandbox.labels[CLIENT_LABEL] == "kumo"


def test_without_a_tier_setting_the_organization_wide_restriction_is_what_is_in_force():
    policy = network_policy("kumo")
    assert policy.tier in (1, 2)
    assert policy.mode is NetworkMode.ORGANIZATION
    assert "whole organization" in policy.description
    assert "Tier 3" in policy.description


@pytest.mark.parametrize("tier", ["1", "2"])
def test_tier_1_and_2_say_the_organization_restriction_wins(monkeypatch, tier):
    monkeypatch.setenv(TIER_VARIABLE, tier)
    assert network_policy("kumo").mode is NetworkMode.ORGANIZATION


@pytest.mark.parametrize("tier", ["3", "4"])
def test_from_tier_3_each_computer_is_held_to_its_own_list(monkeypatch, tier):
    monkeypatch.setenv(TIER_VARIABLE, tier)
    policy = network_policy("kumo")
    assert policy.mode is NetworkMode.PER_COMPUTER
    assert policy.description == (
        "Kumo's computer can reach only these sites: portal.maruyama-supply.example, admin.kumo-coffee.example."
    )


@pytest.mark.parametrize("tier", ["0", "5", "pro", " 3"])
def test_an_unknown_tier_setting_is_an_error(monkeypatch, tier):
    monkeypatch.setenv(TIER_VARIABLE, tier)
    with pytest.raises(ValueError, match=TIER_VARIABLE):
        network_policy("kumo")


def test_the_opened_computer_tells_which_network_mode_is_in_force(daytona):
    assert Computers(daytona).open("kumo").network.mode is NetworkMode.ORGANIZATION
    assert Computers(daytona, tier=3).open("hinata").network.mode is NetworkMode.PER_COMPUTER


def test_from_tier_3_a_found_computer_without_its_allow_list_is_refused(monkeypatch, daytona):
    monkeypatch.setenv(SANDBOX_ALLOW_LIST_VARIABLE, "on")
    daytona.create(CreateSandboxFromSnapshotParams(snapshot="clientops-computer-3", labels={CLIENT_LABEL: "kumo"}))

    with pytest.raises(ComputerError) as caught:
        Computers(daytona, tier=3).open("kumo")

    assert caught.value.problem is ComputerProblem.ALLOW_LIST_MISMATCH
    assert "admin.kumo-coffee.example" in str(caught.value)


def test_on_tier_1_and_2_a_found_computer_is_used_whatever_its_list_says(daytona):
    daytona.create(CreateSandboxFromSnapshotParams(snapshot="clientops-computer-3", labels={CLIENT_LABEL: "kumo"}))
    assert Computers(daytona).open("kumo").created is False


def test_with_sandbox_lists_off_a_found_computer_is_used_even_on_tier_3(daytona):
    daytona.create(CreateSandboxFromSnapshotParams(snapshot="clientops-computer-3", labels={CLIENT_LABEL: "kumo"}))

    computer = Computers(daytona, tier=3).open("kumo")

    assert computer.created is False
    assert computer.network.sandbox_allow_list is False


def test_only_the_word_on_turns_sandbox_lists_on(monkeypatch):
    assert network_policy("kumo").sandbox_allow_list is False
    monkeypatch.setenv(SANDBOX_ALLOW_LIST_VARIABLE, "on")
    assert network_policy("kumo").sandbox_allow_list is True


def test_no_environment_variables_or_secrets_are_given_at_creation(daytona, computers):
    computer = computers.open("kumo")

    [params] = created_params(daytona)
    assert params.env_vars is None
    assert params.secrets is None
    assert not params.public
    computer.sandbox.refresh_data()
    assert computer.sandbox.env == {}


def test_auto_stop_is_always_set_so_an_idle_computer_stops(daytona, computers):
    computers.open("kumo")
    [params] = created_params(daytona)
    assert params.auto_stop_interval == AUTO_STOP_MINUTES
    assert 0 < AUTO_STOP_MINUTES <= 15


def test_opening_records_how_long_it_took(computers):
    assert computers.open("kumo").startup_seconds >= 0


def test_the_daytona_computer_is_a_computer_for_page_operations(computers):
    computer = computers.open("kumo")
    assert isinstance(computer, DaytonaComputer)
    assert isinstance(computer, Computer)


def test_a_command_gets_the_timeout_it_was_given(daytona, computers):
    computers.open("kumo").run("lightpanda run /home/daytona/jobs/job.js", timeout_seconds=120)

    [call] = calls(daytona, "exec")
    assert call.arguments == {
        "command": "lightpanda run /home/daytona/jobs/job.js",
        "cwd": None,
        "env": None,
        "timeout": 120,
    }


def test_a_command_cannot_be_run_without_choosing_a_timeout(computers):
    computer = computers.open("kumo")
    with pytest.raises(TypeError):
        computer.run("true")


@pytest.mark.parametrize("timeout_seconds", [0, -1, True, 2.5])
def test_a_timeout_must_be_a_whole_number_of_seconds_above_zero(daytona, computers, timeout_seconds):
    computer = computers.open("kumo")
    with pytest.raises(ValueError, match="timeout_seconds"):
        computer.run("true", timeout_seconds=timeout_seconds)
    assert calls(daytona, "exec") == []


def test_a_command_returns_its_exit_code_and_output(daytona, computers):
    daytona.script_command("echo 'hello from the client computer'", sample("execute_response_echo.json"))
    result = computers.open("kumo").run("echo 'hello from the client computer'", timeout_seconds=10)
    assert result == CommandResult(exit_code=0, output="hello from the client computer\n")


def test_a_failing_command_returns_its_exit_code_instead_of_raising(daytona, computers):
    daytona.script_command("exit 3", sample("execute_response_exit_3.json"))
    assert computers.open("kumo").run("exit 3", timeout_seconds=10) == CommandResult(exit_code=3, output="")


def test_plain_settings_can_be_passed_to_one_command(daytona, computers):
    computers.open("kumo").run("bun server.ts", timeout_seconds=10, env={"SITES_CLIENTS": "kumo"})
    assert calls(daytona, "exec")[0].arguments["env"] == {"SITES_CLIENTS": "kumo"}


@pytest.mark.parametrize(
    "name",
    ["LP_STORE_PASSWORD", "LP_STORE_USERNAME", "ANTHROPIC_API_KEY", "DAYTONA_API_KEY", "SESSION_TOKEN", "db_password", "CLIENT_SECRET"],
)
def test_secret_looking_settings_never_go_through_daytona(daytona, computers, name):
    computer = computers.open("kumo")
    with pytest.raises(ComputerError) as caught:
        computer.run("true", timeout_seconds=10, env={name: "hunter2-value"})
    assert caught.value.problem is ComputerProblem.SECRET_IN_ENV
    assert name in str(caught.value)
    assert "hunter2-value" not in str(caught.value)
    assert calls(daytona, "exec") == []


def test_a_secret_looking_value_is_refused_whatever_its_name(daytona, computers):
    computer = computers.open("kumo")
    with pytest.raises(ComputerError) as caught:
        computer.run("true", timeout_seconds=10, env={"CONFIG": "sk-ant-api03-abcdef"})
    assert caught.value.problem is ComputerProblem.SECRET_IN_ENV
    assert "sk-ant" not in str(caught.value)


def test_upload_puts_the_bytes_at_the_path(daytona, computers):
    computers.open("kumo").upload(b"const page = new Page();\n", "/home/daytona/jobs/job.js")
    [call] = calls(daytona, "upload_file")
    assert call.arguments == {"dst": "/home/daytona/jobs/job.js", "content": b"const page = new Page();\n"}


def test_lightpanda_is_started_on_loopback_9222_and_checked_in_one_round_trip(daytona, computers):
    computer = computers.open("kumo")
    daytona.script_command(computer.browser_command(), {"exitCode": 0, "result": LIGHTPANDA_VERSION_ANSWER + "\n"})

    browser = computer.start_browser()

    assert browser.version == "1.0.4"
    assert browser.cdp_url == LIGHTPANDA_CDP_URL == "ws://127.0.0.1:9222/"
    [call] = calls(daytona, "exec")
    command = call.arguments["command"]
    assert "lightpanda serve --host 127.0.0.1 --port 9222" in command
    assert "http://127.0.0.1:9222/json/version" in command
    assert "nohup" in command and "< /dev/null &" in command
    assert call.arguments["timeout"] >= 15
    assert call.arguments["env"] is None


def test_lightpanda_that_never_answers_is_an_error_with_its_log(daytona, computers):
    computer = computers.open("kumo")
    daytona.script_command(computer.browser_command(), {"exitCode": 69, "result": "error: GLIBC_2.38 not found\n"})

    with pytest.raises(ComputerError) as caught:
        computer.start_browser()

    assert caught.value.problem is ComputerProblem.BROWSER_NOT_ANSWERING
    assert "GLIBC_2.38 not found" in str(caught.value)


def test_lightpanda_of_another_version_is_an_error(daytona, computers):
    computer = computers.open("kumo")
    answer = LIGHTPANDA_VERSION_ANSWER.replace("1.0.4", "1.0.3")
    daytona.script_command(computer.browser_command(), {"exitCode": 0, "result": answer})

    with pytest.raises(ComputerError) as caught:
        computer.start_browser()

    assert caught.value.problem is ComputerProblem.BROWSER_NOT_ANSWERING
    assert "1.0.3" in str(caught.value) and "1.0.4" in str(caught.value)


def test_an_answer_that_is_not_lightpandas_version_is_an_error(computers):
    computer = computers.open("kumo")
    with pytest.raises(ComputerError) as caught:
        computer.start_browser()
    assert caught.value.problem is ComputerProblem.BROWSER_NOT_ANSWERING


def test_the_demo_sites_start_with_only_that_clients_data(daytona, computers):
    kumo = computers.open("kumo")
    hinata = computers.open("hinata")

    sites = kumo.start_sites()
    hinata.start_sites()

    assert (sites.supplier_url, sites.store_url) == ("http://127.0.0.1:3001", "http://127.0.0.1:3002")
    kumo_command, hinata_command = [call.arguments["command"] for call in calls(daytona, "exec")]
    assert "SITES_CLIENTS=kumo nohup bun /opt/clientops/sites/server.ts" in kumo_command
    assert "SITES_CLIENTS=hinata nohup bun /opt/clientops/sites/server.ts" in hinata_command
    assert "http://127.0.0.1:3001/login" in kumo_command and "http://127.0.0.1:3002/login" in kumo_command


def test_demo_sites_that_never_answer_are_an_error_with_their_log(daytona, computers):
    computer = computers.open("kumo")
    daytona.script_command(computer.sites_command(), {"exitCode": 69, "result": "error: Cannot find module\n"})

    with pytest.raises(ComputerError) as caught:
        computer.start_sites()

    assert caught.value.problem is ComputerProblem.SITES_NOT_ANSWERING
    assert "Cannot find module" in str(caught.value)


def test_stop_stops_the_clients_computer(daytona, computers):
    computer = computers.open("kumo")
    computer.stop()
    assert calls(daytona, "stop")[0].sandbox_id == computer.id
    assert computer.sandbox.state is SandboxState.STOPPED


def test_stopping_by_client_finds_the_computer_and_never_creates_one(daytona, computers):
    assert computers.stop("kumo") is False
    assert calls(daytona, "create") == []

    made = computers.open("kumo")
    assert computers.stop("kumo") is True
    assert calls(daytona, "stop")[0].sandbox_id == made.id
    assert computers.stop("kumo") is False


def test_working_on_a_stopped_computer_is_a_clear_error(computers):
    computer = computers.open("kumo")
    computer.stop()
    with pytest.raises(ComputerError) as caught:
        computer.run("true", timeout_seconds=10)
    assert caught.value.problem is ComputerProblem.FAILED
    assert "kumo's computer" in str(caught.value)


@pytest.mark.parametrize(
    ("error", "problem", "hint"),
    [
        (DaytonaAuthenticationError("Failed to create sandbox: Unauthorized", status_code=401), ComputerProblem.KEY_REFUSED, "DAYTONA_API_KEY"),
        (DaytonaForbiddenError("Failed to create sandbox: Forbidden", status_code=403), ComputerProblem.KEY_REFUSED, "DAYTONA_API_KEY"),
        (DaytonaRateLimitError("Failed to create sandbox: Too many requests", status_code=429), ComputerProblem.BUSY, "try again"),
        (DaytonaQueueTimeoutError("Failed to create sandbox: queue timeout"), ComputerProblem.BUSY, "try again"),
        (DaytonaServiceUnavailableError("Failed to create sandbox: unavailable", status_code=503), ComputerProblem.BUSY, "try again"),
        (DaytonaTimeoutError("Failed to create sandbox: not ready in time"), ComputerProblem.TIMED_OUT, "60 seconds"),
        (DaytonaConnectionError("Failed to create sandbox: connection refused"), ComputerProblem.UNREACHABLE, "network"),
        (DaytonaNotFoundError("Failed to create sandbox: Snapshot not found", status_code=404), ComputerProblem.NOT_FOUND, "computer:snapshot"),
        (DaytonaBadRequestError("Failed to create sandbox: bad domainAllowList", status_code=400), ComputerProblem.REFUSED, "refused"),
        (DaytonaError("Failed to create sandbox: something else"), ComputerProblem.FAILED, "something else"),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else None,
)
def test_daytona_errors_become_clear_computer_errors(error, problem, hint):
    with pytest.raises(ComputerError) as caught:
        Computers(FailingCreateDaytona(error)).open("kumo")

    assert caught.value.problem is problem
    assert caught.value.client_id == "kumo"
    assert caught.value.__cause__ is error
    message = str(caught.value)
    assert message.startswith("Could not create kumo's computer: ")
    assert hint.lower() in message.lower()
    assert str(error) in message


def test_a_command_that_runs_too_long_is_reported_with_its_timeout(computers, monkeypatch):
    computer = computers.open("kumo")

    def too_slow(command, cwd=None, env=None, timeout=None):
        raise DaytonaProcessExecutionTimeoutError("Failed to execute command: timed out")

    monkeypatch.setattr(computer.sandbox.process, "exec", too_slow)
    with pytest.raises(ComputerError) as caught:
        computer.run("sleep 999", timeout_seconds=30)
    assert caught.value.problem is ComputerProblem.TIMED_OUT
    assert "30 seconds" in str(caught.value)


def test_without_an_explicit_client_the_externals_setting_decides(monkeypatch):
    monkeypatch.setattr(computers_module, "daytona_client", lambda: FakeDaytona())
    assert Computers().open("kumo").client_id == "kumo"


def test_without_a_key_the_missing_key_error_is_raised_as_is(monkeypatch):
    for name in ("CLIENTOPS_EXTERNALS", "DAYTONA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(MissingExternalKey):
        Computers()


@pytest.fixture(params=["sh", "bash", "zsh"])
def local_shell_run(request, tmp_path, monkeypatch):
    shell = shutil.which(request.param)
    if shell is None:
        pytest.skip(f"{request.param} is not installed here")
    monkeypatch.setattr(computers_module, "RUN_DIRECTORY", str(tmp_path / "run"))

    def run(start: str, check: str, wait_seconds: int = 10) -> subprocess.CompletedProcess:
        monkeypatch.setattr(computers_module, "WAIT_SECONDS", wait_seconds)
        command = computers_module.start_and_wait_command(start=start, check=check, log=str(tmp_path / "run" / "x.log"))
        return subprocess.run([shell, "-c", command], capture_output=True, text=True, timeout=wait_seconds + 20)

    return run


def test_the_start_and_wait_shell_prints_only_the_answer_even_after_failed_checks(tmp_path, local_shell_run):
    ready = tmp_path / "ready"
    done = local_shell_run(
        start=f"sh -c 'sleep 0.4; echo answer > {ready}'",
        check=f"cat {ready}",
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, "answer\n", "")


def test_the_start_and_wait_shell_does_not_start_a_second_copy(tmp_path, local_shell_run):
    (tmp_path / "ready").write_text("answer\n")
    marker = tmp_path / "started-again"
    done = local_shell_run(start=f"touch {marker}", check=f"cat {tmp_path / 'ready'}")
    assert done.returncode == 0
    assert not marker.exists()


def test_the_start_and_wait_shell_gives_up_with_the_log_tail(tmp_path, local_shell_run):
    done = local_shell_run(
        start="echo 'error: GLIBC_2.38 not found'", check=f"cat {tmp_path / 'never'}", wait_seconds=1
    )
    assert done.returncode == computers_module.NOT_ANSWERING_EXIT_CODE
    assert done.stdout == "error: GLIBC_2.38 not found\n"


def test_the_start_and_wait_shell_checks_compound_checks_as_one(tmp_path, local_shell_run):
    first, second = tmp_path / "first", tmp_path / "second"
    done = local_shell_run(
        start=f"sh -c 'sleep 0.3; touch {first} {second}'",
        check=f"test -f {first} && test -f {second}",
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
