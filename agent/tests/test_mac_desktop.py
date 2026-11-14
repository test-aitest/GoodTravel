import sys

import pytest

from ax_fakes import FakeAX, application, button, group, sheet, static_text, text_field, window
from clientops.elements import CANNOT, NOT_FOUND, NOT_UNIQUE, TOO_BIG, UNAVAILABLE, ElementError, ElementOps, Located, hint_of
from clientops.mac_desktop import (
    AX_API_DISABLED,
    AX_ACTION_UNSUPPORTED,
    AX_ATTRIBUTE_UNSUPPORTED,
    AX_CANNOT_COMPLETE,
    AX_INVALID_ELEMENT,
    AX_NO_VALUE,
    PERMISSION_PATH,
    AXFailure,
    MacDesktop,
    value_text,
)
from clientops.procedure import Hint

PID = 4321
TITLE = "Order Entry"
QUANTITY = Hint("textbox", "Quantity", TITLE)
ADD_ONE = Hint("button", "Add one", TITLE)


def order_entry() -> tuple[FakeAX, dict]:
    parts = {
        "quantity": text_field("Quantity", "1"),
        "password": text_field("Password", "", subrole="AXSecureTextField"),
        "add_one": button("Add one"),
        "status": static_text("Ready"),
        "sheet_ok": button("OK"),
        "other_ok": button("OK"),
    }
    app = application(
        window(
            TITLE,
            group(parts["quantity"], parts["password"]),
            parts["add_one"],
            parts["status"],
            sheet(parts["sheet_ok"]),
        ),
        window("Settings", parts["other_ok"]),
    )
    return FakeAX({PID: app}), parts


def test_the_macos_desktop_has_the_common_shape():
    ax, _ = order_entry()
    desktop = MacDesktop(PID, ax)
    assert isinstance(desktop, ElementOps)
    assert desktop.surface == "macos"


@pytest.mark.parametrize("pid", [0, -1, True, "4321", 1.5])
def test_only_one_named_process_is_ever_read_never_the_whole_system(pid):
    ax, _ = order_entry()
    with pytest.raises(ValueError):
        MacDesktop(pid, ax)


def test_without_accessibility_permission_nothing_is_read_and_the_user_is_told_where_to_allow_it():
    ax, _ = order_entry()
    ax.is_trusted = False
    with pytest.raises(ElementError) as caught:
        MacDesktop(PID, ax).find(ADD_ONE)
    error = caught.value
    assert (error.action, error.code) == ("find", UNAVAILABLE)
    assert PERMISSION_PATH == "System Settings > Privacy & Security > Accessibility"
    assert PERMISSION_PATH in str(error)
    assert "never changes this setting" in str(error)
    assert ax.reads == 0


def test_elements_take_their_names_from_the_title_or_the_label_and_their_window_as_container():
    ax, _ = order_entry()
    listed = [located for located, _ in MacDesktop(PID, ax).elements()]
    assert [(located.role, located.name, located.form) for located in listed] == [
        ("window", TITLE, None),
        ("textbox", "Quantity", TITLE),
        ("textbox", "Password", TITLE),
        ("button", "Add one", TITLE),
        ("dialog", "", TITLE),
        ("button", "OK", TITLE),
        ("window", "Settings", None),
        ("button", "OK", "Settings"),
    ]
    assert len({located.handle for located in listed}) == len(listed)


def test_the_window_tells_two_buttons_with_one_name_apart():
    ax, parts = order_entry()
    desktop = MacDesktop(PID, ax)
    desktop.press(Hint("button", "OK", "Settings"))
    assert ax.performed == [(parts["other_ok"], "AXPress")]


def test_two_matches_are_not_unique_and_none_is_not_found():
    ax, parts = order_entry()
    parts["sheet_ok"].attributes["AXTitle"] = "Add one"
    desktop = MacDesktop(PID, ax)
    with pytest.raises(ElementError) as caught:
        desktop.press(ADD_ONE)
    assert (caught.value.code, caught.value.count) == (NOT_UNIQUE, 2)
    with pytest.raises(ElementError) as missing:
        desktop.press(Hint("button", "Remove", TITLE))
    assert (missing.value.code, missing.value.count) == (NOT_FOUND, 0)
    assert ax.performed == []


def test_every_listed_element_is_found_again_by_its_own_hint():
    ax, _ = order_entry()
    desktop = MacDesktop(PID, ax)
    for located, _ in desktop.elements():
        if located.name:
            assert desktop.find(hint_of(located)) == located


