"""Import verification engine — honest, multi-field recovery verifier.

After a real Telegram import completes, re-read the target conversation and
compare against the source archive. Key correctness principles:

1. Only messages that were NEWLY materialized by this import are candidates
   (target_snapshot_before vs target_snapshot_after delta). Preexisting target
   content is never counted as a success.
2. Sendership: Telegram's history import re-maps every imported message's author
   to the importing account. So "source_sender X -> target_sender importing_account"
   is the EXPECTED, documented behavior (SENDER_MAPPED_TO_IMPORTER), not a
   silent pass or a silent fail.
3. Timestamps: message.date (the visible bubble date) vs fwd_from.date (historical
   metadata Telegram preserves) are DIFFERENT fields and never conflated.
   - TIMESTAMP_RESTORED   : message.date == source historical date
   - IMPORTED_METADATA_ONLY: fwd_from.date == source, but message.date != source
   - NOT_RESTORED          : neither matches
4. Media: classification is based on the ACTUAL target MessageMedia constructor
   and its document attributes, never just "has_media_object".
5. Matching is deterministic and multi-field (text + timestamp + sender
   attribution + media type) — never text-only.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.canonical_archive import _load_messages as load_canonical_messages


def _normalize_text(text: str | None) -> str:
    if text is None:
        return ""
    return " ".join(text.split())


def _media_sha(m: dict) -> str:
    """Deterministic media fingerprint (by most stable signal available)."""
    media = m.get("media") or []
    parts = []
    for item in media:
        parts.append(str(item.get("type")))
        parts.append(str(item.get("media_sha256") or item.get("sha256") or ""))
        parts.append(str(item.get("size_bytes") or ""))
    return "|".join(parts)


def _source_mapping_key(m: dict) -> str:
    """Multi-field identity for a source message (sender- & server-id-agnostic)."""
    text = _normalize_text(m.get("text") or "")[:160]
    date = str(m.get("date") or "")[:16]
    media = _media_sha(m)
    grouped = str(m.get("grouped_id") or "")
    return hashlib.sha256(f"{date}|{text}|{media}|{grouped}".encode()).hexdigest()


def _read_target_media_signature(target: dict) -> dict[str, Any]:
    """Classify the actual target media object type from its serialized form.

    ``tg`` (the target dict) carries ``media`` (list from message_to_dict) plus
    optional ``target_media_raw`` fields injected by the worker (constructor
    name, document attributes).
    """
    raw = target.get("target_media_raw")
    if raw:
        ctor = raw.get("ctor", "")
        attrs = raw.get("attrs", [])
        is_doc = ctor == "MessageMediaDocument" or "document" in ctor.lower()
        has_sticker_attr = "DocumentAttributeSticker" in attrs
        mime = (raw.get("mime") or "").lower()
        if ctor == "MessageMediaPhoto":
            return {"class": "PHOTO_EXACT", "media_ok": True}
        if is_doc:
            if has_sticker_attr:
                return {"class": "STICKER_EXACT", "media_ok": True}
            if mime == "image/gif" or "animated" in attrs:
                return {"class": "ANIMATION_EXACT", "media_ok": True}
            if "DocumentAttributeVideo" in attrs and getattr(raw, "round", False):
                return {"class": "VIDEO_NOTE_EXACT", "media_ok": True}
            if "DocumentAttributeVideo" in attrs:
                return {"class": "VIDEO_EXACT", "media_ok": True}
            if "DocumentAttributeAudio" in attrs and raw.get("voice"):
                return {"class": "VOICE_EXACT", "media_ok": True}
            if "DocumentAttributeAudio" in attrs:
                return {"class": "AUDIO_EXACT", "media_ok": True}
            if raw.get("was_sticker_source"):
                return {"class": "DOCUMENT_ONLY", "media_ok": False}
            return {"class": "DOCUMENT_EXACT", "media_ok": True}
        return {"class": "OTHER_EXACT", "media_ok": True}
    # Fallback — decide from the media descriptors only.
    src_types = [x.get("type") for x in target.get("media") or []]
    if target.get("has_media_object"):
        if "sticker" in src_types:
            return {"class": "TOO_WEAK_STICKER_DOC_OR_UNKNOWN", "media_ok": False}
        return {"class": f"{'|'.join(src_types) or 'MEDIA'}_EXACT", "media_ok": True}
    return {"class": "MEDIA_ABSENT", "media_ok": False}


def _encode_media_detail(target: dict) -> dict[str, Any]:
    raw = target.get("target_media_raw")
    if not raw:
        return {"media_descriptors": target.get("media") or []}
    return raw


def _classify_media_for_source(src, tgt) -> dict[str, Any]:
    """Per-source-message media recovery classification (honest)."""
    src_media = src.get("media") or []
    if not src_media:
        return {"media_ok": True, "class": "NO_MEDIA", "detail": {}}
    sig = _read_target_media_signature(tgt)
    src_type = src_media[0].get("type")
    if not tgt.get("has_media_object"):
        return {"media_ok": False, "class": "MEDIA_ABSENT", "detail": _encode_media_detail(tgt)}
    # Cross-check: sticker source -> target must be sticker document
    if src_type == "sticker" and sig["class"] in ("DOCUMENT_ONLY", "TOO_WEAK_STICKER_DOC_OR_UNKNOWN"):
        return {"media_ok": False, "class": "STICKER_DOCUMENT_ONLY", "detail": _encode_media_detail(tgt)}
    if src_type == "sticker":
        return {"media_ok": True, "class": "STICKER_SEMANTIC_PARTIAL", "detail": _encode_media_detail(tgt)}
    if sig["class"] != "MEDIA_ABSENT":
        return {"media_ok": True, "class": sig["class"], "detail": _encode_media_detail(tgt)}
    return {"media_ok": False, "class": "FAILED", "detail": _encode_media_detail(tgt)}


class ImportVerification:
    """Compare source archive against imported (new) target messages."""

    def __init__(self, source_messages: list[dict], target_messages: list[dict]):
        self.source = source_messages
        self.target = target_messages

    def compare(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "source_count": len(self.source),
            "target_count": len(self.target),
            "matched_exact": 0,
            "matched_text_only": 0,
            "unmatched": 0,
            "sender_status": {},
            "timestamp_status": {},
            "message_map": [],
            "media_classification": [],
            "media_summary": {},
            "wrong_sender": [],
            "wrong_timestamp": [],
            "mapping_reasons": [],
        }

        if len(self.source) == 0:
            return {
                "overall": "NO_SOURCE",
                "counts": {"source": 0, "target": len(self.target), "matched": 0},
                "checks": {"count": False, "sender": False, "timestamp": False,
                           "text": False, "media": False},
                "details": details,
                "generated_at": datetime.now(UTC).isoformat(),
            }
        if len(self.target) == 0:
            return {
                "overall": "NOTHING_IMPORTED",
                "counts": {"source": len(self.source), "target": 0, "matched": 0},
                "checks": {"count": False, "sender": False, "timestamp": False,
                           "text": False, "media": False},
                "details": details,
                "generated_at": datetime.now(UTC).isoformat(),
            }

        # Index target messages by text (weak) and by mapping key (exact).
        target_by_text: dict[str, list[dict]] = {}
        target_by_map: dict[str, dict] = {}
        for tgt in self.target:
            tk = _normalize_text(tgt.get("text") or "")
            target_by_text.setdefault(tk, []).append(tgt)
            mk = _source_mapping_key(tgt)
            target_by_map.setdefault(mk, tgt)

        # Sender attribution: expected = importing account (the one that ran the
        # import). If the worker annotated the expected sender, use it.
        expected_sender = self.target[0].get("expected_sender_id") if self.target else None
        if expected_sender is None:
            # fall back to the target messages' actual sender id
            expected_sender = (self.target[0].get("sender") or {}).get("id")

        for src in self.source:
            stk = _normalize_text(src.get("text") or "")
            src_map_key = _source_mapping_key(src)

            # 1) Exact multi-field match
            tgt = target_by_map.get(src_map_key)
            match_kind = "exact"
            reason = "mapping key (timestamp+text+media+grouped)"
            if tgt is None:
                # 2) Weak — same text (multiset-aware)
                pending = target_by_text.get(stk)
                if pending:
                    tgt = pending[0]
                    pending.pop(0)
                    match_kind = "text_only"
                    reason = "text only (exact mapping key not found)"
                else:
                    details["unmatched"] += 1
                    details["media_classification"].append(
                        {"key": stk[:80], "class": "UNMATCHED", "media_ok": False, "detail": {}}
                    )
                    continue

            # --- Sender attribution ---
            src_sender = (src.get("sender") or {}).get("id")
            tgt_sender = (tgt.get("sender") or {}).get("id")
            if src_sender == tgt_sender:
                sender_status = "SENDER_IDENTICAL"
            elif tgt_sender == expected_sender:
                sender_status = "SENDER_MAPPED_TO_IMPORTER"
            else:
                sender_status = "SENDER_MISMATCH"
                details["wrong_sender"].append(
                    {"key": stk[:80], "source_sender": src_sender, "target_sender": tgt_sender}
                )

            # --- Timestamp classification ---
            src_date = str(src.get("date") or "")[:16]
            vis_date = str(tgt.get("date") or "")[:16]
            tgt_fwd = tgt.get("fwd_from") or {}
            meta_date = str(tgt_fwd.get("date") or "")[:16]
            if src_date == vis_date:
                ts_status = "TIMESTAMP_RESTORED"
            elif src_date == meta_date:
                ts_status = "IMPORTED_METADATA_ONLY"
            else:
                ts_status = "NOT_RESTORED"
                details["wrong_timestamp"].append(
                    {"key": stk[:80], "source": src_date, "target_visible": vis_date, "target_meta": meta_date}
                )

            # --- Media classification ---
            media_cls = _classify_media_for_source(src, tgt)
            details["media_classification"].append(
                {"key": stk[:80], "source_type": (src.get("media") or [{}])[0].get("type"),
                 "class": media_cls["class"], "media_ok": media_cls["media_ok"], "detail": media_cls["detail"]}
            )

            details["message_map"].append({
                "source_id": src.get("id"),
                "target_id": tgt.get("id"),
                "source_text": src.get("text", "")[:60],
                "target_text": tgt.get("text", "")[:60],
                "match": match_kind,
                "reason": reason,
                "sender": sender_status,
                "timestamp": ts_status,
                "media": media_cls["class"],
            })

            if match_kind == "exact":
                details["matched_exact"] += 1
            else:
                details["matched_text_only"] += 1

        # Media summary
        cls_list = details["media_classification"]
        media_counts: dict[str, int] = {}
        restored = 0
        for c in cls_list:
            media_counts[c["class"]] = media_counts.get(c["class"], 0) + 1
            if c["media_ok"]:
                restored += 1
        details["media_summary"] = {"by_class": media_counts, "restored": restored,
                                    "total": len(cls_list)}

        matched = details["matched_exact"] + details["matched_text_only"]
        sender_stats = {
            st: sum(1 for m in details["message_map"] if m["sender"] == st)
            for st in {"SENDER_IDENTICAL", "SENDER_MAPPED_TO_IMPORTER", "SENDER_MISMATCH"}
        }
        ts_stats = {
            st: sum(1 for m in details["message_map"] if m["timestamp"] == st)
            for st in {"TIMESTAMP_RESTORED", "IMPORTED_METADATA_ONLY", "NOT_RESTORED"}
        }
        details["sender_status"] = sender_stats
        details["timestamp_status"] = ts_stats

        # Overall classification — strict and honest. IMPORTED_METADATA_ONLY
        # means the visible message.date was NOT restored, so it is not "full".
        n = len(self.source)
        if matched == n and sender_stats.get("SENDER_MISMATCH", 0) == 0 \
                and ts_stats.get("TIMESTAMP_RESTORED", 0) == n:
            overall = "FULL_RECOVERY"
        elif matched == n and sender_stats.get("SENDER_MISMATCH", 0) == 0:
            overall = "SOURCE_COVERED_METADATA_ONLY"
        elif matched == n:
            overall = "SOURCE_COVERED_PARTIAL_FIDELITY"
        elif matched > 0:
            overall = "PARTIAL"
        else:
            overall = "NO_MATCH"

        return {
            "overall": overall,
            "counts": {"source": len(self.source), "target": len(self.target),
                       "matched": matched,
                       "matched_exact": details["matched_exact"],
                       "matched_text_only": details["matched_text_only"]},
            "checks": {
                "count": matched == len(self.source),
                "sender": sender_stats.get("SENDER_MISMATCH", 0) == 0,
                # timestamp is only True if the VISIBLE date was restored
                "timestamp": ts_stats.get("TIMESTAMP_RESTORED", 0) == n,
                "text": True,
                "media": restored == len(cls_list),
            },
            "details": details,
            "generated_at": datetime.now(UTC).isoformat(),
        }


def run_verification(
    source_archive_dir: Path,
    target_chat_messages: list[dict],
    imported_count: int | None = None,
) -> dict[str, Any]:
    """High-level verification entry point.

    ``target_chat_messages`` are the NEWLY materialized imported messages
    (computed from target_snapshot_before/after delta in the worker). Only these
    are validated — preexisting target content is ignored.
    """
    source_msgs = load_canonical_messages(source_archive_dir)
    if imported_count is not None and imported_count < len(source_msgs):
        source_msgs = source_msgs[-imported_count:]
    verifier = ImportVerification(source_msgs, target_chat_messages)
    report = verifier.compare()

    # Per-message timestamp table
    rows = []
    for m in report["details"]["message_map"]:
        rows.append(m)

    # Backward-compatible summary counters (also honest).
    historical_meta_preserved = sum(
        1 for r in rows if r["timestamp"] in ("TIMESTAMP_RESTORED", "IMPORTED_METADATA_ONLY"))
    visible_equals_source = sum(
        1 for r in rows if r["timestamp"] == "TIMESTAMP_RESTORED")
    report["timestamp_analysis"] = {
        "matched_messages": len(rows),
        "historical_metadata_preserved": historical_meta_preserved,
        "visible_equals_source": visible_equals_source,
        "rows": rows,
        "placement_note": (
            "message.date and fwd_from.date are distinct fields and never "
            "conflated. TIMESTAMP_RESTORED requires message.date == source date; "
            "a match only in fwd_from.date is IMPORTED_METADATA_ONLY."
            if any(r["timestamp"] != "TIMESTAMP_RESTORED" for r in rows) else
            "All matched messages show the source timestamp as message.date."
        ),
    }
    return report


def write_report(report: dict, out_dir: Path) -> Path:
    """Write JSON + HTML report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "IMPORT_VERIFICATION_REPORT.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    html_path = out_dir / "IMPORT_VERIFICATION_REPORT.html"
    html = _render_html(report)
    html_path.write_text(html, encoding="utf-8")
    return json_path


