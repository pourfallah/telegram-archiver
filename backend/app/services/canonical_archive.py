"""Loss-minimizing canonical archive builder.

The canonical archive is a structured, self-contained snapshot of an export that
preserves every piece of original information the MTProto client could read —
including the original date/time, sender identity, grouped/album structure,
media hashes and sub-metadata — regardless of what Telegram's import protocol
will later be able to restore.

Layout::

    archive/
      manifest.json          schema, counts, dates, source refs
      chat.json              id, title, type, username, participants
      participants.json      id -> {name, username, phone?}
      messages/messages.ndjson   one full message record per line (oldest-first)
      media/<type>/<file>        downloaded media (copied, with original names)
      checksums/sha256.json      relative path -> sha256

It is built from an export directory (``messages.json`` or ``messages.jsonl`` +
``media/``), so it works for completed and partial exports.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load_messages(export_dir: Path) -> list[dict]:
    # Canonical archive layout: <archive>/messages/messages.ndjson (oldest-first)
    ndjson_path = export_dir / "messages" / "messages.ndjson"
    if not ndjson_path.exists():
        # Fallbacks for older layouts
        ndjson_path = export_dir / "messages.ndjson"
    msgs_path = export_dir / "messages.json"
    lines_path = export_dir / "messages.jsonl"
    if msgs_path.exists():
        archive = json.loads(msgs_path.read_text(encoding="utf-8"))
        return list(archive.get("messages", []))
    if lines_path.exists():
        messages = [
            json.loads(ln)
            for ln in lines_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        return messages[::-1]  # jsonl is newest-first; canonical is oldest-first
    if ndjson_path.exists():
        return [
            json.loads(ln)
            for ln in ndjson_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    raise FileNotFoundError(f"export has no messages.json/jsonl/ndjson at {export_dir}")


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return str(value)


def build_canonical_archive(
    export_dir: Path, out_dir: Path, chat_header: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Write a canonical archive from an export directory.

    Returns a stats dict: {messages, media, users, date_min, date_max, checksums}.
    """
    messages = _load_messages(export_dir)
    media_src = export_dir / "media"
    messages_dir = out_dir / "messages"
    media_dst = out_dir / "media"
    messages_dir.mkdir(parents=True, exist_ok=True)
    media_dst.mkdir(parents=True, exist_ok=True)

    # participants (id -> display info), never merge distinct ids
    participants: dict[str, dict] = {}
    for m in messages:
        sender = m.get("sender") or {}
        sid = sender.get("id")
        if sid is None:
            continue
        key = str(sid)
        if key not in participants:
            participants[key] = {
                "id": sid,
                "name": (sender.get("name") or "").strip() or None,
                "username": sender.get("username"),
                "phone": None,
            }

    # write messages.ndjson mirrored exactly
    dates: list[str] = []
    media_written = 0
    sha_map: dict[str, str] = {}
    with (messages_dir / "messages.ndjson").open("w", encoding="utf-8") as out:
        for m in messages:
            out.write(json.dumps(m, ensure_ascii=False) + "\n")
            if m.get("date"):
                dates.append(str(m["date"]))
            # copy referenced media deterministically
            for med in m.get("media") or []:
                fname = med.get("filename") or med.get("original_filename")
                if not fname:
                    continue
                src = _resolve_file(media_src, med, fname)
                if src is None:
                    continue
                tgt_dir = media_dst / (med.get("type") or "document")
                tgt_dir.mkdir(parents=True, exist_ok=True)
                dest = tgt_dir / fname
                if not dest.exists():
                    shutil.copy2(src, dest)
                    media_written += 1
                sha_map[str(dest.relative_to(out_dir))] = _sha256(dest)

    # checksums
    (out_dir / "checksums.json").write_text(
        json.dumps(dict(sorted(sha_map.items())), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # chat / participants / manifest
    chat = chat_header or {}
    chat["participant_count"] = len(participants)
    chat["generated_at"] = datetime.now(UTC).isoformat()
    (out_dir / "chat.json").write_text(
        json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "participants.json").write_text(
        json.dumps(dict(sorted(participants.items())), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    stats = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": str(export_dir),
        "messages": len(messages),
        "files": media_written,
        "users": list(participants.keys()),
        "date_min": (sorted(dates)[0] if dates else None),
        "date_max": (sorted(dates)[-1] if dates else None),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return stats




def _resolve_file(media_src: Path, med: dict, fname: str) -> Path | None:
    rel = med.get("file_path")
    cand = None
    if rel:
        cand = media_src.parent / rel if rel.startswith("media/") else media_src / rel
        if cand.exists():
            return cand
    for hit in media_src.rglob(fname):
        return hit
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
