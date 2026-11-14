import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from .boundary import ClientBoundary, Site
from .diff import StockChange
from .page import placeholder

if TYPE_CHECKING:
    from .procedure import Procedure, ReadSteps, SignInSteps, Target
    from .trip_procedure import TripProcedure

INTERPRETER: Final = (Path(__file__).parent / "pandascript.js").read_text()
OPS_START: Final = "const run = {\n"
TRIP_INTERPRETER: Final = (Path(__file__).parent / "trip_pandascript.js").read_text() + INTERPRETER.replace(OPS_START, f"{OPS_START}  ...tripOps,\n", 1)
PLAN_PREFIX: Final = "const plan = "
MAX_PAGES: Final = 50
SAVED_PATTERN: Final = r"\bsaved\b"


def site_of(boundary: ClientBoundary, kind: str) -> Site:
    return boundary.supplier if kind == "supplier" else boundary.store


def sign_in_ops(steps: "SignInSteps", boundary: ClientBoundary) -> list[dict[str, Any]]:
    return site_sign_in_ops(site_of(boundary, steps.site), steps.landing, steps.elements())


def site_sign_in_ops(site: Site, landing: str, elements: dict[str, "Target"]) -> list[dict[str, Any]]:
    username, password = site.logins
    return [
        {"op": "goto", "url": site.sign_in_url},
        *({"op": "one", "as": name, **target.plan()} for name, target in elements.items()),
        {"op": "fill", "el": "user", "value": placeholder(username), "secret": True},
        {"op": "fill", "el": "password", "value": placeholder(password), "secret": True},
        {"op": "click", "el": "submit"},
        {"op": "path", "is": landing, "code": "not-signed-in"},
    ]


def trip_search_plan(procedure: "TripProcedure", site: Site, search_url: str) -> dict[str, Any]:
    return {
        "maxPages": MAX_PAGES,
        "steps": [
            {"stepId": "sign-in", "ops": site_sign_in_ops(site, procedure.landing, procedure.sign_in_elements())},
            {"stepId": "search", "ops": [{"op": "goto", "url": search_url}, {"op": "options", "list": procedure.list_name, "link": procedure.link_prefix, "into": "options"}]},
            {"stepId": "seats", "ops": [{"op": "seatMaps", "from": "options", "base": site.url, "heading": procedure.seats_heading, "into": "seats"}]},
        ],
    }


def compile_trip_search_script(procedure: "TripProcedure", site: Site, search_url: str) -> str:
    return script_source(trip_search_plan(procedure, site, search_url), TRIP_INTERPRETER)


def read_plan(read: "ReadSteps", boundary: ClientBoundary) -> dict[str, Any]:
    return {
        "maxPages": MAX_PAGES,
        "steps": [
            {"stepId": "sign-in", "ops": [*sign_in_ops(read.supplier, boundary), {"op": "page"}]},
            {
                "stepId": "read",
                "ops": [
                    {"op": "table", "into": "supplier", "next": None if read.next_page is None else read.next_page.plan()},
                    {"op": "page"},
                ],
            },
            {
                "stepId": "compare",
                "ops": [*sign_in_ops(read.store, boundary), {"op": "table", "into": "store", "next": None}, {"op": "page"}],
            },
            {"stepId": "check-save", "ops": [{"op": "one", "as": "save", **read.save_button.plan()}]},
        ],
    }


def read_plan_from(read: "ReadSteps", boundary: ClientBoundary, step_id: str) -> dict[str, Any]:
    plan = read_plan(read, boundary)
    steps = plan["steps"]
    rest = steps[[step["stepId"] for step in steps].index(step_id) :]
    signed_in_by = {"read": read.supplier, "check-save": read.store}.get(step_id)
    if signed_in_by is not None:
        first = rest[0]
        rest = [{**first, "ops": [*sign_in_ops(signed_in_by, boundary), *first["ops"]]}, *rest[1:]]
    return {**plan, "steps": rest}


def write_plan(procedure: "Procedure", boundary: ClientBoundary, changes: Iterable[StockChange]) -> dict[str, Any]:
    items = [
        {"sku": change.sku, "css": procedure.field.target(change.sku, change.name).css, "expect": str(change.store), "value": str(change.supplier)}
        for change in changes
    ]
    if not items:
        raise ValueError("A write needs at least one change")
    return {
        "maxPages": MAX_PAGES,
        "steps": [
            {
                "stepId": "save-prepare",
                "ops": [
                    *sign_in_ops(procedure.read.store, boundary),
                    {"op": "one", "as": "save", **procedure.read.save_button.plan()},
                    {"op": "table", "into": "before", "next": None},
                    {"op": "page"},
                    {"op": "fields", "items": items},
                ],
            },
            {
                "stepId": "save-submit",
                "ops": [
                    {"op": "click", "el": "save"},
                    {"op": "one", "as": "status", **procedure.status.plan()},
                    {"op": "says", "el": "status", "pattern": SAVED_PATTERN, "into": "status", "code": "not-saved"},
                    {"op": "path", "is": procedure.read.store.landing, "code": "wrong-path"},
                    {"op": "table", "into": "after", "next": None},
                    {"op": "page"},
                ],
            },
        ],
    }


def script_source(plan: dict[str, Any], interpreter: str = INTERPRETER) -> str:
    return f"{PLAN_PREFIX}{json.dumps(plan, ensure_ascii=False, separators=(',', ':'))};\n{interpreter}"


def plan_of(source: str) -> dict[str, Any]:
    first_line = source.split("\n", 1)[0]
    if not first_line.startswith(PLAN_PREFIX) or not first_line.endswith(";"):
        raise ValueError("This is not a script compiled from saved steps")
    return json.loads(first_line[len(PLAN_PREFIX) : -1])


def compile_read_script(read: "ReadSteps", boundary: ClientBoundary) -> str:
    return script_source(read_plan(read, boundary))


def compile_read_script_from(read: "ReadSteps", boundary: ClientBoundary, step_id: str) -> str:
    return script_source(read_plan_from(read, boundary, step_id))


def compile_write_script(procedure: "Procedure", boundary: ClientBoundary, changes: Iterable[StockChange]) -> str:
    return script_source(write_plan(procedure, boundary, changes))
