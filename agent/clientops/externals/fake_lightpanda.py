import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RECORDINGS = Path(__file__).resolve().parents[2] / "tests" / "contract" / "external" / "lightpanda"
CALL_RECORDINGS = ("supplier-stock", "store-save", "store-renamed-button", "errors-and-placeholders")
SCRIPT_RUNS_FILE = "pandascript-runs.json"
PLACEHOLDER = re.compile(r"\$LP_[A-Za-z0-9_]+")
LOGIN_NOT_SET_RUN = "login-not-set"


class UnrecordedCall(LookupError):
    pass


class CdpError(RuntimeError):
    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"{method} failed: {message} ({code})")
        self.method = method
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ScriptRun:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(slots=True)
class _Call:
    method: str
    params: dict[str, Any]
    response: dict[str, Any]
    wildcards: frozenset[str]
    answered: bool = False

    def matches(self, method: str, params: Mapping[str, Any]) -> bool:
        if self.answered or method != self.method or set(params) != set(self.params):
            return False
        return all(key in self.wildcards or params[key] == value for key, value in self.params.items())


def _load(name: str) -> dict:
    return json.loads((RECORDINGS / name).read_text())


def _script_runs() -> dict[str, ScriptRun]:
    return {
        run["scenario"]: ScriptRun(exit_code=run["exitCode"], stdout=run["stdout"], stderr=run["stderr"])
        for run in _load(SCRIPT_RUNS_FILE)["runs"]
    }


class FakeLightpanda:
    def __init__(self, recording: str, script_run: str = "saved") -> None:
        if recording not in CALL_RECORDINGS:
            raise ValueError(f"No Lightpanda recording named {recording!r}. Recorded: {', '.join(CALL_RECORDINGS)}")
        runs = _script_runs()
        if script_run not in runs:
            raise ValueError(f"No recorded script run named {script_run!r}. Recorded: {', '.join(runs)}")
        self._calls = [
            _Call(
                method=call["request"]["method"],
                params=call["request"]["params"],
                response=call["response"],
                wildcards=frozenset(call.get("redacted", ())),
            )
            for call in _load(f"{recording}.json")["calls"]
        ]
        self._runs = runs
        self._script_run = script_run
        self.recording = recording
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.scripts_run: list[tuple[str, tuple[str, ...]]] = []

    def send(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        sent = dict(params or {})
        self.sent.append((method, sent))
        call = next((call for call in self._calls if call.matches(method, sent)), None)
        if call is None:
            raise UnrecordedCall(f"{method} {json.dumps(sent, sort_keys=True)} is not in the {self.recording} recording")
        call.answered = True
        if "error" in call.response:
            error = call.response["error"]
            raise CdpError(method, error["code"], error["message"])
        return deepcopy(call.response["result"])

    def run_script(self, script: str, env: Mapping[str, str]) -> ScriptRun:
        self.scripts_run.append((script, tuple(sorted(env))))
        missing = {placeholder[1:] for placeholder in PLACEHOLDER.findall(script)} - set(env)
        return self._runs[LOGIN_NOT_SET_RUN if missing else self._script_run]
