import json
import sys
from typing import TextIO

from clientops.boundary import BoundaryError, computer_kind, put_logins
from clientops.computers import ComputerError
from clientops.externals import MissingExternalKey

LARGEST_INPUT = 64 * 1024
USAGE = "Give exactly one client id, and the logins as one JSON object on stdin."
UNEXPECTED = "The logins could not be added."


def reply(stdout: TextIO, body: dict) -> None:
    stdout.write(json.dumps(body) + "\n")
    stdout.flush()


def read_values(stdin: TextIO) -> dict | None:
    text = stdin.read(LARGEST_INPUT + 1)
    if len(text) > LARGEST_INPUT:
        return None
    try:
        values = json.loads(text)
    except ValueError:
        return None
    return values if isinstance(values, dict) else None


def main(arguments: list[str], stdin: TextIO, stdout: TextIO) -> int:
    values = read_values(stdin)
    if len(arguments) != 1 or values is None:
        reply(stdout, {"ok": False, "error": USAGE})
        return 2
    try:
        computer_kind()
    except ValueError as error:
        reply(stdout, {"ok": False, "error": str(error)})
        return 1
    try:
        status = put_logins(arguments[0], values)
    except (BoundaryError, ComputerError, MissingExternalKey) as error:
        reply(stdout, {"ok": False, "error": str(error)})
        return 1
    except Exception:
        reply(stdout, {"ok": False, "error": UNEXPECTED})
        return 1
    reply(stdout, {"ok": True, "present": list(status.present), "missing": list(status.missing)})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))
