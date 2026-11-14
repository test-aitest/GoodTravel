import json
import os
from pathlib import Path

from clientops.boundary import resolve_job
from clientops.procedure import PROCEDURE_KEY, Procedure
from test_replay import kumo_procedure
from test_stock_sync import data_dir

__all__ = ["data_dir"]

CONTRACT = Path(__file__).parent / "contract" / "saved_procedure.json"
RECORD_VARIABLE = "CLIENTOPS_RECORD_CONTRACTS"


def produce() -> dict:
    boundary = resolve_job("kumo")
    return {
        "clientId": boundary.client.id,
        "memory": {"scope": boundary.memory.scope, "scopeId": boundary.memory.scope_id, "key": PROCEDURE_KEY},
        "record": kumo_procedure().to_json(),
    }


def test_the_saved_procedure_record_and_where_it_lives_are_exactly_the_contract_the_screen_server_reads():
    produced = produce()
    if os.environ.get(RECORD_VARIABLE) == "1":
        CONTRACT.write_text(json.dumps(produced, ensure_ascii=False, indent=2) + "\n")
    assert produced == json.loads(CONTRACT.read_text())


def test_the_contract_record_is_one_the_agent_would_replay():
    contract = json.loads(CONTRACT.read_text())
    procedure = Procedure.from_json(contract["record"])

    assert procedure.client_id == contract["clientId"]
    assert procedure.step_count == len(contract["record"]["steps"])
    assert "$LP_" in procedure.script
