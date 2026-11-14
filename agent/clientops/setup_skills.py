from collections.abc import Iterable

from agentfield import AgentRouter
from daytona import SandboxState

from clientops.boundary import ClientBoundary, ClientNotSetUp, VaultError, check_logins, resolve_job
from clientops.computers import BROKEN_STATES, ComputerError, ComputerProblem

COMPUTER_MISSING = "missing"
COMPUTER_READY = "ready"
COMPUTER_UNAVAILABLE = "unavailable"
COMPUTER_FAILED = "failed"
VAULT_PROBLEM = "vault"

router = AgentRouter()


def answer(client_id: str, computer: str, present: Iterable[str] = (), missing: Iterable[str] = ()) -> dict:
    return {"clientId": client_id, "computer": computer, "logins": {"present": list(present), "missing": list(missing)}}


def failed(client_id: str, problem: str) -> dict:
    return {"clientId": client_id, "computer": COMPUTER_FAILED, "problem": problem}


def boundary_of(client_id: str) -> ClientBoundary | None:
    try:
        return resolve_job(client_id)
    except ClientNotSetUp:
        return None


def ready_with_logins(client_id: str) -> dict:
    status = check_logins(client_id)
    return answer(client_id, COMPUTER_READY, status.present, status.missing)


def reported(client_id: str, work) -> dict:
    try:
        return work()
    except ComputerError as error:
        return failed(client_id, error.problem.value)
    except VaultError:
        return failed(client_id, VAULT_PROBLEM)


@router.skill()
def setup_status(client_id: str, wake: bool = False) -> dict:
    boundary = boundary_of(client_id)
    if boundary is None:
        return answer(client_id, COMPUTER_UNAVAILABLE)

    def look() -> dict:
        state = boundary.computer_state()
        if state is None:
            return answer(client_id, COMPUTER_MISSING, missing=boundary.vault.placeholders)
        if state in BROKEN_STATES:
            return failed(client_id, ComputerProblem.BROKEN.value)
        if state is not SandboxState.STARTED and not wake:
            return {"clientId": client_id, "computer": COMPUTER_READY, "logins": None}
        return ready_with_logins(client_id)

    return reported(client_id, look)


@router.skill()
def prepare_computer(client_id: str) -> dict:
    boundary = boundary_of(client_id)
    if boundary is None:
        return answer(client_id, COMPUTER_UNAVAILABLE)

    def prepare() -> dict:
        boundary.prepared_computer()
        return ready_with_logins(client_id)

    return reported(client_id, prepare)
