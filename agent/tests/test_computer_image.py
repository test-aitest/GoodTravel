import json
import os
import re
from pathlib import Path

import pytest
from daytona_api_client import SnapshotState

import clientops.computer_image as computer_image_module
from clientops.computer_image import (
    IMAGE_SPEC_PATH,
    PROJECT_ROOT,
    SnapshotError,
    computer_image,
    ensure_snapshot,
    load_image_spec,
)
from clientops.externals.fake_daytona import FakeDaytona

SPEC = load_image_spec()
SITES = PROJECT_ROOT / "sandbox" / "sites"


def dockerfile_lines() -> list[str]:
    return computer_image(SPEC).dockerfile().splitlines()


def copies() -> list[tuple[str, str]]:
    return [tuple(json.loads(line.removeprefix("COPY "))) for line in dockerfile_lines() if line.startswith("COPY ")]


def relative_imports(path: Path) -> set[str]:
    found = set()
    for name in re.findall(r'from "\./([\w-]+)"', path.read_text()):
        found.add(f"{name}.ts")
        found |= relative_imports(path.parent / f"{name}.ts")
    return found


def test_the_image_definition_lives_in_sandbox_image():
    assert IMAGE_SPEC_PATH == PROJECT_ROOT / "sandbox" / "image" / "image.json"


def test_the_snapshot_name_is_the_temporary_clientops_computer_1():
    assert SPEC.snapshot == "clientops-computer-3"


def test_lightpanda_is_the_1_0_4_linux_x86_64_release_with_its_sha256():
    assert SPEC.lightpanda.version == "1.0.4"
    assert SPEC.lightpanda.url == "https://github.com/lightpanda-io/browser/releases/download/1.0.4/lightpanda-x86_64-linux"
    assert SPEC.lightpanda.sha256 == "7d22651129901203abdb95c64f49069c49e88f5211f4d8c4c6828c123c4adab4"


def test_the_lightpanda_pin_is_the_one_documented_for_the_project():
    doc = (PROJECT_ROOT / "docs" / "external" / "lightpanda.md").read_text()
    assert SPEC.lightpanda.url in doc
    assert SPEC.lightpanda.sha256 in doc


def test_bun_is_the_version_the_project_uses_as_the_linux_x64_baseline_build():
    package = json.loads((PROJECT_ROOT / "package.json").read_text())
    assert package["packageManager"] == f"bun@{SPEC.bun.version}"
    assert SPEC.bun.url == (
        f"https://github.com/oven-sh/bun/releases/download/bun-v{SPEC.bun.version}/bun-linux-x64-baseline.zip"
    )
    assert SPEC.bun.sha256 == "e0446eb55995772eb9894bebae9a6be368d985a8b9271ee124486f9946eedd76"
    assert SPEC.bun.binary_in_zip == "bun-linux-x64-baseline/bun"


def test_the_base_image_is_a_dated_debian_slim_pinned_by_digest():
    assert re.fullmatch(r"debian:trixie-\d{8}-slim@sha256:[0-9a-f]{64}", SPEC.base)


def test_the_computer_size_is_the_smallest_daytona_default():
    assert (SPEC.resources.cpu, SPEC.resources.memory, SPEC.resources.disk) == (1, 1, 3)


def test_the_image_starts_from_the_pinned_base():
    assert dockerfile_lines()[0] == f"FROM {SPEC.base}"


def test_each_download_is_checked_against_its_sha256_before_it_is_used():
    lines = dockerfile_lines()
    lightpanda = next(line for line in lines if SPEC.lightpanda.url in line)
    bun = next(line for line in lines if SPEC.bun.url in line)

    assert f"echo '{SPEC.lightpanda.sha256}  /usr/local/bin/lightpanda' | sha256sum -c -" in lightpanda
    assert lightpanda.index("sha256sum -c -") < lightpanda.index("chmod 755 /usr/local/bin/lightpanda")
    assert f"echo '{SPEC.bun.sha256}  /tmp/bun.zip' | sha256sum -c -" in bun
    assert bun.index("sha256sum -c -") < bun.index("unzip")
    assert all(" && " in line for line in (lightpanda, bun))


def test_the_build_fails_when_the_installed_versions_differ_from_the_pins():
    lines = dockerfile_lines()
    assert f'RUN test "$(lightpanda version)" = "{SPEC.lightpanda.version}"' in lines
    assert f'RUN test "$(bun --version)" = "{SPEC.bun.version}"' in lines


def test_curl_is_in_the_image_for_the_checks_run_inside_the_computer():
    install = next(line for line in dockerfile_lines() if "apt-get install" in line)
    for package in ("ca-certificates", "curl", "unzip"):
        assert f" {package}" in install


def test_lightpanda_telemetry_is_turned_off_in_the_image():
    assert "ENV LIGHTPANDA_DISABLE_TELEMETRY=true" in dockerfile_lines()


def test_the_demo_sites_are_copied_with_every_file_the_server_imports():
    needed = {"server.ts"} | relative_imports(SITES / "server.ts")
    copied = {target for _, target in copies()}
    assert {f"/opt/clientops/sites/{name}" for name in needed} <= copied


