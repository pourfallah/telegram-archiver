"""Deterministic, diversity-aware sampling of a source chat's history.

Used by the real-history sampling test (scripts/sample_historical_recovery.py).

Rules (spec FINAL REAL-HISTORY SAMPLING TEST):
- seed = SHA256(run_id) -> fully reproducible selection
- time buckets by year (or 6-month when few years) -> multi-period coverage
- maximize type diversity: text / formatted / photo / photo+caption / video /
  gif / audio / voice / document / sticker / custom emoji / reaction /
  reply / forward / grouped album
- grouped media: select the COMPLETE group (atomic unit)
- replies: select the reply's PARENT as well (closure)
- deterministic given seed + catalog order
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path


def run_seed(run_id: str) -> int:
    return int(hashlib.sha256(run_id.encode()).hexdigest()[:16], 16)


def bucket_key(date_iso: str, use_year: bool = True) -> str:
    """YYYY for year buckets; YYYY-H1/H2 for 6-month buckets."""
    date = date_iso[:10]
    year = date[:4]
    if not use_year:
        month = int(date[5:7])
        return f"{year}-{'H1' if month <= 6 else 'H2'}"
    return year


def message_types(msg: dict) -> set[str]:
    """Feature tags for diversity scoring; never fabricated.

    Works for BOTH full snapshot dicts (media={...}) and lightweight catalog
    rows (media_type='photo').
    """
    types = set()
    text = msg.get("text") or ""
    media = msg.get("media")
    if isinstance(media, dict):
        mtype = media.get("type")
    else:
        mtype = msg.get("media_type")
    if mtype:
        types.add(mtype if mtype != "webpage" else "text")
        if text and mtype in ("photo", "video", "audio", "voice", "document", "sticker", "gif"):
            types.add(mtype + "_caption")
    elif text:
        types.add("text")
    if msg.get("entities"):
        types.add("formatted")
    if msg.get("reactions") or msg.get("has_reactions"):
        types.add("reaction")
    if msg.get("reply_to") or msg.get("has_reply"):
        types.add("reply")
    if msg.get("fwd_from") or msg.get("fwd"):
        types.add("forward")
    if msg.get("grouped_id"):
        types.add("group")
    return types


def select_sample(
    catalog: list[dict],
    count: int,
    seed: int,
    year_buckets: bool = True,
) -> tuple[list[int], dict]:
    """Select at most `count` source message ids + required closure.

    Diversity-first (spec 50): prefer rare media types / replies / forwards /
    reactions / albums over plain text, spread across years. Deterministic for
    the same catalog order + seed.
    """
    rng = random.Random(seed)
    by_id = {m["message_id"]: m for m in catalog}
    selected: list[int] = []
    used_types: set[str] = set()

    # group ids -> member ids (atomic album units)
    groups: dict[str, list[int]] = {}
    for m in catalog:
        gid = m.get("grouped_id")
        if gid:
            groups.setdefault(str(gid), []).append(m["message_id"])

    # bucket the catalog by year
    buckets: dict[str, list[dict]] = {}
    for m in catalog:
        buckets.setdefault(bucket_key(m["date"], year_buckets), []).append(m)
    stats = {"buckets": {k: len(v) for k, v in buckets.items()}}

    def add_unit(mid: int) -> None:
        """Add message + full album group + reply parent (one atomic unit)."""
        m = by_id.get(mid)
        if not m or mid in selected:
            return
        gid = m.get("grouped_id")
        mem = groups.get(str(gid), []) if gid else [mid]
        for x in mem:
            if x not in selected and x in by_id:
                selected.append(x)
                for t in message_types(by_id[x]):
                    used_types.add(t)
                rp = by_id[x].get("reply_to") or {}
                pid = rp.get("reply_to_msg_id")
                if pid and pid in by_id and pid not in selected:
                    selected.append(pid)
                    for t in message_types(by_id[pid]):
                        used_types.add(t)

    # rarity ordering of feature tags (rare = rich sample)
    rarity = [
        "gif", "voice", "document", "audio", "sticker", "video", "reaction",
        "photo_caption", "forward", "group", "reply", "formatted",
        "photo", "text",
    ]
    tag_rank = {t: i for i, t in enumerate(rarity)}

    def feature_rank(m: dict) -> tuple:
        """(rank_of_rarest_feature, -text_len, random) — lower is better."""
        tags = message_types(m)
        best = min((tag_rank.get(t, 99) for t in tags), default=99)
        return (best, -(m.get("text_len") or 0), rng.random())

    # Pass 1: one pick per DISTINCT RARE CATEGORY per year-round —
    # explicitly span media categories before repeating a category.
    # Prefer messages that add a category never yet sampled.
    def used_cats() -> set:
        u = set()
        for mid in selected:
            u |= message_types(by_id[mid])
        return u

    ordered = sorted(buckets.items())  # deterministic year buckets
    def unit_size(mid: int) -> int:
        """Full unit size: message + complete album group + reply parent."""
        m = by_id.get(mid)
        if not m:
            return 0
        gid = m.get("grouped_id")
        n = len(groups.get(str(gid), [])) if gid else 1
        rp = m.get("reply_to") or {}
        if rp.get("reply_to_msg_id") in by_id:
            n += 1
        return n

    guard = 0
    # PASS 1: one unit per previously-unseen category (globally), year-balanced.
    # Never repeats a category while unseen categories remain anywhere.
    while len(selected) < count and guard < count * 15:
        guard += 1
        cats = used_cats()
        year_counts = {}
        for mid in selected:
            y = by_id[mid]["date"][:4]
            year_counts[y] = year_counts.get(y, 0) + 1
        # prefer untouched years first, then least-covered
        ordered_years = sorted(ordered, key=lambda kv: (year_counts.get(kv[0][:4], -1), kv[0]))
        best_cand, best_key = None, None
        best_score = (999, 999)
        for key, msgs in ordered_years:
            remaining = [m for m in msgs if m["message_id"] not in selected]
            if not remaining:
                continue
            # scan candidates in richness order until one adds a NEW category
            for cand in sorted(remaining, key=feature_rank):
                if len(selected) + unit_size(cand["message_id"]) > count + 3:
                    continue
                cand_cats = message_types(cand) or {"text"}
                newcats = cand_cats - cats
                if not newcats:
                    continue  # only NEW categories here; fill pass handles repeats
                score = (min(tag_rank.get(t, 99) for t in newcats), unit_size(cand["message_id"]))
                if score < best_score:
                    best_score, best_cand, best_key = score, cand, key
                break  # this year's best NEW-category candidate found
        if best_cand is None:
            break
        add_unit(best_cand["message_id"])

    # PASS 2: fill remaining budget, year round-robin, but actively avoid
    # repeating categories already over-represented in the sample.
    if len(selected) < count:
        def cat_count(cat: str) -> int:
            n = 0
            for mid in selected:
                if cat in message_types(by_id[mid]):
                    n += 1
            return n

        remaining = [m for m in catalog if m["message_id"] not in selected]
        by_year: dict[str, list] = {}
        for m in remaining:
            by_year.setdefault(m["date"][:4], []).append(m)
        years_sorted = sorted(by_year)
        i = 0
        guard2 = 0
        while len(selected) < count and any(by_year.values()) and guard2 < count * 20:
            guard2 += 1
            y = years_sorted[i % len(years_sorted)]
            pool_y = by_year[y]
            pick = None
            # find the candidate in this year that adds the most under-covered
            # category; skip if its unit overflows the budget
            best = None
            best_score = 999
            for cand in pool_y:
                if len(selected) + unit_size(cand["message_id"]) > count + 3:
                    continue
                cats_c = message_types(cand) or {"text"}
                score = min(cat_count(t) for t in cats_c)
                if score < best_score:
                    best_score, best = score, cand
            if best is None:
                # nothing fits in this year; drop it
                by_year.pop(y, None)
                years_sorted = sorted(by_year)
                if not years_sorted:
                    break
                i = i % len(years_sorted) if years_sorted else 0
                continue
            pool_y.remove(best)
            add_unit(best["message_id"])
            i += 1
            if not pool_y:
                by_year.pop(y, None)
                years_sorted = sorted(by_year)
                if not years_sorted:
                    break
                i = i % len(years_sorted) if years_sorted else 0

    stats.update(
        {
            "selected_count": len(selected),
            "years": sorted({m["date"][:4] for m in catalog if m["message_id"] in selected}),
            "media_types": sorted({mt for mt in used_types if mt in ("photo", "video", "gif", "audio", "voice", "document", "sticker", "text")}),
            "used_types": sorted(used_types),
        }
    )
    return selected, stats


def build_catalog_from_archive(archive_dir: Path) -> list[dict]:
    """Rebuild a minimal catalog from a v2 archive messages.ndjson (for tests)."""
    out = []
    for line in (archive_dir / "messages.ndjson").read_text().strip().splitlines():
        m = json.loads(line)
        out.append(
            {
                "message_id": m["message_id"],
                "date": m["date"],
                "text": m.get("text"),
                "media": m.get("media"),
                "entities": m.get("entities"),
                "reactions": m.get("reactions"),
                "reply_to": m.get("reply_to"),
                "fwd_from": m.get("fwd_from"),
                "grouped_id": m.get("grouped_id"),
                "sender_label": m.get("sender_label"),
            }
        )
    return out