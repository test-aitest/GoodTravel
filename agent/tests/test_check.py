import json
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

import anthropic
import pytest
from daytona import CreateSnapshotParams, DaytonaConnectionError, SandboxState
from daytona_api_client import SnapshotState

from anthropic_stub import AnthropicStub
from clientops.check import (
    CHECK_CLIENT,
    TREE_COMMAND,
    Asked,
    Services,
    app_ai_asker,
    check_call,
    run,
    sdk_asker,
)
from clientops.computer_image import load_image_spec
from clientops.computers import Computers
from clientops.externals.fake_ai import FakeClaude, choice_text, wire_message, wire_usage
from clientops.externals.fake_daytona import FakeDaytona
from clientops.response_shapes import load_sample

AGENT_DIR = Path(__file__).resolve().parents[1]
TEST_KEY = "sk-ant-test-not-a-real-key"
DAYTONA_KEY = "dtn_test_not_a_real_key"
KEYS = {"ANTHROPIC_API_KEY": TEST_KEY, "DAYTONA_API_KEY": DAYTONA_KEY}
SNAPSHOT = load_image_spec().snapshot
LIGHTPANDA_ANSWER = json.dumps(
    {
        "Browser": "Lightpanda/1.0",
        "Protocol-Version": "1.3",
        "User-Agent": "Lightpanda/1.0",
        "Lightpanda-Version": "1.0.4",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/",
    }
)
SIGN_IN_TREE = """1 RootWebArea 'Partner sign in - Maruyama Supply'
 6 'Maruyama Supply'
 8 'Partner Portal'
 10 form
  11 heading 'Partner sign in'
  13 'Account ID'
  14 [i] textbox 'Account ID'
  16 'Password'
  17 [i] textbox 'Password'
  18 [i] button 'Sign in'
 19 paragraph
  20 'Contact your account manager if you need access.'
 21 contentinfo
  22 'Maruyama Supply Co., Ltd. Partner Portal. For registered partner retailers only.'
"""
DAYTONA_BODIES = {
    "SnapshotDto": load_sample("daytona/snapshot.json"),
    "ListSandboxesResponse": load_sample("daytona/list_sandboxes.json"),
    "Sandbox": load_sample("daytona/sandbox.json"),
    "ExecuteResponse": load_sample("daytona/execute_response_echo.json"),
}


def save_changes_id() -> str:
    _, elements = check_call()
    return next(letter for letter, element in elements.items() if element.name == "Save changes")


def discard_id() -> str:
    _, elements = check_call()
    return next(letter for letter, element in elements.items() if element.name == "Discard")


def sent_request(call: dict) -> dict:
    return {
        "model": call["model"].removeprefix("anthropic/"),
        "messages": [{"role": "user", "content": [{"type": "text", "text": call["user"]}]}],
        "system": [{"type": "text", "text": call["system"]}],
        "max_tokens": call["max_tokens"],
        "output_config": call["output_config"],
    }


def fake_app_ai(fake: FakeClaude, *, sent=sent_request):
    async def ask(call: dict) -> Asked:
        try:
            message = fake.next_wire_message()
        except AssertionError:
            message = None
        response = await fake(**call)
        return Asked(response, message, sent(call))

    return ask


def choice_reply(element_id: str) -> dict:
    return wire_message(choice_text(element_id, "click", True), "end_turn", None, wire_usage(5200, 640, thinking_tokens=590))


def not_called(name: str):
    def fail():
        raise AssertionError(f"{name} must not be used")

    return fail


def recorded(bodies: dict):
    @contextmanager
    def record(responses):
        for kind, body in bodies.items():
            responses.record_daytona(kind, body)
        yield

    return record


def commands_for(client_id: str = CHECK_CLIENT) -> tuple[str, str]:
    computer = Computers(FakeDaytona()).open(client_id)
    return computer.browser_command(), computer.sites_command()


def ready_daytona(*, tree: str = SIGN_IN_TREE, tree_exit: int = 0, browser: str = LIGHTPANDA_ANSWER) -> FakeDaytona:
    daytona = FakeDaytona()
    daytona.snapshot.create(CreateSnapshotParams(name=SNAPSHOT, image="debian:trixie-slim"))
    browser_command, _ = commands_for()
    daytona.script_command(browser_command, {"exitCode": 0, "result": browser})
    daytona.script_command(TREE_COMMAND, {"exitCode": tree_exit, "result": tree})
    daytona.calls.clear()
    return daytona


