import asyncio
from dataclasses import replace

from clientops.boundary import resolve_job
from clientops.compile_script import compile_read_script, compile_read_script_from, plan_of
from clientops.page import ScriptResult
from clientops.procedure import Hint, Procedure, Target
from clientops.refind import FailedStep, NotFound, Recovered, failed_element, rebuilt, refind_by_tree, step_targets
from test_replay import KUMO_DAY2, kumo_procedure, ticking_clock
from test_stock_sync import VAULT, data_dir, install_sites

__all__ = ["data_dir"]

CLASS_SAVE = 'form[action="/inventory"] button.btn-save[name="action"]'
CLASS_SUPPLIER_USER = 'form[action="/login"] input.field[name="username"]'
CLASS_STORE_PASSWORD = 'form[action="/login"] input.field[name="password"][type="password"]'
SAVE_CSS = 'form[action="/inventory"] button[name="action"]'
SUPPLIER_USER_CSS = 'form[action="/login"] input[name="username"]'
LOOK_ON_THE_STORE = ["open", "fill", "fill", "click", "find", "snapshot", "close"]


def recorded_with(step_id: str, element: str, css: str, version: int = 1, base: Procedure | None = None) -> Procedure:
    procedure = base or kumo_procedure(version)
    read = procedure.read
    if step_id == "check-save":
        read = replace(read, save_button=replace(read.save_button, css=css))
    else:
        site = "supplier" if step_id == "sign-in" else "store"
        steps = getattr(read, site)
        read = replace(read, **{site: replace(steps, **{element: replace(getattr(steps, element), css=css)})})
    return replace(procedure, read=read, script=compile_read_script(read, resolve_job("kumo")))


def failed_run(procedure: Procedure) -> FailedStep:
    boundary = resolve_job("kumo")
    ran: ScriptResult = boundary.job_computer().page().run_script(compile_read_script(procedure.read, boundary))
    failure = ran.failure
    assert failure is not None
    return FailedStep(failure.step_id, failure.kind, failure.code, failure.message, ran.done_steps(), procedure, boundary, ran, clock=ticking_clock())


def commands_after_the_script(sites) -> list[str]:
    names = [command["command"] for command in sites.commands]
    return names[names.index("script") + 1 :]


