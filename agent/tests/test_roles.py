import pytest

from clientops.page import KNOWN_ROLES
from clientops.roles import CONTAINER_ROLES, ROLE_ROWS, web_role_of_atspi, web_role_of_ax


def test_every_common_role_is_a_web_role_except_the_status_hint_and_the_desktop_window():
    web_roles = [row.web for row in ROLE_ROWS]
    assert len(web_roles) == len(set(web_roles))
    assert set(web_roles) - KNOWN_ROLES == {"status", "window"}


def test_no_atspi_role_or_ax_role_belongs_to_two_web_roles():
    atspi = [name for row in ROLE_ROWS for name in row.atspi]
    ax = [key for row in ROLE_ROWS for key in row.ax]
    assert len(atspi) == len(set(atspi))
    assert len(ax) == len(set(ax))


@pytest.mark.parametrize("row", ROLE_ROWS, ids=lambda row: row.web)
def test_each_desktop_role_reads_back_as_its_web_role(row):
    for name in row.atspi:
        assert web_role_of_atspi(name) == row.web
    for key in row.ax:
        role, _, subrole = key.partition("/")
        assert web_role_of_ax(role, subrole or None) == row.web


@pytest.mark.parametrize(
    ("atspi", "web"),
    [
        ("push button", "button"),
        ("PUSH_BUTTON", "button"),
        ("push-button", "button"),
        ("  Push   Button ", "button"),
        ("button", "button"),
        ("toggle button", "button"),
        ("entry", "textbox"),
        ("password text", "textbox"),
        ("check box", "checkbox"),
        ("radio button", "radio"),
        ("combo box", "combobox"),
        ("page tab", "tab"),
        ("spin button", "spinbutton"),
        ("link", "link"),
        ("menu item", "menuitem"),
        ("dialog", "dialog"),
        ("alert", "dialog"),
        ("frame", "window"),
        ("form", "form"),
        ("status bar", "status"),
    ],
)
def test_atspi_role_names_map_to_web_roles_whatever_their_spelling(atspi, web):
    assert web_role_of_atspi(atspi) == web


@pytest.mark.parametrize("atspi", ["label", "filler", "panel", "application", "desktop frame", "terminal", "", None])
def test_atspi_roles_with_no_web_role_are_left_out(atspi):
    assert web_role_of_atspi(atspi) is None


@pytest.mark.parametrize(
    ("role", "subrole", "web"),
    [
        ("AXButton", None, "button"),
        ("AXButton", "AXCloseButton", "button"),
        ("AXTextField", None, "textbox"),
        ("AXTextField", "AXSecureTextField", "textbox"),
        ("AXTextField", "AXSearchField", "searchbox"),
        ("AXTextArea", None, "textbox"),
        ("AXCheckBox", None, "checkbox"),
        ("AXCheckBox", "AXSwitch", "switch"),
        ("AXRadioButton", None, "radio"),
        ("AXRadioButton", "AXTabButton", "tab"),
        ("AXPopUpButton", None, "combobox"),
        ("AXIncrementor", None, "spinbutton"),
        ("AXLink", None, "link"),
        ("AXMenuItem", None, "menuitem"),
        ("AXRow", None, "row"),
        ("AXRow", "AXOutlineRow", "treeitem"),
        ("AXWindow", "AXStandardWindow", "window"),
        ("AXWindow", None, "window"),
        ("AXWindow", "AXDialog", "dialog"),
        ("AXSheet", None, "dialog"),
    ],
)
def test_ax_roles_and_subroles_map_to_web_roles(role, subrole, web):
    assert web_role_of_ax(role, subrole) == web


@pytest.mark.parametrize(
    ("role", "subrole"),
    [("AXGroup", None), ("AXStaticText", None), ("AXApplication", None), ("AXUnknown", "AXWhatever"), (None, None), ("", None)],
)
def test_ax_roles_with_no_web_role_are_left_out(role, subrole):
    assert web_role_of_ax(role, subrole) is None


def test_forms_dialogs_and_windows_are_the_containers_a_hint_names():
    assert CONTAINER_ROLES == frozenset({"form", "dialog", "window"})
    assert CONTAINER_ROLES <= {row.web for row in ROLE_ROWS}
