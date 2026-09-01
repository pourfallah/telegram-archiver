"""Hermetic tests for the phased sampling pipeline (recovery.pipeline)."""
from __future__ import annotations

import json
from pathlib import Path

from telethon.tl import types as t

from recovery import pipeline as P
from recovery.telegram_client import tl_to_plain
from tests.fakes import FakeClient, FakeRecoveryClient, dt, doc, doc_message, message, photo_message


def _hist_msg(i, text="", media=None, date=None, reply=None, grouped=None,
              react=None, fwd=None):
    return t.Message(id=i, peer_id=t.PeerUser(500), date=date or dt(-float(i)),
                     message=text, out=False, from_id=t.PeerUser(100), media=media,
                     entities=[], reply_to=reply, fwd_from=fwd, grouped_id=grouped,
                     reactions=react)


def test_lightweight_record_extracts_minimal_fields():
    audio = doc(9, "audio/mpeg", [t.DocumentAttributeAudio(duration=5, voice=False)])
    rec = P.lightweight_record(doc_message(1, audio, text="cap"))
    assert rec["id"] == 1 and rec["has_media"] is True
    assert rec["media_types"] == ["audio"] and rec["date"] is not None
    assert rec["has_reply"] is False and rec["has_reactions"] is False


def test_lightweight_record_sticker_groups_and_reply():
    from telethon.tl import types as tt
    st = doc(2, "image/webp", [tt.DocumentAttributeSticker(alt="x", stickerset=tt.InputStickerSetID(id=5, access_hash=7))])
    m = t.Message(id=2, peer_id=t.PeerUser(500), date=dt(-2), message="", out=False,
                  from_id=t.PeerUser(100), media=t.MessageMediaDocument(document=st),
                  entities=[], reply_to=t.MessageReplyHeader(reply_to_msg_id=1),
                  fwd_from=None, grouped_id=777)
    rec = P.lightweight_record(m)
    assert rec["media_types"] == ["sticker"]
    assert rec["grouped_id"] == 777 and rec["has_reply"] and rec["reply_to_id"] == 1


def test_msg_kind_labels():
    assert P.msg_kind({"grouped_id": 1}) == "album"
    assert P.msg_kind({"has_reply": True}) == "reply"
    assert P.msg_kind({"has_forward": True}) == "forward"
    assert P.msg_kind({"has_media": True, "media_types": ["photo"]}) == "photo"
    assert P.msg_kind({"has_reactions": True}) == "reaction"
    assert P.msg_kind({"text_len": 3, "has_media": False}) == "text"


def test_catalog_resumable_dedup(tmp_path):
    cl = FakeClient(history=[_hist_msg(i) for i in range(1, 6)])
    rc = FakeRecoveryClient(cl, my_id=100)
    cat = tmp_path / "source_catalog.ndjson"
    cp = tmp_path / "checkpoint.json"
    asyncio_run(P.discover_catalog, rc, "peer", cat, cp, True)
    assert cat.exists()
    first = P.load_catalog(cat)
    assert len(first) == 5
    # resume: discover again — no duplicates
    asyncio_run(P.discover_catalog, rc, "peer", cat, cp, True)
    second = P.load_catalog(cat)
    assert len(second) == 5
    assert sorted(r["id"] for r in second) == [1, 2, 3, 4, 5]


def _catalog(tmp_path) -> list[dict]:
    # a synthetic multi-year catalog
    rows = []
    for y in (2019, 2021, 2023, 2026):
        for i in range(30):
            gid = f"{y}-{i // 2}"  # every pair is a 2-member group
            rows.append({"id": y * 100 + i, "date": f"{y}-01-{(i % 27) + 1:02d}T10:00:00+00:00",
                         "sender_id": 7, "grouped_id": gid if i % 7 == 0 else None,
                         "has_media": i % 3 == 0, "media_types": ["photo"] if i % 3 == 0 else [],
                         "has_reply": i % 5 == 4, "reply_to_id": y * 99 if i % 5 == 4 else None,
                         "has_reactions": False, "has_forward": i % 11 == 0,
                         "text_len": 10})
    return rows


def test_select_is_deterministic_for_same_seed():
    cat = _catalog(Path("."))
    a = P.select_ids(cat, 20, "seed123", 3)
    b = P.select_ids(cat, 20, "seed123", 3)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    c = P.select_ids(cat, 20, "other", 3)
    assert [r["id"] for r in c] != [r["id"] for r in a]


def test_select_spans_multiple_years():
    cat = _catalog(Path("."))
    sel = P.select_ids(cat, 20, "seed123", 3)
    years = P.years_covered(sel)
    assert len(years) >= 3, years


def test_closures_add_full_group_members_and_reply_parent():
    # controlled catalog: two-member group (10, 11), and reply child 51 -> parent 50
    cat = [
        {"id": 10, "date": "2020-01-01T00:00:00+00:00", "sender_id": 7, "grouped_id": "G1",
         "has_media": True, "media_types": ["photo"], "has_reply": False,
         "reply_to_id": None, "has_reactions": False, "has_forward": False, "text_len": 1},
        {"id": 11, "date": "2020-01-01T00:01:00+00:00", "sender_id": 7, "grouped_id": "G1",
         "has_media": True, "media_types": ["photo"], "has_reply": False,
         "reply_to_id": None, "has_reactions": False, "has_forward": False, "text_len": 1},
        {"id": 50, "date": "2021-02-01T00:00:00+00:00", "sender_id": 7, "grouped_id": None,
         "has_media": False, "media_types": [], "has_reply": False, "reply_to_id": None,
         "has_reactions": False, "has_forward": False, "text_len": 4},
        {"id": 51, "date": "2021-02-01T00:02:00+00:00", "sender_id": 7, "grouped_id": None,
         "has_media": False, "media_types": [], "has_reply": True, "reply_to_id": 50,
         "has_reactions": False, "has_forward": False, "text_len": 4},
    ]
    # select only album item 10 and reply child 51
    selected = [c for c in cat if c["id"] in (10, 51)]
    closed_ids = {r["id"] for r in P.apply_closures(cat, selected)}
    assert closed_ids == {10, 11, 50, 51}  # sibling 11 + parent 50 added


def test_contextbased_closures_from_catalog():
    cat = _catalog(Path("."))
    ids = [cat[3]["id"]]  # pick one; verify closure is a superset of ids
    closed = P.closures_from_catalog(cat, ids)
    assert len(closed) >= len(ids)
    assert set(ids) <= set(closed)


def test_years_covered_and_range():
    cat = _catalog(Path("."))
    assert P.years_covered(cat) == [2019, 2021, 2023, 2026]
    dmin, dmax = P.date_range(cat)
    assert str(dmin).startswith("2019") and str(dmax).startswith("2026")


def asyncio_run(fn, *a, **k):
    import asyncio
    return asyncio.run(fn(*a, **k))