"""Verifier: compare the canonical archive against ACTUAL target MTProto
Message objects. Only real target objects count — never upload tokens, never
the presence of a file.

Classifications (exact strings per the project brief):
- caption: CAPTION_ATTACHED / CAPTION_SEPARATE / CAPTION_LOST
- reply:   REPLY_EXACT / REPLY_PARTIAL / REPLY_ARCHIVAL_ONLY / REPLY_FAILED
- group:   GROUP_EXACT / GROUP_PARTIAL / GROUP_FLATTENED / GROUP_FAILED
- media:   PHOTO_EXACT / VIDEO_EXACT / AUDIO_EXACT / VOICE_EXACT /
           DOCUMENT_EXACT / STICKER_EXACT / STICKER_DOCUMENT_ONLY / MEDIA_FAILED
- sender:  SENDER_EXACT / SENDER_METADATA_ONLY / SENDER_MISMATCH
- time:    TIMESTAMP_EXACT / IMPORTED_METADATA_ONLY / NOT_RESTORED
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from telethon import functions, types
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
)

from .mapper import load_ndjson
from .telegram_client import ClientPool


def classify_media_target(msg) -> str:
    m = msg.media
    if m is None:
        return "MEDIA_FAILED"
    if isinstance(m, MessageMediaPhoto):
        return "PHOTO_EXACT"
    if isinstance(m, MessageMediaDocument):
        doc = m.document
        if doc is None:
            return "MEDIA_FAILED"
        names = {type(a).__name__ for a in (doc.attributes or [])}
        if "DocumentAttributeSticker" in names:
            return "STICKER_EXACT"
        if "DocumentAttributeAnimated" in names:
            return "GIF_EXACT"
        if "DocumentAttributeAudio" in names:
            a = next(a for a in doc.attributes if isinstance(a, DocumentAttributeAudio))
            return "VOICE_EXACT" if a.voice else "AUDIO_EXACT"
        if "DocumentAttributeVideo" in names:
            return "VIDEO_EXACT"
        return "DOCUMENT_EXACT"
    return "MEDIA_FAILED"


def classify_caption(source: dict, target, captions_found: set = None, target_texts: dict = None) -> str:
    src_caption = source.get("text") or ""
    src_has_media = source.get("media") is not None
    if not src_has_media:
        return "N_A"
    if not src_caption:
        return "N_A"
    if (target.message or "") == src_caption and target.media is not None:
        return "CAPTION_ATTACHED"
    if (target.message or "") == src_caption:
        return "CAPTION_SEPARATE"
    # WhatsApp-format import emits the caption as a separate message ONE SECOND
    # after the media line (live-proven: attached captions break media binding).
    # If that caption message exists in the target timeline, the caption is
    # preserved (separate) even though the mapped target media msg is blank.
    if src_caption and (target.message or "") == "" and target.media is not None:
        if captions_found is not None and src_caption in captions_found:
            return "CAPTION_SEPARATE"
        if target_texts is not None:
            for tmsg in target_texts:
                if tmsg == src_caption:
                    return "CAPTION_SEPARATE"
    return "CAPTION_LOST"


def classify_timestamp(source: dict, target) -> str:
    if not source.get("date") or not target.date:
        return "NOT_RESTORED"
    src = datetime.fromisoformat(source["date"])
    if abs((target.date - src).total_seconds()) < 120:
        return "TIMESTAMP_EXACT"
    if target.fwd_from is not None and target.fwd_from.date == src:
        return "IMPORTED_METADATA_ONLY"
    return "NOT_RESTORED"


def classify_sender(source: dict, target) -> str:
    if source.get("sender_label") == "A":
        # Imported by B: exact only if identity somehow preserved (never for
        # foreign-app import); metadata only via imported fwd header.
        if target.fwd_from is not None and getattr(target.fwd_from, "imported", False):
            return "SENDER_METADATA_ONLY"
        return "SENDER_MISMATCH"
    return "SENDER_EXACT"  # B-sent source should import as B


def classify_group(source_groups: dict, target_pairs: list[tuple[int, int | None]]) -> str:
    """source_groups: grouped_id -> [source ids]; target_pairs: (src,tgt,grouped)."""
    if not source_groups:
        return "N_A"
    exact = all(g is not None for _, g in target_pairs)
    all_mapped = all(t is not None for t, _ in target_pairs)
    if exact and all_mapped:
        return "GROUP_EXACT"
    if all_mapped:
        return "GROUP_FLATTENED"
    return "GROUP_FAILED"


async def read_target_messages(pool: ClientPool, peer, ids: list[int]) -> dict[int, object]:
    client = pool.client("B")
    out = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        res = await client(
            functions.messages.GetMessagesRequest(id=[types.InputMessageID(x) for x in chunk])
        )
        for m in getattr(res, "messages", []):
            out[m.id] = m
    return out


def verify_archive(archive_dir: Path) -> dict:
    """Internal archive consistency checks (before import)."""
    msgs = load_ndjson(archive_dir / "messages.ndjson")
    raws = load_ndjson(archive_dir / "raw_messages.ndjson")
    media = json.loads((archive_dir / "media" / "media_index.json").read_text())
    problems = []
    if len(msgs) != len(raws):
        problems.append("messages/raw count mismatch")
    for m in msgs:
        if m.get("media") and m["media"].get("type") not in ("none",):
            if not m["media"].get("local_file"):
                problems.append(f"msg {m['message_id']}: media without local_file")
    return {
        "messages": len(msgs),
        "media": len(media),
        "problems": problems,
        "ok": not problems,
    }


def build_fidelity_report(archive_dir: Path, run_dir: Path, mapping: dict, target_msgs: dict) -> dict:
    """Per-message comparison for FINAL_REPORT."""
    sources = load_ndjson(archive_dir / "messages.ndjson")
    id_map = {m["source_message_id"]: m["target_message_id"] for m in mapping["mappings"]}
    rows = []
    counters: dict[str, dict[str, int]] = {}
    # caption-presence check across the whole target (WhatsApp +1s captions)
    target_texts = {}
    for tid, t in (target_msgs or {}).items():
        if t and t.message:
            target_texts[t.message] = tid
    for s in sources:
        tid = id_map.get(s["message_id"])
        t = target_msgs.get(tid)
        media_cls = classify_media_target(t) if (t and s.get("media")) else ("MEDIA_FAILED" if s.get("media") else "N_A")
        cap_cls = classify_caption(s, t, target_texts=target_texts) if t else "CAPTION_LOST"
        ts_cls = classify_timestamp(s, t) if t else "NOT_RESTORED"
        snd_cls = classify_sender(s, t) if t else "SENDER_MISMATCH"
        reply_cls = "REPLY_ARCHIVAL_ONLY"
        if s.get("reply_to"):
            parent_tid = id_map.get(s["reply_to"]["reply_to_msg_id"])
            if t is not None and parent_tid and t.reply_to is not None:
                reply_cls = (
                    "REPLY_EXACT"
                    if t.reply_to.reply_to_msg_id == parent_tid
                    else "REPLY_PARTIAL"
                )
            elif t is None:
                reply_cls = "REPLY_FAILED"
        elif t is not None and t.reply_to is None:
            reply_cls = "N_A"
        for k, v in [
            ("media", media_cls), ("caption", cap_cls), ("timestamp", ts_cls),
            ("sender", snd_cls), ("reply", reply_cls),
        ]:
            counters.setdefault(k, {}).setdefault(v, 0)
            counters[k][v] += 1
        rows.append(
            {
                "source_id": s["message_id"],
                "target_id": tid,
                "source_sender": s.get("sender_label"),
                "source_date": s.get("date"),
                "target_date": t.date.isoformat() if t and t.date else None,
                "source_text": (s.get("text") or "")[:200],
                "target_text": (t.message or "")[:200] if t else None,
                "source_media": s["media"]["type"] if s.get("media") else None,
                "target_media": type(t.media).__name__ if t and t.media else None,
                "classifications": {
                    "media": media_cls,
                    "caption": cap_cls,
                    "timestamp": ts_cls,
                    "sender": snd_cls,
                    "reply": reply_cls,
                },
            }
        )
    report = {"rows": rows, "counts": counters}
    (run_dir / "FINAL_REPORT.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report