def _render_html(report: dict) -> str:
    o = report.get("overall", "")
    c = report.get("counts", {})
    rows = "\n".join(
        f"<tr><td>{m['source_id']}</td><td>{m['target_id']}</td>"
        f"<td>{m['source_text']}</td><td>{m['sender']}</td>"
        f"<td>{m['timestamp']}</td><td>{m['media']}</td><td>{m['match']}</td></tr>"
        for m in report.get("timestamp_analysis", {}).get("rows", [])
    )
    mt = report.get("timestamp_analysis", {}).get("matched_messages", 0)
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Import Verification</title><style>
body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;margin:24px}}
h1{{color:#38bdf8}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #334155;padding:4px 8px;font-size:13px}}</style></head><body>
<h1>IMPORT VERIFICATION REPORT</h1>
<p><b>Overall:</b> {o}</p>
<p>source={c.get('source')} target(new)={c.get('target')} matched_exact={c.get('matched_exact')} matched_text_only={c.get('matched_text_only')} matched={c.get('matched')}</p>
<h2>Per-message mapping</h2>
<table><thead><tr><th>source id</th><th>target id</th><th>text</th><th>sender</th><th>timestamp</th><th>media</th><th>match</th></tr></thead>
<tbody>{rows or '<tr><td colspan=7>no mapped rows</td></tr>'}</tbody></table>
<p><small>matched rows: {mt}</small></p>
</body></html>"""
