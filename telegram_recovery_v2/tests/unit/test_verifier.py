"""Fidelity verifier: honest per-feature classification."""
from __future__ import annotations

from types import SimpleNamespace

from recovery.verifier import Verifier

D = "2026-08-01T10:00:00+00:00"


def _src(mid, text="", media=None, caption=None, date=D, reply=None, fwd=None,
         reactions=None, grouped=None, entities=None):
    return {"source_message_id": mid, "text": text, "caption": caption,
            "media": media or [], "date": date, "reply_to": reply, "forward": fwd,
            "reactions": reactions, "grouped_id": grouped,
            "entities": entities or [], "from_id": {"user_id": 100}}


def _tgt(tid, text="", media=None, caption=None, date=D, reply=None, fwd=None,
         grouped=None, entities=None):
    return {"target_message_id": tid, "text": text, "caption": caption,
            "media": media or [], "date": date, "reply_to": reply, "forward": fwd,
            "grouped_id": grouped, "entities": entities or [],
            "from_id": {"user_id": 100}}


def _mapping(*pairs):
    return [SimpleNamespace(source_message_id=s, target_message_id=t)
            for s, t in pairs]


def _photo_ctr():
    return [{"constructor": "MessageMediaPhoto"}]


def _sticker_ctr():
    return [{"constructor": "MessageMediaDocument",
             "attributes": [{"__tl__": "DocumentAttributeSticker"}],
             "sticker": {"alt": "hi"}, "mime": "image/webp"}]


def _doc_only_ctr():
    return [{"constructor": "MessageMediaDocument",
             "attributes": [{"__tl__": "DocumentAttributeFilename"}],
             "mime": "image/webp"}]


def test_caption_attached_when_same_record():
    src = [_src(1, text="gooood music 😍", media=_photo_ctr(), caption="gooood music 😍")]
    tgt = [_tgt(501, text="gooood music 😍", media=_photo_ctr(),
                caption="gooood music 😍")]
    v = Verifier(_mapping((1, 501)))
    rows = v.verify(src, tgt)["rows"]
    assert rows[0]["caption"]["class"] == "CAPTION_ATTACHED"
    assert rows[0]["photo"]["class"] == "EXACT"


def test_caption_separate_when_split_into_text_message():
    src = [_src(1, text="cap", media=_photo_ctr(), caption="cap")]
    # target: media WITHOUT text, plus no caption -> means it was split/lost
    tgt = [_tgt(501, text="", media=_photo_ctr(), caption=None)]
    rows = Verifier(_mapping((1, 501))).verify(src, tgt)["rows"]
    assert rows[0]["caption"]["class"] == "CAPTION_LOST"


def test_sticker_exact_vs_document_only():
    src = [_src(1, media=_sticker_ctr())]
    good = Verifier(_mapping((1, 501))).verify(src, [_tgt(501, media=_sticker_ctr())])["rows"]
    assert good[0]["sticker"]["class"] == "EXACT"
    bad = Verifier(_mapping((1, 502))).verify(src, [_tgt(502, media=_doc_only_ctr())])["rows"]
    assert bad[0]["sticker"]["class"] == "DOCUMENT_ONLY"


def test_reply_exact_vs_archival():
    src = [_src(1, text="parent"),
           _src(2, text="child", reply={"reply_to_msg_id": 1})]
    tgt = [_tgt(501, text="parent"),
           _tgt(502, text="child", reply={"reply_to_msg_id": 501})]
    rows = Verifier(_mapping((1, 501), (2, 502))).verify(src, tgt)["rows"]
    assert rows[0]["reply"]["class"] == "NONE"          # parent itself not a reply
    assert rows[1]["reply"]["class"] == "REPLY_EXACT"   # child -> mapped parent
    assert rows[1]["reply"]["source_parent"] == 1
    assert rows[1]["reply"]["target_parent"] == 501


def test_reaction_classification_via_target_verify():
    src = [_src(1, reactions={"rows": [
        {"reaction": {"__tl__": "ReactionEmoji", "emoticon": "👍"}, "count": 1}]})]
    verify = {1: [{"reaction": {"__tl__": "ReactionEmoji", "emoticon": "👍"},
                   "count": 1}]}
    rows = Verifier(_mapping((1, 501))).verify(src, [], reaction_verify=verify)["rows"]
    assert rows[0]["reaction"]["class"] == "REACTION_EXACT"


def test_reaction_archival_only_when_no_target_verify():
    src = [_src(1, reactions={"rows": [
        {"reaction": {"__tl__": "ReactionEmoji", "emoticon": "❤️"}, "count": 2}]})]
    rows = Verifier(_mapping((1, 501))).verify(src, [])["rows"]
    assert rows[0]["reaction"]["class"] == "ARCHIVAL_ONLY"


def test_matrix_aggregates_counts():
    src = [_src(1, text="a"), _src(2, text="b")]
    tgt = [_tgt(501, text="a"), _tgt(502, text="b")]
    matrix = Verifier(_mapping((1, 501), (2, 502))).verify(src, tgt)["matrix"]
    assert matrix["text"]["EXACT"] == 2
    assert matrix["sender"]["SENDER_EXACT"] == 2