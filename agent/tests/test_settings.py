import json

import pytest

from clientops.boundary import DATA_DIR_VARIABLE, data_directory
from clientops.settings import SETTINGS_VERSION, SettingsError, settings_path, use_claude


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    directory = tmp_path / "clientops-data"
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(directory))
    return directory


def write_settings(data_dir, content) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (data_dir / "settings.json").write_text(text)


def test_the_settings_live_next_to_the_audit_log_in_the_data_directory(data_dir):
    assert settings_path() == data_directory() / "settings.json"
    assert settings_path() == data_dir.resolve() / "settings.json"


def test_the_data_directory_rules_are_the_audit_logs(monkeypatch, tmp_path):
    monkeypatch.delenv(DATA_DIR_VARIABLE, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert settings_path() == (tmp_path / "state" / "clientops" / "settings.json").resolve()
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert settings_path() == (tmp_path / "home" / ".local" / "state" / "clientops" / "settings.json").resolve()


def test_settings_inside_the_project_are_refused(monkeypatch):
    from clientops.computer_image import PROJECT_ROOT

    monkeypatch.setenv(DATA_DIR_VARIABLE, str(PROJECT_ROOT / "agent" / "data"))
    with pytest.raises(ValueError):
        use_claude("kumo")


def test_the_file_is_version_one():
    assert SETTINGS_VERSION == 1


def test_without_a_settings_file_claude_is_used(data_dir):
    assert use_claude("kumo") is True
    assert not data_dir.exists()


def test_a_client_missing_from_the_file_uses_claude(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"hinata": {"useClaude": False}}})
    assert use_claude("kumo") is True
    assert use_claude("hinata") is False


def test_a_client_entry_without_the_switch_uses_claude(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {}}})
    assert use_claude("kumo") is True


def test_a_file_without_clients_uses_claude_for_everyone(data_dir):
    write_settings(data_dir, {"version": 1})
    assert use_claude("kumo") is True


def test_the_switch_is_read_per_client(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {"useClaude": False}, "hinata": {"useClaude": True}}})
    assert use_claude("kumo") is False
    assert use_claude("hinata") is True


def test_other_settings_of_a_client_are_left_alone(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {"useClaude": False, "schedule": "18:00"}}})
    assert use_claude("kumo") is False


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        {"version": 2, "clients": {}},
        {"clients": {"kumo": {"useClaude": False}}},
        {"version": 1, "clients": []},
        {"version": 1, "clients": {"kumo": True}},
        {"version": 1, "clients": {"kumo": {"useClaude": "no"}}},
        {"version": 1, "clients": {"kumo": {"useClaude": 0}}},
    ],
)
def test_a_settings_file_that_cannot_be_trusted_is_an_error_instead_of_a_guess(data_dir, content):
    write_settings(data_dir, content)
    with pytest.raises(SettingsError) as caught:
        use_claude("kumo")
    assert "settings" in str(caught.value).lower()


def test_an_unreadable_settings_file_is_an_error(data_dir):
    write_settings(data_dir, {"version": 1})
    (data_dir / "settings.json").chmod(0)
    try:
        with pytest.raises(SettingsError):
            use_claude("kumo")
    finally:
        (data_dir / "settings.json").chmod(0o600)
