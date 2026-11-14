import json
from pathlib import Path

import pytest

from clientops.externals.fake_lightpanda import RECORDINGS, CdpError, FakeLightpanda, ScriptRun, UnrecordedCall

SAMPLES = Path(__file__).parent / "contract" / "external" / "lightpanda"
CALL_FILES = ["supplier-stock", "store-save", "store-renamed-button", "errors-and-placeholders"]


def sample(name: str):
    return json.loads((SAMPLES / f"{name}.json").read_text())


def recorded_calls():
    for name in CALL_FILES:
        for call in sample(name)["calls"]:
            yield name, call


def documented_returns():
    commands = sample("protocol-lp")["commands"]
    return {f"LP.{command['name']}": {item["name"] for item in command.get("returns", [])} for command in commands}


def tree_nodes(node):
    yield node
    for child in node.get("children", []):
        yield from tree_nodes(child)


def test_the_fake_reads_the_recorded_samples():
    assert RECORDINGS == SAMPLES


def test_every_sample_was_recorded_from_lightpanda_1_0_4_on_2026_11_14():
    for name in [*CALL_FILES, "pandascript-runs", "pandascript-probes", "protocol-lp"]:
        observed = sample(name)["observed"]
        assert observed["lightpanda"] == "1.0.4", name
        assert observed["date"] == "2026-11-14", name


def test_lp_results_hold_only_the_fields_the_protocol_documents():
    returns = documented_returns()
    checked = set()
    for name, call in recorded_calls():
        method = call["request"]["method"]
        if method not in returns or "result" not in call["response"]:
            continue
        result = call["response"]["result"]
        if returns[method]:
            assert set(result) <= returns[method], (name, method, set(result))
        else:
            assert result in ([], {}), (name, method)
        checked.add(method)
    assert checked == set(returns)


def test_semantic_tree_nodes_hold_only_documented_fields():
    documented = {
        "nodeId", "backendDOMNodeId", "nodeName", "xpath", "nodeType", "children", "role", "isInteractive",
        "name", "value", "attributes", "isDisabled", "checked", "ariaExpanded", "ariaChecked", "options", "nodeValue",
    }
    trees = [
        call["response"]["result"]["semanticTree"]
        for _, call in recorded_calls()
        if call["request"]["method"] == "LP.getSemanticTree" and isinstance(call["response"].get("result", {}).get("semanticTree"), dict)
    ]
    assert trees
    for tree in trees:
        for node in tree_nodes(tree):
            assert set(node) <= documented, set(node) - documented


def test_interactive_elements_hold_only_documented_fields():
    documented = {
        "backendNodeId", "tagName", "role", "name", "type", "tabIndex", "listeners", "disabled", "overlay", "id",
        "class", "href", "inputType", "value", "elementName", "placeholder",
    }
    elements = [
        element
        for _, call in recorded_calls()
        if call["request"]["method"] == "LP.getInteractiveElements"
        for element in call["response"]["result"]["elements"]
    ]
    assert elements
    for element in elements:
        assert set(element) <= documented, set(element) - documented
        assert element["type"] in {"native", "aria", "contenteditable", "listener", "focusable"}


def test_sign_in_values_are_not_in_the_samples():
    redacted = [call for _, call in recorded_calls() if call.get("redacted")]
    assert len(redacted) == 6
    for call in redacted:
        assert call["request"]["method"] == "LP.fillNode"
        assert call["redacted"] == ["text"]
        assert call["request"]["params"]["text"] is None
    script = (SAMPLES / "store-set-available.js").read_text()
    assert "$LP_STORE_USERNAME" in script
    assert "$LP_STORE_PASSWORD" in script


def test_send_returns_the_recorded_result_in_order():
    fake = FakeLightpanda("store-save")
    form = {"format": "text", "interactiveOnly": True}
    before = fake.send("LP.getSemanticTree", form)["semanticTree"]
    assert "[i] spinbutton 'Available for Ethiopia Guji 200g' value='18'" in before
    assert "[i] button 'Save changes'" in before
    assert fake.send("LP.getNodeDetails", {"backendNodeId": 43})["nodeDetails"]["value"] == "18"
    assert fake.send("LP.fillNode", {"backendNodeId": 43, "text": "6"}) == []
    assert fake.send("LP.getNodeDetails", {"backendNodeId": 43})["nodeDetails"]["value"] == "6"
    after = fake.send("LP.getSemanticTree", form)["semanticTree"]
    assert "[i] spinbutton 'Available for Ethiopia Guji 200g' value='6'" in after


def test_send_replays_sign_in_fill_and_save():
    fake = FakeLightpanda("store-save")
    target = fake.send("Target.createTarget", {"url": "about:blank", "browserContextId": "BID-1"})["targetId"]
    fake.send("Page.navigate", {"url": "http://127.0.0.1:3002/login"})
    sign_in = fake.send("LP.getInteractiveElements")["elements"]
    by_name = {element.get("elementName") or element["name"]: element["backendNodeId"] for element in sign_in}
    assert fake.send("LP.fillNode", {"backendNodeId": by_name["username"], "text": "any login"}) == []
    assert fake.send("LP.fillNode", {"backendNodeId": by_name["password"], "text": "any password"}) == []
    assert fake.send("LP.clickNode", {"backendNodeId": by_name["Sign in"]}) == []
    assert fake.send("Target.getTargetInfo", {"targetId": target})["targetInfo"]["url"] == "http://127.0.0.1:3002/inventory"
    assert fake.send("LP.clickNode", {"backendNodeId": 333}) == []
    status = fake.send("LP.waitForSelector", {"selector": '[role="status"]'})["backendNodeId"]
    assert fake.send("Target.getTargetInfo", {"targetId": target})["targetInfo"]["url"] == "http://127.0.0.1:3002/inventory?saved=1"
    status_text = fake.send("LP.getSemanticTree", {"format": "text", "backendNodeId": status})["semanticTree"]
    assert "'Saved. 1 product updated.'" in status_text
    assert [method for method, _ in fake.sent][:3] == ["Target.createTarget", "Page.navigate", "LP.getInteractiveElements"]


