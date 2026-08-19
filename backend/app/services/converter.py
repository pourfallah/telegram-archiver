"""WhatsApp-compatible migration package builder.

Converts a Telegram export archive (``messages.json`` + ``media/``) into the
WhatsApp-style package that Telegram's official importer accepts:

    package/
      _chat.txt      DD/MM/YYYY, HH:mm - Sender: message   (+ <Attached: file>)
      media/...      copies of the downloaded media
      manifest.json  counts, users, date range

Sender mapping is strict: each distinct ``sender_id`` maps to exactly one display
name (sender name -> username -> phone placeholder). Distinct senders are never
merged, so a chat imports back with each person's messages attributed correctly.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

PHONE_PLACEHOLDER = "user_%d"


def _sender_display(sender: dict | None) -> str:
    if not sender:
        return "Unknown"
    name = (sender.get("name") or "").strip()
    if name:
        return name
    username = (sender.get("username") or "").strip()
    if username:
        return username
    sid = sender.get("id")
    return PHONE_PLACEHOLDER % sid if sid is not None else "Unknown"


def _sender_names(messages: list[dict]) -> dict[int | None, str]:
    """Map each distinct sender_id -> exactly one display name (never merge)."""
    mapping: dict[int | None, str] = {}
    for m in messages:
        sid = (m.get("sender") or {}).get("id")
        if sid not in mapping:
            mapping[sid] = _sender_display(m.get("sender"))
    return mapping


def _fmt_date(value) -> str:
    """DD/MM/YYYY, HH:mm in local-naive form, matching WhatsApp exports."""
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)
    # WhatsApp's exporter uses the device's local time; we render UTC directly.
    if dt.tzinfo is not None:
        dt = dt.astimezone(None)  # local time
    return dt.strftime("%d/%m/%Y, %H:%M")


def _lines_for_message(m: dict, sender_map: dict, media_root: Path) -> tuple[list[str], list[str]]:
    """Return (chat.txt lines, media files to copy for this message)."""
    sender = sender_map[(m.get("sender") or {}).get("id")]
    when = _fmt_date(m.get("date"))
    lines: list[str] = []
    copies: list[str] = []
    for media in m.get("media") or []:
        fname = media.get("filename") or media.get("original_filename") or "file"
        lines.append(f"{when} - {sender}: <Attached: {fname}>")
        copies.append(_resolve_media(media_root, media, fname))
    text = (m.get("text") or "").replace("\r", "").strip()
    if text:
        lines.append(f"{when} - {sender}: {text}")
    return lines, copies


def _resolve_media(media_root: Path, media: dict, fname: str) -> str | None:
    """Locate a media file inside an export's media/ tree."""
    # media file_path is like "media/<type>/<filename>"
    rel = media.get("file_path") or f"media/{media.get('type') or 'document'}/{fname}"
    candidate = media_root.parent / rel if rel.startswith("media/") else media_root / rel
    if candidate.exists():
        return str(candidate)
    # fallback: search by filename under media_root
    for hit in media_root.rglob(fname):
        return str(hit)
    return None


def _unique_name(dest_dir: Path, fname: str) -> str:
    base = Path(fname).name
    out = base
    n = 1
    while (dest_dir / out).exists():
        stem, suffix = Path(base).stem, Path(base).suffix
        out = f"{stem}_{n}{suffix}"
        n += 1
    return out


def build_whatsapp_package(export_dir: Path, out_dir: Path, limit: int | None = None) -> dict[str, Any]:
    """Build a WhatsApp-style package from an export archive.

    If ``limit`` is given, only the first ``limit`` messages (oldest-first) are
    converted — used to make a small test import package from a real export.

    Returns manifest stats: {messages, media, users, date_min, date_max}.
    """
    msgs_path = export_dir / "messages.json"
    lines_path = export_dir / "messages.jsonl"
    if msgs_path.exists():
        archive = json.loads(msgs_path.read_text(encoding="utf-8"))
        messages: list[dict] = archive.get("messages", [])  # oldest-first
    elif lines_path.exists():
        # Partial / in-progress export: stream the NDJSON workfile instead.
        # It is newest-first, so reverse for the canonical oldest-first order.
        messages = [
            json.loads(ln)
            for ln in lines_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ][::-1]
    else:
        raise FileNotFoundError(f"export has no messages.json/jsonl at {export_dir}")

    if limit:
        messages = messages[:limit]

    sender_map = _sender_names(messages)
    media_root = export_dir / "media"
    media_dir = out_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    text_lines: list[str] = []
    media_copied = 0
    seen_media: set[str] = set()
    for m in messages:
        lines, copies = _lines_for_message(m, sender_map, media_root)
        text_lines.extend(lines)
        for src in copies:
            if src is None or src in seen_media:
                continue
            seen_media.add(src)
            name = _unique_name(media_dir, Path(src).name)
            shutil.copy2(src, media_dir / name)
            media_copied += 1

    (out_dir / "_chat.txt").write_text("\n".join(text_lines) + "\n", encoding="utf-8")

    dates = [m.get("date") for m in messages if m.get("date")]
    manifest = {
        "schema": "whatsapp",
        "generated_at": datetime.now().isoformat(),
        "messages": len(messages),
        "media": media_copied,
        "users": {str(k): v for k, v in sender_map.items()},
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
