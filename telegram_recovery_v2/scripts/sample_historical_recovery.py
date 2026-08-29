#!/usr/bin/env python3
"""REAL-HISTORY SAMPLING TEST — source A<->C, target A<->B (fast, phased).

Phases (each resumable/reusable, source chat READ-ONLY):
  discover  - lightweight comprehensive catalog (concurrent getHistory,
              minimal fields, checkpointed; NO media/reactions/raw bodies)
  sample    - stratified year-bucket sampling from the catalog; lazy FULL
              fetch ONLY for the ~20 candidates + reply parents + complete
              album groups; verify vs live; build sample artifacts + package
              + roundtrip check; generate SAMPLE_PREVIEW.html
  import    - B-side clear (just_clear, revoke=False) + official import API
  (verify)  - separate script: scripts/verify_sampled_history.py

Usage:
  python scripts/sample_historical_recovery.py --phase discover
  python scripts/sample_historical_recovery.py --phase sample --dry-run
  python scripts/sample_historical_recovery.py --phase sample --confirm-recovery-test
  python scripts/sample_historical_recovery.py --phase import --run-id <ID>
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.archive import ArchiveReader, ArchiveWriter
from recovery.catalog import build_catalog, media_type_of
from recovery.config import load_config
from recovery.importer import _ext_for, build_import_file
from recovery.sampler import message_types, run_seed, select_sample
from recovery.telegram_client import ClientPool
from telethon.tl import functions

SOURCE_PHONE = "+989353114546"    # contact C (in A's session) — READ ONLY
TARGET_PHONE = "+5511991966422"   # account B — the recovery target
SOURCE_ACCOUNT = "A"
TARGET_ACCOUNT = "B"


def package_hash(run_dir: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.name not in ("package_hash.json", "source_catalog.ndjson", "source_catalog_checkpoint.json"):
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _sender_of(m, own_ids: dict) -> str | None:
    fid = getattr(m.from_id, "user_id", None) if m.from_id else None
    return own_ids.get(fid)


def _load_catalog(run_dir: Path) -> list[dict]:
    rows = []
    for line in open(run_dir / "source_catalog.ndjson", encoding="utf-8"):
        if line.strip():
            rows.append(json.loads(line))
    return rows


async def phase_discover(cfg, pool: ClientPool, run_dir: Path) -> dict:
    ca = pool.client(SOURCE_ACCOUNT)
    c = await ca.get_entity(SOURCE_PHONE)
    print(f"SOURCE CHAT A<->C: peer id={c.id} type={type(c).__name__} "
          f"name={getattr(c, 'first_name', '')} {getattr(c, 'last_name', '')}", flush=True)
    meta = await build_catalog(ca, c, run_dir)
    rows = _load_catalog(run_dir)
    dates = [r["date"] for r in rows if r.get("date")]
    from collections import Counter

    years = Counter(d[:4] for d in dates)
    meta.update(
        {
            "source_account": SOURCE_ACCOUNT,
            "source_peer_id": c.id,
            "source_peer_type": type(c).__name__,
            "source_phone": SOURCE_PHONE,
            "target_account": TARGET_ACCOUNT,
            "target_phone": TARGET_PHONE,
            "message_count": len(rows),
            "oldest_message_date": min(dates) if dates else None,
            "newest_message_date": max(dates) if dates else None,
            "years": dict(sorted(years.items())),
            "media_types": dict(Counter(r.get("media_type") for r in rows)),
        }
    )
    (run_dir / "source_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"SOURCE HISTORY RANGE: {meta['oldest_message_date'][:10]} -> {meta['newest_message_date'][:10]} "
          f"({len(rows)} messages) years={dict(years)}", flush=True)
    return meta


async def phase_sample(cfg, pool, run_dir: Path, count: int, seed: int, dry_run: bool) -> dict:
    meta = json.loads((run_dir / "source_meta.json").read_text())
    catalog = _load_catalog(run_dir)
    ca = pool.client(SOURCE_ACCOUNT)
    c = await ca.get_entity(SOURCE_PHONE)

    # STRATIFIED selection on the LIGHTWEIGHT catalog (no full re-read)
    selected_ids, stats = select_sample(catalog, count, seed, year_buckets=True)
    by_id = {r["message_id"]: r for r in catalog}
    sample_rows = [by_id[i] for i in selected_ids]

    # ---- LAZY full fetch: only the ~20 candidates (+ reply parents already
    # included by the sampler) (+ complete album groups)
    full = await ca.get_messages(c, ids=selected_ids)
    full_by_id = {m.id: m for m in full if m}
    missing = [i for i in selected_ids if i not in full_by_id]
    if missing:
        raise SystemExit(f"ABORT: source ids missing live: {missing}")

    own_ids = {pool.tg_id("A"): "A", pool.tg_id("B"): "B"}
    # write sample artifacts
    sample_dir = run_dir / "sample"
    sample_dir.mkdir(exist_ok=True)
    snaps = []
    media_manifest = []
    reactions = []
    groups = {}
    for i in selected_ids:
        m = full_by_id[i]
        snaps.append(
            {
                "message_id": m.id,
                "date": m.date.isoformat() if m.date else None,
                "edit_date": m.edit_date.isoformat() if m.edit_date else None,
                "sender_id": getattr(m.from_id, "user_id", None) if m.from_id else None,
                "sender": _sender_of(m, own_ids),
                "text": m.message or "",
                "entities": [e.to_dict() for e in (m.entities or [])],
                "media_type": media_type_of(m),
                "media": m.media.to_dict() if m.media else None,
                "reply_to": (
                    {
                        "reply_to_msg_id": m.reply_to.reply_to_msg_id,
                        "top_msg_id": getattr(m.reply_to, "top_msg_id", None),
                        "quote_text": getattr(m.reply_to, "quote_text", None),
                    }
                    if m.reply_to
                    else None
                ),
                "grouped_id": int(m.grouped_id) if m.grouped_id else None,
                "fwd_from": (
                    {
                        "from_id": getattr(m.fwd_from.from_id, "user_id", None) if m.fwd_from.from_id else None,
                        "date": m.fwd_from.date.isoformat() if m.fwd_from.date else None,
                        "from_name": m.fwd_from.from_name,
                        "imported": bool(getattr(m.fwd_from, "imported", False)),
                        "channel_post": getattr(m.fwd_from, "channel_post", None),
                    }
                    if m.fwd_from
                    else None
                ),
                "reactions": (
                    [
                        {"reaction": getattr(r.reaction, "emoticon", None), "count": r.count, "chosen_order": r.chosen_order}
                        for r in (m.reactions.results or [])
                    ]
                    if m.reactions
                    else None
                ),
            }
        )
        if m.media is not None:
            media_manifest.append(
                {
                    "source_message_id": m.id,
                    "media_type": media_type_of(m),
                    "ctor": type(m.media).__name__,
                }
            )
        if m.reactions and m.reactions.results:
            reactions.append({"source_message_id": m.id, "reaction": getattr(m.reactions.results[0].reaction, "emoticon", None),
                              "count": m.reactions.results[0].count})
        if m.grouped_id:
            groups.setdefault(int(m.grouped_id), []).append(m.id)

    (sample_dir / "sample_source_messages.json").write_text(json.dumps(snaps, indent=1, ensure_ascii=False, default=str))
    (sample_dir / "sample_media_manifest.json").write_text(json.dumps(media_manifest, indent=1))
    (sample_dir / "sample_reactions.json").write_text(json.dumps(reactions, indent=1))
    (sample_dir / "sample_replies.json").write_text(
        json.dumps([{"source_message_id": s["message_id"], "reply_to_msg_id": s["reply_to"]["reply_to_msg_id"]}
                    for s in snaps if s.get("reply_to")], indent=1))
    (sample_dir / "sample_groups.json").write_text(json.dumps(groups, indent=1))
    (sample_dir / "sample_forwards.json").write_text(
        json.dumps([{"source_message_id": s["message_id"], **s["fwd_from"]} for s in snaps if s.get("fwd_from")], indent=1, ensure_ascii=False))
    manifest = {
        "run_id": run_dir.name,
        "source_account": SOURCE_ACCOUNT,
        "source_peer_id": meta["source_peer_id"],
        "target_account": TARGET_ACCOUNT,
        "target_peer": "A<->B",
        "sample_count_requested": count,
        "sample_count_actual": len(snaps),
        "seed": hex(seed),
        "years_covered": stats.get("years", []),
        "message_ids": selected_ids,
        "media_types": sorted({s["media_type"] for s in snaps if s.get("media_type")}),
        "reaction_count": len(reactions),
        "reply_count": sum(1 for s in snaps if s.get("reply_to")),
        "group_count": len(groups),
    }
    (sample_dir / "sample_manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    (run_dir / "source_sample_snapshot.json").write_text(json.dumps(snaps, indent=1, ensure_ascii=False, default=str))

    # ---- build the lossless SAMPLE archive (canonical + raw + media) — only ~20 msgs
    writer = ArchiveWriter(run_dir)
    for i in selected_ids:
        m = full_by_id[i]
        cm = writer.write_message(m, _sender_of(m, own_ids))
        if cm.media is not None:
            await writer.download_media(ca, m, cm.media)
    ameta = writer.finalize()
    ameta["peer"] = {"peer_id": c.id, "type": type(c).__name__}
    ameta["read_at"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    (run_dir / "archive" / "archive_meta.json").write_text(json.dumps(ameta, indent=2, ensure_ascii=False))
    print(f"SAMPLE ARCHIVE: {ameta['message_count']} msgs, {ameta['media_count']} media", flush=True)

    # ---- package (ONLY from this sample) + roundtrip
    archive = ArchiveReader(run_dir)
    out = build_import_file(archive, run_dir / "import_file.txt")
    (run_dir / "package.json").write_text(json.dumps({**out, "run_id": run_dir.name}, indent=2))
    amap = {}
    IMPORTABLE = ("photo", "video", "gif", "audio", "voice", "document", "sticker")
    for m in archive.messages():
        media = m.get("media")
        if media and media.get("local_file") and media.get("type") in IMPORTABLE:
            amap[f"m{m['message_id']}{_ext_for(media)}"] = {"media_id": media["media_id"],
                                                            "source_message_id": m["message_id"]}
    (run_dir / "media_attach_map.json").write_text(json.dumps(amap, indent=2))
    # integrity: specs == declared media count (hard gate in run_import)
    pkg = json.loads((run_dir / "package.json").read_text())
    if pkg["media_count"] != len(amap):
        raise SystemExit(f"ABORT: package declares {pkg['media_count']} media but attach map has {len(amap)}")
    (run_dir / "package_hash.json").write_text(json.dumps({"package_hash": package_hash(run_dir)}))
    print(f"PACKAGE: {out['lines']} lines, {out['media_count']} media hash={package_hash(run_dir)}", flush=True)

    # roundtrip
    ok = await phase_roundtrip(run_dir)
    if not ok:
        raise SystemExit("ABORT: package roundtrip mismatch")

    # human-readable sample preview (Phase 8)
    render_preview(run_dir, snaps)
    print(f"\nSAMPLE PREVIEW: {run_dir / 'SAMPLE_PREVIEW.html'}", flush=True)
    for i, s in enumerate(snaps, 1):
        print(f"  #{i:02d} id={s['message_id']} date={s['date'][:19]} type={s['media_type'] or 'text'} "
              f"text={(s['text'] or '')[:36]!r} reply={bool(s.get('reply_to'))} "
              f"grouped={bool(s.get('grouped_id'))} fwd={bool(s.get('fwd_from'))} "
              f"react={(s.get('reactions') or []) and s['reactions'][0].get('reaction')}", flush=True)
    return manifest


async def phase_roundtrip(run_dir: Path) -> bool:
    snaps = json.loads((run_dir / "source_sample_snapshot.json").read_text())
    lines = (run_dir / "import_file.txt").read_text().strip().splitlines()
    ok = True
    for s in snaps:
        text = (s.get("text") or "").replace("\n", " ⏎ ")
        if not s.get("media_type") and text and not any(text in l for l in lines):
            ok = False
            print(f"ROUNDTRIP MISS text id={s['message_id']} {text[:40]!r}", flush=True)
    (run_dir / "package_roundtrip.json").write_text(json.dumps({"ok": ok, "lines": len(lines)}))
    print("ROUNDTRIP:", "OK" if ok else "MISMATCH", flush=True)
    return ok


def render_preview(run_dir: Path, snaps: list) -> None:
    import html

    rows = "".join(
        f"<tr><td>#{i}</td><td>{s['message_id']}</td><td>{html.escape(s['date'][:19])}</td>"
        f"<td>{s.get('sender') or s.get('sender_id')}</td><td>{html.escape((s.get('text') or '')[:80])}</td>"
        f"<td>{s.get('media_type') or 'text'}</td><td>{bool(s.get('reply_to'))}</td>"
        f"<td>{bool(s.get('grouped_id'))}</td><td>{bool(s.get('fwd_from'))}</td>"
        f"<td>{len(s.get('reactions') or [])}</td></tr>"
        for i, s in enumerate(snaps, 1)
    )
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>SAMPLE PREVIEW {run_dir.name}</title>
<style>body {{ font-family:monospace; padding:24px; background:#0d1117; color:#c9d1d9; }}
h1 {{ color:#58a6ff; }} table {{ border-collapse:collapse; width:100%; }}
th,td {{ border:1px solid #30363d; padding:4px 8px; font-size:12px; text-align:left; }} th {{ background:#161b22; }}</style>
</head><body><h1>SAMPLE PREVIEW — {run_dir.name}</h1>
<p>SOURCE: A &lt;-&gt; +989353114546 &nbsp;|&nbsp; TARGET: A &lt;-&gt; B &nbsp;|&nbsp; {len(snaps)} messages</p>
<table><tr><th>#</th><th>ID</th><th>DATE</th><th>SENDER</th><th>TEXT</th><th>MEDIA</th><th>REPLY</th><th>GROUP</th><th>FWD</th><th>REACT</th></tr>{rows}</table>
</body></html>"""
    (run_dir / "SAMPLE_PREVIEW.html").write_text(page)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seed", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm-recovery-test", action="store_true")
    ap.add_argument("--phase", choices=["discover", "sample", "import"], default=None)
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    cfg = load_config(Path(__file__).resolve().parent.parent)
    run_id = args.run_id or args.seed or dt.datetime.utcnow().strftime("recovery_v2_%Y%m%d_%H%M%S")
    seed = run_seed(run_id)  # seed = SHA256(run_id)
    run_dir = cfg.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    phase = args.phase
    if phase is None:
        phase = "discover" if not (run_dir / "source_catalog.ndjson").exists() else "sample"

    async with ClientPool(cfg) as pool:
        if phase == "discover":
            if (run_dir / "source_catalog.ndjson").exists() and json.loads(
                (run_dir / "source_catalog_checkpoint.json").read_text()
            ).get("status") == "done":
                print("CATALOG already complete — skipping discover")
            else:
                await phase_discover(cfg, pool, run_dir)
            return

        if phase == "sample":
            manifest = await phase_sample(cfg, pool, run_dir, args.count, seed, args.dry_run)
            if args.dry_run:
                print("\n[DRY-RUN] B not touched. Inspect SAMPLE_PREVIEW.html, then run"
                      " --phase sample --confirm-recovery-test (or --phase import).")
            else:
                if not args.confirm_recovery_test:
                    raise SystemExit("\nCONFIRM RECOVERY TEST required: re-run with --confirm-recovery-test")
                print("\nSample verified. Run: python scripts/sample_historical_recovery.py"
                      " --phase import --run-id <RUN_ID>")
            return

        if phase == "import":
            cb = pool.client(TARGET_ACCOUNT)
            a_id = pool.tg_id(SOURCE_ACCOUNT)
            peer_b = await cb.get_input_entity(a_id)
            chk = await cb(functions.messages.CheckHistoryImportPeerRequest(peer=peer_b))
            print(f"TARGET PEER A<->B: {peer_b}\ncheckHistoryImportPeer: "
                  f"{json.dumps(chk.to_dict(), default=str)[:300]}", flush=True)
            from recovery.engine import TelegramRecoveryEngine

            eng = TelegramRecoveryEngine(cfg, run_id)
            res = await eng.clear_target(peer_b)
            print(f"B-SIDE CLEAR pts={getattr(res, 'pts', res)}", flush=True)
            imp = await eng.run_import(peer_b)
            print(f"IMPORT: {json.dumps(imp, default=str)[:300]}", flush=True)
            print("\nImport started. Wait ~3-5 min for materialization, then:"
                  "\n  python scripts/verify_sampled_history.py --run-id <RUN_ID>")


if __name__ == "__main__":
    asyncio.run(main())