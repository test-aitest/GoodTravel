import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from .boundary import ClientBoundary, Site
from .compile_script import compile_read_script
from .computers import ComputerError
from .externals import MissingExternalKey
from .page import Found, Locator, Page, PageError, ScriptResult, Snapshot, placeholder
from .plan import elapsed_ms
from .procedure import Hint, NoStableSelector, Procedure, ReadSteps, SignInSteps, Target, saved_procedure, target_for
from .stock_sync import SIGNED_IN_SELECTOR, SignInFailed

Clock = Callable[[], float]
PageOpener = Callable[[], Page]

TREE_RUNG = "tree"
SCRIPT_RUNG = "script"
ELEMENT_CODES = frozenset({"not-found", "not-one", "wrong-name"})
NOT_ONE_ON_THE_PAGE = frozenset({"not-found", "not-unique"})
LOOK_ERRORS = (ComputerError, MissingExternalKey, PageError, SignInFailed)


@dataclass(frozen=True)
class FailedStep:
    step_id: str | None
    kind: str
    code: str | None
    message: str
    done_steps: tuple[str, ...]
    procedure: Procedure
    boundary: ClientBoundary
    script: ScriptResult
    open_page: PageOpener | None = None
    clock: Clock = time.monotonic


@dataclass(frozen=True)
class Recovered:
    step_id: str
    element: str
    procedure: Procedure
    rung: str
    escalated_from: tuple[str, ...]
    took_ms: int
    snapshot: Snapshot | None = None


@dataclass(frozen=True)
class NotFound:
    step_id: str
    element: str
    hint: Hint
    count: int
    took_ms: int


def step_targets(read: ReadSteps, step_id: str | None) -> dict[str, Target]:
    if step_id == "sign-in":
        return read.supplier.elements()
    if step_id == "compare":
        return read.store.elements()
    if step_id == "check-save":
        return {"save": read.save_button}
    if step_id == "read" and read.next_page is not None:
        return {"next": read.next_page}
    return {}


def failed_element(targets: Mapping[str, Target], message: str) -> str | None:
    named = [(len(target.css), element) for element, target in targets.items() if message.startswith(f"{target.css} ")]
    return max(named)[1] if named else None


def with_target(read: ReadSteps, step_id: str, element: str, target: Target) -> ReadSteps:
    if step_id == "sign-in":
        return replace(read, supplier=replace(read.supplier, **{element: target}))
    if step_id == "compare":
        return replace(read, store=replace(read.store, **{element: target}))
    if step_id == "check-save":
        return replace(read, save_button=target)
    return replace(read, next_page=target)


def rebuilt(procedure: Procedure, boundary: ClientBoundary, step_id: str, element: str, target: Target) -> Procedure:
    read = with_target(procedure.read, step_id, element, target)
    return replace(procedure, read=read, script=compile_read_script(read, boundary))


def locator_of(hint: Hint) -> Locator:
    return Locator(hint.role, hint.name, hint.form)


def sign_in_by_tree(page: Page, site: Site, steps: SignInSteps) -> None:
    page.open(site.sign_in_url)
    username, password = site.logins
    page.fill(locator_of(steps.user.hint), placeholder(username))
    page.fill(locator_of(steps.password.hint), placeholder(password))
    try:
        landed = page.click(locator_of(steps.submit.hint), wait_for=SIGNED_IN_SELECTOR)
    except PageError as error:
        if error.code == "wait-failed":
            raise SignInFailed(site) from None
        raise
    if (urlsplit(landed.page.url).path or "/") != steps.landing:
        raise SignInFailed(site)


def reach(page: Page, boundary: ClientBoundary, read: ReadSteps, step_id: str) -> None:
    if step_id == "sign-in":
        page.open(boundary.supplier.sign_in_url)
    elif step_id == "compare":
        page.open(boundary.store.sign_in_url)
    elif step_id == "read":
        sign_in_by_tree(page, boundary.supplier, read.supplier)
    else:
        sign_in_by_tree(page, boundary.store, read.store)


def find_by_hint(page: Page, hint: Hint) -> Found | int:
    try:
        return page.find(locator_of(hint))
    except PageError as error:
        if error.command != "find" or error.code not in NOT_ONE_ON_THE_PAGE:
            raise
        return error.count or 0


def snapshot_of(page: Page) -> Snapshot | None:
    try:
        return page.snapshot()
    except PageError:
        return None


