import re

from clientops.computer_image import PROJECT_ROOT, load_image_spec
from clientops.page import RUNNER_COMMAND

RUNNER = PROJECT_ROOT / "sandbox" / "runner"
LOCAL_IMPORT = re.compile(r'from "\./([\w.]+)"')


def runner_copy():
    return next(copy for copy in load_image_spec().copies if copy.source == "sandbox/runner")


def imported_by(name: str, seen: set[str]) -> set[str]:
    if name in seen:
        return seen
    seen.add(name)
    for module in LOCAL_IMPORT.findall((RUNNER / name).read_text()):
        imported_by(f"{module}.ts", seen)
    return seen


def test_the_image_ships_every_runner_module_main_needs_and_nothing_else():
    assert set(runner_copy().files) == imported_by("main.ts", set())


def test_the_image_ships_no_tests_or_recordings():
    assert not any(".test." in name or "fixtures" in name for name in runner_copy().files)


def test_the_agent_runs_the_runner_where_the_image_puts_it():
    assert RUNNER_COMMAND == f"bun {runner_copy().target}/main.ts"
