import pytest

import clientops.externals as externals
from clientops.externals.fake_daytona import FakeDaytona


class RecordingDaytona:
    def __init__(self, config=None):
        self.config = config


@pytest.fixture
def no_daytona_settings(monkeypatch):
    for name in ("CLIENTOPS_EXTERNALS", "DAYTONA_API_KEY", "DAYTONA_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(externals, "Daytona", RecordingDaytona)
    return monkeypatch


def test_fake_is_used_only_when_asked_for_explicitly(no_daytona_settings):
    no_daytona_settings.setenv("CLIENTOPS_EXTERNALS", "fake")
    assert isinstance(externals.daytona_client(), FakeDaytona)


def test_the_fake_keeps_its_sandboxes_across_calls_in_one_process(no_daytona_settings):
    no_daytona_settings.setenv("CLIENTOPS_EXTERNALS", "fake")
    assert externals.daytona_client() is externals.daytona_client()


def test_the_fake_is_used_when_asked_for_even_if_a_key_is_set(no_daytona_settings):
    no_daytona_settings.setenv("CLIENTOPS_EXTERNALS", "fake")
    no_daytona_settings.setenv("DAYTONA_API_KEY", "dtn_test")
    assert isinstance(externals.daytona_client(), FakeDaytona)


def test_a_missing_key_is_an_error_and_never_falls_back_to_the_fake(no_daytona_settings):
    with pytest.raises(externals.MissingExternalKey) as caught:
        externals.daytona_client()
    message = str(caught.value)
    assert "DAYTONA_API_KEY" in message
    assert "CLIENTOPS_EXTERNALS=fake" in message


def test_an_empty_key_counts_as_missing(no_daytona_settings):
    no_daytona_settings.setenv("DAYTONA_API_KEY", "")
    with pytest.raises(externals.MissingExternalKey):
        externals.daytona_client()


def test_with_a_key_the_real_client_gets_that_key_and_the_default_api_url(no_daytona_settings):
    no_daytona_settings.setenv("DAYTONA_API_KEY", "dtn_test")
    client = externals.daytona_client()
    assert isinstance(client, RecordingDaytona)
    assert client.config.api_key == "dtn_test"
    assert client.config.api_url == "https://app.daytona.io/api"


def test_an_api_url_from_the_environment_is_passed_through(no_daytona_settings):
    no_daytona_settings.setenv("DAYTONA_API_KEY", "dtn_test")
    no_daytona_settings.setenv("DAYTONA_API_URL", "https://daytona.internal.example/api")
    assert externals.daytona_client().config.api_url == "https://daytona.internal.example/api"


def test_real_can_be_asked_for_by_name(no_daytona_settings):
    no_daytona_settings.setenv("CLIENTOPS_EXTERNALS", "real")
    no_daytona_settings.setenv("DAYTONA_API_KEY", "dtn_test")
    assert isinstance(externals.daytona_client(), RecordingDaytona)


def test_an_unknown_mode_is_an_error(no_daytona_settings):
    no_daytona_settings.setenv("CLIENTOPS_EXTERNALS", "mock")
    with pytest.raises(ValueError, match="CLIENTOPS_EXTERNALS"):
        externals.externals_mode()
