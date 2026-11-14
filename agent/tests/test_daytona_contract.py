import inspect
import json
from importlib.metadata import version

import daytona
import pytest
from daytona._sync.filesystem import FileSystem
from daytona._sync.process import Process
from daytona_api_client import ListSandboxesResponse, SignedPortPreviewUrl, SnapshotDto
from daytona_api_client import Sandbox as SandboxDto
from daytona_toolbox_api_client import ExecuteResponse as WireExecuteResponse

from clientops.externals.fake_daytona import SAMPLES_DIR, FakeDaytona, FakeFileSystem, FakeProcess, FakeSandbox

SAMPLE_MODELS = {
    "sandbox.json": SandboxDto,
    "list_sandboxes.json": ListSandboxesResponse,
    "signed_port_preview_url.json": SignedPortPreviewUrl,
    "execute_response_echo.json": WireExecuteResponse,
    "execute_response_exit_3.json": WireExecuteResponse,
    "snapshot.json": SnapshotDto,
}

SAME_ARGUMENTS = [
    (FakeDaytona.create, daytona.Daytona.create),
    (FakeDaytona.get, daytona.Daytona.get),
    (FakeDaytona.list, daytona.Daytona.list),
    (FakeDaytona.start, daytona.Daytona.start),
    (FakeDaytona.stop, daytona.Daytona.stop),
    (FakeSandbox.start, daytona.Sandbox.start),
    (FakeSandbox.stop, daytona.Sandbox.stop),
    (FakeSandbox.refresh_data, daytona.Sandbox.refresh_data),
    (FakeSandbox.create_signed_preview_url, daytona.Sandbox.create_signed_preview_url),
    (FakeSandbox.expire_signed_preview_url, daytona.Sandbox.expire_signed_preview_url),
    (FakeProcess.exec, Process.exec),
    (FakeFileSystem.upload_file, FileSystem.upload_file),
]


def load(name: str):
    return json.loads((SAMPLES_DIR / name).read_text())


def assert_written_back_the_same(sample, written, path="$"):
    if isinstance(sample, dict):
        for key, value in sample.items():
            assert key in written, f"{path}.{key}"
            assert_written_back_the_same(value, written[key], f"{path}.{key}")
    elif isinstance(sample, list):
        assert len(sample) == len(written), path
        for index, (item, written_item) in enumerate(zip(sample, written)):
            assert_written_back_the_same(item, written_item, f"{path}[{index}]")
    else:
        assert sample == written and type(sample) is type(written), path


def test_the_contract_was_written_against_daytona_0_231_1():
    assert version("daytona") == "0.231.1"


def test_every_sample_file_is_checked_against_an_sdk_model():
    assert sorted(path.name for path in SAMPLES_DIR.glob("*.json")) == sorted(SAMPLE_MODELS)


@pytest.mark.parametrize("name", sorted(SAMPLE_MODELS))
def test_each_sample_is_read_by_the_sdk_model_without_unknown_or_retyped_fields(name):
    sample = load(name)
    model = SAMPLE_MODELS[name].from_dict(sample)
    assert model.additional_properties == {}
    assert_written_back_the_same(sample, json.loads(model.to_json()))


def test_list_items_carry_no_unknown_fields_either():
    listed = ListSandboxesResponse.from_dict(load("list_sandboxes.json"))
    assert [item.additional_properties for item in listed.items] == [{}]


@pytest.mark.parametrize(("fake", "real"), SAME_ARGUMENTS, ids=lambda method: method.__qualname__)
def test_fake_methods_take_the_same_arguments_as_the_sdk(fake, real):
    def arguments(method):
        return [(p.name, p.kind, p.default) for p in inspect.signature(method).parameters.values()]

    assert arguments(fake) == arguments(real)


def test_fake_sandbox_has_exactly_the_sdk_sandbox_fields_and_types():
    assert set(FakeSandbox.model_fields) == set(daytona.Sandbox.model_fields)
    for name, field in daytona.Sandbox.model_fields.items():
        assert FakeSandbox.model_fields[name].annotation == field.annotation, name


def test_fake_sandbox_is_the_same_sdk_model_the_real_sandbox_extends():
    assert issubclass(daytona.Sandbox, SandboxDto)
    assert issubclass(FakeSandbox, SandboxDto)


def test_fake_sandbox_offers_process_and_fs_as_properties_like_the_sdk():
    for tool in ("process", "fs"):
        assert isinstance(inspect.getattr_static(daytona.Sandbox, tool), property)
        assert isinstance(inspect.getattr_static(FakeSandbox, tool), property)
