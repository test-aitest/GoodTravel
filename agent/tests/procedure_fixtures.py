from clientops.diff import StockChange
from clientops.page import Element
from clientops.procedure import ProcedureDraft, ReadSteps, SignInSteps, target_for

SUPPLIER_LOGIN = (
    Element(15, "textbox", "Account ID", "/login", "", "input", "username", "text"),
    Element(18, "textbox", "Password", "/login", "", "input", "password", "password"),
    Element(19, "button", "Sign in", "/login", None, "button"),
)
STORE_LOGIN = (
    Element(681, "textbox", "Username", "/login", "", "input", "username", "text"),
    Element(684, "textbox", "Password", "/login", "", "input", "password", "password"),
    Element(685, "button", "Sign in", "/login", None, "button"),
)
NEXT_LINK = Element(308, "link", "Next", None, None, "a")
SAVE_BUTTON = Element(1016, "button", "Save changes", "/inventory", None, "button", "action")


def field(sku: str, name: str, value: int, ref: int = 720) -> Element:
    return Element(ref, "spinbutton", f"Available for {name}", "/inventory", str(value), "input", f"available-{sku}", "number")


def sign_in_steps(site: str, landing: str, elements: tuple[Element, Element, Element]) -> SignInSteps:
    user, password, submit = (target_for(element) for element in elements)
    return SignInSteps(site=site, landing=landing, user=user, password=password, submit=submit)


def kumo_read_steps(next_link: Element | None = NEXT_LINK) -> ReadSteps:
    return ReadSteps(
        supplier=sign_in_steps("supplier", "/stock", SUPPLIER_LOGIN),
        next_page=None if next_link is None else target_for(next_link),
        store=sign_in_steps("store", "/inventory", STORE_LOGIN),
        save_button=target_for(SAVE_BUTTON),
    )


def kumo_draft(expected_replay_ms: int = 4200) -> ProcedureDraft:
    return ProcedureDraft(read=kumo_read_steps(), expected_replay_ms=expected_replay_ms)


def fields_for(changes: list[StockChange]) -> dict[str, Element]:
    return {change.sku: field(change.sku, change.name, change.store, 720 + index) for index, change in enumerate(changes)}
