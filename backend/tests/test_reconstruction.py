"""Reconstruction service tests — Phase B reaction identity rules."""
from __future__ import annotations

from app.services.reconstruction import (
    build_source_target_mapping,
    classify_plan,
    plan_reactions,
)


def _msg(msg_id, text, date, sender_id, reactions=None, voters=None):
    m = {"id": msg_id, "date": date, "text": text, "media": [],
         "sender": {"id": sender_id, "name": "u" + str(sender_id)}}
    if reactions:
        m["reactions"] = reactions
    if voters is not None:
        m.setdefault("reactions", {})["voters"] = voters
    return m


def test_mapping_exact_then_text_only():
    src = [_msg(1, "hello", "2020-01-01T10:00:00+00:00", 100)]
    # target with same text but different date -> text_only fallback
    tgt = [{"id": 501, "date": "2026-08-25T09:00:00+00:00", "text": "hello",
            "media": [], "sender": {"id": 3, "name": "imp"}}]
    m = build_source_target_mapping(src, tgt)
    assert m[1]["target_id"] == 501
    assert m[1]["match"] == "text_only"


def test_reaction_identity_rules():
    src = [_msg(1, "hi", "2020-01-01T10:00:00+00:00", 100, voters=[
        {"peer_id": 100, "emoji": "👍"},
        {"peer_id": 200, "emoji": "❤️"},
        {"peer_id": 999, "emoji": "😂"},
    ])]
    mapping = {1: {"target_id": 501, "match": "exact", "source_text": "hi"}}
    # Only account 200 (target) session available; 100 = source account.
    plan = plan_reactions(src, mapping, session_account_ids={200},
                          source_me_id=100, target_me_id=200)
    by_reactor = {p["reactor_id"]: p for p in plan}
    # A (100) reacted — its session is NOT in available -> REACTOR_SESSION_REQUIRED
    assert by_reactor[100]["status"] == "REACTOR_SESSION_REQUIRED"
    # B (200) reacted — its session IS available -> SENDABLE
    assert by_reactor[200]["status"] == "SENDABLE"
    # Unknown reactor -> session required (never faked)
    assert by_reactor[999]["status"] == "REACTOR_SESSION_REQUIRED"


def test_reaction_identity_never_crossed():
    """A reaction by A must never be sent by B."""
    src = [_msg(1, "x", "2020-01-01T10:00:00+00:00", 100, voters=[
        {"peer_id": 100, "emoji": "👍"},
    ])]
    mapping = {1: {"target_id": 501, "match": "exact", "source_text": "x"}}
    plan = plan_reactions(src, mapping, session_account_ids={200},
                          source_me_id=100, target_me_id=200)
    # Even though SOME session exists, it's B's, not A's — must not send as A.
    assert plan[0]["status"] != "SENDABLE"
    assert plan[0]["status"] == "REACTOR_SESSION_REQUIRED"


def test_classify_plan_counts():
    plan = [{"status": "SENDABLE"}, {"status": "SENDABLE"},
            {"status": "REACTOR_SESSION_REQUIRED"}]
    c = classify_plan(plan)
    assert c["SENDABLE"] == 2
    assert c["REACTOR_SESSION_REQUIRED"] == 1
