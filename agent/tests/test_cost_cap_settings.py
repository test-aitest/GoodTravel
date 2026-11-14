import json

import pytest

from clientops.boundary import DATA_DIR_VARIABLE
from clientops.settings import CLAUDE_MONTHLY_CAP_KEY, CLAUDE_MONTHLY_CAP_WHEN_UNSET, SettingsError, claude_monthly_cap_usd, use_claude


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    directory = tmp_path / "clientops-data"
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(directory))
    return directory


def write_settings(data_dir, content) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "settings.json").write_text(content if isinstance(content, str) else json.dumps(content))


def test_the_cap_lives_next_to_the_claude_switch_in_each_clients_settings():
    assert CLAUDE_MONTHLY_CAP_KEY == "claudeMonthlyCapUsd"


def test_without_a_settings_file_every_client_may_spend_10_dollars_a_month_on_claude(data_dir):
    assert CLAUDE_MONTHLY_CAP_WHEN_UNSET == 10.0
    assert claude_monthly_cap_usd("kumo") == 10.0
    assert not data_dir.exists()


def test_a_client_without_its_own_cap_gets_the_default(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {"useClaude": True}}})
    assert claude_monthly_cap_usd("kumo") == 10.0
    assert claude_monthly_cap_usd("hinata") == 10.0


def test_each_client_has_its_own_cap(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {"useClaude": True, "claudeMonthlyCapUsd": 2.5}, "hinata": {"claudeMonthlyCapUsd": 40}}})
    assert claude_monthly_cap_usd("kumo") == 2.5
    assert claude_monthly_cap_usd("hinata") == 40.0
    assert use_claude("kumo") is True


def test_a_cap_of_zero_is_allowed(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {"claudeMonthlyCapUsd": 0}}})
    assert claude_monthly_cap_usd("kumo") == 0.0


def test_the_cap_does_not_change_the_claude_switch(data_dir):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {"useClaude": False, "claudeMonthlyCapUsd": 5}}})
    assert use_claude("kumo") is False
    assert claude_monthly_cap_usd("kumo") == 5.0


@pytest.mark.parametrize("value", [-1, "5", True, None, [5], {"usd": 5}])
def test_a_cap_that_is_not_a_number_of_dollars_is_an_error_instead_of_a_guess(data_dir, value):
    write_settings(data_dir, {"version": 1, "clients": {"kumo": {"claudeMonthlyCapUsd": value}}})
    with pytest.raises(SettingsError) as caught:
        claude_monthly_cap_usd("kumo")
    assert CLAUDE_MONTHLY_CAP_KEY in str(caught.value)


@pytest.mark.parametrize("text", ['{"version": 1, "clients": {"kumo": {"claudeMonthlyCapUsd": NaN}}}', '{"version": 1, "clients": {"kumo": {"claudeMonthlyCapUsd": Infinity}}}'])
def test_a_cap_that_is_not_a_finite_number_is_an_error(data_dir, text):
    write_settings(data_dir, text)
    with pytest.raises(SettingsError):
        claude_monthly_cap_usd("kumo")


def test_a_settings_file_that_cannot_be_trusted_stops_the_cap_too(data_dir):
    write_settings(data_dir, {"version": 2, "clients": {}})
    with pytest.raises(SettingsError):
        claude_monthly_cap_usd("kumo")
