import json
import math
import re
from collections.abc import Callable, Iterable

from .ai_instructions import has_instruction, without_instructions

EVENT_NOTE_TAG = "clientops.event"
PAGE_HTML_MAX_BYTES = 262_144
HOST = re.compile(r"(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:\d{1,5})?", re.IGNORECASE)

NoteSender = Callable[[str, list[str]], None]


PROCEDURE_CHANGE_KEYS = frozenset({"stepId", "fromVersion", "toVersion", "before", "after"})


def check_procedure_change(change: dict) -> None:
    versions = (change.get("fromVersion"), change.get("toVersion"))
    texts = (change.get("stepId"), change.get("before"), change.get("after"))
    if set(change) != PROCEDURE_CHANGE_KEYS or not all(isinstance(text, str) and text.strip() for text in texts):
        raise ValueError(f"{change!r} is not a change to one saved step")
    if not all(isinstance(version, int) and not isinstance(version, bool) and version > 0 for version in versions) or versions[1] <= versions[0]:
        raise ValueError(f"{change!r} must go from a saved version to a newer one")


class EventNotes:
    def __init__(self, send: NoteSender, *, start: int = 0) -> None:
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError(f"{start!r} is not a number of notes already written")
        self._send = send
        self._count = start
        self._ignored_hosts: set[str] = set()

    @property
    def count(self) -> int:
        return self._count

    def emit(self, event_type: str, **body) -> dict:
        self._count += 1
        payload = {"n": self._count, "event": {"type": event_type, **body}}
        self._send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), [EVENT_NOTE_TAG])
        return payload

    def approval_requested(
        self,
        approval_id: str,
        step_id: str,
        question: str,
        changes: list[dict],
        *,
        undoable: bool,
        only_in_store: int = 0,
        only_at_supplier: int = 0,
        procedure_change: dict | None = None,
    ) -> dict:
        if not changes:
            raise ValueError("An approval needs at least one change")
        for count in (only_in_store, only_at_supplier):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"{count!r} is not a count of products")
        body = {"approvalId": approval_id, "stepId": step_id, "question": question, "changes": changes, "undoable": undoable}
        if only_in_store or only_at_supplier:
            body["leftAlone"] = {"onlyInStore": only_in_store, "onlyAtSupplier": only_at_supplier}
        if procedure_change is not None:
            check_procedure_change(procedure_change)
            body["procedureChange"] = procedure_change
        return self.emit("approval.requested", **body)

    def cost_recorded(
        self,
        *,
        claude_calls: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        claude_usd: float,
        computer_seconds: int,
        estimated: bool,
    ) -> dict:
        counts = {
            "claudeCalls": claude_calls,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheReadTokens": cache_read_tokens,
            "cacheWriteTokens": cache_write_tokens,
        }
        for name, value in {**counts, "computerSeconds": computer_seconds}.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a whole number of 0 or more, not {value!r}")
        if isinstance(claude_usd, bool) or not isinstance(claude_usd, int | float) or not math.isfinite(claude_usd) or claude_usd < 0:
            raise ValueError(f"claudeUsd must be a number of US dollars of 0 or more, not {claude_usd!r}")
        if not isinstance(estimated, bool):
            raise ValueError(f"estimated must be true or false, not {estimated!r}")
        if claude_calls == 0 and (claude_usd != 0 or any(counts[name] for name in counts if name != "claudeCalls")):
            raise ValueError("A job with no Claude calls has no Claude tokens or cost")
        return self.emit("cost.recorded", **counts, claudeUsd=claude_usd, computerSeconds=computer_seconds, estimated=estimated)

    def page_seen(self, step_id: str, host: str, html: str) -> dict:
        if not HOST.fullmatch(host):
            raise ValueError(f"{host!r} is not a host name with an optional port")
        if not html:
            raise ValueError("A seen page needs its HTML")
        size = len(html.encode("utf-8"))
        if size > PAGE_HTML_MAX_BYTES:
            raise ValueError(f"The page is {size} bytes; the screen takes at most {PAGE_HTML_MAX_BYTES}")
        shown, ignored = without_instructions(html)
        payload = self.emit("page.seen", stepId=step_id, host=host, html=shown)
        if ignored:
            self.instruction_ignored(step_id, host)
        return payload

    def page_read(self, step_id: str, host: str, text: str, names: Iterable[str] = ()) -> dict | None:
        if has_instruction([text, *names]):
            return self.instruction_ignored(step_id, host)
        return None

    def instruction_ignored(self, step_id: str, host: str) -> dict | None:
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError(f"{step_id!r} is not the step that read the page")
        if not HOST.fullmatch(host):
            raise ValueError(f"{host!r} is not a host name with an optional port")
        if host in self._ignored_hosts:
            return None
        self._ignored_hosts.add(host)
        return self.emit("instruction.ignored", stepId=step_id, host=host)
