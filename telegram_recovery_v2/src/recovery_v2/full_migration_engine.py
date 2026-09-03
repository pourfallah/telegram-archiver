"""Production history-migration engine (A<->C -> A<->B) built on the PROVEN recipe.

Recipe (verified by the 10-message micro-probe and typed probes):
  - 3+-line WhatsApp-style file, `[DD/MM/YYYY, HH:MM:SS]` Asia/Tehran timestamps.
  - Media marker `<attached: FILENAME>`; file_name to uploadImportedMedia BYTE-matches.
  - photo -> InputMediaUploadedPhoto ; document -> InputMediaUploadedDocument(attrs).
  - A media marker must be followed by a trailing text line / next entry.
  - Single session per batch: upload _chat.txt -> checkHistoryImport ->
    initHistoryImport(once) -> uploadImportedMedia(xN) -> startHistoryImport(once).

This ENGINE splits the history into bounded chronological batches so only one
batch's messages + media are in memory/disk at a time, and deletes each batch's
temp files after its import succeeds. It keeps a state file so an interrupted run
resumes at the last fully-imported batch (no duplication).

Source A<->C is READ-ONLY. Target A<->B is the only thing modified.

Run:  python -m recovery_v2.full_migration_engine \
        --source-peer +989353114546 --batch-size 5000 --delay 12 --limit 1000
Add --dry-run to build+verify batches locally WITHOUT touching A<->B.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from recovery import pipeline as P
from recovery_v2 import recovery_sample_test as H
from recovery_v2.login_accounts import AccountStore

TEHRAN = ZoneInfo("Asia/Tehran")
TMP_ROOT = Path("test_runs") / "migration_tmp"
STATE_PATH = Path("test_runs") / "migration_state.json"


# ---------------------------------------------------------------------------
# formatting / media helpers (the PROVEN recipe)
# ---------------------------------------------------------------------------
def _tw(dt) -> str:
    d = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(TEHRAN).strftime("[%d/%m/%Y, %H:%M:%S]")


def _ext_from_mime(mime: str | None) -> str:
    if not mime:
        return "bin"
    m = mime.split(";")[0].strip().lower()
    return {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
            "image/gif": "gif", "video/mp4": "mp4", "audio/mpeg": "mp3",
            "audio/ogg": "ogg", "application/x-tgsticker": "tgs",
            "application/pdf": "pdf"}.get(m, m.split("/")[-1] or "bin")


def _map_attrs(doc_attrs) -> list:
    """Reconstruct InputMediaUploadedDocument attributes from a source Document."""
    from telethon.tl import types as tl
    out = []
    for a in doc_attrs or []:
        n = type(a).__name__
        try:
            if n == "DocumentAttributeFilename":
                out.append(tl.DocumentAttributeFilename(file_name=a.file_name))
            elif n == "DocumentAttributeAnimated":
                out.append(tl.DocumentAttributeAnimated())
            elif n == "DocumentAttributeAudio":
                out.append(tl.DocumentAttributeAudio(
                    duration=a.duration or 0, title=a.title, performer=a.performer,
                    voice=bool(a.voice), waveform=a.waveform))
            elif n == "DocumentAttributeVideo":
                out.append(tl.DocumentAttributeVideo(
                    duration=a.duration or 0, w=a.w or 0, h=a.h or 0,
                    round_message=bool(a.round_message),
                    supports_streaming=bool(a.supports_streaming)))
            elif n == "DocumentAttributeSticker":
                ss = a.stickerset
                inp = (tl.InputStickerSetID(id=ss.id, access_hash=ss.access_hash)
                       if ss is not None and getattr(ss, "id", None) is not None
                       else tl.InputStickerSetEmpty())
                out.append(tl.DocumentAttributeSticker(alt=a.alt or "",
                                                       stickerset=inp, mask=bool(a.mask)))
            elif n == "DocumentAttributeImageSize":
                out.append(tl.DocumentAttributeImageSize(w=a.w, h=a.h))
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------------------
# migration state (resumability)
# ---------------------------------------------------------------------------
class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"source_peer": None, "target_peer": None, "batch_size": 0,
                     "processed_count": 0, "total": 0, "batches": []}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def resume_offset(self, batch_size: int) -> int:
        return self.data.get("processed_count", 0)


# ---------------------------------------------------------------------------
# per-batch build + import
# ---------------------------------------------------------------------------
async def build_batch(msgs: list, batch_dir: Path, names: dict[int, str]) -> tuple[list, list]:
    """From full source messages -> (_chat.txt lines, staged media file records).
    Returns (lines, media_records) where media_records = [{file_name,path,media_input_builder}]."""
    lines: list[str] = []
    media: list[dict] = []
    used_fnames: set[str] = set()
    caption_lines: list[str] = []

    for m in msgs:
        when = _tw(getattr(m, "date", ""))
        uid = getattr(getattr(m, "from_id", None), "user_id", None)
        sender = names.get(uid, f"user_{uid}" if uid else "Unknown")
        text = (getattr(m, "message", None) or "").replace("\n", " ").strip()
        med = getattr(m, "media", None)
        photo = getattr(med, "photo", None) if type(med).__name__ == "MessageMediaPhoto" else None
        doc = getattr(med, "document", None) if type(med).__name__ == "MessageMediaDocument" else None
        if photo is None and doc is None:
            if text:
                lines.append(f"{when} {sender}: {text}")
            continue
        # media message
        fname = None
        if doc is not None:
            for a in getattr(doc, "attributes", None) or []:
                if type(a).__name__ == "DocumentAttributeFilename" and a.file_name:
                    fname = a.file_name
                    break
        if not fname:
            mime = getattr(doc, "mime_type", None) if doc else ("image/jpeg" if photo else None)
            fname = f"m_{getattr(m,'id',0)}.{_ext_from_mime(mime)}"
        base = fname
        if fname in used_fnames:
            fname = f"{Path(fname).stem}_{getattr(m,'id',0)}{Path(fname).suffix}"
        used_fnames.add(fname)
        # download bytes
        dl = photo if photo is not None else doc
        bytes_ = b""
        # download via the engine's source client is outside; bytes fetched by caller
        media.append({"file_name": fname, "source_id": getattr(m, "id", None),
                      "is_photo": photo is not None, "mime": getattr(doc, "mime_type", None),
                      "attrs": getattr(doc, "attributes", None) if doc else None})
        lines.append(f"{when} {sender}: <attached: {fname}>")
        if text:
            lines.append(f"{when} {sender}: {text}")  # caption as trailing text line

    # PROVEN rule: a media marker must have a trailing text line / next entry.
    if lines and "<attached:" in lines[-1]:
        lines.append(f"{_tw(datetime.now(tz=timezone.utc) + timedelta(seconds=1))} Migration: <continued>")
    return lines, media


async def import_batch(client, peer, batch_dir: Path, chat_text: str,
                       media: list[dict]) -> dict:
    """Single-session import of one batch via Account B. Returns step trace."""
    from telethon.tl import functions as f
    from telethon.tl import types as tl
    out = {}
    chat_up = await client.upload_file(chat_text.encode("utf-8"), file_name="_chat.txt")
    out["A_upload_chat"] = type(chat_up).__name__
    chk = await client(f.messages.CheckHistoryImportRequest(import_head=chat_text[:4000]))
    out["B_checkHistoryImport"] = {"pm": bool(getattr(chk, "pm", False))}
    init = await client(f.messages.InitHistoryImportRequest(
        peer=peer, file=chat_up, media_count=len(media)))
    out["C_initHistoryImport"] = int(init.id)
    for rec in media:
        path = batch_dir / rec["file_name"]
        up = await client.upload_file(str(path), file_name=rec["file_name"])
        if rec["is_photo"]:
            media_input = tl.InputMediaUploadedPhoto(file=up)
        else:
            attrs = _map_attrs(rec.get("attrs"))
            if not any(type(a).__name__ == "DocumentAttributeFilename" for a in attrs):
                attrs = [tl.DocumentAttributeFilename(rec["file_name"])] + attrs
            media_input = tl.InputMediaUploadedDocument(
                file=up, mime_type=rec.get("mime") or "application/octet-stream", attributes=attrs)
        res = await client(f.messages.UploadImportedMediaRequest(
            peer=peer, import_id=out["C_initHistoryImport"],
            file_name=rec["file_name"], media=media_input))
        out.setdefault("D_uploads", []).append(
            {"file_name": rec["file_name"], "returned": type(res).__name__})
    out["E_startHistoryImport"] = bool(await client(
        f.messages.StartHistoryImportRequest(peer=peer, import_id=out["C_initHistoryImport"])))
    return out


# ---------------------------------------------------------------------------
# engine driver
# ---------------------------------------------------------------------------
async def run(cfg, args) -> int:
    from telethon.tl import functions as f
    src, tgt = await P.build_clients(cfg)
    state = State(TMP_ROOT.parent / STATE_PATH.name if args.state is None else Path(args.state))

    try:
        # peers: source A<->C (via A), target A<->B (via B)
        src_peer = await src.get_peer(args.source_peer)
        tgt_phone = next(x.get("phone") for x in AccountStore().list()
                         if (x.get("phone") or "").startswith("+98"))
        tgt_peer = await tgt.get_peer(tgt_phone)
        state.data["source_peer"] = args.source_peer
        state.data["target_peer"] = tgt_phone
        state.data["batch_size"] = args.batch_size
        state.save()

        # ---- 1) lightweight id index (ids only, chronological oldest->newest) ----
        print("discovering A<->C ids (metadata only)...")
        ids_newest: list[int] = []
        offset_id = 0
        while True:
            if args.limit and len(ids_newest) >= args.limit:
                break
            res = await src.call(f.messages.GetHistoryRequest(
                peer=src_peer, offset_id=offset_id, offset_date=None, add_offset=0,
                limit=100, max_id=0, min_id=0, hash=0))
            ms = getattr(res, "messages", None) or []
            if not ms:
                break
            ids_newest += [m.id for m in ms]
            if len(ms) < 100:
                break
            offset_id = ms[-1].id
            if len(ids_newest) % 5000 == 0:
                print(f"  ... {len(ids_newest)} ids indexed")
        ids = ids_newest[::-1]  # oldest first
        if args.limit:
            ids = ids[:args.limit]
        total = len(ids)
        # Resume: continue from the last fully-imported batch. (Delete the state
        # file to start over from message 0.)
        resume = state.data.get("processed_count", 0)
        if resume > total:
            resume = 0
        state.data["total"] = total
        state.save()
        print(f"indexed {total} messages; batch_size={args.batch_size}; resume_from={resume}")

        # ---- 2) process batches in order ----------------------------------------
        start = resume
        processed = resume
        names: dict[int, str] = {}
        while start < total:
            batch_ids = ids[start:start + args.batch_size]
            batch_dir = TMP_ROOT / f"batch_{start}_{start + len(batch_ids)}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            # fetch full messages in chunks of <=90
            msgs: list = []
            for i in range(0, len(batch_ids), 90):
                chunk = batch_ids[i:i + 90]
                res = await src.call(f.messages.GetMessagesRequest(id=chunk))
                got = getattr(res, "messages", None) or []
                msgs += [m for m in got if getattr(m, "id", None) in set(batch_ids)]
                if getattr(res, "users", None):
                    for u in res.users:
                        names.setdefault(u.id, u.first_name or u.username or f"user_{u.id}")
            msgs.sort(key=lambda m: getattr(m, "date", datetime.min))

            # build + stage media bytes
            lines, media = await build_batch(msgs, batch_dir, names)
            for rec in media:
                src_m = next((m for m in msgs if getattr(m, "id", None) == rec["source_id"]), None)
                dl = None
                if src_m is not None:
                    mm = src_m.media
                    dl = getattr(mm, "photo", None) or getattr(mm, "document", None)
                if dl is not None:
                    b = b""
                    async for chunk in src.client.iter_download(dl):
                        b += chunk
                    (batch_dir / rec["file_name"]).write_bytes(b)
            chat_text = "\n".join(lines) + "\n"

            print(f"[batch {start}:{start + len(batch_ids)}] lines={len(lines)} media={len(media)}", flush=True)

            if args.dry_run:
                # local-only validation, no B mutation
                ok = len(media) == sum(1 for m in media if (batch_dir / m["file_name"]).exists())
                print(f"  dry-run: staged media present={ok}")
                shutil.rmtree(batch_dir, ignore_errors=True)
                start += len(batch_ids)
                continue

            # import (single session, one init per batch)
            try:
                trace = await import_batch(tgt, tgt_peer, batch_dir, chat_text, media)
                print("  import:", json.dumps(trace, ensure_ascii=False)[:200])
            except Exception as e:  # includes FloodWaitError
                print(f"  IMPORT FAILED: {type(e).__name__}: {e}")
                shutil.rmtree(batch_dir, ignore_errors=True)
                state.save()
                raise

            # success -> advance + cleanup
            processed += len(batch_ids)
            state.data["processed_count"] = processed
            state.data.setdefault("batches", []).append(
                {"start": start, "end": start + len(batch_ids), "count": len(batch_ids)})
            state.save()
            shutil.rmtree(batch_dir, ignore_errors=True)
            print(f"  -> committed {len(batch_ids)}; total processed {processed}/{total}", flush=True)

            start += len(batch_ids)
            if args.delay:
                await asyncio.sleep(args.delay)

        print(f"\nDONE: processed {processed}/{total} messages into {state.data['target_peer']}")
        return 0
    finally:
        await src.close()
        await tgt.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-peer", required=True, help="A<->C contact (read-only)")
    ap.add_argument("--batch-size", type=int, default=5000)
    ap.add_argument("--delay", type=int, default=12, help="seconds between batches")
    ap.add_argument("--limit", type=int, default=0, help="max messages to migrate (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="build/verify locally, no A<->B mutation")
    ap.add_argument("--state", default=None)
    args = ap.parse_args(argv)

    from recovery.config import load_dotenv
    load_dotenv()
    cfg = H.prepare_config(H._parser().parse_args(["--count", "2"]))
    H.require_sessions(cfg)
    return asyncio.run(run(cfg, args))


if __name__ == "__main__":
    sys.exit(main())