@pytest.fixture
def claude():
    fake = FakeClaude()
    fake.reply_choice(save_changes_id(), "click", input_tokens=5200, output_tokens=640, thinking_tokens=590)
    return fake


def services(tmp_path, *, app_ai=None, sdk=None, daytona=None, bodies=DAYTONA_BODIES) -> Services:
    return Services(
        app_ai=(lambda: app_ai) if app_ai else not_called("app.ai"),
        sdk=(lambda: sdk) if sdk else not_called("the Anthropic SDK"),
        daytona=(lambda: daytona) if daytona is not None else not_called("Daytona"),
        record_daytona=recorded(bodies),
        out_dir=lambda: tmp_path / "saved",
    )


def run_lines(env: dict, chosen: Services) -> tuple[int, list[str]]:
    printed: list[str] = []
    code = run(env, chosen, printed.append)
    return code, printed


def sandbox_id(daytona: FakeDaytona) -> str:
    return next(call.sandbox_id for call in daytona.calls if call.method == "create")


def test_missing_keys_stop_with_one_line_that_names_them(tmp_path):
    code, printed = run_lines({}, services(tmp_path))
    assert code == 1
    assert printed == [
        "missing Settings    ANTHROPIC_API_KEY missing, DAYTONA_API_KEY missing. "
        "Put them in .env.local, then run bun run check again."
    ]


def test_one_missing_key_is_named_and_the_present_one_is_only_called_set(tmp_path):
    code, printed = run_lines({"ANTHROPIC_API_KEY": TEST_KEY, "DAYTONA_API_KEY": ""}, services(tmp_path))
    assert code == 1
    assert printed == [
        "missing Settings    ANTHROPIC_API_KEY set, DAYTONA_API_KEY missing. "
        "Put it in .env.local, then run bun run check again."
    ]


def test_the_mocks_switch_is_refused_because_the_check_is_about_the_real_services(tmp_path):
    code, printed = run_lines({**KEYS, "CLIENTOPS_EXTERNALS": "fake"}, services(tmp_path))
    assert code == 1
    assert printed == [
        "fail    Settings    CLIENTOPS_EXTERNALS is 'fake', but the check talks to the real services only. "
        "Unset it, then run bun run check again."
    ]


def test_every_check_passes_with_the_documented_mocks(tmp_path, claude):
    daytona = ready_daytona()
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))

    assert code == 0, printed
    computer_id = sandbox_id(daytona)
    assert printed[0] == (
        "ok      Claude      claude-opus-5-5 answered through AgentField app.ai with effort medium and the structured output: "
        'picked "Save changes" (5200 in, 640 out, $0.0336)'
    )
    assert re.fullmatch(
        rf"ok      Daytona     Kumo's computer {computer_id} was made in \d+\.\d s and is started \(snapshot {SNAPSHOT}\)",
        printed[1],
    )
    assert printed[2] == (
        "ok      Lightpanda  Lightpanda 1.0.4 in Kumo's computer read Maruyama Supply's sign-in page: "
        "14 lines in its semantic tree"
    )
    saved = sorted((tmp_path / "saved").iterdir())
    assert len(saved) == 1
    assert printed[3] == (
        "ok      Shapes      6 of 6 real responses match the contract samples field by field. "
        f"Saved in {saved[0]}"
    )
    assert len(printed) == 4
    assert len(claude.received) == 1


def test_the_computer_found_with_the_clients_label_is_used_again(tmp_path, claude):
    daytona = ready_daytona()
    existing = Computers(daytona).open(CHECK_CLIENT)
    existing.stop()
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert code == 0, printed
    assert re.fullmatch(
        rf"ok      Daytona     Kumo's computer {existing.id} was found in \d+\.\d s and is started \(snapshot {SNAPSHOT}\)",
        printed[1],
    )
    assert [call.method for call in daytona.calls].count("create") == 1


def test_the_daytona_check_reads_the_snapshot_finds_or_makes_the_computer_and_reads_it_back(tmp_path, claude):
    daytona = ready_daytona()
    run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    methods = [call.method for call in daytona.calls]
    assert methods[:4] == ["snapshot.get", "list", "create", "refresh_data"]
    assert "list" in methods[4:]
    executed = [call.arguments["command"] for call in daytona.calls if call.method == "exec"]
    browser_command, sites_command = commands_for()
    assert executed == [sites_command, browser_command, TREE_COMMAND]
    assert TREE_COMMAND == "lightpanda fetch --dump semantic_tree_text http://127.0.0.1:3001/login 2>/dev/null"


