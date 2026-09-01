"""Reaction archive / reconstruction / verification over fakes."""
from __future__ import annotations

import asyncio

from recovery.archive import Archive
from recovery.reactions import (
    archive_reactions, reconstruct_reactions, verify_reactions,
)
from recovery.mapper import Mapping
from tests.fakes import FakeClient, FakeRecoveryClient, reaction_counts
from telethon.tl import types as t


def _archive_with_reactions(tmp_path) -> Archive:
    a = Archive(tmp_path / "archive")
    a.create()
    rec = {"schema_version": 1, "source_message_id": 7, "text": "target",
           "media": [], "date": "2026-08-01T10:00:00+00:00", "grouped_id": None,
           "reactions": {"rows": [
               {"reaction": {"__tl__": "ReactionEmoji", "emoticon": "👍"}, "count": 1},
               {"reaction": {"__tl__": "ReactionEmoji", "emoticon": "❤️"}, "count": 1}]}}
    a.append_canonical(rec)
    a.append_raw({"id": 7})
    return a


def _reactors():
    return {7: [
        t.MessagePeerReaction(peer_id=t.PeerUser(100), reaction=t.ReactionEmoji(emoticon="👍"),
                              unread=False, big=True, date=None),
        t.MessagePeerReaction(peer_id=t.PeerUser(200), reaction=t.ReactionEmoji(emoticon="❤️"),
                              unread=False, big=False, date=None),
    ]}


def test_archive_reactions_captures_who_reacted(tmp_path):
    a = _archive_with_reactions(tmp_path)
    src = FakeRecoveryClient(FakeClient(reactors=_reactors()), my_id=100)
    stats = asyncio.run(archive_reactions(src, "peer", a))
    store = a.reactions_dir / "archive.json"
    assert store.exists()
    import json
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["7"]  # one entry per reactor
    ids = {r["reactor_id"] for r in data["7"]}
    assert ids == {100, 200}
    assert stats["reaction_entries"] == 2


def test_reconstruct_uses_each_reactors_session(tmp_path):
    a = _archive_with_reactions(tmp_path)
    # persist the reaction archive as archive_reactions would
    from recovery.reactions import ReactionStore
    st = ReactionStore(a.reactions_dir / "archive.json")
    st.add(7, 100, {"__tl__": "ReactionEmoji", "emoticon": "👍"})
    st.add(7, 200, {"__tl__": "ReactionEmoji", "emoticon": "❤️"})
    st.save()

    src = FakeRecoveryClient(FakeClient(), my_id=100)
    tgt = FakeRecoveryClient(FakeClient(), my_id=200)
    sessions = {"100": src, "200": tgt}
    mapping = [Mapping(7, 507, "SEQUENCE", "x")]
    applied = asyncio.run(reconstruct_reactions(tgt, "peer", a, mapping, sessions))
    rec = {x["status"] for x in applied}
    assert rec == {"RECONSTRUCTED"}
    # A's reaction sent through src, B's through tgt
    assert "SendReactionRequest" in src.client.calls
    assert "SendReactionRequest" in tgt.client.calls


def test_verify_reactions_reads_target(tmp_path):
    src = FakeRecoveryClient(FakeClient(), my_id=100)
    up = t.UpdateMessageReactions(
        peer=t.PeerUser(500), msg_id=507,
        reactions=reaction_counts((t.ReactionEmoji(emoticon="👍"), 1)), top_msg_id=0)
    tgt = FakeRecoveryClient(FakeClient(reaction_updates={(507,): [up]}), my_id=200)
    res = asyncio.run(verify_reactions(tgt, "peer", [507]))
    assert res["checked"] == 1
    assert res["messages"][507][0]["reaction"]["emoticon"] == "👍"