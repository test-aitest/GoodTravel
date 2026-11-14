import inspect
import json

import pytest
from daytona import (
    CreateSnapshotParams,
    DaytonaConflictError,
    DaytonaError,
    DaytonaNotFoundError,
    Image,
    Resources,
)
from daytona._sync.snapshot import SnapshotService
from daytona.common.snapshot import Snapshot
from daytona_api_client import SnapshotDto, SnapshotState

from clientops.externals.fake_daytona import SAMPLES_DIR, FakeDaytona, FakeSnapshotService


def load(name: str):
    return json.loads((SAMPLES_DIR / name).read_text())


def image_with_a_file(tmp_path) -> Image:
    source = tmp_path / "server.ts"
    source.write_text("console.log('sites')\n")
    return Image.base("debian:trixie-20261104-slim").run_commands("true").add_local_file(source, "/opt/sites/server.ts")


@pytest.mark.parametrize("name", ["get", "create", "activate"])
def test_fake_snapshot_methods_take_the_same_arguments_as_the_sdk(name):
    def arguments(method):
        return [(p.name, p.kind, p.default) for p in inspect.signature(method).parameters.values()]

    assert arguments(getattr(FakeSnapshotService, name)) == arguments(getattr(SnapshotService, name))


def test_the_fake_offers_the_snapshot_service_where_the_sdk_does():
    assert isinstance(FakeDaytona().snapshot, FakeSnapshotService)


def test_a_created_snapshot_has_the_shape_of_the_sample(tmp_path):
    daytona = FakeDaytona()
    snapshot = daytona.snapshot.create(
        CreateSnapshotParams(name="clientops-computer-1", image=image_with_a_file(tmp_path), resources=Resources(cpu=1, memory=1, disk=3))
    )

    sample = load("snapshot.json")
    written = json.loads(snapshot.to_json())
    assert isinstance(snapshot, Snapshot)
    assert set(written) == set(sample)
    assert set(written["buildInfo"]) == set(sample["buildInfo"])
    assert snapshot.name == "clientops-computer-1"
    assert snapshot.state is SnapshotState.ACTIVE
    assert (snapshot.cpu, snapshot.gpu, snapshot.mem, snapshot.disk) == (1, 0, 1, 3)
    assert SnapshotDto.from_dict(sample).organization_id == snapshot.organization_id


def test_create_records_the_dockerfile_and_the_local_files_it_would_upload(tmp_path):
    daytona = FakeDaytona()
    image = image_with_a_file(tmp_path)
    daytona.snapshot.create(CreateSnapshotParams(name="clientops-computer-1", image=image))

    call = daytona.calls[-1]
    assert call.method == "snapshot.create"
    assert call.arguments["dockerfile"] == image.dockerfile()
    assert call.arguments["context_paths"] == [str(tmp_path / "server.ts")]
    assert daytona.snapshot.get("clientops-computer-1").build_info.dockerfile_content == image.dockerfile()


def test_an_entrypoint_in_the_params_is_added_to_the_dockerfile_like_the_sdk(tmp_path):
    daytona = FakeDaytona()
    image = image_with_a_file(tmp_path)
    snapshot = daytona.snapshot.create(
        CreateSnapshotParams(name="clientops-computer-1", image=image, entrypoint=["sleep", "infinity"])
    )
    assert snapshot.build_info.dockerfile_content.endswith('ENTRYPOINT ["sleep", "infinity"]\n')
    assert snapshot.entrypoint == ["sleep", "infinity"]


def test_create_reports_progress_to_on_logs_like_the_sdk(tmp_path):
    lines = []
    FakeDaytona().snapshot.create(
        CreateSnapshotParams(name="clientops-computer-1", image=image_with_a_file(tmp_path)), on_logs=lines.append
    )
    assert lines[0].startswith("Creating snapshot clientops-computer-1")
    assert lines[-1] == "Created snapshot clientops-computer-1 (active)"


def test_get_finds_a_snapshot_by_name_or_id(tmp_path):
    daytona = FakeDaytona()
    created = daytona.snapshot.create(CreateSnapshotParams(name="clientops-computer-1", image=image_with_a_file(tmp_path)))
    assert daytona.snapshot.get("clientops-computer-1").id == created.id
    assert daytona.snapshot.get(created.id).name == "clientops-computer-1"


def test_get_of_an_unknown_snapshot_is_a_404():
    with pytest.raises(DaytonaNotFoundError) as caught:
        FakeDaytona().snapshot.get("clientops-computer-1")
    assert caught.value.status_code == 404
    assert str(caught.value).startswith("Failed to get snapshot: ")


def test_a_taken_snapshot_name_is_a_409_conflict(tmp_path):
    daytona = FakeDaytona()
    params = CreateSnapshotParams(name="clientops-computer-1", image=image_with_a_file(tmp_path))
    daytona.snapshot.create(params)
    with pytest.raises(DaytonaConflictError) as caught:
        daytona.snapshot.create(params)
    assert caught.value.status_code == 409


def test_a_failed_build_raises_with_the_reason_and_leaves_the_snapshot_failed(tmp_path):
    daytona = FakeDaytona()
    daytona.snapshot.fail_next_build("sha256sum: WARNING: 1 computed checksum did NOT match")
    with pytest.raises(DaytonaError) as caught:
        daytona.snapshot.create(CreateSnapshotParams(name="clientops-computer-1", image=image_with_a_file(tmp_path)))
    assert str(caught.value) == (
        "Failed to create snapshot: Failed to create snapshot clientops-computer-1, "
        "reason: sha256sum: WARNING: 1 computed checksum did NOT match"
    )
    assert daytona.snapshot.get("clientops-computer-1").state is SnapshotState.BUILD_FAILED


def test_a_local_file_missing_at_upload_time_fails_like_the_sdk(tmp_path):
    image = image_with_a_file(tmp_path)
    (tmp_path / "server.ts").unlink()
    with pytest.raises(DaytonaError) as caught:
        FakeDaytona().snapshot.create(CreateSnapshotParams(name="clientops-computer-1", image=image))
    assert str(caught.value) == f"Failed to create snapshot: Path does not exist: {tmp_path / 'server.ts'}"


def test_a_snapshot_from_a_registry_image_keeps_the_image_name():
    snapshot = FakeDaytona().snapshot.create(CreateSnapshotParams(name="plain", image="debian:trixie-20261104-slim"))
    assert snapshot.image_name == "debian:trixie-20261104-slim"
    assert snapshot.build_info is None


def test_activate_wakes_an_inactive_snapshot_by_name_or_object(tmp_path):
    daytona = FakeDaytona()
    created = daytona.snapshot.create(CreateSnapshotParams(name="clientops-computer-1", image=image_with_a_file(tmp_path)))
    daytona.snapshot._set_state("clientops-computer-1", SnapshotState.INACTIVE)
    assert daytona.snapshot.get("clientops-computer-1").state is SnapshotState.INACTIVE

    woken = daytona.snapshot.activate("clientops-computer-1")

    assert woken.state is SnapshotState.ACTIVE
    daytona.snapshot._set_state("clientops-computer-1", SnapshotState.INACTIVE)
    assert daytona.snapshot.activate(created).state is SnapshotState.ACTIVE
    assert [call.method for call in daytona.calls][-2:] == ["snapshot.activate", "snapshot.activate"]