def test_when_app_ai_fails_the_sdk_is_tried_once_and_the_line_says_which_path_worked(tmp_path):
    failing = FakeClaude()
    failing.fail_invalid_request("output_config.effort: unknown field")
    with AnthropicStub() as stub:
        stub.reply(200, choice_reply(save_changes_id()))
        client = anthropic.Anthropic(api_key=TEST_KEY, base_url=stub.url, max_retries=0)
        code, printed = run_lines(
            KEYS,
            services(tmp_path, app_ai=fake_app_ai(failing), sdk=sdk_asker(client), daytona=ready_daytona()),
        )
        sent = stub.requests
    assert code == 1
    assert printed[0] == (
        "fail    Claude      AgentField app.ai did not work (BadRequestError: litellm.BadRequestError: AnthropicException - "
        '{"type": "error", "error": {"type": "invalid_request_error", "message": "output_config.effort: unknown field"}, '
        '"request_id": "req_fake400"}); the Anthropic SDK directly did: claude-opus-5-5 picked "Save changes" '
        "with effort medium and the structured output (5200 in, 640 out, $0.0336)"
    )
    [request] = sent
    assert request.body["model"] == "claude-opus-5-5"
    assert request.body["output_config"] == check_call()[0]["output_config"]
    assert request.body["max_tokens"] == 16000
    assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
    assert printed[-1].startswith("ok      Shapes      5 of 5 real responses match the contract samples field by field.")
    assert printed[-1].endswith("Not recorded: anthropic/app-ai-returns.json (choice).")


def test_when_both_paths_fail_the_line_names_both_failures(tmp_path):
    failing = FakeClaude()
    failing.fail_unauthorized()
    client = anthropic.Anthropic(api_key=TEST_KEY, base_url="http://127.0.0.1:9", max_retries=0)
    code, printed = run_lines(
        KEYS, services(tmp_path, app_ai=fake_app_ai(failing), sdk=sdk_asker(client), daytona=ready_daytona())
    )
    assert code == 1
    assert printed[0] == (
        "fail    Claude      AgentField app.ai did not work (AuthenticationError: litellm.AuthenticationError: AnthropicException - "
        '{"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}, "request_id": "req_fake401"}), '
        "and neither did the Anthropic SDK directly (APIConnectionError: Connection error.)"
    )


@pytest.mark.parametrize(
    ("script", "problem"),
    [
        (lambda fake: fake.reply_refusal("cyber"), "declined the request"),
        (lambda fake: fake.reply_cut_off('{"element_id":"d'), "stopped at max_tokens before the JSON was complete"),
        (lambda fake: fake.reply_text("Save changes is the button."), "did not answer with the agreed JSON"),
        (lambda fake: fake.reply_choice(save_changes_id(), "click", confident=False), "was not sure"),
        (lambda fake: fake.reply_choice(discard_id(), "click"), 'picked "Discard", not "Save changes"'),
        (lambda fake: fake.reply_choice("zz", "click"), "answered with an element that is not on the page"),
    ],
)
def test_an_answer_that_is_not_usable_fails_without_a_second_call(tmp_path, script, problem):
    fake = FakeClaude()
    script(fake)
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(fake), daytona=ready_daytona()))
    assert code == 1
    assert printed[0] == f"fail    Claude      claude-opus-5-5 answered through AgentField app.ai, but {problem}"
    assert len(fake.received) == 1


def test_a_request_sent_without_effort_medium_and_the_schema_fails(tmp_path, claude):
    def high_effort(call):
        return {**sent_request(call), "output_config": {"effort": "high"}}

    code, printed = run_lines(
        KEYS, services(tmp_path, app_ai=fake_app_ai(claude, sent=high_effort), daytona=ready_daytona())
    )
    assert code == 1
    assert printed[0] == (
        "fail    Claude      AgentField app.ai sent output_config {\"effort\": \"high\"}, "
        "not effort medium with the JSON schema"
    )


