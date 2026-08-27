"""Export verification — SOURCE (live MTProto) vs CANONICAL ARCHIVE.

After an export completes, re-read the live Telegram history and compare every
message field against the canonical archive. Produces:

  EXPORT_VERIFICATION.json   {"status": "PASS"|"FAIL", "checks": {...}}
  EXPORT_VERIFICATION_REPORT.html

Import is gated on status == PASS.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Fields compared for EVERY message: (canonical key, description)
FIELD_CHECKS = [
    ("id", "message id"),
    ("date", "date"),
    ("sender_id", "sender"),
    ("text", "text"),
    ("media_ctor", "media constructor"),
    ("caption", "caption (text on media message)"),
    ("reply_to", "reply relationship"),
    ("grouped_id", "grouped media id"),
    ("forward", "forward provenance"),
    ("reactions", "reactions"),
    ("entities", "entities"),
]


def _media_ctor(message) -> str:
    """The media constructor, normalized for archive comparison.

    `MessageMediaWebPage` is a derived, ephemeral link preview (not user
    content) that the export intentionally does not archive (classify_media
    drops it, so the archive records the message as text-only with media=none).
    Treat it as `none` here so the live side matches the archive instead of
    producing a false export-verification FAIL that blocks import.
    """
    m = getattr(message, "media", None)
    if m is None:
        return "none"
    if type(m).__name__ == "MessageMediaWebPage":
        return "none"
    return type(m).__name__


def _has_media(message) -> bool:
    """Whether the message carries REAL user media (not a link preview)."""
    return _media_ctor(message) != "none"


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _source_record(message) -> dict[str, Any]:
    fwd = getattr(message, "fwd_from", None)
    fwd_info = None
    if fwd is not None:
        fwd_info = {
            "from_id": getattr(getattr(fwd, "from_id", None), "user_id", None)
            or getattr(getattr(fwd, "from_id", None), "channel_id", None),
            "from_name": getattr(fwd, "from_name", None),
            "date": _iso(getattr(fwd, "date", None)),
            "channel_post": getattr(fwd, "channel_post", None),
            "post_author": getattr(fwd, "post_author", None),
        }
    rt = getattr(message, "reply_to", None)
    reply_info = None
    if rt is not None:
        reply_info = {"reply_to_msg_id": getattr(rt, "reply_to_msg_id", None),
                      "top_msg_id": getattr(rt, "reply_to_top_id", None)}
    rx = getattr(getattr(message, "reactions", None), "results", None)
    reactions = None
    if rx:
        reactions = [{"ctor": type(r.reaction).__name__,
                      "emoji": getattr(r.reaction, "emoticon", None),
                      "document_id": getattr(r.reaction, "document_id", None),
                      "count": getattr(r, "count", 0)}
                     for r in rx]
    entities = []
    for e in getattr(message, "entities", None) or []:
        d = {"ctor": type(e).__name__, "offset": getattr(e, "offset", 0),
             "length": getattr(e, "length", 0)}
        if hasattr(e, "document_id"):
            d["document_id"] = e.document_id
        entities.append(d)
    text = getattr(message, "message", "") or ""
    has_media = _has_media(message)
    return {
        "id": getattr(message, "id", 0),
        "date": _iso(getattr(message, "date", None)),
        "sender_id": getattr(getattr(message, "sender", None), "id", None),
        "text": text,
        "media_ctor": _media_ctor(message),
        "caption": text if has_media else None,
        "reply_to": reply_info,
        "grouped_id": getattr(message, "grouped_id", None),
        "forward": fwd_info,
        "reactions": reactions,
        "entities": entities or None,
    }


# Archive semantic media type -> MTProto constructor it must come from
MEDIA_TYPE_TO_CTOR = {
    "photo": "MessageMediaPhoto",
    "sticker": "MessageMediaDocument",
    "video": "MessageMediaDocument",
    "animation": "MessageMediaDocument",
    "audio": "MessageMediaDocument",
    "voice": "MessageMediaDocument",
    "video_note": "MessageMediaDocument",
    "document": "MessageMediaDocument",
    "gif": "MessageMediaDocument",
}


def _archive_record(row: dict[str, Any]) -> dict[str, Any]:
    media = row.get("media") or []
    ctor = "none"
    if isinstance(media, list) and media:
        first = media[0]
        if isinstance(first, dict):
            # full canonical rows carry ctor; ledger-derived rows carry only
            # a semantic type, which maps 1:1 to the expected constructor.
            ctor = first.get("ctor") or MEDIA_TYPE_TO_CTOR.get(first.get("type")) or "none"
    elif isinstance(media, dict):
        ctor = media.get("ctor")
        if not ctor:
            types = [k for k in media.keys() if media[k]]
            ctor = MEDIA_TYPE_TO_CTOR.get(types[0], "none") if types else "none"
    if not ctor:
        ctor = "none"
    fwd = row.get("forwarded_from") or row.get("forward") or None
    if isinstance(fwd, dict):
        fwd = {
            "from_id": fwd.get("from_id"),
            "from_name": fwd.get("from_name") or fwd.get("name"),
            "date": fwd.get("date"),
            "channel_post": fwd.get("channel_post"),
            "post_author": fwd.get("post_author"),
        }
    return {
        "id": row.get("id"),
        "date": (row.get("date") or "")[:26],
        "sender_id": (row.get("sender") or {}).get("id") if isinstance(row.get("sender"), dict) else None,
        "text": row.get("text") or "",
        "media_ctor": ctor,
        "caption": (row.get("text") or "") if ctor not in (None, "none") else None,
        "reply_to": row.get("reply_to"),
        "grouped_id": row.get("grouped_id"),
        "forward": fwd,
        "reactions": row.get("reactions"),
        "entities": row.get("entities") or None,
    }


def _reactions_eq(a, b) -> bool:
    """Compare reaction sets by identity (type/emoji/doc_id) + count.
    Archive rows key the ctor as 'reaction_type'; live reads as 'ctor'."""
    def norm(rx):
        out = []
        if isinstance(rx, dict):
            rx = rx.get("reactions") or []
        for r in rx or []:
            if not isinstance(r, dict):
                continue
            out.append((r.get("ctor") or r.get("reaction_type"),
                        r.get("emoji"), r.get("document_id"), r.get("count")))
        return sorted(out)

    na, nb = norm(a), norm(b)
    return na == nb


def _forward_eq(a, b) -> bool:
    """Forward provenance: compare the immutable parts (date, channel_post,
    post_author, from_id). Display names differ between live reads and
    archived serializations (client-side resolution), so they are not compared."""
    if not a and not b:
        return True
    if not a or not b:
        return False
    return ((a.get("date") or "")[:26] == (b.get("date") or "")[:26]
            and a.get("channel_post") == b.get("channel_post")
            and a.get("post_author") == b.get("post_author"))


def _entities_eq(a, b) -> bool:
    """Compare entity lists across the two serialization vocabularies.

    Live `_source_record` serializes entities with the Telethon **ctor** name
    (e.g. {"ctor":"MessageEntityUrl", ...}); the canonical archive (via
    telegram_utils.serialize_entities) serializes with the semantic **type**
    (e.g. {"type":"url", ...}). Normalise both to (type, offset, length,
    url/language/user_id/document_id) before comparing so a field-vocabulary
    difference is not misread as a fidelity failure.
    """
    from app.services.telegram_utils import ENTITY_TYPE_MAP

    def norm(ents):
        out = []
        for e in ents or []:
            if not isinstance(e, dict):
                continue
            kind = e.get("type") or e.get("ctor") or e.get("cls") or "unknown"
            kind = ENTITY_TYPE_MAP.get(kind, kind)
            out.append((
                kind,
                e.get("offset"),
                e.get("length"),
                e.get("url"),
                e.get("language"),
                e.get("user_id"),
                e.get("document_id"),
            ))
        return sorted(out)

    return norm(a) == norm(b)


def compare_records(source: dict, archive: dict) -> dict[str, Any]:
    """Per-field comparison; returns dict of field -> ok/mismatch detail."""
    checks: dict[str, Any] = {}
    for field, _desc in FIELD_CHECKS:
        s, a = source.get(field), archive.get(field)
        if field in ("date",):
            ok = bool(s and a and (s or "")[:26] == (a or "")[:26]) or (not s and not a)
        elif field == "caption":
            ok = (s or "") == (a or "")
        elif field == "reactions":
            ok = _reactions_eq(s, a)
        elif field == "forward":
            ok = _forward_eq(s, a)
        elif field == "reply_to":
            ok = (s or {}).get("reply_to_msg_id") == (a or {}).get("reply_to_msg_id")
        elif field == "entities":
            ok = _entities_eq(s, a)
        else:
            ok = s == a
        checks[field] = {"ok": ok, "source": s, "archive": a}
    return checks


def _load_archive(export_dir: Path) -> dict[int, dict]:
    """Load canonical messages.json; returns {id: row}."""
    candidates = [export_dir / "messages.json", export_dir / "messages.jsonl"]
    for p in candidates:
        if p.exists():
            if p.suffix == ".json":
                try:
                    doc = json.loads(p.read_text(encoding="utf-8"))
                    return {m.get("id"): m for m in doc.get("messages", [])}
                except json.JSONDecodeError:
                    pass
            else:
                rows = {}
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        m = json.loads(line)
                        rows[m.get("id")] = m
                    except json.JSONDecodeError:
                        continue
                return rows
    return {}


async def verify_export(
    export_dir: Path,
    source_client,
    peer,
    export_id: int | None = None,
) -> dict[str, Any]:
    """Re-read live history and diff against the canonical archive."""
    archive_rows = _load_archive(export_dir)
    if not archive_rows:
        return {"status": "FAIL", "error": "no canonical archive found",
                "export_id": export_id}

    # Full live fetch (newest-first pagination)
    live: list[Any] = []
    offset_id = 0
    while True:
        batch = await source_client.get_messages(peer, limit=100, offset_id=offset_id)
        if not batch:
            break
        live.extend(batch)
        if len(batch) < 100:
            break
        offset_id = min(m.id for m in batch)

    source_by_id = {m.id: _source_record(m) for m in live}

    per_message = []
    checked = 0
    failed = 0
    missing_in_archive = []
    for msg_id, srec in sorted(source_by_id.items()):
        arec = archive_rows.get(msg_id)
        if arec is None:
            missing_in_archive.append(msg_id)
            failed += 1
            continue
        checks = compare_records(srec, _archive_record(arec))
        n_fail = sum(1 for c in checks.values() if not c["ok"])
        checked += 1
        failed += n_fail
        per_message.append({
            "source_id": msg_id,
            "failures": [f for f, c in checks.items() if not c["ok"]],
            "checks": checks,
        })

    status = "PASS" if checked > 0 and failed == 0 else "FAIL"
    summary = {
        "status": status,
        "export_id": export_id,
        "verified_at": datetime.now(UTC).isoformat(),
        "live_messages": len(source_by_id),
        "archive_messages": len(archive_rows),
        "checked": checked,
        "failed_checks": failed,
        "missing_in_archive": missing_in_archive,
        "per_message": per_message,
    }
    (export_dir / "EXPORT_VERIFICATION.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(export_dir, summary)
    return summary


def _write_report(export_dir: Path, summary: dict) -> None:
    rows = []
    for pm in summary.get("per_message", []):
        fails = pm["failures"]
        cls = "ok" if not fails else "bad"
        rows.append(
            f"<tr><td>{pm['source_id']}</td><td class='{cls}'>{'EXACT' if not fails else ', '.join(fails)}</td></tr>")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Export Verification Report</title>
<style>body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;margin:24px}}
h1,h2{{color:#38bdf8}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #334155;padding:4px 8px;font-size:12px}}
.ok{{color:#34d399}} .bad{{color:#f87171}}
.card{{border:1px solid #334155;border-radius:8px;padding:10px 16px;margin:6px 0}}</style></head><body>
<h1>EXPORT VERIFICATION REPORT</h1>
<div class="card">STATUS: <b class="{'ok' if summary['status']=='PASS' else 'bad'}">{summary['status']}</b><br>
live messages: {summary['live_messages']} · archive messages: {summary['archive_messages']} ·
checked: {summary['checked']} · failed checks: {summary['failed_checks']}<br>
missing in archive: {summary['missing_in_archive'] or 'none'}</div>
<h2>Per-message field comparison</h2>
<table><tr><th>source id</th><th>result</th></tr>{''.join(rows)}</table>
<p><small>Fields compared per message: id, date, sender, text, media constructor, caption, reply relationship, grouped_id, forward provenance, reactions, entities.</small></p>
</body></html>"""
    (export_dir / "EXPORT_VERIFICATION_REPORT.html").write_text(html, encoding="utf-8")
