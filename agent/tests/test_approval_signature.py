import hashlib
import hmac
import json
from pathlib import Path

VECTOR = json.loads((Path(__file__).parent / "contract" / "approval_signature.json").read_text())
PREFIX = "sha256="


def agentfield_accepts(secret: str, body: bytes, header: str) -> bool:
    signature = header[len(PREFIX) :] if len(header) > len(PREFIX) and header.startswith(PREFIX) else header
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.encode(), expected.encode())


def test_the_recorded_header_is_hmac_sha256_of_the_raw_body():
    digest = hmac.new(VECTOR["secret"].encode(), VECTOR["body"].encode(), hashlib.sha256).hexdigest()
    assert VECTOR["header"] == f"{PREFIX}{digest}"


def test_the_body_is_the_flat_approval_shape_agentfield_reads():
    assert json.loads(VECTOR["body"]) == {"requestId": "apr-exec_first_run", "decision": "approved", "feedback": ""}


def test_agentfield_would_accept_it_and_reject_any_change():
    body = VECTOR["body"].encode()
    assert agentfield_accepts(VECTOR["secret"], body, VECTOR["header"])
    assert not agentfield_accepts("another-secret", body, VECTOR["header"])
    assert not agentfield_accepts(VECTOR["secret"], body.replace(b"approved", b"rejected"), VECTOR["header"])
    assert not agentfield_accepts(VECTOR["secret"], body + b" ", VECTOR["header"])
