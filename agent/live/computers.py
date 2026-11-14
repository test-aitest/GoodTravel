import os
import sys

from daytona import DaytonaNotFoundError

from clientops.check import secret_values
from clientops.computer_image import load_image_spec
from clientops.computers import CLIENT_LABEL, Computers
from clientops.externals import daytona_client, externals_mode
from clientops.response_shapes import without_secrets

CLIENTS = ("kumo", "hinata")


def say(line: str) -> None:
    print(without_secrets(line, secret_values(os.environ)), flush=True)


def shown(state: object) -> str:
    return str(getattr(state, "value", state))


def show() -> None:
    daytona = daytona_client()
    name = load_image_spec().snapshot
    try:
        snapshot = daytona.snapshot.get(name)
        say(f"snapshot {name}: {shown(snapshot.state)}")
    except DaytonaNotFoundError:
        say(f"snapshot {name}: not found")
    listed = list(daytona.list())
    say(f"sandboxes: {len(listed)}")
    for sandbox in listed:
        labels = sandbox.labels or {}
        say(
            f"sandbox {sandbox.id}: client={labels.get(CLIENT_LABEL, '-')} labels={sorted(labels)} "
            f"state={shown(sandbox.state)} auto_stop={sandbox.auto_stop_interval} snapshot={sandbox.snapshot}"
        )


def stop() -> None:
    computers = Computers(daytona_client())
    for client in CLIENTS:
        stopped = computers.stop(client)
        say(f"{client}: {'stopped now' if stopped else 'was not running'}, state {computers.state(client)}")


def main(arguments: list[str]) -> int:
    if externals_mode() != "real" or not os.environ.get("DAYTONA_API_KEY"):
        print("This probe talks to the real Daytona only: set DAYTONA_API_KEY and leave CLIENTOPS_EXTERNALS unset.", file=sys.stderr)
        return 1
    actions = {"show": show, "stop": stop}
    chosen = actions.get(arguments[0] if arguments else "show")
    if chosen is None:
        print(f"Say {' or '.join(actions)}.", file=sys.stderr)
        return 2
    chosen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
