"""Source -> target message mapping for Telegram Recovery v2.

After an import the new target messages carry NEW ids. We map each source
message to its reconstructed target twin using composite fingerprints —
media SHA-256 first, then (text + date + sender), then positional sequence
fallback. Text alone is never used as the only signal.

Evidence per mapping: ``confidence`` and ``reason`` explain why a link was made,
so a human (or the fidelity report) can judge reliability.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Any

DATE_TOLERANCE_SECONDS = 2 * 24 * 3600  # importers may shift timestamps
_WS = re.compile(r"\s+")


def _norm_text(t: str | None) -> str:
    return _WS.sub(" ", (t or "")).strip().lower()


def _media_shas(rec: dict) -> list[str]:
    out = []
    for m in rec.get("media") or []:
        if m.get("sha256"):
            out.append(m["sha256"])
    return sorted(set(out))


def _media_doc_ids(rec: dict) -> list[str]:
    out = []
    for m in rec.get("media") or []:
        if m.get("media_id") is not None:
            out.append(str(m["media_id"]))
    return sorted(set(out))


def _date_secs(rec: dict) -> float | None:
    d = rec.get("date")
    if d is None:
        return None
    if isinstance(d, (int, float)):
        return float(d)
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


@dataclass
class Mapping:
    source_message_id: int
    target_message_id: int
    confidence: str  # EXACT_MEDIA | MATCH_TEXT_DATE | SEQUENCE | NONE
    reason: str
    features: dict[str, Any] = field(default_factory=dict)


def build_index(target: list[dict]) -> dict:
    """Index target records for lookups."""
    by_sha: dict[str, list[dict]] = {}
    by_doc: dict[str, list[dict]] = {}
    for t in target:
        for s in _media_shas(t):
            by_sha.setdefault(s, []).append(t)
        for d in _media_doc_ids(t):
            by_doc.setdefault(d, []).append(t)
    return {"by_sha": by_sha, "by_doc": by_doc}


def map_source_to_target(source: list[dict], target: list[dict],
                         delta_target_ids: set[int] | None = None,
                         date_tolerance: float = DATE_TOLERANCE_SECONDS) -> list[Mapping]:
    """Greedy deterministic source->target mapping.

    Only records whose target id is in ``delta_target_ids`` (the current run's
    new messages) are candidates by default; pass ``None`` to allow the whole
    target history.
    """
    candidates = [t for t in target
                  if delta_target_ids is None or t["target_message_id"] in delta_target_ids]
    idx = build_index(candidates)
    used: set[int] = set()
    mappings: list[Mapping] = []

    for s in source:
        target_id, confidence, reason, features = _match_source(
            s, candidates, used, idx, date_tolerance)
        if target_id is None:
            # sequence fallback in read order among still-unused candidates
            target_id, reason = _sequence_fallback(s, candidates, used)
            if target_id is None:
                mappings.append(Mapping(s["source_message_id"], -1, "NONE",
                                        "no target twin found", features))
                continue
            confidence, features = "SEQUENCE", {"sequence": True}
        used.add(target_id)
        mappings.append(Mapping(s["source_message_id"], target_id, confidence,
                                reason, features))

    mappings.sort(key=lambda m: m.source_message_id)
    return mappings


def _source_userid(s: dict) -> int | None:
    fi = s.get("from_id") or {}
    return fi.get("user_id")


def _fwd_minute(t: dict) -> float | None:
    """Rounded-to-minute UTC minute of an imported target's fwd_from.date."""
    fwd = t.get("forward") or {}
    fd = fwd.get("date")
    if fd is None:
        return None
    if isinstance(fd, (int, float)):
        return round(float(fd) / 60.0)
    try:
        return round(datetime.fromisoformat(str(fd).replace("Z", "+00:00")).timestamp() / 60.0)
    except ValueError:
        return None


def _match_source(s, candidates, used, idx, tol):
    # 1) exact media hash / document id
    for keyname, value_name in (("sha256", "by_sha"), ("media_id", "by_doc")):
        keys = _media_shas(s) if keyname == "sha256" else _media_doc_ids(s)
        for k in keys:
            for t in idx[value_name].get(k, []):
                if t["target_message_id"] in used:
                    continue
                return (t["target_message_id"], "EXACT_MEDIA",
                        f"matching {keyname} {k[:12]}", {"key": keyname, "value": k})
    # 2) imported-message anchor: the target's fwd_from.date (rounded to the
    #    minute) must match the source UTC minute. Telegram stamps every imported
    #    message with fwd_from.date = the encoded historical instant, so this is
    #    the authoritative link and far more reliable than text (which the import
    #    may reformat) or fuzzy position.
    sd_m = round(_date_secs(s) / 60.0) if _date_secs(s) is not None else None
    src_uid = _source_userid(s)
    if sd_m is not None:
        best = None
        for t in candidates:
            if t["target_message_id"] in used:
                continue
            tm = _fwd_minute(t)
            if tm is None:
                continue
            if abs(tm - sd_m) > 3 and abs(tm - (sd_m + 60)) > 3:
                continue
            if src_uid is not None:
                want = f"user_{src_uid}"
                if (t.get("forward") or {}).get("from_name") != want:
                    continue
            best = t
            break
        if best is not None:
            delta = (tm - sd_m) * 60
            return (best["target_message_id"], "IMPORTED_FWD_DATE",
                    "fwd_from.date matches source minute",
                    {"delta_secs": round(delta, 1)})
    # 3) text + date + presence-of-media (sender id differs after import)
    text = _norm_text(s.get("text"))
    if text:
        for t in candidates:
            if t["target_message_id"] in used:
                continue
            if _norm_text(t.get("text")) == text:
                sd, td = _date_secs(s), _date_secs(t)
                if sd is not None and td is not None and abs(sd - td) <= tol:
                    # photo+caption must stay ONE record; skip if shapes diverge
                    if bool(s.get("media")) != bool(t.get("media")):
                        continue
                    return (t["target_message_id"], "MATCH_TEXT_DATE",
                            "text + timestamp match",
                            {"text": text[:40], "delta_secs": sd - td})
    return None, None, None, {}


def _sequence_fallback(s, candidates, used):
    # positional fallback: assumes the importer preserves source order 1:1
    # from index 0. Only safe when exactly one unused candidate remains per
    # position — we take the FIRST free candidate in read order.
    free = [t for t in candidates if t["target_message_id"] not in used]
    if not free:
        return None, "no free target candidates"
    return free[0]["target_message_id"], "positional fallback"


def dump_mapping(mappings: list[Mapping], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "mappings": [
            {"source_message_id": m.source_message_id,
             "target_message_id": m.target_message_id,
             "confidence": m.confidence, "reason": m.reason}
            for m in mappings
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path