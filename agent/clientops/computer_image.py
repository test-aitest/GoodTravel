import json
from collections.abc import Callable
from contextlib import chdir
from dataclasses import dataclass
from pathlib import Path

from daytona import CreateSnapshotParams, Daytona, DaytonaError, DaytonaNotFoundError, Image, Resources
from daytona.common.snapshot import Snapshot
from daytona_api_client import SnapshotState

from clientops.externals import MissingExternalKey, daytona_client
from clientops.externals.fake_daytona import FakeDaytona

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_SPEC_PATH = PROJECT_ROOT / "sandbox" / "image" / "image.json"
LIGHTPANDA_PATH = "/usr/local/bin/lightpanda"
BUN_ZIP_PATH = "/tmp/bun.zip"
BUILD_TIMEOUT_SECONDS = 30 * 60


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Download:
    version: str
    url: str
    sha256: str


@dataclass(frozen=True)
class BunDownload(Download):
    binary_in_zip: str


@dataclass(frozen=True)
class CopiedFiles:
    source: str
    files: tuple[str, ...]
    target: str

    def pairs(self) -> list[tuple[str, str]]:
        return [(f"{self.source}/{name}", f"{self.target}/{name}") for name in self.files]


@dataclass(frozen=True)
class ImageSpec:
    snapshot: str
    base: str
    resources: Resources
    user: str
    lightpanda: Download
    bun: BunDownload
    copies: tuple[CopiedFiles, ...]

    def copied_to(self, source: str) -> str:
        return next(copy.target for copy in self.copies if copy.source == source)


def load_image_spec(path: Path = IMAGE_SPEC_PATH) -> ImageSpec:
    data = json.loads(path.read_text())
    bun = data["bun"]
    return ImageSpec(
        snapshot=data["snapshot"],
        base=data["base"],
        resources=Resources(**data["resources"]),
        user=data["user"],
        lightpanda=Download(**data["lightpanda"]),
        bun=BunDownload(
            version=bun["version"], url=bun["url"], sha256=bun["sha256"], binary_in_zip=bun["binaryInZip"]
        ),
        copies=tuple(
            CopiedFiles(source=copy["from"], files=tuple(copy["files"]), target=copy["to"]) for copy in data["copies"]
        ),
    )


def computer_image(spec: ImageSpec) -> Image:
    lightpanda, bun = spec.lightpanda, spec.bun
    with chdir(PROJECT_ROOT):
        image = (
            Image.base(spec.base)
            .env({"LIGHTPANDA_DISABLE_TELEMETRY": "true"})
            .run_commands(
                "apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip"
                " && rm -rf /var/lib/apt/lists/*",
                f"curl -fsSL -o {LIGHTPANDA_PATH} {lightpanda.url}"
                f" && echo '{lightpanda.sha256}  {LIGHTPANDA_PATH}' | sha256sum -c -"
                f" && chmod 755 {LIGHTPANDA_PATH}",
                f"curl -fsSL -o {BUN_ZIP_PATH} {bun.url}"
                f" && echo '{bun.sha256}  {BUN_ZIP_PATH}' | sha256sum -c -"
                f" && unzip -j {BUN_ZIP_PATH} {bun.binary_in_zip} -d /usr/local/bin"
                f" && chmod 755 /usr/local/bin/bun && rm {BUN_ZIP_PATH}",
                f'test "$(lightpanda version)" = "{lightpanda.version}"',
                f'test "$(bun --version)" = "{bun.version}"',
                f"useradd --create-home --shell /bin/bash {spec.user}",
            )
        )
        for copy in spec.copies:
            for source, target in copy.pairs():
                image = image.add_local_file(source, target)
    return (
        image.dockerfile_commands([f"USER {spec.user}"])
        .workdir(f"/home/{spec.user}")
        .entrypoint(["sleep", "infinity"])
    )


def ensure_snapshot(
    daytona: Daytona | FakeDaytona,
    spec: ImageSpec | None = None,
    on_logs: Callable[[str], None] | None = None,
) -> Snapshot:
    spec = spec or load_image_spec()
    existing = _existing_snapshot(daytona, spec.snapshot)
    if existing is not None:
        return _usable(daytona, existing, spec)
    with chdir(PROJECT_ROOT):
        try:
            return daytona.snapshot.create(
                CreateSnapshotParams(name=spec.snapshot, image=computer_image(spec), resources=spec.resources),
                on_logs=on_logs,
                timeout=BUILD_TIMEOUT_SECONDS,
            )
        except DaytonaError as error:
            raise SnapshotError(f"Could not build the snapshot {spec.snapshot}: {error}") from error


def _existing_snapshot(daytona: Daytona | FakeDaytona, name: str) -> Snapshot | None:
    try:
        return daytona.snapshot.get(name)
    except DaytonaNotFoundError:
        return None
    except DaytonaError as error:
        raise SnapshotError(f"Could not look up the snapshot {name}: {error}") from error


def _usable(daytona: Daytona | FakeDaytona, snapshot: Snapshot, spec: ImageSpec) -> Snapshot:
    if snapshot.state is SnapshotState.ACTIVE:
        return snapshot
    if snapshot.state is SnapshotState.INACTIVE:
        try:
            return daytona.snapshot.activate(snapshot)
        except DaytonaError as error:
            raise SnapshotError(f"Could not wake the snapshot {spec.snapshot}: {error}") from error
    reason = f" ({snapshot.error_reason})" if snapshot.error_reason else ""
    raise SnapshotError(
        f"The snapshot {spec.snapshot} is {snapshot.state.value}{reason}. "
        f"Wait for it, or delete it in Daytona or give it a new name in "
        f"{IMAGE_SPEC_PATH.relative_to(PROJECT_ROOT)} and build again."
    )


def main() -> None:
    try:
        snapshot = ensure_snapshot(daytona_client(), on_logs=print)
    except (MissingExternalKey, SnapshotError) as error:
        raise SystemExit(str(error)) from error
    print(f"Snapshot {snapshot.name} is {snapshot.state.value} (id {snapshot.id}).")


if __name__ == "__main__":
    main()
