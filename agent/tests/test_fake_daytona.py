import json
import uuid

import pytest
from daytona import (
    CreateSandboxFromSnapshotParams,
    DaytonaBadRequestError,
    DaytonaConflictError,
    DaytonaError,
    DaytonaNotFoundError,
    ExecuteResponse,
    ExecutionArtifacts,
    ListSandboxesQuery,
    SandboxState,
)
from daytona_api_client import SandboxListItem, SignedPortPreviewUrl
from daytona_api_client import Sandbox as SandboxDto

from clientops.externals.fake_daytona import SAMPLES_DIR, FakeCall, FakeDaytona

CLIENT_LABEL = "clientops-client"
KUMO_SITES = "portal.maruyama-supply.example,admin.kumo-coffee.example"
ECHO = "echo 'hello from the client computer'"


def load(name: str):
    return json.loads((SAMPLES_DIR / name).read_text())


def kumo_params(**overrides) -> CreateSandboxFromSnapshotParams:
    fields = {
        "snapshot": "clientops-computer-1",
        "labels": {CLIENT_LABEL: "kumo"},
        "domain_allow_list": KUMO_SITES,
        "auto_stop_interval": 15,
    }
    fields.update(overrides)
    return CreateSandboxFromSnapshotParams(**fields)


def hinata_params() -> CreateSandboxFromSnapshotParams:
    return CreateSandboxFromSnapshotParams(
        snapshot="clientops-computer-1",
        labels={CLIENT_LABEL: "hinata"},
        domain_allow_list="portal.maruyama-supply.example,admin.hinata-skincare.example",
        auto_stop_interval=15,
    )


def without_identity(model) -> dict:
    return {key: value for key, value in model.model_dump().items() if key not in ("id", "name")}


def test_created_sandbox_has_the_shape_of_the_sample_create_response():
    sandbox = FakeDaytona().create(kumo_params())

    expected = SandboxDto.from_dict(load("sandbox.json"))
    assert isinstance(sandbox, SandboxDto)
    assert without_identity(sandbox) == without_identity(expected)
    assert sandbox.state is SandboxState.STARTED


def test_created_sandbox_gets_a_uuid_and_its_name_defaults_to_the_id():
    sandbox = FakeDaytona().create(kumo_params())
    assert str(uuid.UUID(sandbox.id)) == sandbox.id
    assert sandbox.name == sandbox.id


def test_ids_are_the_same_for_the_same_sequence_of_calls():
    first, second = FakeDaytona(), FakeDaytona()
    assert [first.create(kumo_params()).id, first.create(hinata_params()).id] == [
        second.create(kumo_params()).id,
        second.create(hinata_params()).id,
    ]


def test_create_adds_the_language_label_like_the_sdk_does():
    params = kumo_params()
    sandbox = FakeDaytona().create(params)
    assert sandbox.labels == {CLIENT_LABEL: "kumo", "code-toolbox-language": "python"}
    assert params.labels == sandbox.labels
    assert params.language == "python"


def test_auto_stop_defaults_to_15_minutes_when_not_given():
    sandbox = FakeDaytona().create(kumo_params(auto_stop_interval=None))
    assert sandbox.auto_stop_interval == 15


def test_create_keeps_env_empty_when_none_is_given():
    sandbox = FakeDaytona().create(kumo_params())
    assert sandbox.env == {}


def test_create_records_the_params_object_it_was_given():
    daytona = FakeDaytona()
    params = kumo_params()
    sandbox = daytona.create(params)
    assert daytona.calls == [FakeCall("create", sandbox.id, {"params": params})]


def test_block_all_together_with_an_allow_list_is_a_400_like_the_api():
    with pytest.raises(DaytonaBadRequestError) as caught:
        FakeDaytona().create(kumo_params(network_block_all=True))
    assert caught.value.status_code == 400


def test_negative_auto_stop_is_rejected_before_any_request_like_the_sdk():
    with pytest.raises(DaytonaBadRequestError) as caught:
        FakeDaytona().create(kumo_params(auto_stop_interval=-1))
    assert caught.value.message == "Failed to create sandbox: auto_stop_interval must be a non-negative integer"
    assert caught.value.status_code is None


