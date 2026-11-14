import inspect
import json
from copy import deepcopy

import daytona
import pytest
from daytona import CreateSandboxFromSnapshotParams

from clientops.computer_image import load_image_spec
from clientops.computers import DaytonaComputer, network_policy
from clientops.elements import CANNOT, NOT_FOUND, NOT_UNIQUE, TOO_BIG, UNAVAILABLE, ElementError, ElementOps, Located, hint_of
from clientops.externals.fake_daytona import FakeDaytona
from clientops.externals.fake_daytona_desktop import ACCESSIBILITY_SAMPLES_DIR
from clientops.linux_desktop import LinuxDesktop, open_linux_desktop
from clientops.procedure import Hint

DIALOG = "Kumo inventory"
OK = Hint("button", "OK", DIALOG)
CANCEL = Hint("button", "Cancel", DIALOG)
GUJI_200 = Hint("textbox", "Available for Ethiopia Guji 200g", DIALOG)
OK_ID = ":1.31:/org/a11y/atspi/accessible/6"
ENTRY_ID = ":1.31:/org/a11y/atspi/accessible/4"
ZENITY_PID = 4242


def sample_tree() -> dict:
    return json.loads((ACCESSIBILITY_SAMPLES_DIR / "tree_all.json").read_text())["root"]


def kumo_computer(cloud: FakeDaytona) -> DaytonaComputer:
    sandbox = cloud.create(CreateSandboxFromSnapshotParams(snapshot="clientops-computer-1", labels={"clientops-client": "kumo"}))
    return DaytonaComputer("kumo", sandbox, network_policy("kumo", 1), load_image_spec(), created=True, startup_seconds=0.0)


@pytest.fixture
def cloud() -> FakeDaytona:
    return FakeDaytona()


@pytest.fixture
def computer(cloud) -> DaytonaComputer:
    return kumo_computer(cloud)


@pytest.fixture
def desktop(computer) -> LinuxDesktop:
    return open_linux_desktop(computer)


def calls(cloud: FakeDaytona, prefix: str = "") -> list[tuple[str, dict]]:
    return [(call.method, call.arguments) for call in cloud.calls if call.method.startswith(prefix)]


def test_the_fake_sandbox_offers_computer_use_as_a_property_like_the_sdk():
    from clientops.externals.fake_daytona import FakeSandbox

    assert isinstance(inspect.getattr_static(daytona.Sandbox, "computer_use"), property)
    assert isinstance(inspect.getattr_static(FakeSandbox, "computer_use"), property)


def test_the_linux_desktop_has_the_common_shape(desktop):
    assert isinstance(desktop, ElementOps)
    assert desktop.surface == "linux"


def test_opening_the_desktop_starts_computer_use_on_that_clients_computer_once(cloud, computer, desktop):
    assert calls(cloud, "computer_use") == [("computer_use.start", {})]
    assert [call.sandbox_id for call in cloud.calls if call.method == "computer_use.start"] == [computer.id]


def test_without_computer_use_started_the_desktop_is_unavailable_and_says_why(cloud, computer):
    early = LinuxDesktop(computer.sandbox.computer_use.accessibility, client_id="kumo")
    with pytest.raises(ElementError) as caught:
        early.find(OK)
    error = caught.value
    assert (error.action, error.code) == ("find", UNAVAILABLE)
    assert "Kumo's computer" in str(error)
    assert "computer use" in str(error)
    assert calls(cloud, "accessibility") == []


def test_find_reads_the_whole_tree_once_and_names_the_dialog_as_the_container(cloud, desktop):
    assert desktop.find(OK) == Located(OK_ID, "button", "OK", DIALOG)
    assert calls(cloud, "accessibility") == [("accessibility.get_tree", {"scope": "all", "pid": None, "max_depth": None})]


def test_with_a_pid_only_that_apps_tree_is_read(cloud, computer):
    cloud.desktop(computer.id).pids[ZENITY_PID] = "zenity"
    desktop = open_linux_desktop(computer, pid=ZENITY_PID)
    assert desktop.find(GUJI_200) == Located(ENTRY_ID, "textbox", "Available for Ethiopia Guji 200g", DIALOG)
    assert calls(cloud, "accessibility") == [("accessibility.get_tree", {"scope": "pid", "pid": ZENITY_PID, "max_depth": None})]


def test_the_panel_button_is_in_the_panel_window_not_in_the_dialog(desktop):
    assert desktop.find(Hint("button", "Applications", "xfce4-panel")).handle == ":1.12:/org/a11y/atspi/accessible/2"
    with pytest.raises(ElementError) as caught:
        desktop.find(Hint("button", "Applications", DIALOG))
    assert (caught.value.code, caught.value.count) == (NOT_FOUND, 0)