def test_no_test_files_go_into_the_image():
    assert not any(".test." in source for source, _ in copies())


def test_copied_files_are_named_relative_to_the_project_so_no_local_folder_leaks():
    text = computer_image(SPEC).dockerfile()
    assert str(PROJECT_ROOT).lstrip("/") not in text
    for source, _ in copies():
        assert (PROJECT_ROOT / source).is_file(), source
        assert not source.startswith(("/", ".."))


def test_the_computer_runs_as_the_daytona_user_and_stays_up():
    lines = dockerfile_lines()
    user_line = lines.index("USER daytona")
    assert all(not line.startswith("RUN ") for line in lines[user_line:])
    assert any("useradd" in line and "daytona" in line for line in lines[:user_line])
    assert lines[-1] == 'ENTRYPOINT ["sleep", "infinity"]'


def test_the_image_can_be_built_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert computer_image(SPEC).dockerfile() == computer_image(SPEC).dockerfile()
    assert Path.cwd() == tmp_path


def test_ensure_snapshot_builds_the_image_once_with_the_spec(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    daytona = FakeDaytona()

    snapshot = ensure_snapshot(daytona)

    [call] = [call for call in daytona.calls if call.method == "snapshot.create"]
    params = call.arguments["params"]
    assert snapshot.name == params.name == "clientops-computer-3"
    assert snapshot.state is SnapshotState.ACTIVE
    assert (params.resources.cpu, params.resources.memory, params.resources.disk) == (1, 1, 3)
    assert call.arguments["dockerfile"] == computer_image(SPEC).dockerfile()
    assert sorted(call.arguments["context_paths"]) == sorted(source for source, _ in copies())
    assert Path.cwd() == tmp_path


def test_ensure_snapshot_reuses_an_active_snapshot(tmp_path):
    daytona = FakeDaytona()
    first = ensure_snapshot(daytona)
    second = ensure_snapshot(daytona)
    assert second.id == first.id
    assert len([call for call in daytona.calls if call.method == "snapshot.create"]) == 1


def test_ensure_snapshot_wakes_a_snapshot_that_went_inactive():
    daytona = FakeDaytona()
    made = ensure_snapshot(daytona)
    daytona.snapshot._set_state(made.name, SnapshotState.INACTIVE)

    woken = ensure_snapshot(daytona)

    assert woken.state is SnapshotState.ACTIVE
    assert [call.method for call in daytona.calls][-1] == "snapshot.activate"


def test_a_failed_build_is_a_clear_error_naming_the_snapshot_and_the_reason():
    daytona = FakeDaytona()
    daytona.snapshot.fail_next_build("sha256sum: WARNING: 1 computed checksum did NOT match")

    with pytest.raises(SnapshotError) as caught:
        ensure_snapshot(daytona)

    message = str(caught.value)
    assert "clientops-computer-3" in message
    assert "checksum did NOT match" in message


def test_a_snapshot_left_failed_by_an_earlier_build_is_reported_not_rebuilt_over():
    daytona = FakeDaytona()
    daytona.snapshot.fail_next_build("out of disk")
    with pytest.raises(SnapshotError):
        ensure_snapshot(daytona)

    with pytest.raises(SnapshotError) as caught:
        ensure_snapshot(daytona)

    assert "build_failed" in str(caught.value)
    assert "sandbox/image/image.json" in str(caught.value)
    assert len([call for call in daytona.calls if call.method == "snapshot.create"]) == 1


def test_the_command_line_entry_builds_the_snapshot_and_reports_it(monkeypatch, capsys):
    daytona = FakeDaytona()
    monkeypatch.setattr(computer_image_module, "daytona_client", lambda: daytona)

    computer_image_module.main()

    output = capsys.readouterr().out
    assert "Snapshot clientops-computer-3 is active" in output
    assert [call.method for call in daytona.calls] == ["snapshot.create"]


def test_the_working_directory_is_left_as_it_was(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensure_snapshot(FakeDaytona())
    assert os.getcwd() == str(tmp_path)


def test_the_snapshot_is_built_with_bun_run_computer_snapshot():
    package = json.loads((PROJECT_ROOT / "package.json").read_text())
    assert package["scripts"]["computer:snapshot"] == "uv run --directory agent python -m clientops.computer_image"


def test_the_command_line_entry_stops_with_the_reason_instead_of_a_traceback(monkeypatch):
    daytona = FakeDaytona()
    daytona.snapshot.fail_next_build("out of disk")
    monkeypatch.setattr(computer_image_module, "daytona_client", lambda: daytona)

    with pytest.raises(SystemExit) as caught:
        computer_image_module.main()

    assert "out of disk" in str(caught.value.code)


def test_the_command_line_entry_without_a_key_says_which_key_is_missing(monkeypatch):
    for name in ("CLIENTOPS_EXTERNALS", "DAYTONA_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as caught:
        computer_image_module.main()

    assert "DAYTONA_API_KEY" in str(caught.value.code)