def test_an_unrecorded_request_is_said_but_does_not_fail_the_answer(tmp_path, claude):
    code, printed = run_lines(
        KEYS, services(tmp_path, app_ai=fake_app_ai(claude, sent=lambda call: None), daytona=ready_daytona())
    )
    assert code == 0, printed
    assert printed[0] == (
        "ok      Claude      claude-opus-5-5 answered through AgentField app.ai: picked \"Save changes\" "
        "(5200 in, 640 out, $0.0336). The request itself was not recorded, so effort and schema are unconfirmed"
    )


def test_the_app_ai_asker_hands_the_call_to_app_ai_unchanged(claude):
    import asyncio

    call, _ = check_call()
    asked = asyncio.run(app_ai_asker(claude)(call))
    [received] = claude.received
    assert received.model == "anthropic/claude-opus-5-5"
    assert received.output_config == call["output_config"]
    assert received.max_tokens == 16000
    assert asked.response.text == choice_text(save_changes_id(), "click", True)


def test_a_missing_snapshot_is_named_with_how_to_build_it_and_lightpanda_is_skipped(tmp_path, claude):
    daytona = FakeDaytona()
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert code == 1
    assert printed[1] == (
        f"fail    Daytona     The computer snapshot {SNAPSHOT} is not built yet. "
        "Build it with bun run computer:snapshot, then run bun run check again."
    )
    assert printed[2] == "skip    Lightpanda  needs Kumo's computer, which did not open"
    assert not [call for call in daytona.calls if call.method == "create"]


def test_a_sleeping_snapshot_is_named_with_how_to_wake_it(tmp_path, claude):
    daytona = ready_daytona()
    daytona.snapshot._set_state(SNAPSHOT, SnapshotState.INACTIVE)
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert code == 1
    assert printed[1] == (
        f"fail    Daytona     The computer snapshot {SNAPSHOT} is inactive. "
        "Run bun run computer:snapshot to wake or rebuild it, then run bun run check again."
    )


class UnreachableDaytona(FakeDaytona):
    def list(self, query=None, request_timeout=None):
        raise DaytonaConnectionError("Failed to list sandboxes: connection refused")


def test_an_unreachable_daytona_is_a_fail_line_and_the_next_check_is_skipped(tmp_path, claude):
    daytona = UnreachableDaytona()
    daytona.snapshot.create(CreateSnapshotParams(name=SNAPSHOT, image="debian:trixie-slim"))
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert code == 1
    assert printed[1] == (
        "fail    Daytona     Could not find kumo's computer: Daytona could not be reached. "
        "Check the network connection. (Daytona: Failed to list sandboxes: connection refused)"
    )
    assert printed[2] == "skip    Lightpanda  needs Kumo's computer, which did not open"


def test_lightpanda_that_does_not_answer_is_a_fail_line(tmp_path, claude):
    daytona = ready_daytona(browser="")
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert code == 1
    assert printed[2].startswith("fail    Lightpanda  Lightpanda did not answer on Kumo's computer within 15 seconds.")


def test_a_tree_that_cannot_be_read_is_a_fail_line_with_the_output(tmp_path, claude):
    daytona = ready_daytona(tree="error: ConnectionRefused\n", tree_exit=1)
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert code == 1
    assert printed[2] == (
        "fail    Lightpanda  Lightpanda in Kumo's computer could not read Maruyama Supply's sign-in page "
        "(exit code 1): error: ConnectionRefused"
    )


def test_an_answer_that_is_not_a_semantic_tree_is_a_fail_line(tmp_path, claude):
    daytona = ready_daytona(tree="<html><body>hello</body></html>\n")
    code, printed = run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert code == 1
    assert printed[2] == (
        "fail    Lightpanda  Lightpanda in Kumo's computer answered with something other than a semantic tree: "
        "<html><body>hello</body></html>"
    )


def test_a_real_response_with_another_shape_is_printed_field_by_field(tmp_path, claude):
    sandbox = {**DAYTONA_BODIES["Sandbox"], "backupState": "None", "autoStopInterval": "15"}
    del sandbox["cpu"]
    bodies = {**DAYTONA_BODIES, "Sandbox": sandbox}
    code, printed = run_lines(
        KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=ready_daytona(), bodies=bodies)
    )
    assert code == 1
    assert printed[3] == (
        "diff    Shapes      daytona/sandbox.json: cpu is missing; autoStopInterval is string, not number; "
        "backupState is new (string)"
    )
    saved = sorted((tmp_path / "saved").iterdir())[0]
    assert printed[4] == f"diff    Shapes      5 of 6 real responses match the contract samples field by field. Saved in {saved}"
    assert json.loads((saved / "daytona" / "sandbox.json").read_text())["backupState"] == "None"


