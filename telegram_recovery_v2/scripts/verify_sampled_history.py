#!/usr/bin/env python3
"""Verify + report phase for the sampled-history recovery test.

Run AFTER scripts/sample_historical_recovery.py (--confirm-recovery-test) and
after the materialization delay (~3-5 min). Reads the real A<->B target
through MTProto, maps source->target, reconstructs reactions per reactor
session, verifies each capability and writes:

  test_runs/<run_id>/FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.json
  test_runs/<run_id>/FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.html
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.config import load_config
from recovery.importer import _ext_for
from recovery.mapper import map_source_to_target
from recovery.telegram_client import ClientPool
from telethon.tl import functions, types as T

SOURCE_ACCOUNT, TARGET_ACCOUNT = "A", "B"
A_UID = None  # filled at runtime


def classify_timestamp(source_date: str, t_date, fwd_date) -> str:
    from datetime import datetime

    src = datetime.fromisoformat(source_date)
    if t_date is not None and abs((t_date - src).total_seconds()) < 120:
        return "TIMESTAMP_EXACT"
    if fwd_date is not None and abs((fwd_date - src).total_seconds()) < 120:
        return "IMPORTED_METADATA_ONLY"
    return "NOT_RESTORED"


def classify_media_target(m) -> str:
    if m is None or m.media is None:
        return "MEDIA_MISSING"
    from telethon.tl import types as T

    if isinstance(m.media, T.MessageMediaPhoto):
        return "PHOTO_EXACT"
    if isinstance(m.media, T.MessageMediaDocument):
        docs = m.media.document.attributes if m.media.document else []
        names = [type(a).__name__ for a in docs]
        if "DocumentAttributeSticker" in names:
            return "STICKER_EXACT"
        if "DocumentAttributeAnimated" in names:
            return "ANIMATION_EXACT"
        if any(getattr(a, "voice", False) for a in docs):
            return "VOICE_EXACT"
        if "DocumentAttributeAudio" in names:
            return "AUDIO_EXACT"
        if "DocumentAttributeVideo" in names:
            return "VIDEO_EXACT"
        if "DocumentAttributeFilename" in names:
            return "DOCUMENT_EXACT"
        return "DOCUMENT_EXACT"
    return "MEDIA_MISSING"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--peer-b", type=int, required=True, help="B's own user id")
    args = ap.parse_args()

    cfg = load_config(Path(__file__).resolve().parent.parent)
    run_dir = cfg.runs_dir / args.run_id

    async with ClientPool(cfg) as pool:
        cb = pool.client(TARGET_ACCOUNT)
        ca = pool.client(SOURCE_ACCOUNT)
        peer_b = await cb.get_input_entity(pool.tg_id(SOURCE_ACCOUNT))
        peer_a = await ca.get_input_entity(pool.tg_id(TARGET_ACCOUNT))

        # ---- target read (whole A<->B, only new messages belong to this run)
        target = []
        async for m in cb.iter_messages(peer_b, limit=None):
            target.append(
                {
                    "message_id": m.id,
                    "date": m.date.isoformat() if m.date else None,
                    "text": m.message or "",
                    "media": {"type": type(m.media).__name__} if m.media else None,
                    "imported": bool(m.fwd_from and getattr(m.fwd_from, "imported", False)),
                    "fwd_date": m.fwd_from.date.isoformat() if m.fwd_from and m.fwd_from.date else None,
                    "sender_id": getattr(m.from_id, "user_id", None) if m.from_id else None,
                    "grouped_id": int(m.grouped_id) if m.grouped_id else None,
                    "reply_to": m.reply_to.reply_to_msg_id if m.reply_to else None,
                }
            )
        # map: delta = all imported messages (imported flag) — the sample run
        # was the only importer into A<->B after clear
        mapping = map_source_to_target(
            run_dir / "archive", run_dir / "target_after.json" if False else _write_target(target, run_dir),
            set(), run_dir,
        )
        id_map = {m["source_message_id"]: m["target_message_id"] for m in mapping["mappings"]}

        # resolve target ids per message
        tgt_by_id = {t["message_id"]: t for t in target}
        # read real MTProto objects for mapped targets
        tgt_ids = list(id_map.values())
        real = {}
        for i in range(0, len(tgt_ids), 50):
            chunk = [t for t in tgt_ids[i : i + 50] if t]
            if chunk:
                msgs = await cb.get_messages(peer_b, ids=chunk)
                for m in msgs:
                    if m:
                        real[m.id] = m

        sample = json.loads((run_dir / "source_sample_snapshot.json").read_text())
        rows = []
        for s in sample:
            sid = s["message_id"]
            tid = id_map.get(sid)
            t = real.get(tid)
            ts_cls = classify_timestamp(s["date"], t.date if t else None,
                                        t.fwd_from.date if t and t.fwd_from else None)
            media_cls = classify_media_target(t) if s.get("media") else "N_A"
            caption_cls = "CAPTION_ATTACHED" if (t and (t.message or "") == (s.get("text") or "") and t.media) else (
                "CAPTION_SEPARATE" if (t and (t.message or "") == (s.get("text") or "")) else
                "CAPTION_LOST" if s.get("media") and s.get("text") else "N_A"
            )
            sender_cls = "SENDER_METADATA_ONLY" if (t and t.fwd_from and getattr(t.fwd_from, "imported", False)) else (
                "SENDER_EXACT" if t else "SENDER_MISMATCH"
            )
            reply_cls = ("REPLY_EXACT" if (t and t.reply_to and t.reply_to.reply_to_msg_id == id_map.get((s.get("reply_to") or {}).get("reply_to_msg_id"))) else
                         "REPLY_NOT_RESTORED" if s.get("reply_to") else "N_A")
            group_cls = ("GROUP_EXACT" if (s.get("grouped_id") and t and t.grouped_id == s["grouped_id"]) else
                         "GROUP_FLATTENED" if s.get("grouped_id") else "N_A")
            rows.append({
                "source_id": sid, "target_id": tid, "source_date": s["date"], "target_date": t.date.isoformat() if t and t.date else None,
                "source_text": (s.get("text") or "")[:200], "target_text": (t.message or "")[:200] if t else None,
                "source_media": (s.get("media") or {}).get("type"), "target_media": type(t.media).__name__ if t and t.media else None,
                "sender": sender_cls, "timestamp": ts_cls, "media": media_cls, "caption": caption_cls,
                "reply": reply_cls, "group": group_cls,
                "fwd": bool(s.get("fwd_from")), "reactions": bool(s.get("reactions")),
            })

        # ---- aggregate
        from collections import Counter
        agg = {}
        for k in ("timestamp", "media", "caption", "sender", "reply", "group"):
            agg[k] = dict(Counter(r[k] for r in rows))
        years = sorted({s["date"][:4] for s in sample})
        report = {
            "run_id": args.run_id,
            "source_peer": "A<->C", "target_peer": "A<->B",
            "source_message_count": len(sample), "target_new_message_count": len(tgt_ids),
            "date_range": f"{min(s['date'] for s in sample)[:10]} -> {max(s['date'] for s in sample)[:10]}",
            "years_sampled": years,
            "aggregate": agg,
            "rows": rows,
            "feature_results": {
                "TIMESTAMP_EXACT": f"{agg['timestamp'].get('TIMESTAMP_EXACT', 0)}/{len(rows)}",
                "IMPORTED_METADATA_ONLY": f"{agg['timestamp'].get('IMPORTED_METADATA_ONLY', 0)}/{len(rows)}",
                "MEDIA": f"{sum(v for k, v in agg['media'].items() if k.endswith('_EXACT'))}/{len([r for r in rows if r['source_media']])}",
                "SENDER_METADATA_ONLY": f"{agg['sender'].get('SENDER_METADATA_ONLY', 0)}/{len(rows)}",
                "CAPTION": f"{agg['caption'].get('CAPTION_SEPARATE', 0) + agg['caption'].get('CAPTION_ATTACHED', 0)}/{len([r for r in rows if r['source_media'] and r['source_text']])}",
            },
        }
        (run_dir / "FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps({k: report[k] for k in ("source_message_count", "target_new_message_count", "date_range", "years_sampled", "aggregate", "feature_results")}, indent=2, ensure_ascii=False))
        print(f"\nreport -> {run_dir / 'FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.json'}")


def _write_target(target: list, run_dir: Path) -> Path:
    p = run_dir / "target_after.json"
    with open(p, "w", encoding="utf-8") as f:
        for t in target:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return p


if __name__ == "__main__":
    asyncio.run(main())