def test_labels_and_fillers_are_not_elements_and_unknown_hints_are_not_found(desktop):
    roles = {located.role for located, _ in desktop.elements()}
    assert roles == {"window", "button", "dialog", "textbox"}
    with pytest.raises(ElementError) as caught:
        desktop.find(Hint("button", "Save changes", DIALOG))
    assert (caught.value.code, caught.value.count) == (NOT_FOUND, 0)


def test_two_ok_buttons_in_one_dialog_are_not_unique(cloud, computer, desktop):
    tree = sample_tree()
    dialog_children = tree["children"][1]["children"][0]["children"][0]["children"]
    twin = deepcopy(dialog_children[-1])
    twin["id"] = ":1.31:/org/a11y/atspi/accessible/7"
    dialog_children.append(twin)
    cloud.desktop(computer.id).tree = tree
    with pytest.raises(ElementError) as caught:
        desktop.press(OK)
    assert (caught.value.action, caught.value.code, caught.value.count) == ("press", NOT_UNIQUE, 2)
    assert calls(cloud, "accessibility.invoke") == []


def test_every_listed_element_is_found_again_by_its_own_hint(desktop):
    for located, _ in desktop.elements():
        assert desktop.find(hint_of(located)) == located


def test_press_runs_the_primary_action_of_the_one_matching_node(cloud, desktop):
    assert desktop.press(OK) == Located(OK_ID, "button", "OK", DIALOG)
    assert calls(cloud, "accessibility.invoke") == [("accessibility.invoke_node", {"node_id": OK_ID, "action": None})]


def test_a_node_with_no_actions_cannot_be_pressed_and_nothing_is_invoked(cloud, computer, desktop):
    tree = sample_tree()
    tree["children"][1]["children"][0]["children"][0]["children"][2]["actions"] = []
    cloud.desktop(computer.id).tree = tree
    with pytest.raises(ElementError) as caught:
        desktop.press(CANCEL)
    assert (caught.value.action, caught.value.code) == ("press", CANNOT)
    assert calls(cloud, "accessibility.invoke") == []


def test_set_value_writes_the_text_to_the_one_matching_node(cloud, computer, desktop):
    assert desktop.set_value(GUJI_200, "6").handle == ENTRY_ID
    assert calls(cloud, "accessibility.set") == [("accessibility.set_node_value", {"node_id": ENTRY_ID, "value": "6"})]
    assert cloud.desktop(computer.id).values == {ENTRY_ID: "6"}


def test_vault_placeholders_never_travel_through_daytona(cloud, desktop):
    with pytest.raises(ElementError) as caught:
        desktop.set_value(GUJI_200, "$LP_STORE_PASSWORD")
    assert (caught.value.action, caught.value.code) == ("set-value", CANNOT)
    assert calls(cloud, "accessibility") == []


def test_daytonas_refusal_to_set_a_value_is_a_clear_error(desktop):
    with pytest.raises(ElementError) as caught:
        desktop.set_value(OK, "6")
    assert (caught.value.action, caught.value.code) == ("set-value", "failed")
    assert "Daytona" in str(caught.value)


def test_a_node_that_went_away_before_the_press_is_not_found(cloud, computer, desktop, monkeypatch):
    fake_desktop = cloud.desktop(computer.id)
    original = fake_desktop.node

    def gone(node_id):
        return None if node_id == OK_ID else original(node_id)

    monkeypatch.setattr(fake_desktop, "node", gone)
    with pytest.raises(ElementError) as caught:
        desktop.press(OK)
    assert (caught.value.action, caught.value.code, caught.value.count) == ("press", NOT_FOUND, 0)


def test_read_value_finds_the_node_but_says_the_api_has_no_way_to_read_a_value(cloud, desktop):
    with pytest.raises(ElementError) as caught:
        desktop.read_value(GUJI_200)
    assert (caught.value.action, caught.value.code) == ("read-value", CANNOT)
    assert "no call that reads a value" in str(caught.value)
    with pytest.raises(ElementError) as missing:
        desktop.read_value(Hint("textbox", "Nothing", DIALOG))
    assert missing.value.code == NOT_FOUND


def test_a_cut_short_tree_is_too_big_to_tell_elements_apart(cloud, computer, desktop):
    cloud.desktop(computer.id).truncate = True
    with pytest.raises(ElementError) as caught:
        desktop.find(OK)
    assert (caught.value.code, caught.value.action) == (TOO_BIG, "find")


def test_a_stopped_computer_is_a_failure_not_a_silent_miss(computer, desktop):
    computer.sandbox.stop()
    with pytest.raises(ElementError) as caught:
        desktop.find(OK)
    assert caught.value.code == "failed"
