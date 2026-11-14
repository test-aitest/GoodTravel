import pytest

from clientops.elements import (
    NOT_FOUND,
    NOT_UNIQUE,
    TOO_BIG,
    CANNOT,
    ElementError,
    ElementOps,
    Located,
    NodeView,
    flatten,
    hint_of,
    locate,
    refuse_placeholder,
)
from clientops.procedure import Hint

SAVE = Located("1016", "button", "Save changes", "/inventory")
SAVE_OUTSIDE = Located("7", "button", "Save changes", None)
NEXT = Located("31", "link", "Next", None)


def test_one_element_matches_by_role_name_and_form():
    assert locate([SAVE, SAVE_OUTSIDE, NEXT], Hint("button", "Save changes", "/inventory"), "find") == SAVE


def test_a_form_of_none_means_outside_any_container_like_on_the_web():
    assert locate([SAVE, SAVE_OUTSIDE, NEXT], Hint("button", "Save changes", None), "find") == SAVE_OUTSIDE


def test_names_match_after_collapsing_spaces_but_roles_and_forms_must_be_equal():
    spaced_out = Located("9", "button", "  Save \n changes ", "/inventory")
    assert locate([spaced_out], Hint("button", "Save changes", "/inventory"), "find") == spaced_out
    with pytest.raises(ElementError):
        locate([spaced_out], Hint("link", "Save changes", "/inventory"), "find")
    with pytest.raises(ElementError):
        locate([spaced_out], Hint("button", "Save changes", "/inventory/"), "find")
    with pytest.raises(ElementError):
        locate([spaced_out], Hint("button", "save changes", "/inventory"), "find")


def test_no_match_is_not_found_with_a_count_of_zero_and_says_what_was_looked_for():
    with pytest.raises(ElementError) as caught:
        locate([SAVE, NEXT], Hint("button", "Update inventory", "/inventory"), "press")
    error = caught.value
    assert (error.action, error.code, error.count) == ("press", NOT_FOUND, 0)
    assert str(error) == 'No button named "Update inventory" in /inventory'


def test_two_matches_are_not_unique_with_the_count():
    twin = Located("2000", "button", "Save changes", "/inventory")
    with pytest.raises(ElementError) as caught:
        locate([SAVE, twin], Hint("button", "Save changes", "/inventory"), "find")
    assert (caught.value.code, caught.value.count) == (NOT_UNIQUE, 2)
    assert str(caught.value) == '2 buttons named "Save changes" in /inventory'


def test_a_hint_without_a_form_is_described_as_outside_any_container():
    with pytest.raises(ElementError) as caught:
        locate([], Hint("link", "Next", None), "find")
    assert str(caught.value) == 'No link named "Next" outside any form or window'


def test_a_located_element_gives_back_the_hint_that_finds_it():
    assert hint_of(SAVE) == Hint("button", "Save changes", "/inventory")
    assert hint_of(SAVE).to_json() == {"role": "button", "name": "Save changes", "form": "/inventory"}


TREE = {
    "kind": ("frame", "Kumo inventory"),
    "children": [
        {"kind": ("group", ""), "children": [{"kind": ("button", "OK"), "children": []}]},
        {
            "kind": ("dialog", "Confirm"),
            "children": [{"kind": ("button", "OK"), "children": []}, {"kind": ("text", "Really?"), "children": []}],
        },
        {"kind": ("dialog", ""), "children": [{"kind": ("button", "Cancel"), "children": []}]},
    ],
}
ROLES = {"frame": "window", "dialog": "dialog", "button": "button", "group": None, "text": None}


def view(node) -> NodeView:
    kind, name = node["kind"]
    return NodeView(ROLES[kind], name, f"{kind}:{name}")


def test_flatten_gives_each_element_its_nearest_named_container_in_document_order():
    listed = [located for located, _ in flatten(TREE, view, lambda node: node["children"], action="find")]
    assert listed == [
        Located("frame:Kumo inventory", "window", "Kumo inventory", None),
        Located("button:OK", "button", "OK", "Kumo inventory"),
        Located("dialog:Confirm", "dialog", "Confirm", "Kumo inventory"),
        Located("button:OK", "button", "OK", "Confirm"),
        Located("dialog:", "dialog", "", "Kumo inventory"),
        Located("button:Cancel", "button", "Cancel", "Kumo inventory"),
    ]


def test_flatten_keeps_the_native_node_next_to_each_element():
    pairs = flatten(TREE, view, lambda node: node["children"], action="find")
    assert all(view(node).handle == located.handle for located, node in pairs)


def test_flatten_stops_at_the_limit_instead_of_reading_a_huge_tree():
    with pytest.raises(ElementError) as caught:
        flatten(TREE, view, lambda node: node["children"], action="press", limit=4)
    assert (caught.value.action, caught.value.code) == ("press", TOO_BIG)


def test_flatten_spaces_names_like_the_runner_does():
    tree = {"kind": ("button", " Save\n\tchanges "), "children": []}
    [(located, _)] = flatten(tree, view, lambda node: node["children"], action="find")
    assert located.name == "Save changes"


@pytest.mark.parametrize("value", ["$LP_STORE_PASSWORD", "user $LP_X_1 here"])
def test_vault_placeholders_cannot_be_typed_into_a_desktop_app(value):
    with pytest.raises(ElementError) as caught:
        refuse_placeholder(value, "set-value")
    assert caught.value.code == CANNOT
    assert "inside the client's computer" in str(caught.value)


@pytest.mark.parametrize("value", ["6", "LP_STORE_PASSWORD", "$5.00", ""])
def test_plain_values_pass(value):
    refuse_placeholder(value, "set-value")


def test_the_common_shape_is_four_operations_and_a_surface_name():
    class Minimal:
        surface = "test"

        def find(self, hint):
            return SAVE

        def press(self, hint):
            return SAVE

        def set_value(self, hint, value):
            return SAVE

        def read_value(self, hint):
            return None

    assert isinstance(Minimal(), ElementOps)
    assert not isinstance(object(), ElementOps)
