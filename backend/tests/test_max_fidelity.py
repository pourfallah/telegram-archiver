"""Regression: maximum-fidelity serializers preserve rich source data."""
from __future__ import annotations

from app.services.telegram_utils import (
    message_to_dict,
    serialize_entities,
    serialize_reactions,
    serialize_reply,
)


class _E:
    pass


def _entity(name, offset, length, **kw):
    e = _E()
    e.offset = offset
    e.length = length
    for k, v in kw.items():
        setattr(e, k, v)
    e.__class__.__name__ = name  # type: ignore[attr-defined]
    return e


def test_custom_emoji_preserves_document_id():
    m = _Message(entities=[_entity("MessageEntityCustomEmoji", 0, 2, document_id=123456)])
    ents = serialize_entities(m)
    assert ents[0]["type"] == "custom_emoji"
    assert ents[0]["document_id"] == 123456
    assert ents[0]["offset"] == 0 and ents[0]["length"] == 2


def test_reactions_not_flattened():
    class _ReactionEmoji:
        emoticon = "👍"

    class _ReactionCustomEmoji:
        document_id = 999

    class _ReactionResult:
        def __init__(self, reaction, count):
            self.reaction = reaction
            self.count = count
            self.chosen = False

    class _Reactions:
        results = [_ReactionResult(_ReactionEmoji(), 3), _ReactionResult(_ReactionCustomEmoji(), 1)]

    m = _Message(reactions=_Reactions())
    out = serialize_reactions(m)
    assert out["reactions"][0]["emoji"] == "👍"
    assert out["reactions"][0]["count"] == 3
    assert out["reactions"][1]["reaction_type"] == "_ReactionCustomEmoji"
    assert out["reactions"][1]["document_id"] == 999


def test_reply_keeps_quote_and_peer():
    class _Nested:
        message_id = 42

    class _Reply:
        reply_to_msg_id = 42
        reply_to_peer_id = 7
        reply_to_top_id = 40
        quote = "quoted text"

    m = _Message(reply_to=_Reply())
    out = serialize_reply(m)
    assert out["reply_to_msg_id"] == 42
    assert out["reply_to_peer_id"] == 7
    assert out["top_msg_id"] == 40
    assert out["quote"] == "quoted text"


def test_message_to_dict_has_raw_snapshot_and_flags():
    m = _Message(id=5, text="hi", pinned=True, p=False)
    d = message_to_dict(m)
    assert d["pinned"] is True
    assert "raw_message" in d
    assert d["reply_to"] is None or isinstance(d["reply_to"], dict)


def _Message(**kw):
    class M:
        pass

    m = M()
    for k, v in kw.items():
        setattr(m, k, v)
    defaults = {
        "id": 1, "date": None, "edit_date": None, "grouped_id": None,
        "sender": None, "message": "", "entities": [], "reply_to": None,
        "forward": None, "reactions": None, "views": None, "forwards": None,
        "media": None, "replies": None, "via_bot_id": None, "post_author": None,
        "pinned": False, "noforwards": False, "silent": False, "mentioned": False,
        "media_unread": False, "post": False,
    }
    for k, v in defaults.items():
        if not hasattr(m, k):
            setattr(m, k, v)
    return m