def test_a_taken_name_is_a_409_conflict():
    daytona = FakeDaytona()
    daytona.create(kumo_params(name="kumo-computer"))
    with pytest.raises(DaytonaConflictError) as caught:
        daytona.create(kumo_params(name="kumo-computer"))
    assert caught.value.status_code == 409


def test_find_a_clients_sandbox_by_label():
    daytona = FakeDaytona()
    kumo = daytona.create(kumo_params())
    daytona.create(hinata_params())

    found = list(daytona.list(ListSandboxesQuery(labels={CLIENT_LABEL: "kumo"})))

    assert [sandbox.id for sandbox in found] == [kumo.id]


def test_list_without_a_query_returns_every_sandbox_in_creation_order():
    daytona = FakeDaytona()
    created = [daytona.create(kumo_params()).id, daytona.create(hinata_params()).id]
    assert [sandbox.id for sandbox in daytona.list()] == created


def test_list_can_also_filter_by_state():
    daytona = FakeDaytona()
    kumo = daytona.create(kumo_params())
    daytona.create(hinata_params())
    kumo.stop()
    found = daytona.list(ListSandboxesQuery(states=[SandboxState.STOPPED]))
    assert [sandbox.id for sandbox in found] == [kumo.id]


def test_listed_sandboxes_have_the_shape_of_the_sample_list_item():
    daytona = FakeDaytona()
    daytona.create(kumo_params()).stop()

    listed = next(iter(daytona.list(ListSandboxesQuery(labels={CLIENT_LABEL: "kumo"}))))

    expected = SandboxListItem.from_dict(load("list_sandboxes.json")["items"][0])
    for key, value in without_identity(expected).items():
        assert getattr(listed, key) == value, key


def test_listed_sandboxes_leave_out_env_until_refreshed_like_the_sdk():
    daytona = FakeDaytona()
    daytona.create(kumo_params())
    listed = next(iter(daytona.list()))
    assert listed.env is None
    listed.refresh_data()
    assert listed.env == {}


def test_get_by_id_or_by_name():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params(name="kumo-computer"))
    assert daytona.get(sandbox.id).id == sandbox.id
    assert daytona.get("kumo-computer").id == sandbox.id


def test_get_of_an_unknown_sandbox_is_a_404():
    with pytest.raises(DaytonaNotFoundError) as caught:
        FakeDaytona().get("no-such-sandbox")
    assert caught.value.status_code == 404


def test_get_with_an_empty_id_is_rejected_like_the_sdk():
    with pytest.raises(DaytonaBadRequestError) as caught:
        FakeDaytona().get("")
    assert caught.value.message == "Failed to get sandbox: sandbox_id_or_name is required"


def test_stop_and_start_change_the_state_seen_by_every_client_object():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    sandbox.stop()
    assert sandbox.state is SandboxState.STOPPED
    assert daytona.get(sandbox.id).state is SandboxState.STOPPED
    sandbox.start()
    assert sandbox.state is SandboxState.STARTED


def test_daytona_start_and_stop_act_on_the_given_sandbox():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    daytona.stop(sandbox)
    assert sandbox.state is SandboxState.STOPPED
    daytona.start(sandbox)
    assert sandbox.state is SandboxState.STARTED


def test_refresh_data_picks_up_changes_made_through_another_object():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    daytona.get(sandbox.id).stop()
    assert sandbox.state is SandboxState.STARTED
    sandbox.refresh_data()
    assert sandbox.state is SandboxState.STOPPED


def test_exec_returns_the_scripted_sample_response_as_the_sdk_type():
    daytona = FakeDaytona()
    daytona.script_command(ECHO, load("execute_response_echo.json"))
    sandbox = daytona.create(kumo_params())

    response = sandbox.process.exec(ECHO)

    assert isinstance(response, ExecuteResponse)
    assert response.exit_code == 0
    assert response.result == "hello from the client computer\n"
    assert response.artifacts == ExecutionArtifacts(stdout="hello from the client computer\n", charts=[])


def test_a_failing_command_returns_its_exit_code_instead_of_raising():
    daytona = FakeDaytona()
    daytona.script_command("exit 3", load("execute_response_exit_3.json"))
    response = daytona.create(kumo_params()).process.exec("exit 3")
    assert (response.exit_code, response.result) == (3, "")


