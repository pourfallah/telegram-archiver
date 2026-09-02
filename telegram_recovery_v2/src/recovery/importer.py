"""Import package builder + import engine for Telegram Recovery v2.

Two responsibilities, both internal to this module:

1. ``build_import_package`` — generate a package DIRECTLY from the canonical
   archive. Telegram's official history import (``messages.checkHistoryImport``
   etc.) only accepts a chat-export text file, so we produce that ``_chat.txt``
   plus the media files it references. No WhatsApp converter is involved — the
   package is derived straight from the lossless archive.

2. ``ImportEngine`` — the ONE implementation that performs the actual MTProto
   import for CLI, tests and the future web app:
       messages.checkHistoryImport -> checkHistoryImportPeer
       -> initHistoryImport -> uploadImportedMedia (x N) -> startHistoryImport

Import IDs are persisted so a crash never re-inits (and thus never re-imports)
the same package twice (#54).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from telethon.tl import functions as tg_functions

from .archive import Archive
from .media import safe_filename

# Canonical human/display timezone for the recovery test (users are in Tehran).
# Historical DST rules for Asia/Tehran are applied by the IANA tz database.
TEHRAN = ZoneInfo("Asia/Tehran")
# Telegram's import-file timestamp format is minute precision (DD/MM/YYYY, HH:MM).
TEHRAN_FILE_FMT = "%d/%m/%Y, %H:%M"


def _to_utc(d: str) -> datetime:
    dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def tehran_local_of(d: str) -> datetime:
    """Return the Asia/Tehran historical local wall-clock of an ISO instant."""
    return _to_utc(d).astimezone(TEHRAN)


def tehran_local_str(d: str) -> str:
    return tehran_local_of(d).strftime(TEHRAN_FILE_FMT)


def tehran_timestamp_checks() -> list[dict[str, Any]]:
    """Deterministic round-trip checks for the import-file timestamp encoder.

    For each source UTC instant: build the Asia/Tehran historical local
    wall-clock, encode the `_chat.txt` timestamp, then parse that timestamp
    back as Asia/Tehran local and confirm the recovered UTC minute equals the
    source UTC minute (no timezone shift; only Telegram's unavoidable minute
    precision is lost).
    """
    cases = [
        "2015-12-31T20:35:57+00:00",  # -> 2016-01-01 00:05 +03:30 (source 5307)
        "2016-08-01T12:00:00+00:00",  # Tehran DST in effect (+04:30)
        "2016-11-01T12:00:00+00:00",  # after DST ended (+03:30)
    ]
    out: list[dict[str, Any]] = []
    for src in cases:
        utc = _to_utc(src)
        local = utc.astimezone(TEHRAN)
        file_ts = _date_str(src)
        naive = datetime.strptime(file_ts, TEHRAN_FILE_FMT)
        intended = naive.replace(tzinfo=TEHRAN).astimezone(timezone.utc)
        minute_exact = (
            intended.strftime("%Y-%m-%d %H:%M") == utc.strftime("%Y-%m-%d %H:%M")
        )
        out.append(
            {
                "source_utc": utc.isoformat(),
                "tehran_local": local.isoformat(),
                "offset_hours": (local.utcoffset() or timedelta(0)).total_seconds() / 3600,
                "file_timestamp": file_ts,
                "intended_utc": intended.isoformat(),
                "minute_exact": minute_exact,
            }
        )
    return out


def verify_timestamp_encoding() -> bool:
    """Pre-execution gate: every round-trip check must be minute-exact."""
    return all(c["minute_exact"] for c in tehran_timestamp_checks())

# ---------------------------------------------------------------------------
# 1. Import package (generated directly from the canonical archive)
# ---------------------------------------------------------------------------
def _date_str(d: str | None) -> str:
    """Encode an ISO instant as the WhatsApp-format Telegram import timestamp.

    Telegram's import-file parser interprets the naive `DD/MM/YYYY, HH:MM`
    timestamp as the local wall-clock in Asia/Tehran and stores the matching
    UTC instant.  So we write the *historical Asia/Tehran local* wall-clock of
    the source instant (applying the IANA DST rules that applied on that date).
    Format is minute precision; seconds cannot be represented exactly.
    """
    if not d:
        return ""
    try:
        return tehran_local_str(d)
    except ValueError:
        return d


def _sender_display(rec: dict) -> str:
    from_id = rec.get("from_id") or {}
    name = from_id.get("first_name") or from_id.get("last_name") or from_id.get("title")
    if name:
        return name
    username = from_id.get("username")
    if username:
        return str(username)
    uid = from_id.get("user_id") or from_id.get("channel_id")
    return f"user_{uid}" if uid is not None else "Unknown"


def build_import_package(archive: Archive, out_dir: Path,
                         messages: list[dict] | None = None) -> dict[str, Any]:
    """Build ``_chat.txt`` + ``media/`` + ``manifest.json`` from the archive."""
    out = Path(out_dir)
    media_out = out / "media"
    media_out.mkdir(parents=True, exist_ok=True)

    records = messages if messages is not None else list(archive.read_messages())
    lines: list[str] = []
    manifest_rows: list[dict[str, Any]] = []
    copied_sha: dict[str, str] = {}  # sha -> local name (dedupe)

    for rec in records:
        when = _date_str(rec.get("date"))
        sender = _sender_display(rec)
        row: dict[str, Any] = {
            "source_message_id": rec["source_message_id"],
            "date": rec.get("date"),
            "sender": sender,
            "media": [],
        }
        media_tokens: list[str] = []
        for media in rec.get("media") or []:
            if not media.get("path"):
                continue
            src = (archive.root / media["path"])
            if not src.exists():
                row["media"].append({"sha256": media.get("sha256"), "file": None,
                                     "missing": True})
                continue
            sha = media.get("sha256")
            if sha and sha in copied_sha:
                fname = copied_sha[sha]
            else:
                fname = _unique(media_out, Path(media.get("filename") or "media"))
                shutil.copy2(src, media_out / fname)
                if sha:
                    copied_sha[sha] = fname
            media_tokens.append(f"<Attached: {fname}>")
            row["media"].append({"sha256": sha, "file": fname})
        text = (rec.get("text") or "").strip()
        # WhatsApp import format: a caption stays on the SAME line as the
        # <Attached:> media token so Telegram imports it as ONE message whose
        # ``message`` is the caption. A separate text line would detach it.
        if media_tokens:
            line = f"{when} - {sender}: {' '.join(media_tokens)}"
            if text:
                line += " " + text
            lines.append(line)
        elif text:
            lines.append(f"{when} - {sender}: {text}")
        manifest_rows.append(row)

    (out / "_chat.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "schema": "recovery-v2-import",
        "generated_at": datetime.now().isoformat(),
        "source_messages": len(records),
        "chat_lines": len(lines),
        "media_files": len(copied_sha),
        "rows": manifest_rows,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"messages": len(records), "media": len(copied_sha),
            "chat_lines": len(lines)}


def _unique(dest: Path, fname: str) -> str:
    base = Path(fname).name
    out = base
    n = 1
    while (dest / out).exists():
        stem, suffix = Path(base).stem, Path(base).suffix
        out = f"{stem}_{n}{suffix}"
        n += 1
    return out


# ---------------------------------------------------------------------------
# 2. ImportEngine — the single production import implementation
# ---------------------------------------------------------------------------
@dataclass
class MediaImportTrace:
    source_message_id: int
    source_media_index: int
    file_name: str
    upload_rpc_ok: bool
    note: str = ""

    def to_dict(self) -> dict:
        return {"source_message_id": self.source_message_id,
                "source_media_index": self.source_media_index,
                "file_name": self.file_name,
                "upload_rpc_ok": self.upload_rpc_ok, "note": self.note}


@dataclass
class ImportOutcome:
    import_id: int | None = None
    checked: str | None = None
    peer_ok: bool | None = None
    media_traces: list[MediaImportTrace] = field(default_factory=list)
    rpc_order: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"import_id": self.import_id, "checked": self.checked,
                "peer_ok": self.peer_ok,
                "media_traces": [m.to_dict() for m in self.media_traces],
                "rpc_order": self.rpc_order}


class ImportEngine:
    """Performs the actual history import. One class: CLI == tests == web."""

    def __init__(self, source_client, target_client, peer, run_dir: Path,
                 media_provider=None) -> None:
        # source_client rarely used here but kept for symmetry / verification.
        self.source = source_client
        self.target = target_client
        self.peer = peer
        self.run_dir = Path(run_dir)
        self.media_provider = media_provider or self.reconstruct_input_media
        self.whatsapp = False  # placeholder for users who still want that path

    # ---- the five official calls ---------------------------------------
    async def check_history_import(self, chat_txt: bytes) -> Any:
        """messages.checkHistoryImport — determines if the file is importable."""
        return await self.target.call(tg_functions.messages.CheckHistoryImportRequest(
            import_head=chat_txt))

    async def check_history_import_peer(self) -> Any:
        """messages.checkHistoryImportPeer — is this peer allowed to receive?"""
        return await self.target.call(tg_functions.messages.CheckHistoryImportPeerRequest(
            peer=self.peer))

    async def init_history_import(self, chat_txt_bytes: bytes) -> int:
        """messages.initHistoryImport — start the import, return import_id."""
        uploaded = await self.target.client.upload_file(
            chat_txt_bytes, file_name="_chat.txt")
        # media_count = number of <Attached:> files referenced by the package
        media_count = len(self._package_media_files)
        res = await self.target.call(tg_functions.messages.InitHistoryImportRequest(
            peer=self.peer, file=uploaded, media_count=media_count))
        return res.id

    async def upload_imported_media(self, import_id: int, file_name: str,
                                    media: Any) -> Any:
        """messages.uploadImportedMedia — upload one referenced media file."""
        return await self.target.call(tg_functions.messages.UploadImportedMediaRequest(
            peer=self.peer, import_id=import_id, file_name=file_name, media=media))

    async def start_history_import(self, import_id: int) -> Any:
        """messages.startHistoryImport — commit the import."""
        return await self.target.call(tg_functions.messages.StartHistoryImportRequest(
            peer=self.peer, import_id=import_id))

    # ---- orchestration --------------------------------------------------
    async def run_import(self, package_dir: Path,
                         import_id_state: dict | None = None) -> ImportOutcome:
        """Full import; respects an existing import_id so a crash retry never
        re-inits (#54)."""
        import_id_state = import_id_state or {}
        outcome = ImportOutcome()
        pkg = Path(package_dir)
        chat_txt = (pkg / "_chat.txt").read_bytes()
        self._set_package_media(pkg)

        # 1 + 2 checks
        outcome.checked = str(await self.check_history_import(chat_txt))
        outcome.rpc_order.append("checkHistoryImport")
        peer_res = await self.check_history_import_peer()
        outcome.peer_ok = _truthy(peer_res)
        outcome.rpc_order.append("checkHistoryImportPeer")

        # 3 init (resume-aware)
        import_id = import_id_state.get("import_id")
        if import_id is None:
            import_id = await self.init_history_import(chat_txt)
            import_id_state["import_id"] = import_id
        outcome.import_id = import_id
        outcome.rpc_order.append("initHistoryImport")

        # 4 upload every referenced media file (await the media provider)
        for fname in self._package_media_files:
            f = pkg / "media" / fname
            if not f.exists():
                outcome.media_traces.append(
                    MediaImportTrace(0, 0, fname, False, note="file missing on disk"))
                continue
            media_input = await self.media_provider(fname, f.read_bytes())
            res = await self.upload_imported_media(import_id, fname, media_input)
            ok = _truthy(res)
            outcome.media_traces.append(
                MediaImportTrace(0, 0, fname, ok, note=str(res)[:200]))
            outcome.rpc_order.append("uploadImportedMedia")

        # 5 commit
        outcome.rpc_order.append("startHistoryImport")
        await self.start_history_import(import_id)
        return outcome

    # ---- media input reconstruction ------------------------------------
    _package_media_files: list[str]

    def _set_package_media(self, pkg: Path) -> None:
        self._package_media_files = []
        media_dir = pkg / "media"
        if media_dir.is_dir():
            self._package_media_files = sorted(
                f.name for f in media_dir.iterdir() if f.is_file())

    async def reconstruct_input_media(self, file_name: str, data: bytes):
        """Default media provider: upload bytes and wrap as an uploaded-media
        InputMedia. Kept async-compatible because uploads may need a client."""
        up = await self.target.client.upload_file(data, file_name=file_name)
        ext = Path(file_name).suffix.lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            from telethon.tl.types import InputMediaUploadedPhoto
            return InputMediaUploadedPhoto(file=up)
        from telethon.tl.types import InputMediaUploadedDocument
        return InputMediaUploadedDocument(file=up, mime_type=_mime(ext),
                                          attributes=[])


def _mime(ext: str) -> str:
    return _MIME.get(ext, "application/octet-stream")


_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4",
         ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".pdf": "application/pdf"}


def _truthy(obj: Any) -> bool:
    """Best-effort truthiness for unknown RPC result objects."""
    if obj is None:
        return False
    if isinstance(obj, bool):
        return obj
    if hasattr(obj, "__dict__"):
        return True
    return bool(obj)