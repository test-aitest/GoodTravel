import json
import re
from pathlib import Path

import pytest

from clientops.boundary import resolve_job
from clientops.compile_script import (
    INTERPRETER,
    MAX_PAGES,
    compile_read_script,
    compile_write_script,
    plan_of,
    read_plan,
    write_plan,
)
from clientops.diff import StockChange
from clientops.page import Element
from clientops.procedure import NoStableSelector, Target, field_pattern, target_for
from procedure_fixtures import NEXT_LINK, SAVE_BUTTON, STORE_LOGIN, SUPPLIER_LOGIN, fields_for, kumo_read_steps
from test_stock_sync import VAULT, data_dir

__all__ = ["data_dir"]

FIXTURES = Path(__file__).parent / "fixtures"
KUMO_DAY2 = json.loads((FIXTURES / "kumo-day2.json").read_text())
CHANGES = [StockChange(**change) for change in KUMO_DAY2["changes"]]
PLACEHOLDER = re.compile(r"\$(LP_[A-Za-z0-9_]+)")


def ops_of(plan: dict, step_id: str) -> list[dict]:
    [step] = [step for step in plan["steps"] if step["stepId"] == step_id]
    return step["ops"]


def kinds(ops: list[dict]) -> list[str]:
    return [op["op"] for op in ops]


def test_selectors_come_from_the_elements_that_were_used():
    assert target_for(SUPPLIER_LOGIN[0]) == Target('form[action="/login"] input[name="username"]', None, target_for(SUPPLIER_LOGIN[0]).hint)
    assert target_for(SUPPLIER_LOGIN[1]).css == 'form[action="/login"] input[name="password"][type="password"]'
    assert (target_for(SUPPLIER_LOGIN[2]).css, target_for(SUPPLIER_LOGIN[2]).text) == ('form[action="/login"] button', "Sign in")
    assert (target_for(NEXT_LINK).css, target_for(NEXT_LINK).text) == ("a", "Next")
    assert (target_for(SAVE_BUTTON).css, target_for(SAVE_BUTTON).text) == ('form[action="/inventory"] button[name="action"]', "Save changes")


def test_every_target_keeps_the_role_name_and_form_as_a_hint_for_finding_it_again():
    assert target_for(SAVE_BUTTON).hint.to_json() == {"role": "button", "name": "Save changes", "form": "/inventory"}
    assert target_for(NEXT_LINK).hint.to_json() == {"role": "link", "name": "Next", "form": None}


def test_a_form_known_only_by_its_place_is_scoped_by_that_place_and_quotes_are_escaped():
    element = Element(5, "textbox", "Code", "/html[1]/body[1]/main[1]/form[2]", None, "input", 'promo"code', "text")
    assert target_for(element).css == 'html > body:nth-of-type(1) > main:nth-of-type(1) > form:nth-of-type(2) input[name="promo\\"code"]'


@pytest.mark.parametrize(
    "element",
    [
        Element(5, "textbox", "Search", "/search", None, "input", None, "text"),
        Element(6, "button", " ", "/inventory", None, "button", "action"),
        Element(7, "button", "Go", "/go", None, None, None),
    ],
)
def test_an_element_without_a_stable_handle_is_not_turned_into_a_selector(element):
    with pytest.raises(NoStableSelector):
        target_for(element)


def test_the_read_script_checks_that_every_element_is_the_only_one_before_using_it():
    plan = read_plan(kumo_read_steps(), resolve_job("kumo"))

    for step_id in ("sign-in", "compare"):
        ops = ops_of(plan, step_id)
        used = {op["el"] for op in ops if op["op"] in ("fill", "click")}
        checked = {op["as"] for op in ops if op["op"] == "one"}
        assert used <= checked
        for position, op in enumerate(ops):
            if op["op"] in ("fill", "click"):
                assert any(earlier["op"] == "one" and earlier["as"] == op["el"] for earlier in ops[:position])
    assert ops_of(plan, "check-save") == [{"op": "one", "as": "save", "css": 'form[action="/inventory"] button[name="action"]', "text": "Save changes"}]
    [table] = [op for op in ops_of(plan, "read") if op["op"] == "table"]
    assert table == {"op": "table", "into": "supplier", "next": {"css": "a", "text": "Next"}}
    assert plan["maxPages"] == MAX_PAGES


def test_every_step_of_the_read_script_checks_what_its_action_did():
    plan = read_plan(kumo_read_steps(), resolve_job("kumo"))

    assert [step["stepId"] for step in plan["steps"]] == ["sign-in", "read", "compare", "check-save"]
    sign_in = ops_of(plan, "sign-in")
    assert kinds(sign_in) == ["goto", "one", "one", "one", "fill", "fill", "click", "path", "page"]
    assert sign_in[0] == {"op": "goto", "url": "http://127.0.0.1:3001/login"}
    assert sign_in[7] == {"op": "path", "is": "/stock", "code": "not-signed-in"}
    compare = ops_of(plan, "compare")
    assert kinds(compare) == ["goto", "one", "one", "one", "fill", "fill", "click", "path", "table", "page"]
    assert compare[0] == {"op": "goto", "url": "http://127.0.0.1:3002/login"}
    assert compare[7] == {"op": "path", "is": "/inventory", "code": "not-signed-in"}
    assert compare[8] == {"op": "table", "into": "store", "next": None}


