"""E2E recovery import stage: media download + import + reaction voters + verify.

Run: python3 recovery_e2e_import.py e2e_20260825 [import|voters|verify|report]
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import redis.asyncio as aioredis

from app.config import get_settings
from app.database import async_session_factory
from app.models import TelegramSession
from app.services.session_manager import SessionManager

A_SESSION_ID = 1
B_SESSION_ID = 3
A_VIEW_PEER = 7768075024
B_VIEW_PEER = 165649921
TZ_OFFSET = 210  # Iran +3:30


def _iso(dt) -> str:
    return dt.isoformat()[:19] if dt else None


async def stage_voters(run_dir: Path):
    """Read-only: fetch WHO reacted on the intact A-side history."""
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))

    from telethon import functions
    client, release = await manager.acquire_client(acc_a)
    voters_out = []
    try:
        peer = await client.get_entity(A_VIEW_PEER)
        react_src = json.loads((run_dir / "source" / "source_reactions.json").read_text())
        msg_ids = sorted({r["message_id"] for r in react_src})
        for mid in msg_ids:
            try:
                rl = await client(functions.messages.GetMessageReactionsListRequest(
                    peer=peer, id=mid, limit=50))
            except Exception as exc:  # noqa: BLE001
                voters_out.append({"message_id": mid, "error": type(exc).__name__})
                continue
            for item in getattr(rl, "reactions", None) or []:
                r = getattr(item, "reaction", None)
                p = getattr(item, "peer_id", None)
                voters_out.append({
                    "message_id": mid,
                    "reaction_ctor": type(r).__name__ if r else None,
                    "emoji": getattr(r, "emoticon", None) if r else None,
                    "document_id": getattr(r, "document_id", None) if r else None,
                    "reactor_peer_id": (getattr(p, "user_id", None)
                                        or getattr(p, "channel_id", None)
                                        or getattr(p, "chat_id", None)),
                    "date": _iso(getattr(item, "date", None)),
                })
    finally:
        await release()
    (run_dir / "source" / "source_reaction_voters.json").write_text(
        json.dumps(voters_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "voters", "items": len(voters_out)}, ensure_ascii=False, indent=2))
    return voters_out


async def stage_import(run_dir: Path):
    """Download media from A (read-only) -> build import file -> direct MTProto
    import as B into the SAME A<->B peer."""
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))

    src_dir = run_dir / "source"
    msgs = json.loads((src_dir / "source_messages.json").read_text())
    media_manifest = json.loads((src_dir / "source_media_manifest.json").read_text())
    names = json.loads((src_dir / "source_participants.json").read_text())

    media_dir = run_dir / "media"
    media_dir.mkdir(exist_ok=True)

    # 1) Download media files from A (read-only); fall back to the media
    #    already stored in the run snapshot (e.g. source side later cleared).
    import hashlib
    files = []
    try:
        ca, ra = await manager.acquire_client(acc_a)
        try:
            peer_a = await ca.get_entity(A_VIEW_PEER)
            source_msgs = await ca.get_messages(peer_a, limit=200)
            by_id = {m.id: m for m in source_msgs}
            for mm in media_manifest:
                m = by_id.get(mm["source_message_id"])
                if m is None or m.media is None:
                    continue
                fname = None
                for a in mm.get("attrs", []):
                    if a.get("ctor") == "DocumentAttributeFilename" and a.get("file_name"):
                        fname = a["file_name"]
                if fname is None:
                    ext = (mm.get("mime") or "bin").split("/")[-1].split(";")[0]
                    fname = f"media_{mm['source_message_id']}.{ext}"
                dest = media_dir / fname
                await ca.download_media(m, file=str(dest))
                files.append({"source_message_id": mm["source_message_id"],
                              "file_name": fname, "path": str(dest),
                              "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                              "mime": mm.get("mime")})
        finally:
            await ra()
    except Exception:  # noqa: BLE001
        files = []

    if not files:
        # fallback: snapshot media already stored on disk
        for mm in media_manifest:
            for a in mm.get("attrs", []):
                if a.get("ctor") == "DocumentAttributeFilename" and a.get("file_name"):
                    dest = media_dir / a["file_name"]
                    if dest.exists():
                        files.append({"source_message_id": mm["source_message_id"],
                                      "file_name": a["file_name"], "path": str(dest),
                                      "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                                      "mime": mm.get("mime")})
                    break
    print(json.dumps({"stage": "media_download", "files": [
        {"id": f["source_message_id"], "name": f["file_name"],
         "size": Path(f["path"]).stat().st_size, "sha": f["sha256"][:16]}
        for f in files]}, ensure_ascii=False, indent=2))

    # 2) Build import file (WhatsApp syntax, Iran local time)
    def wa(dt_str: str) -> str:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        local = dt + timedelta(minutes=TZ_OFFSET)
        return local.strftime("[%d/%m/%Y, %H:%M:%S]")

    lines = []
    for m in msgs:
        when = wa(m["date"])
        sender = names.get(str(m["sender_id"]), "Unknown")
        text = (m.get("text") or "").replace("\n", " ")
        if m["media"].get("ctor"):
            # find matching file
            f = next((x for x in files if x["source_message_id"] == m["source_message_id"]), None)
            fname = f["file_name"] if f else "unknown"
            if text:
                lines.append(f"{when} {sender}: <attached: {fname}>")
                lines.append(f"{when} {sender}: {text}")
            else:
                lines.append(f"{when} {sender}: <attached: {fname}>")
        else:
            lines.append(f"{when} {sender}: {text}")
    import_file = run_dir / "import.txt"
    import_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"stage": "import_file", "lines": len(lines), "head": lines[:4]},
                     ensure_ascii=False, indent=2))

    # 3) Direct MTProto import as B
    from telethon import functions
    cb, rb = await manager.acquire_client(acc_b)
    upload_log = []
    try:
        peer_b = await cb.get_entity(B_VIEW_PEER)

        # snapshot BEFORE (ids)
        before_list = await cb.get_messages(peer_b, limit=200)
        before_ids = {m.id for m in before_list}
        (run_dir / "target" / "target_before_ids.json").write_text(
            json.dumps({"ids": sorted(before_ids)}, ensure_ascii=False), encoding="utf-8")

        handle = await cb.upload_file(str(import_file))
        import_head = import_file.read_text(encoding="utf-8").splitlines()[0][:120]
        chk = await cb(functions.messages.CheckHistoryImportRequest(import_head=import_head))
        upload_log.append({"step": "checkHistoryImport", "result": type(chk).__name__,
                           "pm": getattr(chk, "pm", None)})

        init = await cb(functions.messages.InitHistoryImportRequest(
            peer=peer_b, file=handle, media_count=len(files)))
        import_id = getattr(init, "id", None)
        upload_log.append({"step": "initHistoryImport", "import_id": import_id})

        for f in files:
            fh = await cb.upload_file(f["path"])
            is_photo = (f["mime"] or "").startswith("image/") and f["mime"] != "image/webp"
            if is_photo:
                media = __import__("telethon", fromlist=["types"]).types.InputMediaUploadedPhoto(file=fh)
            else:
                from telethon import types as tl
                attrs = [tl.DocumentAttributeFilename(f["file_name"])]
                # Rich attributes from the source manifest — improves target
                # semantics (audio performer/title/duration, video dims, etc.)
                mm = next((x for x in media_manifest
                           if x["source_message_id"] == f["source_message_id"]), {})
                for a in mm.get("attrs", []):
                    ctor = a.get("ctor")
                    try:
                        if ctor == "DocumentAttributeAudio":
                            attrs.append(tl.DocumentAttributeAudio(
                                voice=(a.get("voice") == "True"),
                                duration=int(float(a.get("duration", 0))),
                                performer=a.get("performer"),
                                title=a.get("title")))
                        elif ctor == "DocumentAttributeVideo":
                            attrs.append(tl.DocumentAttributeVideo(
                                duration=int(float(a.get("duration", 0))),
                                w=int(a.get("w", 0)) or None,
                                h=int(a.get("h", 0)) or None,
                                round_message=(a.get("round_message") == "True")))
                        elif ctor == "DocumentAttributeAnimated":
                            attrs.append(tl.DocumentAttributeAnimated())
                    except Exception:  # noqa: BLE001
                        continue
                media = tl.InputMediaUploadedDocument(
                    file=fh,
                    mime_type=f["mime"] or "application/octet-stream",
                    attributes=attrs,
                )
            res = await cb(functions.messages.UploadImportedMediaRequest(
                peer=peer_b, import_id=import_id, file_name=f["file_name"], media=media))
            upload_log.append({"step": "uploadImportedMedia",
                               "source_message_id": f["source_message_id"],
                               "file_name": f["file_name"],
                               "input_media": type(media).__name__,
                               "returned": type(res).__name__})

        start = await cb(functions.messages.StartHistoryImportRequest(
            peer=peer_b, import_id=import_id))
        upload_log.append({"step": "startHistoryImport", "result": type(start).__name__,
                           "ok": getattr(start, "ok", None)})
        (run_dir / "import_protocol_log.json").write_text(
            json.dumps(upload_log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"stage": "import", "log": upload_log}, ensure_ascii=False, indent=2))
    finally:
        await rb()


async def stage_verify(run_dir: Path, sample_times=(0, 30, 60, 180, 300)):
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))

    before = json.loads((run_dir / "target" / "target_before_ids.json").read_text())
    before_ids = set(before["ids"])

    samples = {}
    cb, rb = await manager.acquire_client(acc_b)
    try:
        peer_b = await cb.get_entity(B_VIEW_PEER)
        for i, delay in enumerate(sample_times):
            if i > 0:
                await asyncio.sleep(sample_times[i] - sample_times[i - 1])
            after_list = await cb.get_messages(peer_b, limit=200)
            new = [m for m in after_list if m.id not in before_ids]
            recs = []
            for m in sorted(new, key=lambda x: x.id):
                fwd = getattr(m, "fwd_from", None)
                med = m.media
                attrs = []
                doc = getattr(med, "document", None) if med else None
                if doc:
                    for a in getattr(doc, "attributes", None) or []:
                        attrs.append(type(a).__name__)
                recs.append({
                    "target_id": m.id,
                    "message_date": _iso(m.date),
                    "fwd_from_date": _iso(getattr(fwd, "date", None)) if fwd else None,
                    "imported": bool(getattr(fwd, "imported", False)) if fwd else False,
                    "fwd_from_name": getattr(fwd, "from_name", None) if fwd else None,
                    "text": (m.message or "")[:40],
                    "media_ctor": type(med).__name__ if med else None,
                    "media_attrs": attrs,
                    "mime": getattr(doc, "mime_type", None) if doc else None,
                })
            samples[f"T+{delay}"] = {"new_count": len(recs), "records": recs}
            print(f"[T+{delay}] new={len(recs)}")
    finally:
        await rb()
    (run_dir / "target" / "target_after_samples.json").write_text(
        json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "verify_samples", "sample_times": sample_times}, ensure_ascii=False))
    return samples


async def stage_react(run_dir: Path):
    """Reconstruct reactions with STRICT identity: A's reactions via A's
    session, B's reactions via B's session. Never cross identities."""
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    manager = SessionManager(settings=settings, redis=redis)
    async with async_session_factory() as db:
        from sqlalchemy import select
        acc_a = await db.scalar(select(TelegramSession).where(TelegramSession.id == A_SESSION_ID))
        acc_b = await db.scalar(select(TelegramSession).where(TelegramSession.id == B_SESSION_ID))

    voters = json.loads((run_dir / "source" / "source_reaction_voters.json").read_text())
    src_msgs = json.loads((run_dir / "source" / "source_messages.json").read_text())
    samples = json.loads((run_dir / "target" / "target_after_samples.json").read_text())
    target_recs = samples["T+300"]["records"]

    # Build source->target mapping by (date, text) — deterministic.
    def norm(t: str) -> str:
        return " ".join((t or "").split())[:40]

    def _iso_key(m) -> str:
        date = (m.get("date") or m.get("message_date") or "")[:16]
        text = norm(m.get("text", ""))
        return f"{date}|{text}"

    tgt_by_key = {_iso_key(r): r for r in target_recs}

    mapping = {}  # source_message_id -> target_id
    for m in src_msgs:
        key = _iso_key(m)
        tgt = tgt_by_key.get(key)
        if tgt:
            mapping[m["source_message_id"]] = tgt["target_id"]

    # Group reactors: A = 165649921 (session 1), B = 7768075024 (session 3)
    from telethon import functions
    from telethon import types as tl

    results = []
    for v in voters:
        src_id = v["message_id"]
        tgt_id = mapping.get(src_id)
        reactor = v.get("reactor_peer_id")
        if tgt_id is None:
            results.append({"source_message_id": src_id, "reactor": reactor,
                            "emoji": v.get("emoji"), "status": "NO_TARGET_MAPPING"})
            continue
        if v.get("error"):
            results.append({"source_message_id": src_id, "reactor": reactor,
                            "status": f"SOURCE_ERROR_{v['error']}"})
            continue
        if reactor == 165649921:
            client, release = await manager.acquire_client(acc_a)
            who = "A"
        elif reactor == 7768075024:
            client, release = await manager.acquire_client(acc_b)
            who = "B"
        else:
            results.append({"source_message_id": src_id, "reactor": reactor,
                            "emoji": v.get("emoji"), "status": "UNKNOWN_REACTOR"})
            continue
        try:
            peer = await client.get_entity(B_VIEW_PEER if who == "B" else A_VIEW_PEER)
            # Message IDs are per-participant in private chats; visible dates
            # also differ per side. Match by TEXT (newest-first so the latest
            # import block wins over older duplicate copies).
            view_tid = None
            src_rec = next((s for s in src_msgs if s["source_message_id"] == src_id), None)
            if src_rec is None:
                results.append({"source_message_id": src_id, "reactor": reactor,
                                "emoji": v.get("emoji"), "status": "SOURCE_NOT_IN_SNAPSHOT"})
            else:
                want = norm(src_rec.get("text", ""))
                view_msgs = await client.get_messages(peer, limit=80)
                # Multiple import copies can exist in this view; pick the
                # NEWEST-dated copy (the latest import block, which is the one
                # the target sees). Fall back to the first text match.
                matches = [vm for vm in view_msgs if norm(vm.message or "") == want]
                if matches:
                    view_tid = max(matches, key=lambda vm: vm.date).id
            if view_tid is None:
                results.append({"source_message_id": src_id, "reactor": reactor,
                                "emoji": v.get("emoji"), "status": "TARGET_NOT_IN_VIEW"})
            else:
                reaction = [tl.ReactionEmoji(emoticon=v["emoji"])]
                await client(functions.messages.SendReactionRequest(
                    peer=peer, msg_id=view_tid, reaction=reaction))
                results.append({"source_message_id": src_id, "target_id": tgt_id,
                                "view_target_id": view_tid,
                                "reactor": reactor, "reacted_as": who, "emoji": v["emoji"],
                                "status": "RECONSTRUCTED_AFTER_IMPORT"})
        except Exception as exc:  # noqa: BLE001
            results.append({"source_message_id": src_id, "target_id": tgt_id,
                            "reactor": reactor, "reacted_as": who, "emoji": v.get("emoji"),
                            "status": f"FAILED: {type(exc).__name__}"})
        finally:
            await release()

    (run_dir / "reaction_reconstruction.json").write_text(
        json.dumps({"mapping": mapping, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({"stage": "react", "mapping": mapping, "results": results},
                     ensure_ascii=False, indent=2))


async def main(run_id: str, stage: str):
    run_dir = Path("/data/fidelity/test_runs") / run_id
    if stage == "voters":
        await stage_voters(run_dir)
    elif stage == "import":
        await stage_import(run_dir)
    elif stage == "verify":
        await stage_verify(run_dir)
    elif stage == "react":
        await stage_react(run_dir)
    else:
        print("unknown stage")
        return 2
    return 0


if __name__ == "__main__":
    rid, stg = sys.argv[1], sys.argv[2]
    raise SystemExit(asyncio.run(main(rid, stg)))