def test_nothing_recorded_skips_the_comparison(tmp_path):
    failing = FakeClaude()
    failing.fail_unauthorized()
    client = anthropic.Anthropic(api_key=TEST_KEY, base_url="http://127.0.0.1:9", max_retries=0)
    code, printed = run_lines(
        KEYS,
        services(tmp_path, app_ai=fake_app_ai(failing), sdk=sdk_asker(client), daytona=FakeDaytona(), bodies={}),
    )
    assert code == 1
    assert printed[-1] == "skip    Shapes      nothing real was recorded, so nothing was compared"
    assert not (tmp_path / "saved").exists()


def test_an_unexpected_error_in_one_check_becomes_its_fail_line_and_the_rest_still_run(tmp_path, claude):
    def broken_daytona():
        raise RuntimeError("the client could not be built")

    chosen = Services(
        app_ai=lambda: fake_app_ai(claude),
        sdk=not_called("the Anthropic SDK"),
        daytona=broken_daytona,
        record_daytona=recorded({}),
        out_dir=lambda: tmp_path / "saved",
    )
    code, printed = run_lines(KEYS, chosen)
    assert code == 1
    assert printed[0].startswith("ok      Claude")
    assert printed[1] == "fail    Daytona     stopped unexpectedly: RuntimeError: the client could not be built"
    assert printed[2] == "skip    Lightpanda  needs Kumo's computer, which did not open"


def test_no_secret_value_is_ever_printed_or_saved(tmp_path):
    secrets = {
        **KEYS,
        "AGENTFIELD_APPROVAL_WEBHOOK_SECRET": "whsec-test-not-real-1234",
        "LP_STORE_PASSWORD": "demo-pass-not-real",
    }
    failing = FakeClaude()
    failing.fail_invalid_request(f"bad key {TEST_KEY} and {secrets['LP_STORE_PASSWORD']}")

    class LeakyDaytona(FakeDaytona):
        def list(self, query=None, request_timeout=None):
            raise DaytonaConnectionError(f"refused for key {DAYTONA_KEY}")

    daytona = LeakyDaytona()
    daytona.snapshot.create(CreateSnapshotParams(name=SNAPSHOT, image="debian:trixie-slim"))
    leaky_bodies = {**DAYTONA_BODIES, "Sandbox": {**DAYTONA_BODIES["Sandbox"], "name": secrets["AGENTFIELD_APPROVAL_WEBHOOK_SECRET"]}}
    with AnthropicStub() as stub:
        stub.reply(401, {"type": "error", "error": {"type": "authentication_error", "message": f"invalid x-api-key {TEST_KEY}"}, "request_id": "req_1"})
        client = anthropic.Anthropic(api_key=TEST_KEY, base_url=stub.url, max_retries=0)
        code, printed = run_lines(
            secrets,
            services(tmp_path, app_ai=fake_app_ai(failing), sdk=sdk_asker(client), daytona=daytona, bodies=leaky_bodies),
        )
    output = "\n".join(printed)
    saved = "\n".join(path.read_text() for path in (tmp_path / "saved").rglob("*.json"))
    assert code == 1
    for value in secrets.values():
        assert value not in output
        assert value not in saved
    assert "[hidden]" in output

    _, missing = run_lines({"ANTHROPIC_API_KEY": TEST_KEY}, services(tmp_path))
    assert TEST_KEY not in "\n".join(missing)