def test_press_performs_axpress_on_the_one_matching_element():
    ax, parts = order_entry()
    located = MacDesktop(PID, ax).press(ADD_ONE)
    assert isinstance(located, Located) and located.name == "Add one"
    assert ax.performed == [(parts["add_one"], "AXPress")]


def test_an_element_without_axpress_cannot_be_pressed():
    ax, _ = order_entry()
    with pytest.raises(ElementError) as caught:
        MacDesktop(PID, ax).press(QUANTITY)
    assert (caught.value.action, caught.value.code) == ("press", CANNOT)
    assert ax.performed == []


def test_set_value_writes_axvalue_and_read_value_reads_it_back():
    ax, parts = order_entry()
    desktop = MacDesktop(PID, ax)
    desktop.set_value(QUANTITY, "4")
    assert ax.written == [(parts["quantity"], "AXValue", "4")]
    assert desktop.read_value(QUANTITY) == "4"


def test_a_value_that_cannot_be_set_is_refused_before_writing():
    ax, _ = order_entry()
    with pytest.raises(ElementError) as caught:
        MacDesktop(PID, ax).set_value(ADD_ONE, "4")
    assert (caught.value.action, caught.value.code) == ("set-value", CANNOT)
    assert ax.written == []


def test_vault_placeholders_are_never_typed_into_a_mac_app():
    ax, _ = order_entry()
    with pytest.raises(ElementError) as caught:
        MacDesktop(PID, ax).set_value(Hint("textbox", "Password", TITLE), "$LP_STORE_PASSWORD")
    assert caught.value.code == CANNOT
    assert ax.written == []


@pytest.mark.parametrize(
    ("value", "text"),
    [("5", "5"), (5, "5"), (5.0, "5"), (2.5, "2.5"), (True, "true"), (False, "false"), (None, None), ("", "")],
)
def test_values_read_as_text(value, text):
    assert value_text(value) == text


def test_numbers_in_axvalue_read_as_text():
    ax, parts = order_entry()
    parts["quantity"].attributes["AXValue"] = 7
    assert MacDesktop(PID, ax).read_value(QUANTITY) == "7"


@pytest.mark.parametrize(
    ("failure", "code"),
    [(AX_API_DISABLED, UNAVAILABLE), (AX_CANNOT_COMPLETE, UNAVAILABLE), (AX_INVALID_ELEMENT, NOT_FOUND), (AX_ACTION_UNSUPPORTED, CANNOT), (-25200, "failed")],
)
def test_ax_errors_while_pressing_become_common_codes(failure, code):
    ax, _ = order_entry()
    ax.failures["perform"] = failure
    with pytest.raises(ElementError) as caught:
        MacDesktop(PID, ax).press(ADD_ONE)
    assert (caught.value.action, caught.value.code) == ("press", code)
    assert isinstance(caught.value.__cause__, AXFailure)


def test_an_app_that_is_gone_is_unavailable():
    ax, _ = order_entry()
    with pytest.raises(ElementError) as caught:
        MacDesktop(9999, ax).find(ADD_ONE)
    assert caught.value.code == UNAVAILABLE
    assert "9999" in str(caught.value)


def test_a_huge_app_is_too_big_to_read():
    ax, _ = order_entry()
    ax.apps[PID] = application(window(TITLE, *[button(f"Item {number}") for number in range(30)]))
    with pytest.raises(ElementError) as caught:
        MacDesktop(PID, ax, limit=20).find(ADD_ONE)
    assert caught.value.code == TOO_BIG


@pytest.mark.skipif(sys.platform != "darwin", reason="the AX constants come from macOS")
def test_the_error_numbers_are_apples():
    import ApplicationServices

    assert AX_API_DISABLED == ApplicationServices.kAXErrorAPIDisabled
    assert AX_CANNOT_COMPLETE == ApplicationServices.kAXErrorCannotComplete
    assert AX_INVALID_ELEMENT == ApplicationServices.kAXErrorInvalidUIElement
    assert AX_ACTION_UNSUPPORTED == ApplicationServices.kAXErrorActionUnsupported
    assert AX_NO_VALUE == ApplicationServices.kAXErrorNoValue
    assert AX_ATTRIBUTE_UNSUPPORTED == ApplicationServices.kAXErrorAttributeUnsupported
