"""Import assistant: validate a generated package and prepare import instructions.

Validates the structure of a WhatsApp-style package (``_chat.txt``, ``media/``,
``manifest.json``) and returns a truthful, step-by-step import guide that reflects
what Telegram's official importer can and cannot preserve.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

LINE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4}, \d{2}:\d{2}) - (.+?): (.*)$")


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_package(package_dir: Path) -> dict[str, Any]:
    """Validate a package and produce a report + stats."""
    chat_txt = package_dir / "_chat.txt"
    manifest_path = package_dir / "manifest.json"
    media_dir = package_dir / "media"

    issues: list[str] = []
    if not chat_txt.exists():
        issues.append("missing _chat.txt")
    if not manifest_path.exists():
        issues.append("missing manifest.json")
    if not media_dir.is_dir():
        issues.append("missing media/ directory (the official importer needs at least one media file)")

    messages = 0
    parsed = 0
    media_files = 0
    users: set[str] = set()
    date_min = date_max = None

    # Authoritative counts come from manifest.json (the converter wrote them).
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            messages = int(manifest.get("messages", 0))
            media_files = int(manifest.get("media", 0))
            users = {str(v) for v in (manifest.get("users") or {}).values()}
            date_min = _parse_ts(manifest.get("date_min"))
            date_max = _parse_ts(manifest.get("date_max"))
        except (ValueError, TypeError):
            issues.append("manifest.json is unreadable")

    if chat_txt.exists():
        for line in chat_txt.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            m = LINE_RE.match(line)
            if m:
                parsed += 1
                users.add(m.group(2))
    if media_dir.exists() and not manifest_path.exists():
        media_files = sum(1 for p in media_dir.rglob("*") if p.is_file())
    if media_files == 0:
        issues.append("no media files found in media/ — the official importer requires at least one")

    status = "valid" if not issues else ("warnings" if messages > 0 else "invalid")
    return {
        "validation_status": status,
        "issues": issues,
        "stats": {
            "messages": messages,
            "lines_parsed": parsed,
            "media": media_files,
            "users": sorted(users),
            "date_min": date_min.isoformat() if date_min else None,
            "date_max": date_max.isoformat() if date_max else None,
        },
    }


def generate_instructions(package_dir: Path) -> list[dict[str, str]]:
    """Return truthful, step-by-step import instructions (official + manual)."""
    report = validate_package(package_dir)
    stats = report["stats"]
    media_note = "at least one media file is present" if stats["media"] > 0 else "NO media files detected"
    return [
        {
            "step": "1",
            "title": "Use a fresh target account",
            "detail": (
                "Telegram's official importer restores history into an empty chat of a "
                "fresh account. Create a new account (or a brand-new empty chat) to receive "
                "this history."
            ),
        },
        {
            "step": "2",
            "title": "Open Telegram Desktop",
            "detail": "Install Telegram Desktop on your computer and log into the target account.",
        },
        {
            "step": "3",
            "title": "Start the import",
            "detail": (
                "Settings → Advanced → 'Import from WhatsApp' → choose this package folder. "
                f"Package has {stats['messages']} messages and {media_note}."
            ),
        },
        {
            "step": "4",
            "title": "What the importer preserves",
            "detail": (
                "Sender names and timestamps and text/media are restored. Reactions, edits, "
                "reply chains, forward provenance and message/order ids are NOT preserved. "
                f"Messages without attached media may be skipped. Users: {', '.join(stats['users']) or 'n/a'}."
            ),
        },
        {
            "step": "5",
            "title": "Manual fallback",
            "detail": (
                "If the official importer rejects the package or skips too many text-only "
                "messages, re-share media through the app manually, or use the exported "
                "messages.json directly for archival rather than migration."
            ),
        },
    ]


def write_instructions(package_dir: Path) -> Path:
    """Persist instructions as INSTRUCTIONS.md next to the package."""
    text_lines = ["# Import Instructions", ""]
    for it in generate_instructions(package_dir):
        text_lines.append(f"## {it['step']}. {it['title']}")
        text_lines.append(it["detail"])
        text_lines.append("")
    out = package_dir / "INSTRUCTIONS.md"
    out.write_text("\n".join(text_lines), encoding="utf-8")
    return out


def preview_package(package_dir: Path, limit: int = 100) -> dict[str, Any]:
    """Preview the first ``limit`` messages of a package's ``_chat.txt``.

    Returns {total_lines, messages: [{when, sender, text, is_media}]}.
    """
    chat_txt = package_dir / "_chat.txt"
    messages: list[dict[str, Any]] = []
    total = 0
    if chat_txt.exists():
        for line in chat_txt.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total += 1
            m = LINE_RE.match(line)
            if m:
                when, sender, text = m.groups()
                is_media = text.startswith("<Attached:")
                messages.append(
                    {"when": when, "sender": sender, "text": text, "is_media": is_media}
                )
            elif len(messages) < limit:
                messages.append({"when": "", "sender": "", "text": line, "is_media": False})
            if len(messages) >= limit:
                break
    return {"total_lines": total, "messages": messages}
