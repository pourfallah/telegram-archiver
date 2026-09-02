"""Single-media history-import probe (the gate before any multi-message import).

Imports EXACTLY ONE source message (default id 5307, a photo + caption from
A<->C) into A<->B via B using the official five-RPC history-import flow, logs the
exact InputMedia payload and the returned MessageMedia, and reads the target at
T0/T1/T2/T3 for durability / rollback detection.

This is deliberately small and isolated. It exists to answer, with evidence:

  WHY did uploadImportedMedia succeed but no media attach?

Per docs/TELEGRAM_IMPORT_PROTOCOL.md and docs/TELEGRAM_IMPORT_MEDIA.md.

Usage:
  python -m recovery_v2.import_media_probe             # photo (default)
  python -m recovery_v2.import_media_probe --variant document

Never touches A<->C except read (fetch 5307). Target A<->B may be modified via
the official import flow only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from recovery import pipeline as P
from recovery_v2 import recovery_sample_test as H

DEFAULT_MSG = 5307
RUN_ROOT = Path("test_runs") / ("import_probe_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"))


async def read_target_paginated(tgt, tgt_peer, cap=100000):
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


def msg_snapshot(m) -> dict:
    return {
        "id": getattr(m, "id", None),
        "date": str(getattr(m, "date", None)),
        "message": (getattr(m, "message", None) or "")[:80],
        "media": type(getattr(m, "media", None)).__name__ if getattr(m, "media", None) is not None else None,
        "media_is_empty": isinstance(getattr(m, "media", None), __import__("telethon.tl.types", fromlist=["MessageMediaEmpty"]).MessageMediaEmpty),
        "from_id": str(getattr(m, "from_id", None)),
        "fwd_imported": bool(getattr(getattr(m, "fwd_from", None), "imported", False)),
        "fwd_date": str(getattr(getattr(m, "fwd_from", None), "date", None)),
        "fwd_from_id": str(getattr(getattr(m, "fwd_from", None), "from_id", None)),
        "fwd_from_name": getattr(getattr(m, "fwd_from", None), "from_name", None),
    }


async def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", type=int, default=DEFAULT_MSG)
    ap.add_argument("--variant", default="photo", choices=["photo", "document"])
    ap.add_argument("--marker", default="{fname} (file attached)")
    ap.add_argument("--no-caption", action="store_true",
                    help="emit ONLY the marker-only media line (strict iOS form)")
    ap.add_argument("--nowait", action="store_true",
                    help="log T0 only (skip T1/T2/T3) for fast marker hypothesis tests")
    args = ap.parse_args(argv)

    from recovery.config import load_dotenv
    load_dotenv()
    cfg = H.prepare_config(H._parser().parse_args(["--count", "2"]))
    H.require_sessions(cfg)

    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    report: dict = {"message_id": args.message, "variant": args.variant,
                    "started": datetime.now(timezone.utc).isoformat()}

    src, tgt = await P.build_clients(cfg)
    try:
        # --- resolve peers (source A<->C read-only, target A<->B) ----------
        from recovery_v2.login_accounts import AccountStore
        accounts = AccountStore().list()
        a_phone = next(x.get("phone") for x in accounts
                       if (x.get("phone") or "").startswith("+98"))
        src_peer_desc = await P.resolve_peer(src, "+989353114546")
        tgt_peer_desc = await P.resolve_peer(tgt, a_phone)
        src_id, tgt_id = await P.identify(src_peer_desc), await P.identify(tgt_peer_desc)
        P.assert_target_is_ab(src, tgt_peer_desc)
        src_peer = await src.get_peer("+989353114546")
        tgt_peer = await tgt.get_peer(a_phone)
        report["source_peer"] = src_id
        report["target_peer"] = tgt_id
        print(f"source {src_id} -> target {tgt_id} (asserted A<->B)")

        # --- 1. fetch source 5307 + download the photo -----------------------
        from telethon.tl import functions as f
        from telethon.tl.types import (MessageMediaPhoto,
                                       MessageMediaDocument, InputFile, InputFileBig,
                                       InputMediaUploadedPhoto, InputMediaUploadedDocument,
                                       MessageMediaEmpty, Document)
        res = await src.call(f.messages.GetMessagesRequest(id=[args.message]))
        msgs = getattr(res, "messages", None) or []
        m = next((x for x in msgs if getattr(x, "id", None) == args.message), None)
        if m is None:
            report["error"] = f"source {args.message} not found"
            print(report["error"]); return 2
        media = getattr(m, "media", None)
        report["source"] = msg_snapshot(m)
        report["source"]["media_ctor"] = type(media).__name__
        # photo -> take largest size; document -> big file / thumb
        photo = getattr(media, "photo", None) if isinstance(media, MessageMediaPhoto) else None
        doc = getattr(media, "document", None) if isinstance(media, MessageMediaDocument) else None
        dl_obj = photo if photo is not None else doc
        bytes_ = b""
        if dl_obj is not None:
            async for chunk in src.client.iter_download(dl_obj):
                bytes_ += chunk
        report["source"]["bytes_len"] = len(bytes_)
        report["source"]["sha256"] = hashlib.sha256(bytes_).hexdigest()
        if not bytes_:
            report["error"] = "photo download empty"
            print(report["error"]); return 2

        # deterministic local filename (photos have no native filename)
        fname = "probe_%d.jpg" % args.message
        (RUN_ROOT / fname).write_bytes(bytes_)
        marker = args.marker.format(fname=fname)
        caption = getattr(m, "message", None) or ""
        a_name = getattr(getattr(src, "_me", None), "first_name", None) or "First"
        b_name = a_name + "2"  # second participant name (private-chat shape)

        def _tw(dt):
            d = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(ZoneInfo("Asia/Tehran")).strftime("[%d/%m/%Y, %H:%M:%S]")

        mdt = datetime.fromisoformat(str(getattr(m, "date", "")).replace("Z", "+00:00"))
        if mdt.tzinfo is None:
            mdt = mdt.replace(tzinfo=timezone.utc)
        # WhatsApp 3-line private-chat shape (proven working reproducer):
        # leading text, MEDIA line in the middle, trailing text/caption.
        lines = [
            f"{_tw(mdt - timedelta(seconds=10))} {a_name}: Hello",
            f"{_tw(mdt)} {b_name}: {marker}",
        ]
        if caption and not args.no_caption:
            lines.append(f"{_tw(mdt + timedelta(seconds=10))} {a_name}: {caption}")
        chat_text = "\n".join(lines) + "\n"
        (RUN_ROOT / "_chat.txt").write_text(chat_text, encoding="utf-8")
        print("EXPORT LINES (WhatsApp shape, media in middle):\n  " + "\n  ".join(lines))
        print("EXPORT FILE ok (%d line(s), 1 media marker, media_count=1)" % len(lines))

        # --- 2. checkHistoryImport --------------------------------------------
        head = chat_text.encode("utf-8").decode("utf-8")[:4000]
        parsed = await tgt.call(f.messages.CheckHistoryImportRequest(import_head=head))
        is_pm = bool(getattr(parsed, "pm", False))
        is_group = bool(getattr(parsed, "group", False))
        title = getattr(parsed, "title", None)
        report["checkHistoryImport"] = {"pm": is_pm, "group": is_group, "title": title}
        print(f"checkHistoryImport -> pm={is_pm} group={is_group} title={title!r}")
        # NOTE: the proven working reproducer did NOT gate on pm — media still
        # attached. We log pm and proceed (the media read-back is the authority).

        # --- 3. checkHistoryImportPeer ----------------------------------------
        peer_check = await tgt.call(f.messages.CheckHistoryImportPeerRequest(peer=tgt_peer))
        report["checkHistoryImportPeer"] = str(peer_check)[:200]
        print("checkHistoryImportPeer ok:", str(peer_check)[:120])

        # --- 4. initHistoryImport (media_count=1) -----------------------------
        # The export FILE for init is the _chat.txt text (NOT the media bytes).
        chat_bytes = chat_text.encode("utf-8")
        up_chat = await tgt.client.upload_file(chat_bytes, file_name="_chat.txt")
        hist = await tgt.call(f.messages.InitHistoryImportRequest(
            peer=tgt_peer, file=up_chat, media_count=1))
        import_id = hist.id
        report["import_id"] = import_id
        print(f"initHistoryImport -> import_id={import_id} (media_count=1, file=_chat.txt)")

        # --- 5. uploadImportedMedia (ONE) -------------------------------------
        # The MEDIA upload is the photo/document bytes, via a separate InputFile.
        up_media = await tgt.client.upload_file(bytes_, file_name=fname)
        report["uploaded_file"] = {
            "ctor": type(up_media).__name__, "id": getattr(up_media, "id", None),
            "parts": getattr(up_media, "parts", None), "name": getattr(up_media, "name", None)}
        if args.variant == "photo":
            media_input = InputMediaUploadedPhoto(file=up_media)
        else:
            media_input = InputMediaUploadedDocument(
                file=up_media, mime_type="image/jpeg",
                attributes=[__import__("telethon.tl.types", fromlist=["DocumentAttributeFilename"])
                            .DocumentAttributeFilename(file_name=fname)])
        # log the exact serialized payload
        tl_str = str(media_input)
        report["upload_file_name"] = fname
        report["upload_media_input"] = tl_str[:600]
        report["upload_rpc"] = f"UploadImportedMedia(peer={tgt_peer}, import_id={import_id}, file_name={fname!r}, media={tl_str[:150]}...)"
        print("INPUT_MEDIA (serialized):", tl_str[:400])
        up_res = await tgt.call(f.messages.UploadImportedMediaRequest(
            peer=tgt_peer, import_id=import_id, file_name=fname, media=media_input))
        up_res_name = type(up_res).__name__
        report["upload_result"] = {"ctor": up_res_name, "repr": str(up_res)[:400]}
        print(f"uploadImportedMedia -> {up_res_name}  (empty={isinstance(up_res, MessageMediaEmpty)})")

        # GATE: do not startHistoryImport if the media did NOT attach
        if isinstance(up_res, MessageMediaEmpty):
            report["error"] = "uploadImportedMedia returned MessageMediaEmpty: media did not attach. Not calling startHistoryImport."
            print("\n**** GATE: uploadImportedMedia returned MessageMediaEmpty -> media NOT attached. STOP. ****")
            (RUN_ROOT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            return 3

        # --- 6. startHistoryImport --------------------------------------------
        started = await tgt.call(f.messages.StartHistoryImportRequest(peer=tgt_peer, import_id=import_id))
        report["startHistoryImport"] = str(started)
        print("startHistoryImport ->", started)

        # --- 7. durability T0/T1/T2/T3 ---------------------------------------
        report["durability"] = []
        report["durability"].append(await _read_now(tgt, tgt_peer, "T0"))
        print("T0:", report["durability"][-1]["total"], "total,", report["durability"][-1]["with_media"], "with media")
        if not args.nowait:
            for label, dt in (("T1", 30), ("T2", 90), ("T3", 180)):
                await asyncio.sleep(dt)
                d = await _read_now(tgt, tgt_peer, label)
                report["durability"].append(d)
                print(f"{label}: {d['total']} total, {d['with_media']} with media (imported={d['imported_count']})")

        first = report["durability"][0]["imported_count"]
        last = report["durability"][-1]["imported_count"]
        report["SERVER_SIDE_ROLLBACK"] = (first > 0) and (last == 0)
        print("\nSERVER_SIDE_ROLLBACK =", report["SERVER_SIDE_ROLLBACK"])
        (RUN_ROOT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("report:", (RUN_ROOT / "report.json").resolve())
        return 0
    finally:
        await src.close()
        await tgt.close()


async def _read_now(tgt, tgt_peer, label):
    msgs = await read_target_paginated(tgt, tgt_peer)
    snaps = [msg_snapshot(m) for m in msgs]
    new = [s for s in snaps if s["fwd_imported"]]
    return {"label": label, "total": len(msgs),
            "imported_count": len(new),
            "with_media": sum(1 for s in snaps if s["media"] not in (None, "MessageMediaEmpty")),
            "imported": new}


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))