def look_again(page: Page, boundary: ClientBoundary, read: ReadSteps, step_id: str, element: str) -> tuple[Found | int, dict[str, Found | int], Snapshot | None]:
    reach(page, boundary, read, step_id)
    targets = step_targets(read, step_id)
    first = find_by_hint(page, targets[element].hint)
    if isinstance(first, int):
        return first, {}, None
    others = {key: find_by_hint(page, target.hint) for key, target in targets.items() if key != element}
    return first, others, snapshot_of(page)


def look_at_step(page: Page, boundary: ClientBoundary, read: ReadSteps, step_id: str) -> dict[str, Found | int]:
    reach(page, boundary, read, step_id)
    seen: dict[str, Found | int] = {}
    for element, target in step_targets(read, step_id).items():
        seen[element] = find_by_hint(page, target.hint)
        if isinstance(seen[element], int):
            break
    return seen


def selector_of(found: Found | int) -> Target | None:
    if isinstance(found, int):
        return None
    try:
        return target_for(found.element)
    except NoStableSelector:
        return None


def differs(target: Target, saved: Target) -> bool:
    return (target.css, target.text) != (saved.css, saved.text)


def problem_of(error: Exception) -> str:
    if isinstance(error, PageError):
        return error.code
    if isinstance(error, ComputerError):
        return error.problem.value
    if isinstance(error, SignInFailed):
        return "not-signed-in"
    return type(error).__name__


async def close(page: Page) -> None:
    try:
        await asyncio.to_thread(page.close)
    except LOOK_ERRORS:
        return


def page_opener(boundary: ClientBoundary, open_page: PageOpener | None) -> PageOpener:
    return open_page or (lambda: boundary.job_computer().page())


async def refind_by_tree(failed: FailedStep) -> Recovered | NotFound | None:
    if failed.step_id is None or failed.kind != "check" or failed.code not in ELEMENT_CODES:
        return None
    targets = step_targets(failed.procedure.read, failed.step_id)
    element = failed_element(targets, failed.message)
    if element is None:
        return None
    started = failed.clock()
    try:
        page = await asyncio.to_thread(page_opener(failed.boundary, failed.open_page))
    except LOOK_ERRORS:
        return None
    try:
        first, others, snapshot = await asyncio.to_thread(look_again, page, failed.boundary, failed.procedure.read, failed.step_id, element)
    except LOOK_ERRORS:
        return None
    finally:
        await close(page)
    if isinstance(first, int):
        return NotFound(failed.step_id, element, targets[element].hint, first, elapsed_ms(started, failed.clock()))
    target = selector_of(first)
    if target is None or not differs(target, targets[element]):
        return None
    read = with_target(failed.procedure.read, failed.step_id, element, target)
    for key, found in others.items():
        other = selector_of(found)
        if other is not None and differs(other, targets[key]):
            read = with_target(read, failed.step_id, key, other)
    return Recovered(
        failed.step_id,
        element,
        replace(failed.procedure, read=read, script=compile_read_script(read, failed.boundary)),
        TREE_RUNG,
        (SCRIPT_RUNG,),
        elapsed_ms(started, failed.clock()),
        snapshot,
    )


async def refind_saved_step(
    boundary: ClientBoundary,
    memory: Any,
    step_id: str,
    *,
    open_page: PageOpener | None = None,
    clock: Clock = time.monotonic,
) -> dict[str, Any]:
    answer: dict[str, Any] = {"clientId": boundary.client.id, "stepId": step_id}
    procedure = await saved_procedure(memory, boundary)
    if procedure is None:
        return {**answer, "found": False, "problem": "no-saved-steps"}
    targets = step_targets(procedure.read, step_id)
    if not targets:
        return {**answer, "found": False, "problem": "no-elements"}
    started = clock()
    try:
        page = await asyncio.to_thread(page_opener(boundary, open_page))
    except LOOK_ERRORS as error:
        return {**answer, "found": False, "problem": problem_of(error)}
    try:
        seen = await asyncio.to_thread(look_at_step, page, boundary, procedure.read, step_id)
    except LOOK_ERRORS as error:
        return {**answer, "found": False, "problem": problem_of(error)}
    finally:
        await close(page)
    took = {"tookMs": elapsed_ms(started, clock())}
    elements: dict[str, Any] = {}
    for element, found in seen.items():
        if isinstance(found, int):
            return {**answer, "found": False, "element": element, "count": found, **took}
        target = selector_of(found)
        if target is None:
            return {**answer, "found": False, "element": element, "problem": "no-stable-selector", **took}
        elements[element] = {**target.to_json(), "changed": differs(target, targets[element])}
    return {**answer, "found": True, "procedureVersion": procedure.version, "elements": elements, **took}
