"""Import engine: official Telegram history-import API, driven by the
canonical archive. This is the ONLY importer in recovery v2 — tests, CLI and
(later) the web app all call this class.

Protocol (https://core.telegram.org/api/import):

    checkHistoryImport(head)             # from B: validate the file format
    checkHistoryImportPeer(peer)         # from B: confirm the target peer
    initHistoryImport(peer, file, media_count) -> import_id
    uploadImportedMedia(peer, import_id, file_name, media) -> MessageMedia
    startHistoryImport(peer, import_id)  # server processes; materializes later

Known protocol reality (live-proven, see docs/LIMITATIONS.md):
- media binds ONLY on bare `<attached: FILENAME>` lines, matched by exact
  filename; one token per line — repeated files need unique attach names;
- a caption line following the attach marker breaks media binding, so the
  caption is emitted as a separate message (+1s) — CAPTION_SEPARATE;
- uploadImportedMedia returns MessageMediaEmpty in every case; that is NOT
  diagnostic. Only the materialized target Message is truth.

Resumability: import_id is persisted to import_state.json before
startHistoryImport. If a run crashes after init but before start, re-running
the import resumes from the persisted import_id instead of re-initializing.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from telethon import functions, types
from telethon.errors import (
    ChatAdminRequiredError,
    ImportFileInvalidError,
)

from .archive import ArchiveReader
from .telegram_client import ClientPool

WHATSAPP_TZ_OFFSET = "+03"  # placeholder, timestamps are absolute in the file


@dataclass
class MediaImportSpec:
    """One `<attached: ...>` line awaiting its uploaded token."""

    attach_name: str          # filename written into the import file
    local_path: Path          # bytes on disk (from the archive)
    source_message_id: int
    media_id: str
    media_type: str           # photo/video/gif/audio/voice/document/sticker
    is_photo: bool
    mime: str
    uploaded: types.MessageMedia | None = None
    trace: dict = field(default_factory=dict)


@dataclass
class ImportState:
    import_id: int | None = None
    media_count: int = 0
    uploaded_files: dict = field(default_factory=dict)  # attach_name -> true
    started: bool = False
    started_at: str | None = None

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "import_id": self.import_id,
                    "media_count": self.media_count,
                    "uploaded_files": sorted(self.uploaded_files),
                    "started": self.started,
                    "started_at": self.started_at,
                },
                indent=2,
            )
        )

    @classmethod
    def load(cls, path: Path) -> "ImportState":
        d = json.loads(path.read_text())
        st = cls(**d)
        if isinstance(st.uploaded_files, list):  # save() writes sorted list
            st.uploaded_files = {name: True for name in st.uploaded_files}
        return st


def _wa_ts(ts: str, tz_offset_minutes: int = 210) -> str:
    """Archive ISO ts (YYYY-MM-DDTHH:MM:SS, UTC) -> accepted WhatsApp syntax
    DD/MM/YYYY, HH:MM:SS.

    EXPERIMENTALLY PROVEN (run recovery_v2_20260829_091021_548599): Telegram
    parses the file's naive timestamps in the TARGET ACCOUNT's local timezone
    (observed UTC+3:30 for these accounts) — writing UTC wall-clock shifted
    every visible date by -3:30h. We therefore pre-shift UTC instants by the
    account tz offset before rendering. Default 210 (Iran); the observed
    regression: file 07:52:20 UTC -> target visible 04:22:20 UTC.
    """
    from datetime import datetime, timedelta

    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S") + timedelta(minutes=tz_offset_minutes)
    return dt.strftime("%d/%m/%Y, %H:%M:%S")


def build_import_file(archive: ArchiveReader, out_path: Path) -> dict:
    """Render the canonical archive into the foreign-app text file that
    messages.checkHistoryImport accepts (WhatsApp syntax, live-verified).

    Media attaches ONLY on a bare `<attached: NAME>` line; captions become a
    separate message one second later. Attach names are unique per media
    record (`m{msg_id}{ext}`) so duplicate base filenames never collide.
    """
    lines: list[str] = []
    media_specs: dict[str, MediaImportSpec] = {}
    msgs = list(archive.messages())
    msgs.sort(key=lambda m: m["date"])

    for m in msgs:
        sender = "Alice" if m.get("sender_label") == "A" else "Bob"
        ts = m["date"][:19]
        text = (m.get("text") or "").replace("\n", " ⏎ ")
        media = m.get("media")
        if media and media.get("type") in ("photo", "video", "gif", "audio", "voice", "document", "sticker"):
            suffix = _ext_for(media)
            attach = f"m{m['message_id']}{suffix}"
            local = archive.dir / media["local_file"] if media.get("local_file") else None
            if local is None or not Path(local).exists():
                # No bytes archived: keep the caption text only, mark lost media.
                lines.append(f"[{_wa_ts(ts)}] {sender}: [media not archived: {media['type']}]")
                continue
            media_specs[attach] = MediaImportSpec(
                attach_name=attach,
                local_path=Path(local),
                source_message_id=m["message_id"],
                media_id=media["media_id"],
                media_type=media["type"],
                is_photo=media["type"] == "photo",
                mime=media.get("mime") or "application/octet-stream",
                trace={"source_message_id": m["message_id"], "media_id": media["media_id"]},
            )
            lines.append(f"[{_wa_ts(ts)}] {sender}: <attached: {attach}>")
            # Caption: WhatsApp syntax cannot attach a caption to the SAME
            # message (proven: it breaks media binding). Emit as separate +1s.
            caption = m.get("text")
            if caption:
                cap_ts = _plus_1s(ts)
                lines.append(f"[{_wa_ts(cap_ts)}] {sender}: {caption}")
        else:
            if text:
                lines.append(f"[{_wa_ts(ts)}] {sender}: {text}")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"lines": len(lines), "media_count": len(media_specs)}


def _ext_for(media: dict) -> str:
    """Suffix for the attach name — MUST match engine.build_package's attach
    map (single source of truth = the archived bytes' local_file suffix).

    Live-proven divergence (v2 run 121240): using `filename` here (voice.ogg)
    while the attach map used local_file's suffix (.oga) uploaded the token
    under mXXXX.oga while the import line said <attached: mXXXX.ogg> — the
    line never bound and imported as literal text.
    """
    lf = media.get("local_file") or ""
    if "." in lf:
        return "." + lf.rsplit(".", 1)[1].lower()
    fn = media.get("filename") or ""
    if "." in fn:
        return "." + fn.rsplit(".", 1)[1].lower()
    return {
        "photo": ".jpg",
        "video": ".mp4",
        "gif": ".mp4",
        "audio": ".mp3",
        "voice": ".oga",
        "sticker": ".webp",
    }.get(media.get("type"), ".bin")


def _plus_1s(ts: str) -> str:
    from datetime import datetime, timedelta

    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
    return (dt + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")


class ImportEngine:
    """The canonical importer. Uses ONLY the official import RPCs."""

    def __init__(self, pool: ClientPool, peer, run_dir: Path) -> None:
        self.pool = pool
        self.peer = peer  # input peer as seen by B
        self.run_dir = run_dir
        self.state_path = run_dir / "import_state.json"
        self.state = (
            ImportState.load(self.state_path)
            if self.state_path.exists()
            else ImportState()
        )
        self.media_trace: list[dict] = []

    # ------------------------------------------------------------ steps

    async def check_format(self, import_file: Path) -> dict:
        client = self.pool.client("B")
        head = import_file.read_text(encoding="utf-8").splitlines()[:100]
        res = await client(
            functions.messages.CheckHistoryImportRequest(import_head="\n".join(head))
        )
        return {
            "pm": raw_to_json_safe(getattr(res, "pm", None)),
            "group": raw_to_json_safe(getattr(res, "group", None)),
            "new_messages": getattr(res, "new_messages", None),
        }

    async def check_peer(self) -> dict:
        client = self.pool.client("B")
        res = await client(
            functions.messages.CheckHistoryImportPeerRequest(peer=self.peer)
        )
        return {
            "confirm_text": getattr(res, "confirm_text", None),
        }

    async def init_import(self, import_file: Path, media_count: int) -> int:
        if self.state.import_id is not None:
            return self.state.import_id  # resume; never double-init
        client = self.pool.client("B")
        file = await client.upload_file(str(import_file), file_name="history.txt")
        res = await client(
            functions.messages.InitHistoryImportRequest(
                peer=self.peer, file=file, media_count=media_count
            )
        )
        self.state.import_id = res.id
        self.state.media_count = media_count
        self.state.save(self.state_path)
        return res.id

    async def upload_media(self, specs: list[MediaImportSpec]) -> list[dict]:
        from .media import build_input_media

        client = self.pool.client("B")
        for spec in specs:
            if spec.attach_name in self.state.uploaded_files:
                continue  # already uploaded in a previous attempt
            handle = await client.upload_file(str(spec.local_path))
            media = build_input_media(
                Path(spec.local_path),
                spec.mime,
                attach_name=spec.attach_name,
                media_type=spec.media_type,
                sticker=spec.media_type == "sticker",
                animated=spec.media_type == "gif",
                file_handle=handle,
            )
            res = await client(
                functions.messages.UploadImportedMediaRequest(
                    peer=self.peer,
                    import_id=self.state.import_id,
                    file_name=spec.attach_name,
                    media=media,
                )
            )
            spec.uploaded = res
            spec.trace["upload_result"] = type(res).__name__  # not diagnostic!
            spec.trace["uploaded_at"] = time.time()
            self.state.uploaded_files[spec.attach_name] = True
            self.state.save(self.state_path)
            self.media_trace.append(spec.trace)
        (self.run_dir / "media_import_trace.json").write_text(
            json.dumps(self.media_trace, indent=2, default=str)
        )
        return self.media_trace

    async def start(self) -> None:
        if self.state.started:
            return
        client = self.pool.client("B")
        await client(
            functions.messages.StartHistoryImportRequest(
                peer=self.peer, import_id=self.state.import_id
            )
        )
        self.state.started = True
        self.state.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.state.save(self.state_path)


def raw_to_json_safe(obj) -> dict | bool | int | None:
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj
    return json.loads(json.dumps(obj.to_dict(), default=str))
