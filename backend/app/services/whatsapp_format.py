"""Media marker format experiment helpers.

Problem observed (job #25): our import file used
    20.08.2026 06:49 - First Dev.: <attached: photo_0.jpg>
Telegram imported the line as LITERAL TEXT and attached no media, even though
the media files were uploaded via uploadImportedMedia with matching filenames.

A genuine WhatsApp import in the same chat shows media correctly as
[ ❤️ Sticker ] / [ Photo ] — so the parser DOES support media markers, but only
in the exact WhatsApp export line format:
    8/20/2026, 10:19 AM - Name: <attached: file.ext>
(note: M/D/YYYY, comma after date, 12-hour clock with AM/PM)
"""
from __future__ import annotations

from datetime import datetime


def format_whatsapp_ts(dt: datetime) -> str:
    """M/D/YYYY, H:MM AM/PM — the format Telegram's importer demonstrably parses."""
    twelve = dt.strftime("%I:%M %p")
    return f"{dt.month}/{dt.day}/{dt.year}, {twelve}"
