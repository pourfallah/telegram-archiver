"""Sampler unit tests: determinism, buckets, groups, reply closure."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from recovery.sampler import run_seed, select_sample


def _msg(mid, date, text="t", media=None, gid=None, reply=None, fwd=False, reacts=False, ents=False):
    m = {
        "message_id": mid,
        "date": date,
        "text": text,
        "media": media,
        "entities": [{"_": "MessageEntityBold"}] if ents else None,
        "reactions": [{"reaction": {"_": "ReactionEmoji", "emoticon": "👍"}}] if reacts else None,
        "reply_to": {"reply_to_msg_id": reply} if reply else None,
        "fwd_from": {"from_id": 1} if fwd else None,
        "grouped_id": gid,
        "sender_label": "A",
    }
    return m


class TestSampler:
    def test_deterministic_same_seed(self):
        cat = [_msg(i, f"202{i % 5}-0{i % 9 + 1}-01T10:00:00") for i in range(1, 101)]
        s1, _ = select_sample(cat, 20, run_seed("run-a"))
        s2, _ = select_sample(cat, 20, run_seed("run-a"))
        assert s1 == s2

    def test_different_seed_different_sample(self):
        cat = [_msg(i, f"202{i % 5}-0{i % 9 + 1}-01T10:00:00") for i in range(1, 501)]
        s1, _ = select_sample(cat, 20, run_seed("run-a"))
        s2, _ = select_sample(cat, 20, run_seed("run-b"))
        assert s1 != s2

    def test_multi_year_coverage(self):
        cat = []
        for year in (2019, 2021, 2023, 2025, 2026):
            for i in range(50):
                cat.append(_msg(len(cat) + 1, f"{year}-06-01T10:00:00", text=f"y{year} m{i}"))
        sel, stats = select_sample(cat, 20, run_seed("run"))
        years = {cat[m - 1]["date"][:4] for m in sel}
        assert len(years) >= 3, years

    def test_group_closure(self):
        cat = [
            _msg(1, "2023-01-01T10:00:00", gid=100),
            _msg(2, "2023-01-01T10:00:01", gid=100),
            _msg(3, "2023-01-01T10:00:02", gid=100, text="album caption", media={"type": "photo"}),
            _msg(4, "2023-01-01T10:00:03", media={"type": "photo"}),
        ]
        sel, _ = select_sample(cat, 2, run_seed("run"))
        if 1 in sel or 2 in sel or 3 in sel:
            assert {1, 2, 3} <= set(sel), sel  # complete group imported together

    def test_reply_closure(self):
        cat = [
            _msg(10, "2023-01-01T10:00:00", text="parent"),
            _msg(11, "2023-01-01T10:00:05", text="child", reply=10),
            _msg(12, "2023-01-01T10:00:10", text="other"),
        ]
        sel, _ = select_sample(cat, 1, run_seed("run"))
        if 11 in sel:
            assert 10 in sel

    def test_media_diversity_preferred(self):
        cat = (
            [_msg(i, f"2022-03-01T10:00:00", text=f"plain{i}") for i in range(1, 40)]
            + [
                _msg(100, "2022-03-02T10:00:00", media={"type": "photo"}),
                _msg(101, "2022-03-03T10:00:00", media={"type": "video"}),
                _msg(102, "2022-03-04T10:00:00", media={"type": "audio"}),
                _msg(103, "2022-03-05T10:00:00", media={"type": "sticker"}),
            ]
        )
        sel, stats = select_sample(cat, 8, run_seed("run"))
        by_id = {m["message_id"]: m for m in cat}
        types = {by_id[m]["media"]["type"] for m in sel if by_id[m].get("media")}
        assert len(types) >= 3, types