def test_a_save_button_whose_selector_no_longer_matches_is_found_again_by_role_name_and_form(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    failed = failed_run(recorded_with("check-save", "save", CLASS_SAVE))

    found = asyncio.run(refind_by_tree(failed))

    assert isinstance(found, Recovered)
    assert found.step_id == "check-save"
    assert found.element == "save"
    assert found.rung == "tree"
    assert found.escalated_from == ("script",)
    assert found.took_ms > 0
    assert found.snapshot is not None and found.snapshot.host == "127.0.0.1:3002"
    assert found.procedure.read.save_button == Target(SAVE_CSS, "Save changes", Hint("button", "Save changes", "/inventory"))
    assert found.procedure.version == 1
    assert commands_after_the_script(sites) == LOOK_ON_THE_STORE
    [find] = [command for command in sites.commands if command["command"] == "find"]
    assert find == {"command": "find", "role": "button", "name": "Save changes", "form": "/inventory"}
    assert sites.saved is False


def test_only_that_selector_is_rebuilt_and_the_script_is_compiled_again(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2)
    recorded = recorded_with("check-save", "save", CLASS_SAVE)

    found = asyncio.run(refind_by_tree(failed_run(recorded)))

    assert found.procedure.read.supplier == recorded.read.supplier
    assert found.procedure.read.store == recorded.read.store
    assert found.procedure.read.next_page == recorded.read.next_page
    assert (found.procedure.field, found.procedure.status, found.procedure.saved_at) == (recorded.field, recorded.status, recorded.saved_at)
    assert found.procedure.script == compile_read_script(found.procedure.read, resolve_job("kumo"))
    assert CLASS_SAVE not in found.procedure.script


def test_signing_in_to_look_again_uses_only_placeholders_for_the_login(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    asyncio.run(refind_by_tree(failed_run(recorded_with("check-save", "save", CLASS_SAVE))))

    fills = [command for command in sites.commands if command["command"] == "fill"]
    assert [command["text"] for command in fills] == ["$LP_STORE_USERNAME", "$LP_STORE_PASSWORD"]
    assert [command["target"] for command in fills] == [
        {"role": "textbox", "name": "Username", "form": "/login"},
        {"role": "textbox", "name": "Password", "form": "/login"},
    ]
    for value in VAULT.values():
        assert value not in str(sites.commands)


def test_a_sign_in_field_is_looked_for_on_the_sign_in_page_without_signing_in(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)

    found = asyncio.run(refind_by_tree(failed_run(recorded_with("sign-in", "user", CLASS_SUPPLIER_USER))))

    assert found.step_id == "sign-in"
    assert found.element == "user"
    assert found.procedure.read.supplier.user.css == SUPPLIER_USER_CSS
    assert found.procedure.read.supplier == replace(recorded_with("sign-in", "user", CLASS_SUPPLIER_USER).read.supplier, user=found.procedure.read.supplier.user)
    assert commands_after_the_script(sites) == ["open", "find", "find", "find", "snapshot", "close"]
    assert sites.opened[-1] == "http://127.0.0.1:3001/login"


def test_every_element_of_the_step_whose_name_attribute_changed_is_rebuilt_together(monkeypatch):
    install_sites(monkeypatch, day=KUMO_DAY2, restyled=True)
    failed = failed_run(kumo_procedure())
    assert (failed.step_id, failed.code) == ("compare", "not-found")

    found = asyncio.run(refind_by_tree(failed))

    store = found.procedure.read.store
    assert found.element == "user"
    assert store.user.css == 'form[action="/login"] input[name="f-username"]'
    assert store.password.css == 'form[action="/login"] input[name="f-password"][type="password"]'
    assert store.submit == kumo_procedure().read.store.submit
    assert found.procedure.read.supplier == kumo_procedure().read.supplier
    assert found.procedure.read.save_button == kumo_procedure().read.save_button


def test_a_renamed_button_is_not_found_and_nothing_is_pressed(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, save_label="Update inventory")
    failed = failed_run(kumo_procedure())
    assert failed.code == "wrong-name"

    found = asyncio.run(refind_by_tree(failed))

    assert found == NotFound("check-save", "save", Hint("button", "Save changes", "/inventory"), 0, found.took_ms)
    assert commands_after_the_script(sites) == ["open", "fill", "fill", "click", "find", "close"]
    assert sites.saved is False


def test_two_buttons_with_the_saved_name_are_not_one_so_nothing_is_chosen(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2, extra_store_buttons=("Save changes",))

    found = asyncio.run(refind_by_tree(failed_run(recorded_with("check-save", "save", CLASS_SAVE))))

    assert isinstance(found, NotFound)
    assert found.count == 2
    assert found.element == "save"
    assert sites.saved is False
    assert commands_after_the_script(sites)[-1] == "close"


def test_a_failure_that_is_not_about_one_element_is_not_looked_for(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    failed = failed_run(recorded_with("check-save", "save", CLASS_SAVE))
    before = len(sites.commands)

    for changed in (
        replace(failed, code="not-signed-in", message="the page is /login, expected /inventory"),
        replace(failed, code="not-found", message="page 1 has no table"),
        replace(failed, kind="step", code=None, message="click failed: NodeNotFound"),
        replace(failed, step_id=None),
    ):
        assert asyncio.run(refind_by_tree(changed)) is None

    assert len(sites.commands) == before


def test_a_page_that_cannot_be_reached_is_not_reported_as_a_missing_element(monkeypatch):
    sites = install_sites(monkeypatch, day=KUMO_DAY2)
    failed = failed_run(recorded_with("check-save", "save", CLASS_SAVE))
    sites.accounts = {**sites.accounts, "store": ("someone", "else")}

    assert asyncio.run(refind_by_tree(failed)) is None
    assert commands_after_the_script(sites)[-1] == "close"


def test_the_failed_element_is_the_one_whose_selector_starts_the_message():
    targets = {
        "short": Target("form button", None, Hint("button", "Go", None)),
        "long": Target("form button span", None, Hint("button", "Go", None)),
    }

    assert failed_element(targets, "form button span matched 0 elements, expected 1") == "long"
    assert failed_element(targets, "form button matched 2 elements, expected 1") == "short"
    assert failed_element(targets, 'form button says "Stop", expected "Go"') == "short"
    assert failed_element(targets, "page 1 has no table") is None


def test_each_step_looks_only_at_its_own_elements():
    read = kumo_procedure().read

    assert set(step_targets(read, "sign-in")) == {"user", "password", "submit"}
    assert step_targets(read, "sign-in")["user"] == read.supplier.user
    assert step_targets(read, "compare")["password"] == read.store.password
    assert step_targets(read, "read") == {"next": read.next_page}
    assert step_targets(read, "check-save") == {"save": read.save_button}
    assert step_targets(read, "ask") == {}
    assert step_targets(replace(read, next_page=None), "read") == {}


def test_rebuilding_a_store_sign_in_field_leaves_the_supplier_one_alone():
    procedure = kumo_procedure()
    new = Target('form[action="/login"] input[name="pass"][type="password"]', None, Hint("textbox", "Password", "/login"))

    changed = rebuilt(procedure, resolve_job("kumo"), "compare", "password", new)

    assert changed.read.store.password == new
    assert changed.read.supplier.password == procedure.read.supplier.password
    assert changed.read.store.user == procedure.read.store.user
    assert changed.version == procedure.version


def test_the_script_from_a_step_signs_in_again_only_where_that_step_needs_the_session():
    read = kumo_procedure().read
    boundary = resolve_job("kumo")

    def ops(step_id):
        return [[op["op"] for op in step["ops"]] for step in plan_of(compile_read_script_from(read, boundary, step_id))["steps"]]

    def ids(step_id):
        return [step["stepId"] for step in plan_of(compile_read_script_from(read, boundary, step_id))["steps"]]

    sign_in = ["goto", "one", "one", "one", "fill", "fill", "click", "path"]
    assert ids("sign-in") == ["sign-in", "read", "compare", "check-save"]
    assert plan_of(compile_read_script_from(read, boundary, "sign-in")) == plan_of(compile_read_script(read, boundary))
    assert ids("read") == ["read", "compare", "check-save"]
    assert ops("read")[0] == [*sign_in, "table", "page"]
    assert ids("compare") == ["compare", "check-save"]
    assert ops("compare")[0] == [*sign_in, "table", "page"]
    assert ids("check-save") == ["check-save"]
    assert ops("check-save") == [[*sign_in, "one"]]
    store_sign_in = plan_of(compile_read_script_from(read, boundary, "check-save"))["steps"][0]["ops"]
    assert store_sign_in[0] == {"op": "goto", "url": "http://127.0.0.1:3002/login"}
    assert [op["value"] for op in store_sign_in if op["op"] == "fill"] == ["$LP_STORE_USERNAME", "$LP_STORE_PASSWORD"]