def test_an_unscripted_command_succeeds_with_no_output():
    response = FakeDaytona().create(kumo_params()).process.exec("lightpanda version")
    assert (response.exit_code, response.result) == (0, "")


def test_exec_records_the_command_and_its_options():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    sandbox.process.exec("lightpanda run job.js", cwd="/home/daytona/jobs", env={"LP_SITE": "store"}, timeout=120)
    assert daytona.calls[-1] == FakeCall(
        "exec",
        sandbox.id,
        {"command": "lightpanda run job.js", "cwd": "/home/daytona/jobs", "env": {"LP_SITE": "store"}, "timeout": 120},
    )


def test_exec_on_a_stopped_sandbox_fails():
    sandbox = FakeDaytona().create(kumo_params())
    sandbox.stop()
    with pytest.raises(DaytonaError):
        sandbox.process.exec(ECHO)


def test_upload_bytes_records_the_destination_and_content():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    sandbox.fs.upload_file(b"console.log('step 1')", "/home/daytona/jobs/job.js")
    assert daytona.calls[-1] == FakeCall(
        "upload_file", sandbox.id, {"dst": "/home/daytona/jobs/job.js", "content": b"console.log('step 1')"}
    )


def test_upload_from_a_local_path_records_the_file_content(tmp_path):
    local = tmp_path / "job.js"
    local.write_bytes(b"console.log('step 2')")
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    sandbox.fs.upload_file(str(local), "jobs/job.js")
    assert daytona.calls[-1].arguments == {"dst": "jobs/job.js", "content": b"console.log('step 2')"}


def test_upload_to_a_stopped_sandbox_fails():
    sandbox = FakeDaytona().create(kumo_params())
    sandbox.stop()
    with pytest.raises(DaytonaError):
        sandbox.fs.upload_file(b"x", "jobs/job.js")


def test_signed_preview_url_has_the_shape_of_the_sample():
    sandbox = FakeDaytona().create(kumo_params())

    preview = sandbox.create_signed_preview_url(3002, expires_in_seconds=3600)

    sample = SignedPortPreviewUrl.from_dict(load("signed_port_preview_url.json"))
    assert isinstance(preview, SignedPortPreviewUrl)
    assert preview.sandbox_id == sandbox.id
    assert preview.port == 3002
    assert len(preview.token) == len(sample.token)
    assert preview.url == f"https://3002-{preview.token}.proxy.daytona.work"
    assert sample.url == f"https://{sample.port}-{sample.token}.proxy.daytona.work"


def test_signed_preview_tokens_are_deterministic_and_differ_per_call():
    first, second = FakeDaytona(), FakeDaytona()
    a, b = first.create(kumo_params()), second.create(kumo_params())
    tokens_a = [a.create_signed_preview_url(3002).token, a.create_signed_preview_url(3002).token]
    tokens_b = [b.create_signed_preview_url(3002).token, b.create_signed_preview_url(3002).token]
    assert tokens_a == tokens_b
    assert tokens_a[0] != tokens_a[1]


def test_signed_preview_expiry_is_recorded_with_the_60_second_default():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    preview = sandbox.create_signed_preview_url(3002)
    assert daytona.calls[-1] == FakeCall(
        "create_signed_preview_url", sandbox.id, {"port": 3002, "expires_in_seconds": 60, "token": preview.token}
    )


@pytest.mark.parametrize("seconds", [0, 86401])
def test_signed_preview_expiry_outside_1_to_86400_seconds_is_rejected(seconds):
    sandbox = FakeDaytona().create(kumo_params())
    with pytest.raises(DaytonaBadRequestError):
        sandbox.create_signed_preview_url(3002, expires_in_seconds=seconds)


def test_expiring_a_signed_preview_url_is_recorded():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    preview = sandbox.create_signed_preview_url(3002, expires_in_seconds=3600)
    sandbox.expire_signed_preview_url(3002, preview.token)
    assert daytona.calls[-1] == FakeCall("expire_signed_preview_url", sandbox.id, {"port": 3002, "token": preview.token})


def test_calls_are_kept_in_order():
    daytona = FakeDaytona()
    sandbox = daytona.create(kumo_params())
    sandbox.process.exec(ECHO)
    sandbox.stop()
    assert [call.method for call in daytona.calls] == ["create", "exec", "stop"]
