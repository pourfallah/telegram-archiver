"""Typed single-message history-import probe (one media/text per run).

Extends the working WhatsApp-shape import (3-line private chat, media line in the
middle, bracket+seconds timestamps in Asia/Tehran, exact filename binding) to a
generic source message of ANY type: text, photo, video, document, audio, voice,
sticker, gif/animation. Runs the official five-RPC import as B into A<->B, then
verifies the NEW target messages (live) and their durability (T0..T3).

Usage:
  python -m recovery_v2.import_media_probe --source-id 5683694 [--type auto]
         [--nowait] [--clear] [--no-caption]

Only A<->C is read; A<->B is modified only via the official import (and, if
--clear, a safe B-side just_clear/revoke=false before import).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from recovery import pipeline as P
from recovery_v2 import recovery_sample_test as H

RUN_ROOT = Path("test_runs") / ("typed_probe_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"))


def _tw(dt) -> str:
    d = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(ZoneInfo("Asia/Tehran")).strftime("[%d/%m/%Y, %H:%M:%S]")


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
                if ss is not None and getattr(ss, "id", None) is not None:
                    inp = tl.InputStickerSetID(id=ss.id, access_hash=ss.access_hash)
                else:
                    inp = tl.InputStickerSetEmpty()
                out.append(tl.DocumentAttributeSticker(
                    alt=a.alt or "", stickerset=inp, mask=bool(a.mask)))
            elif n == "DocumentAttributeImageSize":
                out.append(tl.DocumentAttributeImageSize(w=a.w, h=a.h))
        except Exception:  # noqa: BLE001
            continue
    return out


def _snap(m) -> dict:
    med = getattr(m, "media", None)
    doc = getattr(med, "document", None) if med else None
    attrs = []
    if doc:
        for a in getattr(doc, "attributes", None) or []:
            d = {"ctor": type(a).__name__}
            for f in ("file_name", "alt", "duration", "performer", "title",
                      "voice", "w", "h", "round_message", "supports_streaming"):
                if hasattr(a, f):
                    v = getattr(a, f)
                    if not isinstance(v, (bytes, bytearray)):
                        d[f] = v
            attrs.append(d)
    fwd = getattr(m, "fwd_from", None)
    return {"id": getattr(m, "id", None),
            "date": str(getattr(m, "date", None)),
            "message": (getattr(m, "message", None) or "")[:300],
            "media_ctor": type(med).__name__ if med else None,
            "doc_mime": getattr(doc, "mime_type", None) if doc else None,
            "attrs": attrs,
            "grouped_id": getattr(m, "grouped_id", None),
            "reply_to_reply_msg_id": getattr(getattr(m, "reply_to", None), "reply_to_msg_id", None),
            "fwd_imported": bool(getattr(fwd, "imported", False)),
            "fwd_from_name": getattr(fwd, "from_name", None),
            "from_id": str(getattr(m, "from_id", None))}


async def read_target_paginated(tgt, tgt_peer, cap=5000):
    from telethon.tl import functions as f
    out = []
    offset_id = 0
    while True:
        res = await tgt.call(f.messages.GetHistoryRequest(
            peer=tgt_peer, offset_id=offset_id, offset_date=None, add_offset=0,
            limit=100, max_id=0, min_id=0, hash=0))
        msgs = getattr(res, "messages", None) or []
        if not msgs:
            break
        out.extend(msgs)
        if len(msgs) < 100:
            break
        offset_id = msgs[-1].id
        if len(out) >= cap:
            break
    return out


async def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-id", type=int, default=0, help="source message id in A<->C")
    ap.add_argument("--nowait", action="store_true")
    ap.add_argument("--no-caption", action="store_true")
    ap.add_argument("--clear", action="store_true",
                    help="safe B-side clear (just_clear, revoke=false) before import")
    ap.add_argument("--doc-minimal", action="store_true",
                    help="using only DocumentAttributeFilename for document media")
    ap.add_argument("--marker", default="<attached: {fname}>",
                    help="media marker template; {fname} = filename (<attached: …> is the canonical token)")
    args = ap.parse_args(argv)

    from recovery.config import load_dotenv
    from telethon.tl import functions as f
    from telethon.tl import types as tl
    load_dotenv()
    cfg = H.prepare_config(H._parser().parse_args(["--count", "2"]))
    H.require_sessions(cfg)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    report = {"started": datetime.now(timezone.utc).isoformat()}

    src, tgt = await P.build_clients(cfg)
    try:
        from recovery_v2.login_accounts import AccountStore
        a_phone = next(x.get("phone") for x in AccountStore().list()
                       if (x.get("phone") or "").startswith("+98"))
        src_peer = await src.get_peer("+989353114546")
        tgt_peer = await tgt.get_peer(a_phone)
        src_desc = await P.resolve_peer(src, "+989353114546")
        tgt_desc = await P.resolve_peer(tgt, a_phone)
        P.assert_target_is_ab(src, tgt_desc)
        report["source_peer"], report["target_peer"] = await P.identify(src_desc), await P.identify(tgt_desc)
        print(f"source {report['source_peer']} -> target {report['target_peer']} (asserted A<->B)")

        # fetch source message
        res = await src.call(f.messages.GetMessagesRequest(id=[args.source_id]))
        msgs = getattr(res, "messages", None) or []
        m = next((x for x in msgs if getattr(x, "id", None) == args.source_id), None)
        if m is None:
            report["error"] = f"source {args.source_id} not found"
            print(report["error"]); return 2
        med = getattr(m, "media", None)
        photo = getattr(med, "photo", None) if type(med).__name__ == "MessageMediaPhoto" else None
        doc = getattr(med, "document", None) if type(med).__name__ == "MessageMediaDocument" else None
        report["source"] = _snap(m)

        # download media bytes (photo or document)
        dl_obj = photo if photo is not None else doc
        bytes_ = b""
        if dl_obj is not None:
            async for chunk in src.client.iter_download(dl_obj):
                bytes_ += chunk
        report["source"]["bytes_len"] = len(bytes_)
        report["source"]["sha256"] = hashlib.sha256(bytes_).hexdigest()

        # filename
        fname = None
        if doc is not None:
            for a in getattr(doc, "attributes", None) or []:
                if type(a).__name__ == "DocumentAttributeFilename" and a.file_name:
                    fname = a.file_name
                    break
        if not fname:
            mime = getattr(doc, "mime_type", None) if doc else None
            if photo is not None and not mime:
                mime = "image/jpeg"
            fname = f"m_{args.source_id}.{_ext_from_mime(mime)}"
        report["source"]["filename"] = fname

        # clear target (safe) BEFORE import if requested
        if args.clear:
            await tgt.call(f.messages.DeleteHistoryRequest(peer=tgt_peer, max_id=0,
                                                           just_clear=True, revoke=False))
            print("cleared B-side target (just_clear, revoke=false)")

        # build 3-line WhatsApp file
        mdt = datetime.fromisoformat(str(getattr(m, "date", "")).replace("Z", "+00:00"))
        if mdt.tzinfo is None:
            mdt = mdt.replace(tzinfo=timezone.utc)
        src_sender = getattr(getattr(m, "from_id", None), "user_id", None) or "First"
        # two participant names for the private-chat shape
        name_a = getattr(getattr(src, "_me", None), "first_name", None) or "First"
        name_b = f"U{src_sender}" if isinstance(src_sender, int) else (name_a + "2")
        caption = (getattr(m, "message", None) or "")
        marker = args.marker.format(fname=fname) if dl_obj is not None else caption
        lines = [f"{_tw(mdt - timedelta(seconds=10))} {name_a}: Hello"]
        if dl_obj is not None:
            lines.append(f"{_tw(mdt)} {name_b}: {marker}")
            # ALWAYS a trailing line so media sits in the MIDDLE of >=3 lines
            # (a media line at the end of the file is not bound by the parser).
            if caption and not args.no_caption:
                lines.append(f"{_tw(mdt + timedelta(seconds=10))} {name_a}: {caption}")
            else:
                lines.append(f"{_tw(mdt + timedelta(seconds=10))} {name_a}: Thanks")
        else:
            lines.append(f"{_tw(mdt)} {name_b}: {caption}")
            lines.append(f"{_tw(mdt + timedelta(seconds=10))} {name_a}: ok")
        chat_text = "\n".join(lines) + "\n"
        (RUN_ROOT / "_chat.txt").write_text(chat_text, encoding="utf-8")
        print("EXPORT LINES:\n  " + "\n  ".join(lines))

        # checkHistoryImport (log; do NOT gate — proven working reproducer doesn't)
        head = chat_text[:4000]
        parsed = await tgt.call(f.messages.CheckHistoryImportRequest(import_head=head))
        report["checkHistoryImport"] = {"pm": bool(getattr(parsed, "pm", False)),
                                        "group": bool(getattr(parsed, "group", False))}
        print(f"checkHistoryImport -> pm={report['checkHistoryImport']['pm']}")

        # before-id snapshot, then init (media_count = 1 if media else 0)
        before_msgs = await read_target_paginated(tgt, tgt_peer)
        before_ids = {getattr(x, "id", None) for x in before_msgs}
        up_chat = await tgt.client.upload_file(chat_text.encode("utf-8"), file_name="_chat.txt")
        media_count = 1 if dl_obj is not None else 0
        init = await tgt.call(f.messages.InitHistoryImportRequest(
            peer=tgt_peer, file=up_chat, media_count=media_count))
        import_id = init.id
        report["import_id"] = import_id
        print(f"initHistoryImport -> import_id={import_id} (media_count={media_count})")

        # upload media (single)
        up_res_ctor = None
        if dl_obj is not None:
            up_media = await tgt.client.upload_file(bytes_, file_name=fname)
            report["uploaded_file"] = {"ctor": type(up_media).__name__, "id": getattr(up_media, "id", None),
                                       "parts": getattr(up_media, "parts", None), "name": fname}
            if photo is not None:
                media_input = tl.InputMediaUploadedPhoto(file=up_media)
            else:
                if args.doc_minimal:
                    attrs = [tl.DocumentAttributeFilename(file_name=fname)]
                else:
                    attrs = _map_attrs(getattr(doc, "attributes", None) or [])
                    check_fname = any(type(a).__name__ == "DocumentAttributeFilename" for a in attrs)
                    if not check_fname:
                        attrs = [tl.DocumentAttributeFilename(file_name=fname)] + attrs
                media_input = tl.InputMediaUploadedDocument(
                    file=up_media, mime_type=getattr(doc, "mime_type", None) or "application/octet-stream",
                    attributes=attrs)
            report["media_input"] = str(media_input)[:600]
            print("INPUT_MEDIA:", str(media_input)[:400])
            up_res = await tgt.call(f.messages.UploadImportedMediaRequest(
                peer=tgt_peer, import_id=import_id, file_name=fname, media=media_input))
            up_res_ctor = type(up_res).__name__
            report["upload_result"] = {"ctor": up_res_ctor, "repr": str(up_res)[:200],
                                       "empty": isinstance(up_res, tl.MessageMediaEmpty)}
            print(f"uploadImportedMedia -> {up_res_ctor} (empty={isinstance(up_res, tl.MessageMediaEmpty)})")

        start = await tgt.call(f.messages.StartHistoryImportRequest(peer=tgt_peer, import_id=import_id))
        report["startHistoryImport"] = str(start)
        print("startHistoryImport ->", start)

        # durability
        await asyncio.sleep(6.0)  # import materialization lags a few seconds
        report["durability"] = [await _read_target(tgt, tgt_peer, before_ids, "T0")]
        print("T0:", report["durability"][0]["new_count"], "new,", report["durability"][0]["with_media"], "with media")
        if not args.nowait:
            for label, dt in (("T1", 30), ("T2", 90), ("T3", 180)):
                await asyncio.sleep(dt)
                d = await _read_target(tgt, tgt_peer, before_ids, label)
                report["durability"].append(d)
                print(f"{label}: {d['new_count']} new, {d['with_media']} with media")
        first = report["durability"][0]["with_media"]
        last = report["durability"][-1]["with_media"]
        report["SERVER_SIDE_ROLLBACK"] = (first > 0) and (last == 0)
        (RUN_ROOT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("report:", (RUN_ROOT / "report.json").resolve())
        return 0
    finally:
        await src.close()
        await tgt.close()


async def _read_target(tgt, tgt_peer, before_ids, label):
    msgs = await read_target_paginated(tgt, tgt_peer)
    new = [m for m in msgs if getattr(m, "id", None) not in before_ids]
    recs = [_snap(m) for m in sorted(new, key=lambda x: x.id)]
    return {"label": label, "new_count": len(new),
            "with_media": sum(1 for s in recs if s["media_ctor"] not in (None, "MessageMediaEmpty")),
            "records": recs}


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))