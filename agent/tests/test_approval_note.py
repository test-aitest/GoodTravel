import json

import pytest

from clientops.records import EventNotes

CHANGES = [{"sku": "KC-ETH-200", "name": "Ethiopia Guji 200g", "store": 18, "supplier": 6}]


def notes_into(sent: list) -> EventNotes:
    return EventNotes(lambda message, tags: sent.append(json.loads(message)))


def request(notes: EventNotes, **left_alone) -> None:
    notes.approval_requested("apr-1", "ask", "Save 1 stock change to the store admin?", CHANGES, undoable=True, **left_alone)


def test_an_approval_with_nothing_left_alone_has_no_left_alone_field():
    sent = []
    request(notes_into(sent))
    request(notes_into(sent), only_in_store=0, only_at_supplier=0)
    expected = {
        "type": "approval.requested",
        "approvalId": "apr-1",
        "stepId": "ask",
        "question": "Save 1 stock change to the store admin?",
        "changes": CHANGES,
        "undoable": True,
    }
    assert [note["event"] for note in sent] == [expected, expected]


@pytest.mark.parametrize(("only_in_store", "only_at_supplier"), [(2, 0), (0, 3), (1, 4)])
def test_products_left_alone_on_either_side_are_counted_without_names(only_in_store, only_at_supplier):
    sent = []
    request(notes_into(sent), only_in_store=only_in_store, only_at_supplier=only_at_supplier)
    assert sent[0]["event"]["leftAlone"] == {"onlyInStore": only_in_store, "onlyAtSupplier": only_at_supplier}


@pytest.mark.parametrize(("only_in_store", "only_at_supplier"), [(-1, 0), (0, 1.5), (True, 0), ("2", 0)])
def test_counts_that_the_screen_would_reject_are_refused_before_any_note(only_in_store, only_at_supplier):
    sent = []
    notes = notes_into(sent)
    with pytest.raises(ValueError):
        request(notes, only_in_store=only_in_store, only_at_supplier=only_at_supplier)
    assert sent == []


def test_an_approval_needs_at_least_one_change():
    sent = []
    with pytest.raises(ValueError):
        notes_into(sent).approval_requested("apr-1", "ask", "Save?", [], undoable=True)
    assert sent == []
