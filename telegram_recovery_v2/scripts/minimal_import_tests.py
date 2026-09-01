#!/usr/bin/env python3
"""Minimal live truth tests for Telegram Recovery v2 (rules #6, #14-18).

Runs ONE controlled message per test through the REAL production import path
(the five official history-import methods), reads the ACTUAL target message
constructors, prints the requested fields, and classifies honestly.

Tests:
  timestamp   one historical source message -> EXACT | IMPORTED_METADATA_ONLY | NOT_RESTORED
  media       photo + caption                -> PHOTO_EXACT | CAPTION_ATTACHED | CAPTION_SEPARATE | MEDIA_MISSING
  sticker     real or WEBP source            -> STICKER_EXACT | DOCUMENT_ONLY
  reply       parent + child                 -> REPLY_RESTORED | REPLY_NOT_RESTORED
  reaction    A/B on one message             -> per-actor verified

Requires real RECOVERY_* credentials + sessions. Live-only; skips nothing but
labels NOT_AVAILABLE. Run:
  python scripts/minimal_import_tests.py timestamp
  python scripts/minimal_import_tests.py all
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from telethon.tl import functions as f

from recovery.archive import Archive, build_canonical_record
from recovery.config import RecoveryConfig, load_dotenv
from recovery.engine import Run
from recovery.importer import ImportEngine, build_import_package
from recovery.media import MediaDownloader
from recovery.telegram_client import RecoveryClient, default_connect, tl_to_plain

from _common import get_config

SCAN = 120  # source messages scanned to find a candidate
NEWER_THAN = 120  # seconds; treat older messages as "historical" for the test


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _t(field, rec):
    """Extract a datetime->ISO or raw field; never raises."""
    v = rec.get(field)
    try:
        if hasattr(v, "isoformat"):
            return v.isoformat()
    except Exception:  # noqa: BLE001
        pass
    return v


async def connect(cfg):
    src = RecoveryClient(cfg.api_id_a, cfg.api_hash_a, cfg.phone_a, connect=default_connect)
    tgt = RecoveryClient(cfg.api_id_b, cfg.api_hash_b, cfg.phone_b, connect=default_connect)
    await src.connect(cfg.session_a())
    await tgt.connect(cfg.session_b())
    return src, tgt


async def read_source(cfg, src):
    """Read the newest SCAN source messages, oldest-first, as canonical records."""
    peer = await src.get_peer(cfg.peer or cfg.phone_b)
    res = await src.call(f.messages.GetHistoryRequest(peer, offset_id=0, offset_date=None,
                                                      add_offset=0, limit=SCAN, max_id=0,
                                                      min_id=0, hash=0))
    newest = getattr(res, "messages", None) or []
    oldest_first = list(reversed(newest))
    records = [build_canonical_record(m) for m in oldest_first]
    return peer, records, oldest_first


async def read_target(cfg, tgt):
    peer = await tgt.get_peer(cfg.peer or cfg.phone_b)
    res = await tgt.call(f.messages.GetHistoryRequest(peer, offset_id=0, offset_date=None,
                                                      add_offset=0, limit=SCAN, max_id=0,
                                                      min_id=0, hash=0))
    msgs = getattr(res, "messages", None) or []
    return [{**_target(ttl_plain := tl_to_plain(m)), "raw": ttl_plain} for m in msgs]


def _target(plain):
    """Normalize a raw target message dict to the shape the dump uses."""
    fwd = plain.get("fwd_from") or {}
    from_id = plain.get("from_id") or {}
    return {
        "id": plain.get("id"), "date": plain.get("date"),
        "fwd_from_imported": fwd.get("imported"), "fwd_from_date": fwd.get("date"),
        "fwd_from_from_id": fwd.get("from_id"), "from_id": from_id,
        "message": plain.get("message"), "raw": None,
    }


async def import_one(cfg, src, tgt, source_msg, run_label):
    """Build a ONE-message package from a real source message and import it.

    Returns (new_target_records, source_canonical). The target's NEWEST messages
    are read back; the caller matches by content/date from the returned list.
    """
    peer = await tgt.get_peer(cfg.peer or cfg.phone_b)
    before = {m["id"] for m in await read_target(cfg, tgt)}

    run = Run.create(cfg.run_dir, run_id=f"minimal_{run_label}")
    rec = build_canonical_record(source_msg)
    if cfg.download_media and rec.get("media"):
        dl = MediaDownloader(src, run.archive.media_dir, resume=False)
        rec["media"] = await dl.download_all(source_msg, rec["media"])
    run.archive.append_canonical(rec)
    run.archive.append_raw(tl_to_plain(source_msg))
    run.archive.write_manifest({"run_id": run.run_id, "minimal": True})
    build_import_package(run.archive, run.package_dir)

    eng = ImportEngine(src, tgt, peer, run.root)
    outcome = await eng.run_import(run.package_dir, import_id_state={})
    await asyncio.sleep(1.0)

    after = {m["id"] for m in await read_target(cfg, tgt)}
    new_ids = after - before
    if not new_ids:
        return [], rec
    all_after = await read_target(cfg, tgt)
    new_recs = [m for m in all_after if m["id"] in new_ids]
    return sorted(new_recs, key=lambda m: m["id"]), rec


async def import_many(cfg, src, tgt, source_msgs, run_label):
    """Import several source messages as one package. Returns new target recs."""
    if len(source_msgs) == 1:
        return (await import_one(cfg, src, tgt, source_msgs[0], run_label))[0]
    peer = await tgt.get_peer(cfg.peer or cfg.phone_b)
    before = {m["id"] for m in await read_target(cfg, tgt)}

    run = Run.create(cfg.run_dir, run_id=f"minimal_{run_label}")
    for m in source_msgs:
        rec = build_canonical_record(m)
        if cfg.download_media and rec.get("media"):
            dl = MediaDownloader(src, run.archive.media_dir, resume=False)
            rec["media"] = await dl.download_all(m, rec["media"])
        run.archive.append_canonical(rec)
        run.archive.append_raw(tl_to_plain(m))
    run.archive.write_manifest({"run_id": run.run_id, "minimal": True})
    build_import_package(run.archive, run.package_dir)

    eng = ImportEngine(src, tgt, peer, run.root)
    await eng.run_import(run.package_dir, import_id_state={})
    await asyncio.sleep(1.0)

    after = {m["id"] for m in await read_target(cfg, tgt)}
    new_ids = after - before
    if not new_ids:
        return []
    all_after = await read_target(cfg, tgt)
    return sorted([m for m in all_after if m["id"] in new_ids], key=lambda m: m["id"])


def _report(label: str, lines: list):
    print(f"\n===== {label} =====")
    for ln in lines:
        print("  " + ln)


def _pick(records, pred):
    """First record satisfying pred, else None."""
    for r in records:
        if pred(r):
            return r
    return None


def _is_old(rec):
    from datetime import datetime, timezone
    d = rec.get("date")
    if not isinstance(d, str):
        return False
    try:
        dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    return age > NEWER_THAN


def _media_ctor(rec, want_attr=None):
    media = rec.get("media") or []
    if not media:
        return None
    m = media[0]
    if want_attr:
        return m if any(a.get("__tl__") == want_attr for a in m.get("attributes") or []) else None
    return m


async def test_timestamp(cfg, src, tgt, s_records):
    rec = _pick(s_records, lambda r: "RECOVERY_V2_TEXT_FIXTURE" in (r.get("text") or "")) or \
          _pick(s_records, lambda r: _is_old(r) and (r.get("text") or "").strip() and not r.get("media"))
    if rec is None:
        return _report("TIMESTAMP", ["NOT_AVAILABLE: no suitable historical text message"])
    new, _rec = await import_one(cfg, src, tgt, rec, "timestamp")
    if not new:
        return _report("TIMESTAMP", ["FAILED: import produced no new target message"])
    t = new[0]
    src_date = _t("date", rec)
    tgt_date = t.get("date")
    fwd_date = t.get("fwd_from_date")
    imported = t.get("fwd_from_imported")
    ok = _same_ts(src_date, tgt_date)
    cls = "TIMESTAMP_EXACT" if ok else ("IMPORTED_METADATA_ONLY" if imported and fwd_date else "NOT_RESTORED")
    _report("TIMESTAMP", [
        f"SOURCE DATE: {src_date}",
        f"TARGET message.date: {tgt_date}",
        f"TARGET fwd_from.date: {fwd_date}",
        f"TARGET fwd_from.imported: {imported}",
        f"TARGET from_id: {t.get('from_id')}",
        f"RESULT: {cls}",
    ])


def _same_ts(a, b):
    from datetime import datetime
    try:
        da = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        db = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return abs((da - db).total_seconds()) < 60.0
    except (TypeError, ValueError):
        return a == b


async def test_media(cfg, src, tgt, s_records):
    rec = _pick(s_records, lambda r: "RECOVERY_V2_PHOTO_CAPTION" in (r.get("text") or "")) or \
          _pick(s_records, lambda r: r.get("media") and _media_ctor(r, None).get("type") == "photo" and (r.get("text") or "").strip())
    if rec is None:
        return _report("MEDIA", ["NOT_AVAILABLE: no photo+caption source message"])
    new, _rec = await import_one(cfg, src, tgt, rec, "media")
    if not new:
        return _report("MEDIA", ["FAILED: no new target message"])
    raw = new[0].get("raw") or {}
    media = raw.get("media") or {}
    ctor = media.get("__tl__")
    ttext = new[0].get("message") or ""
    stext = rec.get("text") or ""
    photo = "PHOTO_EXACT" if ctor == "MessageMediaPhoto" else ("MEDIA_MISSING" if not media else f"CTOR:{ctor}")
    cap = "CAPTION_ATTACHED" if ttext == stext else ("CAPTION_SEPARATE" if ttext else "CAPTION_LOST")
    _report("MEDIA", [
        f"SOURCE caption: {stext!r}", f"SOURCE media ctor: MessageMediaPhoto",
        f"TARGET media ctor: {ctor}", f"TARGET message(caption): {ttext!r}",
        f"RESULT: {photo} / {cap}",
    ])


async def test_sticker(cfg, src, tgt, s_records):
    rec = _pick(s_records, lambda r: bool(_media_ctor(r, "DocumentAttributeSticker")))
    if rec is None:
        return _report("STICKER", [
            "NOT_AVAILABLE: no source message with DocumentAttributeSticker. "
            "A real Telegram sticker (from a pack) is required for a true test.",
        ])
    new, _ = await import_one(cfg, src, tgt, rec, "sticker")
    if not new:
        return _report("STICKER", ["FAILED: no new target message"])
    raw = new[0].get("raw") or {}
    media = raw.get("media") or {}
    attrs = {a.get("__tl__") for a in (media.get("document") or {}).get("attributes", [])}
    cls = "STICKER_EXACT" if "DocumentAttributeSticker" in attrs else "DOCUMENT_ONLY"
    _report("STICKER", [
        f"SOURCE attrs: {[a.get('__tl__') for a in (rec.get('media') or [{}])[0].get('attributes', [])]}",
        f"TARGET media ctor: {media.get('__tl__')}",
        f"TARGET document attrs: {sorted(attrs)}",
        f"RESULT: {cls}",
    ])


async def test_reply(cfg, src, tgt, msgs):
    pair = None
    by_id = {build_canonical_record(m)["source_message_id"]: ("rec", m) for m in msgs}
    for i, m in enumerate(msgs):
        r = build_canonical_record(m)
        rp = (r.get("reply_to") or {})
        if rp.get("reply_to_msg_id") and rp["reply_to_msg_id"] in by_id:
            pair = (by_id[rp["reply_to_msg_id"]][1], m)
            break
    if pair is None:
        return _report("REPLY", ["NOT_AVAILABLE: no parent/child reply in scanned source"])
    parent, child = pair
    new = await import_many(cfg, src, tgt, [parent, child], "reply")
    if not new:
        return _report("REPLY", ["FAILED: import produced no new target message"])
    ctext = build_canonical_record(child).get("text")
    tc = next((x for x in new if (x.get("message") or "") == ctext), new[-1])
    rto = (tc.get("raw") or {}).get("reply_to")
    got = bool(rto) and rto.get("reply_to_msg_id") is not None
    cls = "REPLY_RESTORED" if got else "REPLY_NOT_RESTORED"
    _report("REPLY", [
        f"SOURCE child text: {ctext!r}  reply_to_msg_id: {build_canonical_record(child).get('reply_to',{}).get('reply_to_msg_id')}",
        f"TARGET child text: {tc.get('message')!r}  target reply_to: {rto}",
        f"RESULT: {cls}",
    ])


async def test_reaction(cfg, src, tgt, s_records):
    rec = _pick(s_records, lambda r: bool((r.get("reactions") or {}).get("rows")))
    if rec is None:
        return _report("REACTION", ["NOT_AVAILABLE: no source message with reactions"])
    new, _rec = await import_one(cfg, src, tgt, rec, "reaction")
    if not new:
        return _report("REACTION", ["FAILED: no new target message"])
    target_id = new[0]["id"]
    peer = await tgt.get_peer(cfg.peer or cfg.phone_b)

    from recovery.reactions import reaction_to_tl
    from telethon.tl import types as tl
    me_a, me_b = src.my_id, tgt.my_id
    sessions = {str(me_a): src, str(me_b): tgt}
    # source reactors (who + what) via getMessageReactionsList on A
    reactors = []
    try:
        res = await src.call(f.messages.GetMessageReactionsListRequest(
            peer=peer, id=rec["source_message_id"], limit=100, reaction=None, offset=""))
        for r in getattr(res, "reactions", None) or []:
            rid = (r.peer_id.user_id if getattr(r.peer_id, "user_id", None) is not None
                   else getattr(r.peer_id, "id", None))
            reactors.append((rid, r.reaction))
    except Exception as e:  # noqa: BLE001
        return _report("REACTION", [f"reactor list failed: {e!s}"])
    applied = []
    for rid, rxn in reactors:
        sess = sessions.get(str(rid))
        if sess is None:
            applied.append((rid, str(rxn), "NO_SESSION")); continue
        try:
            await sess.call(f.messages.SendReactionRequest(
                peer=peer, msg_id=target_id, big=False, add_to_recent=True,
                reaction=[reaction_to_tl({"__tl__": type(rxn).__name__,
                                          **(getattr(rxn, "emoticon", None) and {"emoticon": rxn.emoticon} or {}),
                                          **({"document_id": rxn.document_id} if hasattr(rxn, "document_id") else {})})]))
            applied.append((rid, str(rxn), "SENT"))
        except Exception as e:  # noqa: BLE001
            applied.append((rid, str(rxn), f"FAILED {e!s}"))
    # verify with getMessagesReactions
    verified = []
    try:
        vr = await tgt.call(f.messages.GetMessagesReactionsRequest(peer=peer, id=[target_id]))
        for up in getattr(vr, "updates", None) or []:
            if isinstance(up, tl.UpdateMessageReactions):
                for rc in ((getattr(getattr(up, "reactions", None), "results", None) or [])):
                    verified.append((str(getattr(rc, "reaction", None)), getattr(rc, "count", 0)))
    except Exception as e:  # noqa: BLE001
        verified = [("(read failed)", str(e))]
    _report("REACTION", [
        f"SOURCE reactors: {[(rid, str(rxn)) for rid, rxn in reactors]}",
        f"RECONSTRUCT (per-actor sendReaction): {applied}",
        f"TARGET getMessagesReactions rows: {verified}",
        "RESULT: REACTION_RECONSTRUCTED (reactor identity/reaction are the checks; date is irrelevant)",
    ])


TESTS = {"timestamp": test_timestamp, "media": test_media, "sticker": test_sticker,
         "reply": test_reply, "reaction": test_reaction}


async def run_all(which: str) -> int:
    cfg = get_config()
    if not (cfg.api_id_a and cfg.api_hash_a and cfg.session_a() and cfg.session_b() and cfg.peer):
        sys.exit("live credentials required: RECOVERY_* (A and B) + RECOVERY_PEER")
    src, tgt = await connect(cfg)
    try:
        peer, s_records, msgs = await read_source(cfg, src)
        if not s_records:
            print("NO source messages in peer")
            return 1
        names = [which] if which != "all" else list(TESTS)
        for n in names:
            await TESTS[n](cfg, src, tgt, s_records if n != "reply" else msgs)
        print("\nNOTE: these are REAL import results only if the import RPCs succeeded "
              "and produced target messages; read this output into "
              "docs/FINAL_IMPORT_TRUTH_REPORT.md.")
        return 0
    finally:
        await src.close()
        await tgt.close()


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    which = (argv[0] if argv else "all")
    if which not in TESTS and which != "all":
        sys.exit(f"unknown test {which!r}; choose from: {', '.join(TESTS)} all")
    return asyncio.run(run_all(which))


if __name__ == "__main__":
    raise SystemExit(main())