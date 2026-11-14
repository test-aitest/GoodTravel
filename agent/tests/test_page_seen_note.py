import json

import pytest

from clientops.records import PAGE_HTML_MAX_BYTES, EventNotes

PAGE = "<!doctype html><html><body><h1>Stock list</h1></body></html>"


def notes_into(sent: list) -> EventNotes:
    return EventNotes(lambda message, tags: sent.append(json.loads(message)))


def test_a_seen_page_becomes_one_page_seen_note():
    sent = []
    notes_into(sent).page_seen("read", "portal.maruyama-supply.example", PAGE)
    assert sent == [{"n": 1, "event": {"type": "page.seen", "stepId": "read", "host": "portal.maruyama-supply.example", "html": PAGE}}]


def test_a_host_with_a_port_is_fine():
    sent = []
    notes_into(sent).page_seen("read", "127.0.0.1:3001", PAGE)
    assert sent[0]["event"]["host"] == "127.0.0.1:3001"


def test_the_cap_matches_the_screen_and_the_runner():
    assert PAGE_HTML_MAX_BYTES == 262_144


@pytest.mark.parametrize(
    ("host", "html"),
    [
        ("https://portal.maruyama-supply.example", PAGE),
        ("portal.maruyama-supply.example/stock", PAGE),
        ("", PAGE),
        ("portal.maruyama-supply.example", ""),
        ("portal.maruyama-supply.example", "x" * (PAGE_HTML_MAX_BYTES + 1)),
        ("portal.maruyama-supply.example", "é" * (PAGE_HTML_MAX_BYTES // 2 + 1)),
    ],
)
def test_a_page_the_screen_would_reject_is_refused_before_any_note_is_written(host, html):
    sent = []
    notes = notes_into(sent)
    with pytest.raises(ValueError):
        notes.page_seen("read", host, html)
    assert sent == []
    assert notes.count == 0