def test_logins_appear_only_as_vault_placeholders_and_no_value_is_written_into_the_script():
    source = compile_read_script(kumo_read_steps(), resolve_job("kumo"))

    fills = [op for step in plan_of(source)["steps"] for op in step["ops"] if op["op"] == "fill"]
    assert [(op["value"], op["secret"]) for op in fills] == [
        ("$LP_SUPPLIER_USERNAME", True),
        ("$LP_SUPPLIER_PASSWORD", True),
        ("$LP_STORE_USERNAME", True),
        ("$LP_STORE_PASSWORD", True),
    ]
    assert set(PLACEHOLDER.findall(source)) == set(VAULT)
    for value in VAULT.values():
        assert value not in source


def test_the_script_is_the_fixed_interpreter_with_the_plan_as_data():
    steps = kumo_read_steps()
    source = compile_read_script(steps, resolve_job("kumo"))

    first_line, rest = source.split("\n", 1)
    assert first_line == f"const plan = {json.dumps(read_plan(steps, resolve_job('kumo')), ensure_ascii=False, separators=(',', ':'))};"
    assert rest == INTERPRETER
    assert not PLACEHOLDER.search(INTERPRETER)
    assert "//" not in INTERPRETER.replace("://", "")


def test_a_supplier_list_on_one_page_is_read_without_looking_for_a_next_page():
    plan = read_plan(kumo_read_steps(next_link=None), resolve_job("kumo"))

    [table] = [op for op in ops_of(plan, "read") if op["op"] == "table"]
    assert table["next"] is None


def test_the_urls_come_from_this_clients_boundary_not_from_the_saved_steps():
    hinata = read_plan(kumo_read_steps(), resolve_job("hinata"))

    gotos = [op["url"] for step in hinata["steps"] for op in step["ops"] if op["op"] == "goto"]
    assert gotos == [resolve_job("hinata").supplier.sign_in_url, resolve_job("hinata").store.sign_in_url]


def test_the_field_pattern_is_taken_from_the_fields_that_were_written():
    pattern = field_pattern(fields_for(CHANGES), CHANGES)

    target = pattern.target("KC-HSE-200", "House Blend 200g")
    assert target.css == 'form[action="/inventory"] input[name="available-KC-HSE-200"]'
    assert target.text is None
    assert target.hint.to_json() == {"role": "spinbutton", "name": "Available for House Blend 200g", "form": "/inventory"}


def test_fields_that_do_not_carry_the_sku_in_their_name_give_no_pattern():
    fields = {change.sku: Element(1, "spinbutton", "Quantity", "/inventory", "1", "input", "quantity", "number") for change in CHANGES}
    with pytest.raises(NoStableSelector):
        field_pattern(fields, CHANGES)


def test_the_write_script_checks_the_store_is_unchanged_then_fills_our_numbers_then_checks_the_save(monkeypatch):
    from procedure_fixtures import kumo_draft
    from clientops.procedure import Procedure

    procedure = Procedure.from_draft(kumo_draft(), field_pattern(fields_for(CHANGES), CHANGES), client_id="kumo", version=1, saved_at="2026-11-14T09:00:00Z", script="")
    plan = write_plan(procedure, resolve_job("kumo"), CHANGES)

    assert [step["stepId"] for step in plan["steps"]] == ["save-prepare", "save-submit"]
    prepare = ops_of(plan, "save-prepare")
    assert kinds(prepare) == ["goto", "one", "one", "one", "fill", "fill", "click", "path", "one", "table", "page", "fields"]
    assert prepare[8] == {"op": "one", "as": "save", "css": 'form[action="/inventory"] button[name="action"]', "text": "Save changes"}
    assert prepare[9] == {"op": "table", "into": "before", "next": None}
    assert prepare[11]["items"] == [
        {"sku": change.sku, "css": f'form[action="/inventory"] input[name="available-{change.sku}"]', "expect": str(change.store), "value": str(change.supplier)}
        for change in CHANGES
    ]
    submit = ops_of(plan, "save-submit")
    assert submit == [
        {"op": "click", "el": "save"},
        {"op": "one", "as": "status", "css": '[role="status"]', "text": None},
        {"op": "says", "el": "status", "pattern": "\\bsaved\\b", "into": "status", "code": "not-saved"},
        {"op": "path", "is": "/inventory", "code": "wrong-path"},
        {"op": "table", "into": "after", "next": None},
        {"op": "page"},
    ]
    source = compile_write_script(procedure, resolve_job("kumo"), CHANGES)
    assert plan_of(source) == plan
    for value in VAULT.values():
        assert value not in source
