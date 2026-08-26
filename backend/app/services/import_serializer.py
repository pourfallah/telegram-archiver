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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


# Telegram import date format per docs: DD.MM.YYYY HH:MM (dot separator)
# WhatsApp export uses DD/MM/YYYY, HH:MM (slash). Telegram accepts both.
# We'll use the dot format as it's the documented Telegram preference.
#
# TIMEZONE SEMANTICS (verified by controlled experiment 2026-08-23):
# The importer parses naive "DD.MM.YYYY HH:MM" strings in the TARGET ACCOUNT's
# local timezone (observed UTC+3:30 for the test account) and stores
# date = parsed_wall_clock - tz_offset. Therefore, to make the visible timeline
# show the TRUE historical instant, we must convert source UTC timestamps to the
# target timezone BEFORE writing them. build_import_file takes `tz_offset_minutes`
# (offset of the target account from UTC, e.g. 210 for UTC+3:30). When unknown,
# pass None and times are written as-is (UTC wall clock).
def _format_ts(
    dt: str | datetime | None, tz_offset_minutes: int | None = None
) -> str:
    """Format a timestamp in the canonical WhatsApp export style.

    VERIFIED BEHAVIOR (2026-08-24, live Telegram):
    - "[DD/MM/YYYY, HH:MM:SS]" (brackets, day-first, 24h, SECONDS) is the format
      the importer's parser recognizes; with it, <attached: FILENAME> lines are
      bound to real media objects via uploadImportedMedia.
    - The earlier "[M/D/YYYY, H:MM AM/PM]" and "DD.MM.YYYY HH:MM" variants made
      the parser treat <attached: ...> as literal text (media=None).
    => We emit the bracket, DD/MM/YYYY, HH:MM:SS, seconds format exclusively.

    TIMEZONE: the importer parses naive timestamps in the TARGET account's local
    timezone, so shift source UTC by tz_offset_minutes first (e.g. 210 for Iran).
    """
    if dt is None:
        return "[01/01/1970, 00:00:00]"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(dt, "%d/%m/%Y, %H:%M")
            except ValueError:
                return "[01/01/1970, 00:00:00]"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if tz_offset_minutes is not None:
        # shift true instant into the target account's local wall clock
        dt = dt + timedelta(minutes=tz_offset_minutes)
        dt = dt.replace(tzinfo=None)
    return dt.strftime("[%d/%m/%Y, %H:%M:%S]")


def _escape(text: str) -> str:
    # minimal escaping for the import format — replace newlines with spaces
    return text.replace("\n", " ").replace("\r", " ")


def _media_marker(filename: str, media_type: str) -> str:
    """Media reference token for the WhatsApp-style import format.

    VERIFIED SYNTAX (independent implementation filippz/telegram_import +
    tdlib MessageImportManager.cpp):
      "{ts} - {sender}: {filename} (file attached)"          -> MEDIA ONLY
      "{ts} - {sender}: {filename} (file attached)\\n{caption}" -> MEDIA + CAPTION
                                                               as ONE message
    The caption belongs INSIDE the same message block: the first physical line
    carries the timestamp/sender/media marker, continuation lines (no new
    timestamp prefix) are the caption of THAT media message.
    """
    return f"{filename} (file attached)"


def build_import_file(
    export_dir: Path,
    out_file: Path,
    limit: int | None = None,
    sender_map: dict[int, str] | None = None,
    tz_offset_minutes: int | None = None,
) -> dict[str, Any]:
    """
    Build the import text file from a canonical archive or export.

    tz_offset_minutes: UTC offset of the TARGET account (e.g. 210 = UTC+3:30).
    The Telegram importer parses naive timestamps in the target's local zone, so
    shifting source instants by this offset makes visible dates match history.

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

    # D-1 fix: guarantee strict ascending order (timestamp, then source id)
    def _sort_key(m: dict):
        dt = m.get("date") or ""
        if isinstance(dt, str):
            dt = dt.replace("Z", "+00:00")
        return (str(dt), int(m.get("id") or 0))

    messages = sorted(messages, key=_sort_key)

    if limit:
        # Take the OLDEST N messages (the head of history), not the newest.
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
            ts = _format_ts(m.get("date"), tz_offset_minutes)
            sender = m.get("sender") or {}
            sid = sender.get("id")
            name = sender_map.get(sid) if sid is not None else sender.get("name") or "Unknown"
            text = m.get("text") or ""

            if sid is not None:
                users.add(str(sid))
            dates.append(ts)

            # ONE source message = ONE import message block.
            # Media+caption: "{ts} - {name}: {file} (file attached)\n{caption}"
            # (caption is a continuation line INSIDE the same block — verified
            # against filippz/telegram_import and tdlib's parser). Text-only:
            # "{ts} - {name}: {text}". Never split one source message into two
            # timestamped lines.
            media_items = m.get("media") or []
            media_fname = None
            for med in media_items:
                fname = med.get("filename") or med.get("original_filename")
                if fname:
                    media_fname = fname
                    media_refs += 1
                    break

            if media_fname:
                line = f"{ts} - {name}: {_media_marker(media_fname, 'document')}"
                if text:
                    line += f"\n{_escape(text)}"
                f.write(line + "\n")
            elif text:
                f.write(f"{ts} - {name}: {_escape(text)}\n")
            else:
                # completely empty message — skip (nothing to import)
                continue

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
