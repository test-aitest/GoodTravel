import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class RoleRow:
    web: str
    atspi: tuple[str, ...]
    ax: tuple[str, ...]


ROLE_ROWS: Final = (
    RoleRow("button", ("push button", "button", "toggle button"), ("AXButton", "AXMenuButton")),
    RoleRow("link", ("link",), ("AXLink",)),
    RoleRow("textbox", ("entry", "text", "password text"), ("AXTextField", "AXTextArea", "AXTextField/AXSecureTextField")),
    RoleRow("searchbox", (), ("AXTextField/AXSearchField",)),
    RoleRow("checkbox", ("check box",), ("AXCheckBox",)),
    RoleRow("switch", (), ("AXCheckBox/AXSwitch",)),
    RoleRow("radio", ("radio button",), ("AXRadioButton",)),
    RoleRow("combobox", ("combo box",), ("AXComboBox", "AXPopUpButton")),
    RoleRow("listbox", ("list box", "list"), ("AXList",)),
    RoleRow("option", ("list item",), ()),
    RoleRow("menu", ("menu",), ("AXMenu",)),
    RoleRow("menubar", ("menu bar",), ("AXMenuBar",)),
    RoleRow("menuitem", ("menu item",), ("AXMenuItem", "AXMenuBarItem")),
    RoleRow("menuitemcheckbox", ("check menu item",), ()),
    RoleRow("menuitemradio", ("radio menu item",), ()),
    RoleRow("tab", ("page tab",), ("AXRadioButton/AXTabButton",)),
    RoleRow("tablist", ("page tab list",), ("AXTabGroup",)),
    RoleRow("slider", ("slider",), ("AXSlider",)),
    RoleRow("spinbutton", ("spin button",), ("AXIncrementor",)),
    RoleRow("scrollbar", ("scroll bar",), ("AXScrollBar",)),
    RoleRow("heading", ("heading",), ("AXHeading",)),
    RoleRow("img", ("image", "icon"), ("AXImage",)),
    RoleRow("cell", ("table cell",), ("AXCell",)),
    RoleRow("row", ("table row",), ("AXRow",)),
    RoleRow("tree", ("tree", "tree table"), ("AXOutline",)),
    RoleRow("treeitem", ("tree item",), ("AXRow/AXOutlineRow",)),
    RoleRow("status", ("status bar",), ()),
    RoleRow("form", ("form",), ()),
    RoleRow("dialog", ("dialog", "alert", "file chooser"), ("AXSheet", "AXWindow/AXDialog", "AXWindow/AXSystemDialog")),
    RoleRow("window", ("frame", "window"), ("AXWindow",)),
)

CONTAINER_ROLES: Final = frozenset({"form", "dialog", "window"})

SEPARATORS = re.compile(r"[\s_-]+")


def atspi_key(role: str) -> str:
    return SEPARATORS.sub(" ", role).strip().lower()


ATSPI_TO_WEB: Final = {atspi_key(name): row.web for row in ROLE_ROWS for name in row.atspi}
AX_TO_WEB: Final = {key: row.web for row in ROLE_ROWS for key in row.ax}


def web_role_of_atspi(role: str | None) -> str | None:
    return ATSPI_TO_WEB.get(atspi_key(role)) if role else None


def web_role_of_ax(role: str | None, subrole: str | None = None) -> str | None:
    if not role:
        return None
    if subrole and f"{role}/{subrole}" in AX_TO_WEB:
        return AX_TO_WEB[f"{role}/{subrole}"]
    return AX_TO_WEB.get(role)
