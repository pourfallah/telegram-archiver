"""Export verification comparator tests — SOURCE vs CANONICAL field checks."""
from __future__ import annotations

from app.services.export_verification import (
    _archive_record,
    _reactions_eq,
    _source_record,
    compare_records,
)


class _FakeReaction:
    def __init__(self, ctor, emoji, document_id, count):
        self.ctor = ctor
        self.emoji = emoji
        self.document_id = document_id
        self.count = count


def _fake_reactions(results):
    class R:
        pass
    class Container:
        pass
    if not results:
        return None
    rx = Container()
    rx.results = []
    for ctor, emoji, doc_id, count in results:
        r = R()
        r.count = count
        rr = R()
        rr.ctor = ctor
        rr.emoticon = emoji
        rr.document_id = doc_id
        r.reaction = rr
        rx.results.append(r)
    return rx


def test_caption_stays_on_media_message():
    """A media message with text is ONE message: text == caption. The archive
    must never split it into two messages."""
    class M:
        id = 123
        date = None
        media = object()
        message = "gooood music 😍"
        entities = None
        reply_to = None
        grouped_id = None
        fwd_from = None
        sender = None
        edit_date = None
        reactions = None

    rec = _source_record(M)
    assert rec["text"] == "gooood music 😍"
    assert rec["caption"] == "gooood music 😍"  # same object, ONE logical message
    assert rec["media_ctor"] != "none"


def test_reply_relationship_preserved():
    class ReplyHeader:
        reply_to_msg_id = 100
        reply_to_top_id = 100

    class M:
        id = 101
        date = None
        media = None
        message = ""
        entities = None
        reply_to = ReplyHeader
        grouped_id = None
        fwd_from = None
        sender = None
        edit_date = None
        reactions = None

    rec = _source_record(M)
    assert rec["reply_to"]["reply_to_msg_id"] == 100
    # archive row with the same relationship -> compare passes
    arec = _archive_record({
        "id": 101, "date": None, "sender": None, "text": "",
        "media": [], "reply_to": {"reply_to_msg_id": 100}, "grouped_id": None,
        "forwarded_from": None, "reactions": None, "entities": None,
    })
    checks = compare_records(rec, arec)
    assert checks["reply_to"]["ok"] is True
    assert checks["caption"]["ok"] is True


def test_reactions_compare_by_type_emoji_count():
    a = {"reactions": [{"ctor": "ReactionEmoji", "emoji": "❤", "document_id": None, "count": 1}]}
    b = {"reactions": [{"ctor": "ReactionEmoji", "emoji": "❤", "document_id": None, "count": 1}]}
    c = {"reactions": [{"ctor": "ReactionEmoji", "emoji": "👍", "document_id": None, "count": 1}]}
    assert _reactions_eq(a, b) is True
    assert _reactions_eq(a, c) is False
    assert _reactions_eq(None, None) is True
    assert _reactions_eq(a, None) is False
