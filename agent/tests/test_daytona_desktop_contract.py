import inspect
import json

import pytest
from daytona import DaytonaA11yUnavailableError
from daytona._sync.computer_use import Accessibility, ComputerUse
from daytona.common.errors import create_daytona_error
from daytona_toolbox_api_client import AccessibilityTreeResponse, ComputerUseStartResponse, ErrorResponse

from clientops.externals.fake_daytona_desktop import ACCESSIBILITY_SAMPLES_DIR, FakeAccessibility, FakeComputerUse
from test_daytona_contract import assert_written_back_the_same

SAMPLE_MODELS = {
    "tree_all.json": AccessibilityTreeResponse,
    "computer_use_start.json": ComputerUseStartResponse,
    "error_a11y_unavailable.json": ErrorResponse,
}

SAME_ARGUMENTS = [
    (FakeComputerUse.start, ComputerUse.start),
    (FakeAccessibility.get_tree, Accessibility.get_tree),
    (FakeAccessibility.invoke_node, Accessibility.invoke_node),
    (FakeAccessibility.set_node_value, Accessibility.set_node_value),
]


def load(name: str):
    return json.loads((ACCESSIBILITY_SAMPLES_DIR / name).read_text())


def nodes(node: dict):
    yield node
    for child in node.get("children", []):
        yield from nodes(child)


def test_the_samples_sit_next_to_the_other_daytona_samples():
    assert ACCESSIBILITY_SAMPLES_DIR.parent.name == "daytona"
    assert sorted(path.name for path in ACCESSIBILITY_SAMPLES_DIR.glob("*.json")) == sorted(SAMPLE_MODELS)


@pytest.mark.parametrize("name", sorted(SAMPLE_MODELS))
def test_each_sample_is_read_by_the_sdk_model_without_unknown_or_retyped_fields(name):
    sample = load(name)
    model = SAMPLE_MODELS[name].from_dict(sample)
    assert model.additional_properties == {}
    assert_written_back_the_same(sample, json.loads(model.to_json()))


def test_no_node_in_the_tree_sample_carries_a_field_the_sdk_does_not_know():
    tree = AccessibilityTreeResponse.from_dict(load("tree_all.json"))
    pending = [tree.root]
    while pending:
        node = pending.pop()
        assert node.additional_properties == {}
        pending.extend(node.children or [])


def test_node_ids_have_the_documented_bus_name_and_object_path_shape():
    ids = [node["id"] for node in nodes(load("tree_all.json")["root"])]
    assert len(ids) == len(set(ids))
    for node_id in ids:
        bus, _, path = node_id.rpartition(":")
        assert bus.startswith(":1.") and path.startswith("/org/a11y/atspi/accessible/")


def test_the_unavailable_sample_becomes_the_sdks_a11y_unavailable_error():
    sample = load("error_a11y_unavailable.json")
    error = create_daytona_error(sample["message"], status_code=sample["statusCode"], code=sample["code"], source=sample["source"])
    assert isinstance(error, DaytonaA11yUnavailableError)
    assert error.status_code == 503


@pytest.mark.parametrize(("fake", "real"), SAME_ARGUMENTS, ids=lambda method: method.__qualname__)
def test_fake_methods_take_the_same_arguments_as_the_sdk(fake, real):
    def arguments(method):
        return [(name, parameter.kind, parameter.default) for name, parameter in inspect.signature(method).parameters.items()]

    assert arguments(fake) == arguments(real)
