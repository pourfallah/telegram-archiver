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
    """Encode in the import parser's FIXED +03:30 frame.

    The server decodes _chat.txt timestamps at a fixed +03:30 (it ignores Iran's
    historical DST), so writing the DST-aware Asia/Tehran wall-clock made
    DST-period instants land +1h late (user-observed date corruption). Writing
    instant + 03:30 yields target.message.date == source date for EVERY date;
    standard-offset era dates are unchanged (wall clock == instant + 03:30).
    """
    d = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (d + timedelta(hours=3, minutes=30)).strftime("[%d/%m/%Y, %H:%M:%S]")


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
async def _with_flood_retry(factory, label: str, max_retries: int = 10,
                           max_wait: int = 3600):
    """Call an RPC factory, sleeping out FloodWait (+2s buffer) and retrying.

    Safe for history import: each retry runs import_batch fresh (a new
    initHistoryImport yields a new import_id; nothing is committed until
    startHistoryImport, so a flooded batch never leaves partial state).
    """
    from telethon.errors import FloodWaitError
    attempt = 0
    while True:
        try:
            return await factory()
        except FloodWaitError as e:
            attempt += 1
            secs = int(getattr(e, "seconds", 30))
            sleep = min(secs, max_wait) + 2
            print(f"  [{label}] FloodWaitError {secs}s; sleeping {sleep}s then retrying "
                  f"(attempt {attempt}/{max_retries})", flush=True)
            if attempt >= max_retries:
                raise
            await asyncio.sleep(sleep)


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
def _mem_kb() -> int:
    """Resident set size in KiB (Linux /proc)."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except Exception:
        return -1


def _du_kb(p) -> int:
    """Total size of a tree in KiB."""
    if not Path(p).exists():
        return 0
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file()) // 1024


async def build_batch(msgs: list, batch_dir: Path, names: dict[int, str],
                      a_id: int, c_id: int) -> tuple[list, list]:
    """From full source messages -> (_chat.txt lines, staged media file records).
    Returns (lines, media_records) where media_records = [{file_name,path,media_input_builder}]."""
    lines: list[str] = []
    media: list[dict] = []
    used_fnames: set[str] = set()
    caption_lines: list[str] = []
    group_when: dict = {}  # grouped_id -> _chat.txt timestamp (same for all album members)

    for m in msgs:
        when = _tw(getattr(m, "date", ""))
        gid = getattr(m, "grouped_id", None)
        if gid:
            # ALBUM: force the EXACT same timestamp across every member so Telegram
            # groups consecutive same-grouped media into one album on import.
            when = group_when.setdefault(gid, when)
        uid = getattr(getattr(m, "from_id", None), "user_id", None)
        if uid is None:
            # id-fetched messages may omit from_id; the out flag disambiguates
            # the private-chat sender (A=src account, else C).
            uid = a_id if getattr(m, "out", False) else c_id
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
    """Single-session import of one batch via Account B (RecoveryClient)."""
    from telethon.tl import functions as f
    from telethon.tl import types as tl
    out = {}
    chat_up = await client.client.upload_file(chat_text.encode("utf-8"), file_name="_chat.txt")
    out["A_upload_chat"] = type(chat_up).__name__
    chk = await client.call(f.messages.CheckHistoryImportRequest(import_head=chat_text[:4000]))
    out["B_checkHistoryImport"] = {"pm": bool(getattr(chk, "pm", False))}
    init = await client.call(f.messages.InitHistoryImportRequest(
        peer=peer, file=chat_up, media_count=len(media)))
    out["C_initHistoryImport"] = int(init.id)
    for rec in media:
        up = await client.client.upload_file(str(batch_dir / rec["file_name"]),
                                             file_name=rec["file_name"])
        if rec["is_photo"]:
            media_input = tl.InputMediaUploadedPhoto(file=up)
        else:
            attrs = _map_attrs(rec.get("attrs"))
            if not any(type(a).__name__ == "DocumentAttributeFilename" for a in attrs):
                attrs = [tl.DocumentAttributeFilename(rec["file_name"])] + attrs
            media_input = tl.InputMediaUploadedDocument(
                file=up, mime_type=rec.get("mime") or "application/octet-stream", attributes=attrs)
        res = await client.call(f.messages.UploadImportedMediaRequest(
            peer=peer, import_id=out["C_initHistoryImport"],
            file_name=rec["file_name"], media=media_input))
        out.setdefault("D_uploads", []).append(
            {"file_name": rec["file_name"], "returned": type(res).__name__})
    out["E_startHistoryImport"] = bool(await client.call(
        f.messages.StartHistoryImportRequest(peer=peer, import_id=out["C_initHistoryImport"])))
    return out


async def replay_reactions(src, tgt, tgt_peer, msgs: list, src_peer) -> dict:
    """Re-apply source MessageReactions onto the just-imported target messages.

    The import 5-RPC flow has NO reaction parameter (primary-source fact). The
    only mechanism is a SUBSEQUENT messages.sendReaction on the imported target
    message, issued from a target participant's own session, after the batch
    commits. Source reactions are fetched with messages.getMessagesReactions
    (GetMessagesRequest-by-id does not populate Message.reactions). Best effort:
    the source message's distinct emoji set is re-applied via the importer's (B)
    session; exact per-user attribution is not preserved (C's reactions cannot
    map to a participant of A<->B).
    """
    from telethon.tl import functions as f
    from telethon.tl import types as tl
    out: dict = {"reacted_msgs": 0, "emojis": 0}
    try:
        ids = [getattr(m, "id", 0) for m in msgs if getattr(m, "id", None)]
        emo_by_id: dict[int, list[str]] = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            rr = await _with_flood_retry(lambda: src.call(
                f.messages.GetMessagesReactionsRequest(peer=src_peer, id=chunk)), "reactSrc")
            # getMessagesReactions returns Updates; reactions ride in
            # UpdateMessageReactions entries.
            for u in (getattr(rr, "updates", None) or []):
                if type(u).__name__ != "UpdateMessageReactions":
                    continue
                mid = getattr(u, "msg_id", None)
                if not mid:
                    continue
                emos: list[str] = []
                for rc in (getattr(getattr(u, "reactions", None), "results", None) or []):
                    emo = getattr(getattr(rc, "reaction", None), "emoticon", None)
                    if emo and emo not in emos:
                        emos.append(emo)
                if emos:
                    emo_by_id[mid] = emos
        if not emo_by_id:
            out["no_source_reactions"] = True
            return out
        res = await _with_flood_retry(lambda: tgt.call(f.messages.GetHistoryRequest(
            peer=tgt_peer, offset_id=0, offset_date=None, add_offset=0,
            limit=len(msgs), max_id=0, min_id=0, hash=0)), "reactMap")
        t_msgs = [m for m in (getattr(res, "messages", None) or [])
                  if getattr(getattr(m, "fwd_from", None), "imported", False)]
        t_msgs.sort(key=lambda m: getattr(m, "date", datetime.min))
        ordered = sorted(msgs, key=lambda m: getattr(m, "date", datetime.min))
        for sm, tm in zip(ordered, t_msgs):
            emos = emo_by_id.get(getattr(sm, "id", None))
            if not emos:
                continue
            await _with_flood_retry(lambda: tgt.call(f.messages.SendReactionRequest(
                peer=tgt_peer, msg_id=tm.id,
                reaction=[tl.ReactionEmoji(emoticon=e) for e in emos],
                add_to_recent=False)), "react")
            out["reacted_msgs"] += 1
            out["emojis"] += len(emos)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
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

        # ---- 1) id index, chronological OLDEST->NEWEST (ascending id/date) ----
        if args.ids:
            # targeted window: exact ids, oldest-first, ONE batch
            ids = sorted(int(x) for x in args.ids.split(",") if x.strip())
            print(f"using --ids window: {len(ids)} messages (oldest-first, single batch)")
        else:
            print("discovering A<->C ids (metadata only)...")
            ids_newest: list[int] = []
            offset_id = 0
            while True:
                if args.limit and len(ids_newest) >= args.limit:
                    break
                res = await _with_flood_retry(
                    lambda: src.call(f.messages.GetHistoryRequest(
                        peer=src_peer, offset_id=offset_id, offset_date=None, add_offset=0,
                        limit=100, max_id=0, min_id=0, hash=0)), "discover")
                ms = getattr(res, "messages", None) or []
                if not ms:
                    break
                ids_newest += [m.id for m in ms]
                if len(ms) < 100:
                    break
                offset_id = ms[-1].id
                if len(ids_newest) % 5000 == 0:
                    print(f"  ... {len(ids_newest)} ids indexed")
            ids = ids_newest[::-1]  # oldest-first (id ascending = chronological forward)
            if args.limit:
                ids = ids[:args.limit]
        total = len(ids)
        # Resume: continue from the last fully-imported batch. (Delete the state
        # file to start over from message 0.)
        resume = 0 if args.ids else state.data.get("processed_count", 0)
        if resume > total:
            resume = 0
        state.data["total"] = total
        state.save()
        print(f"indexed {total} messages; batch_size={args.batch_size}; resume_from={resume}")

        # ---- 2) process batches in order ----------------------------------------
        start = resume
        processed = resume
        me_a = await src.client.get_me()
        c_id = getattr(src_peer, "user_id", None)
        # Sender names MUST be the TARGET chat's participant names. Two-sided
        # mapping: A's messages use A's name (as B sees it); C's messages use
        # B's name, so the migrated A<->C history reads as a two-sided A<->B
        # conversation. The parser matches line senders against target
        # participants; any other name is dropped and mis-attributed (measured:
        # 345 msgs landed with fwd.from_id = importing account B).
        names: dict[int, str] = {}
        try:
            me_b = await tgt.client.get_me()
            b_name = me_b.first_name or me_b.username or f"user_{me_b.id}"
            names[me_b.id] = b_name
            if c_id:
                names[c_id] = b_name  # C (source other-side) surfaces as B's side
            a_ent = await tgt.client.get_entity(tgt_peer)
            a_id = getattr(a_ent, "user_id", None) or getattr(a_ent, "id", None)
            names[a_id] = a_ent.first_name or a_ent.username or f"user_{a_id}"
        except Exception as e:
            print(f"  WARN participant name resolution failed: {e}")
        while start < total:
            batch_ids = ids[start:start + args.batch_size]
            batch_dir = TMP_ROOT / f"batch_{start}_{start + len(batch_ids)}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            # fetch full messages in chunks of <=90
            msgs: list = []
            for i in range(0, len(batch_ids), 90):
                chunk = batch_ids[i:i + 90]
                res = await _with_flood_retry(
                    lambda: src.call(f.messages.GetMessagesRequest(id=chunk)), "getMessages")
                got = getattr(res, "messages", None) or []
                msgs += [m for m in got if getattr(m, "id", None) in set(batch_ids)]
            msgs.sort(key=lambda m: getattr(m, "date", datetime.min))

            # build + stage media bytes
            lines, media = await build_batch(msgs, batch_dir, names, me_a.id, c_id)
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

            print(f"[batch {start}:{start + len(batch_ids)}] lines={len(lines)} media={len(media)} "
                  f"| rss={_mem_kb()}KiB tmp={_du_kb(TMP_ROOT)}KiB", flush=True)

            if args.dry_run:
                # local-only validation, no B mutation
                ok = len(media) == sum(1 for m in media if (batch_dir / m["file_name"]).exists())
                print(f"  dry-run: staged media present={ok}")
                shutil.rmtree(batch_dir, ignore_errors=True)
                start += len(batch_ids)
                continue

            # import (single session, one init per batch)
            try:
                trace = await _with_flood_retry(
                    lambda: import_batch(tgt, tgt_peer, batch_dir, chat_text, media), "import")
                print("  import:", json.dumps(trace, ensure_ascii=False)[:200])
            except Exception as e:
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
            print(f"  -> committed {len(batch_ids)}; total processed {processed}/{total} "
                  f"| rss={_mem_kb()}KiB tmp={_du_kb(TMP_ROOT)}KiB", flush=True)
            if not args.no_reactions:
                rx = await replay_reactions(src, tgt, tgt_peer, msgs, src_peer)
                print(f"  reactions: {json.dumps(rx, ensure_ascii=False)}", flush=True)

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
    ap.add_argument("--limit", type=int, default=0, help="max messages (0=all)")
    ap.add_argument("--ids", default="",
                    help="comma-separated exact src ids to migrate as ONE batch (oldest-first); "
                         "skips full discovery")
    ap.add_argument("--dry-run", action="store_true", help="build/verify locally, no A<->B mutation")
    ap.add_argument("--no-reactions", action="store_true",
                    help="skip post-import reaction replay (sendReaction)")
    ap.add_argument("--state", default=None)
    args = ap.parse_args(argv)

    from recovery.config import load_dotenv
    load_dotenv()
    cfg = H.prepare_config(H._parser().parse_args(["--count", "2"]))
    H.require_sessions(cfg)
    return asyncio.run(run(cfg, args))


if __name__ == "__main__":
    sys.exit(main())