def test_the_module_prints_the_missing_line_on_stdout_and_exits_1_without_keys():
    env = {name: value for name, value in os.environ.items() if name in ("PATH", "HOME", "UV_CACHE_DIR", "TMPDIR")}
    finished = subprocess.run(
        ["uv", "run", "python", "-m", "clientops.check"],
        cwd=AGENT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert finished.returncode == 1, finished.stderr
    assert finished.stdout == (
        "missing Settings    ANTHROPIC_API_KEY missing, DAYTONA_API_KEY missing. "
        "Put them in .env.local, then run bun run check again.\n"
    )


def test_the_check_stops_its_computer_never_and_leaves_auto_stop_to_daytona(tmp_path, claude):
    daytona = ready_daytona()
    run_lines(KEYS, services(tmp_path, app_ai=fake_app_ai(claude), daytona=daytona))
    assert not [call for call in daytona.calls if call.method == "stop"]
    assert Computers(daytona).state(CHECK_CLIENT) is SandboxState.STARTED


def never_recorded(responses):
    raise AssertionError("Daytona must not be recorded")


def claude_only(tmp_path, *, app_ai=None, sdk=None) -> Services:
    return Services(
        app_ai=(lambda: app_ai) if app_ai else not_called("app.ai"),
        sdk=(lambda: sdk) if sdk else not_called("the Anthropic SDK"),
        daytona=not_called("Daytona"),
        record_daytona=never_recorded,
        out_dir=lambda: tmp_path / "saved",
        agentfield=not_called("AgentField"),
    )


def claude_only_lines(env: dict, chosen: Services) -> tuple[int, list[str]]:
    printed: list[str] = []
    code = run(env, chosen, printed.append, only="claude")
    return code, printed


def test_only_claude_needs_only_the_anthropic_key_and_leaves_agentfield_and_daytona_alone(tmp_path, claude):
    code, printed = claude_only_lines({"ANTHROPIC_API_KEY": TEST_KEY}, claude_only(tmp_path, app_ai=fake_app_ai(claude)))
    assert code == 0, printed
    [saved] = sorted((tmp_path / "saved").iterdir())
    assert printed == [
        "ok      Claude      claude-opus-5-5 answered through AgentField app.ai with effort medium and the structured output: "
        'picked "Save changes" (5200 in, 640 out, $0.0336)',
        f"ok      Shapes      2 of 2 real responses match the contract samples field by field. Saved in {saved}",
    ]
    assert len(claude.received) == 1
    assert sorted(path.relative_to(saved).as_posix() for path in saved.rglob("*.json")) == [
        "anthropic/app-ai-returns.json",
        "anthropic/messages-choice.json",
    ]


def test_only_claude_names_only_the_anthropic_key_when_it_is_missing(tmp_path):
    code, printed = claude_only_lines({"DAYTONA_API_KEY": DAYTONA_KEY}, claude_only(tmp_path))
    assert code == 1
    assert printed == ["missing Settings    ANTHROPIC_API_KEY missing. Put it in .env.local, then run bun run check again."]


def test_only_claude_still_refuses_the_mocks_switch(tmp_path):
    code, printed = claude_only_lines({"ANTHROPIC_API_KEY": TEST_KEY, "CLIENTOPS_EXTERNALS": "fake"}, claude_only(tmp_path))
    assert code == 1
    assert printed == [
        "fail    Settings    CLIENTOPS_EXTERNALS is 'fake', but the check talks to the real services only. "
        "Unset it, then run bun run check again."
    ]


def test_only_claude_through_the_sdk_compares_the_message_and_names_the_app_ai_sample_it_could_not_record(tmp_path):
    failing = FakeClaude()
    failing.fail_invalid_request("output_config.effort: unknown field")
    with AnthropicStub() as stub:
        stub.reply(200, choice_reply(save_changes_id()))
        client = anthropic.Anthropic(api_key=TEST_KEY, base_url=stub.url, max_retries=0)
        code, printed = claude_only_lines(
            {"ANTHROPIC_API_KEY": TEST_KEY}, claude_only(tmp_path, app_ai=fake_app_ai(failing), sdk=sdk_asker(client))
        )
        sent = stub.requests
    assert code == 1
    assert len(sent) == 1
    assert len(printed) == 2
    assert printed[0].startswith("fail    Claude      AgentField app.ai did not work (")
    assert printed[1].startswith("ok      Shapes      1 of 1 real responses match the contract samples field by field. Saved in ")
    assert printed[1].endswith("Not recorded: anthropic/app-ai-returns.json (choice).")


def test_the_module_runs_only_claude_when_asked_and_refuses_a_part_it_does_not_know():
    env = {name: value for name, value in os.environ.items() if name in ("PATH", "HOME", "UV_CACHE_DIR", "TMPDIR")}

    def module(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["uv", "run", "python", "-m", "clientops.check", *arguments],
            cwd=AGENT_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    only_claude = module("--only", "claude")
    assert only_claude.returncode == 1, only_claude.stderr
    assert only_claude.stdout == "missing Settings    ANTHROPIC_API_KEY missing. Put it in .env.local, then run bun run check again.\n"
    unknown = module("--only", "agentfield")
    assert unknown.returncode == 2
    assert unknown.stdout == ""
    assert "invalid choice: 'agentfield'" in unknown.stderr
