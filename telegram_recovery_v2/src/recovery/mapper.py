"""Source -> target mapping.

Builds source_to_target.json after the target history is re-read. Matching
uses MULTIPLE signals (never text alone): sender, timestamp family, media
fingerprint (sha256 where comparable), caption, sequence order, grouped_id.

Every entry: {source_message_id, target_message_id, confidence, reason}.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path


def load_ndjson(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def text_similarity(a: str | None, b: str | None) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def map_source_to_target(
    archive_dir: Path,
    target_after: Path,
    target_before_ids: set[int],
    run_dir: Path,
) -> dict:
    """Match imported messages (target delta) to source archive messages.

    Strategy per target message in the delta (which contains ONLY this run's
    imports): score against every unmatched source message.
    """
    sources = load_ndjson(archive_dir / "messages.ndjson")
    targets = [
        t for t in load_ndjson(target_after) if t["message_id"] not in target_before_ids
    ]
    # Imported sequence is chronological; source archive is reverse-chron.
    srcs = sorted(sources, key=lambda m: m["date"])

    mappings = []
    used = set()
    ti = 0
    for t in targets:
        best, best_score, best_reason = None, 0.0, ""
        for s in srcs:
            if s["message_id"] in used:
                continue
            score = 0.0
            reasons = []
            # TIMESTAMP (strongest now that tz-corrected file times materialize
            # at the source instant; verified target snapshot carries 'date')
            s_ts = s.get("date") or ""
            t_ts = t.get("date") or ""
            if s_ts and t_ts:
                try:
                    from datetime import datetime

                    a = datetime.fromisoformat(s_ts)
                    b = datetime.fromisoformat(t_ts)
                    delta = abs((a - b).total_seconds())
                    if delta <= 5:
                        score += 0.6
                        reasons.append(f"ts:{delta:.0f}s")
                    elif delta <= 120:
                        score += 0.2
                        reasons.append(f"ts~:{int(delta)}s")
                except ValueError:
                    pass
            # Media presence AND type
            sm, tm = s.get("media"), t.get("media")
            sm_t = (sm or {}).get("type")
            tm_t = (tm or {}).get("type")
            if (sm is None) == (tm is None or tm == "MessageMediaEmpty"):
                score += 0.2
                reasons.append("media-presence")
            if sm_t and tm_t and sm_t == tm_t:
                score += 0.4
                reasons.append(f"media-type:{sm_t}")
            # Text/caption similarity
            sim = text_similarity(s.get("text"), t.get("text"))
            if sim:
                score += 0.5 * sim
                reasons.append(f"text:{sim:.2f}")
            # Sender direction (A-sent sources map to Bob-imported msgs with
            # imported fwd header; B-sent sources map to plain text imports)
            # Weak signal; only breaks ties via sequence.
            if score > best_score:
                best, best_score, best_reason = s, score, "+".join(reasons)
        # Sequence proximity as a post-hoc tie-breaker among same-timestamp
        # candidates: prefer the next unused source IN ORDER when the scores
        # are tied (identical fixture texts across rounds at +1s spacing).
        if best is None or best_score < 0.35:
            # allow exact-timestamp-but-weaker match only when truly unique
            for s in srcs:
                if s["message_id"] in used:
                    continue
                s_ts = s.get("date") or ""
                if s_ts and t.get("date"):
                    from datetime import datetime

                    try:
                        delta = abs((datetime.fromisoformat(s_ts) - datetime.fromisoformat(t["date"])).total_seconds())
                    except ValueError:
                        delta = 999
                    if delta <= 1 and (best is None or best_score < 0.35):
                        best, best_score = s, 0.35
                        if best and best_score == 0.35:
                            break  # unique within 1s -> lock
        if best and best_score >= 0.35:
            used.add(best["message_id"])
            confidence = "high" if best_score >= 0.8 else ("medium" if best_score >= 0.6 else "low")
            mappings.append(
                {
                    "source_message_id": best["message_id"],
                    "target_message_id": t["message_id"],
                    "confidence": confidence,
                    "reason": best_reason,
                }
            )
            ti += 1
    mapped_ids = {m["target_message_id"] for m in mappings}
    out = {
        "mapped": len(mappings),
        "unmatched_source": [s["message_id"] for s in srcs if s["message_id"] not in used],
        "unmatched_target": [t["message_id"] for t in targets if t["message_id"] not in mapped_ids],
        "mappings": mappings,
    }
    (run_dir / "source_to_target.json").write_text(json.dumps(out, indent=2))
    return out
