"""Source->target mapper: composite matching, never text alone."""
from __future__ import annotations

from recovery.mapper import map_source_to_target


def _src(mid, text="", media=None, date="2026-08-01T10:00:00+00:00", grouped=None):
    return {"source_message_id": mid, "text": text, "media": media or [],
            "date": date, "grouped_id": grouped}


def _tgt(tid, text="", media=None, date="2026-08-01T10:00:00+00:00", grouped=None):
    r = _src(tid, text, media, date, grouped)
    r.pop("source_message_id")
    r["target_message_id"] = tid
    return r


def test_media_hash_maps_exactly():
    src = [_src(1, media=[{"sha256": "abc"}]), _src(2)]
    tgt = [_tgt(901, media=[{"sha256": "abc"}]), _tgt(902, text="hi")]
    m = map_source_to_target(src, tgt, delta_target_ids={901, 902})
    by = {x.source_message_id: x for x in m}
    assert by[1].target_message_id == 901 and by[1].confidence == "EXACT_MEDIA"
    assert by[2].target_message_id == 902


def test_text_and_date_never_text_alone():
    src = [_src(5, text="unique msg")]
    tgt = [_tgt(550, text="unique msg")]
    m = map_source_to_target(src, tgt, delta_target_ids={550})
    assert m[0].confidence == "MATCH_TEXT_DATE"


def test_ambiguous_text_not_mapped_when_date_differs_and_no_delta():
    # same text, wildly different date, no delta restriction -> no confident match
    src = [_src(7, text="dup")]
    tgt = [_tgt(700, text="dup", date="2010-01-01T00:00:00+00:00")]
    m = map_source_to_target(src, tgt, delta_target_ids={700})
    # falls back to sequence (single candidate)
    assert m[0].target_message_id == 700


def test_delta_restriction_keeps_other_history_out():
    src = [_src(1, text="first")]
    old = _tgt(1, text="first")            # pre-existing target history
    new = _tgt(2, text="first")            # current run's imported message
    m = map_source_to_target(src, [old, new], delta_target_ids={2})
    assert m[0].target_message_id == 2


def test_sticker_media_hash_maps_even_without_text():
    media = {"sha256": "sticker-sha", "type": "sticker"}
    src = [_src(10, media=[media])]
    tgt = [_tgt(1010, media=[media])]
    m = map_source_to_target(src, tgt, delta_target_ids={1010})
    assert m[0].confidence == "EXACT_MEDIA"