def test_the_renamed_button_recording_shows_the_new_name():
    tree = FakeLightpanda("store-renamed-button").send("LP.getSemanticTree", {"format": "text", "interactiveOnly": True})["semanticTree"]
    assert "[i] button 'Update inventory'" in tree
    assert "'Save changes'" not in tree


def test_the_supplier_recording_reads_all_three_stock_pages():
    fake = FakeLightpanda("supplier-stock")
    pages = [fake.send("LP.getSemanticTree", {"format": "text"})["semanticTree"] for _ in range(3)]
    assert "'Showing 1–20 of 42'" in pages[0]
    assert "'Showing 21–40 of 42'" in pages[1]
    assert "'Showing 41–42 of 42'" in pages[2]


def test_a_recorded_error_raises_cdp_error():
    fake = FakeLightpanda("errors-and-placeholders")
    with pytest.raises(CdpError) as raised:
        fake.send("LP.fillNode", {"backendNodeId": 99999, "text": "6"})
    assert raised.value.code == -31998
    assert raised.value.message == "InvalidNodeId"
    with pytest.raises(CdpError, match="InvalidParam"):
        fake.send("LP.clickNode")


def test_cdp_does_not_resolve_placeholders():
    fake = FakeLightpanda("errors-and-placeholders")
    assert fake.send("LP.fillNode", {"backendNodeId": 1, "text": "$LP_STORE_USERNAME"}) == []
    assert fake.send("LP.getNodeDetails", {"backendNodeId": 1})["nodeDetails"]["value"] == "$LP_STORE_USERNAME"


def test_an_unrecorded_call_raises():
    fake = FakeLightpanda("store-save")
    with pytest.raises(UnrecordedCall, match="LP.clickNode"):
        fake.send("LP.clickNode", {"backendNodeId": 12345})


def test_a_call_is_answered_only_as_many_times_as_it_was_recorded():
    fake = FakeLightpanda("store-save")
    fake.send("LP.detectForms")
    with pytest.raises(UnrecordedCall):
        fake.send("LP.detectForms")


def test_an_unknown_recording_is_refused_with_the_known_names():
    with pytest.raises(ValueError, match="store-save"):
        FakeLightpanda("store-delete")


def test_run_script_returns_the_recorded_successful_run():
    script = (SAMPLES / "store-set-available.js").read_text()
    run = FakeLightpanda("store-save").run_script(script, {"LP_STORE_USERNAME": "user", "LP_STORE_PASSWORD": "secret"})
    assert isinstance(run, ScriptRun)
    assert run.ok
    assert run.exit_code == 0
    assert json.loads(run.stdout) == {"sku": "KC-ETH-200", "before": "18", "after": "6", "message": "Saved. 1 product updated."}
    assert run.stderr == ""


def test_run_script_returns_the_recorded_failed_check():
    script = (SAMPLES / "store-set-available.js").read_text()
    fake = FakeLightpanda("store-renamed-button", script_run="renamed-button")
    run = fake.run_script(script, {"LP_STORE_USERNAME": "user", "LP_STORE_PASSWORD": "secret"})
    assert not run.ok
    assert run.exit_code == 1
    assert run.stdout == ""
    assert "Error: check failed:" in run.stderr
    assert 'says "Update inventory", expected "Save changes"' in run.stderr


def test_run_script_without_the_login_in_env_fails_sign_in_as_recorded():
    script = (SAMPLES / "store-set-available.js").read_text()
    run = FakeLightpanda("store-save").run_script(script, {"LP_STORE_USERNAME": "user"})
    assert run.exit_code == 1
    assert "after signing in the page is /login, expected /inventory" in run.stderr


def test_run_script_keeps_the_env_names_but_never_the_values():
    fake = FakeLightpanda("store-save")
    fake.run_script("return 1;", {"LP_STORE_USERNAME": "user", "LP_STORE_PASSWORD": "secret"})
    assert fake.scripts_run == [("return 1;", ("LP_STORE_PASSWORD", "LP_STORE_USERNAME"))]
    assert "secret" not in repr(fake.scripts_run)


def test_an_unknown_script_run_is_refused_with_the_known_names():
    with pytest.raises(ValueError, match="renamed-button"):
        FakeLightpanda("store-save", script_run="crashed")


def test_tree_find_element_and_node_details_are_not_available_inside_scripts():
    probes = {probe["name"]: probe for probe in sample("pandascript-probes")["probes"]}
    available = json.loads(probes["probe-tree"]["stdout"])
    for name in ("tree", "findElement", "nodeDetails", "interactiveElements"):
        assert available[name] == "typeof undefined"
    assert probes["probe-missing"]["exitCode"] == 1
    assert "click failed: NodeNotFound" in probes["probe-missing"]["stderr"]
    assert probes["probe-unset"]["exitCode"] == 0
    assert '"$LP_NOT_SET"' in probes["probe-unset"]["stdout"]
