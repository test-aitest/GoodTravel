import json

from clientops.records import EVENT_NOTE_TAG, EventNotes


def test_each_event_becomes_one_tagged_note_numbered_in_order():
    sent = []
    notes = EventNotes(lambda message, tags: sent.append((message, tags)))

    notes.emit("step.started", stepId="read-store", activity="Reading the store admin")
    notes.emit("step.done", stepId="read-store", summary="Read the store admin", tookMs=500)

    assert [tags for _, tags in sent] == [[EVENT_NOTE_TAG], [EVENT_NOTE_TAG]]
    assert [json.loads(message) for message, _ in sent] == [
        {"n": 1, "event": {"type": "step.started", "stepId": "read-store", "activity": "Reading the store admin"}},
        {"n": 2, "event": {"type": "step.done", "stepId": "read-store", "summary": "Read the store admin", "tookMs": 500}},
    ]


def test_the_tag_is_the_one_the_screen_server_reads():
    assert EVENT_NOTE_TAG == "clientops.event"


def test_non_ascii_text_is_kept_as_is():
    sent = []
    EventNotes(lambda message, tags: sent.append(message)).emit("step.started", stepId="s", activity="代官山の在庫")
    assert "代官山の在庫" in sent[0]


def test_it_counts_the_notes_it_wrote_so_the_reader_knows_when_it_has_them_all():
    notes = EventNotes(lambda message, tags: None)
    assert notes.count == 0
    notes.emit("step.started", stepId="s", activity="Reading")
    notes.emit("step.done", stepId="s", summary="Read", tookMs=1)
    assert notes.count == 2
