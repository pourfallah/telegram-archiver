"""Telegram import file serializer.

Generates the line-based import file that Telegram's checkHistoryImport /
initHistoryImport expects. Per https://core.telegram.org/api/import, the format
is a text file with one message per line, date format DD.MM.YYYY HH:MM (or
DD/MM/YYYY, HH:MM), sender, message text, and special markers for media.

Reference format (WhatsApp-like):
    25.12.2023 15:30 - John Doe: Hello!
    25.12.2023 15:31 - Jane Smith: <attached: photo.jpg>

Media references are matched by filename during initHistoryImport, then
uploadImportedMedia returns MessageMedia tokens that must be spliced into the
file. The exact token format is Telegram-internal; we produce the initial
file with media placeholders and the worker handles token splicing.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


# Telegram import date format per docs: DD.MM.YYYY HH:MM (dot separator)
# WhatsApp export uses DD/MM/YYYY, HH:MM (slash). Telegram accepts both.
# We'll use the dot format as it's the documented Telegram preference.
def _format_ts(dt: str | datetime | None) -> str:
    if dt is None:
        return "01.01.1970 00:00"
    if isinstance(dt, str):
        # try ISO first, then WhatsApp style
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(dt, "%d/%m/%Y, %H:%M")
            except ValueError:
                return "01.01.1970 00:00"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%d.%m.%Y %H:%M")


def _escape(text: str) -> str:
    # minimal escaping for the import format — replace newlines with spaces
    return text.replace("\n", " ").replace("\r", " ")


def _media_marker(filename: str, media_type: str) -> str:
    """Generate a media reference line."""
    # Telegram import format uses <attached: filename> or <media: filename>
    return f"<attached: {filename}>"


def build_import_file(
    export_dir: Path,
    out_file: Path,
    limit: int | None = None,
    sender_map: dict[int, str] | None = None,
) -> dict[str, Any]:
    """
    Build the import text file from a canonical archive or export.

    Returns stats: {messages, media_refs, users, date_min, date_max}
    """
    # Try canonical archive first
    archive_dir = export_dir / "archive"
    msgs_file = archive_dir / "messages" / "messages.ndjson"
    if not msgs_file.exists():
        msgs_file = export_dir / "messages.jsonl"
    if not msgs_file.exists():
        msgs_file = export_dir / "messages.json"

    if msgs_file.suffix == ".json":
        data = json.loads(msgs_file.read_text(encoding="utf-8"))
        messages = data.get("messages", [])
    else:
        messages = [
            json.loads(ln)
            for ln in msgs_file.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        messages = messages[::-1]  # ndjson is newest-first; import wants oldest-first

    if limit:
        messages = messages[:limit]

    # Build sender map if not provided
    if sender_map is None:
        sender_map = {}
        for m in messages:
            sender = m.get("sender") or {}
            sid = sender.get("id")
            if sid is not None and sid not in sender_map:
                sender_map[sid] = sender.get("name") or sender.get("username") or f"User{sid}"

    out_file.parent.mkdir(parents=True, exist_ok=True)

    media_refs = 0
    users = set()
    dates = []

    with out_file.open("w", encoding="utf-8") as f:
        for m in messages:
            ts = _format_ts(m.get("date"))
            sender = m.get("sender") or {}
            sid = sender.get("id")
            name = sender_map.get(sid) if sid is not None else sender.get("name") or "Unknown"
            text = m.get("text") or ""

            if sid is not None:
                users.add(str(sid))
            dates.append(ts)

            # Media lines come AFTER the message they belong to
            media_items = m.get("media") or []
            has_media = False
            for med in media_items:
                fname = med.get("filename") or med.get("original_filename")
                if not fname:
                    continue
                mtype = med.get("type") or "document"
                f.write(f"{ts} - {name}: {_media_marker(fname, mtype)}\n")
                media_refs += 1
                has_media = True

            # Message text (can be empty if only media)
            if text or not has_media:
                f.write(f"{ts} - {name}: {_escape(text)}\n")
            elif not has_media and not text:
                # skip completely empty lines
                pass

    stats = {
        "messages": len(messages),
        "media_refs": media_refs,
        "users": list(users),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
    }
    return stats


def parse_import_head(file_path: Path, max_lines: int = 100) -> str:
    """Read the first max_lines of the import file for checkHistoryImport."""
    lines = file_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:max_lines])
