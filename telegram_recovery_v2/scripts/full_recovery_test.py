#!/usr/bin/env python3
"""Full recovery test: export -> package -> clear B -> import -> verify.

Uses an existing run archive if --run-id is given; otherwise creates a new
fixture + fresh run. Generates FINAL_SUMMARY.json in the run directory.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.config import load_config
from recovery.engine import TelegramRecoveryEngine
from recovery.telegram_client import ClientPool
from recovery.source_reader import SourceReader


async def materialize_polls(eng, reader, peer_b, attempts=15, delay=30):
    import asyncio as aio

    prev = -1
    for i in range(attempts):
        snap = await eng.snapshot_target(reader, peer_b, f"target_poll_{i}")
        n = snap["messages"]
        eng.log(f"materialization poll {i}: {n} target messages")
        if n == prev and n > 0:
            return n
        prev = n
        await aio.sleep(delay)
    return prev


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None, help="reuse an existing exported run")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip-clear", action="store_true")
    ap.add_argument("--skip-import", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    eng = TelegramRecoveryEngine(cfg, args.run_id)

    if args.run_id is None:
        # create fixture + export
        from create_fixture import send_fixture
        async with ClientPool(cfg) as pool:
            peer_a = await pool.resolve_peer("A", pool.tg_id("B"))
            peer_b = await pool.resolve_peer("B", pool.tg_id("A"))
            await send_fixture(pool, peer_a, peer_b, eng.run_dir / "fixture_index.json", False)
        async with ClientPool(cfg) as pool:
            peer_a = await pool.resolve_peer("A", pool.tg_id("B"))
            await eng.export(peer_a, limit=args.limit)
            await eng.build_reaction_plan(peer_a)
    eng.verify_export()
    pkg = await eng.build_package()
    # attach map for the importer
    from recovery.archive import ArchiveReader
    from recovery.media import attach_name_for
    attach_map = {}
    archive = ArchiveReader(eng.run_dir)
    for m in archive.messages():
        media = m.get("media")
        if media and media.get("local_file"):
            attach = f"m{m['message_id']}{Path(media['local_file']).suffix}"
            attach_map[attach] = {"media_id": media["media_id"], "source_message_id": m["message_id"]}
    (eng.run_dir / "media_attach_map.json").write_text(json.dumps(attach_map, indent=2))

    async with ClientPool(cfg) as pool:
        peer_a = await pool.resolve_peer("A", pool.tg_id("B"))
        peer_b = await pool.resolve_peer("B", pool.tg_id("A"))
        reader = SourceReader(pool)
        before = await eng.snapshot_target(reader, peer_b, "target_before")
        before_ids = {
            json.loads(l)["message_id"]
            for l in open(eng.run_dir / "target_before.json", encoding="utf-8")
        }
        if not args.skip_clear:
            await eng.clear_target(peer_b)
        import_res = {"skipped": args.skip_import}
        if not args.skip_import:
            import_res = await eng.run_import(peer_b)
            await materialize_polls(eng, reader, peer_b)
        await eng.snapshot_target(reader, peer_b, "target_after")

    verify_res = await eng.verify(peer_b, before_ids)
    final = {
        "run_id": eng.run_id,
        "package": pkg,
        "import": import_res,
        "verify": {
            "mapped": verify_res["mapping"]["mapped"],
            "unmatched_source": len(verify_res["mapping"]["unmatched_source"]),
            "counts": verify_res["report_counts"],
            "reactions": verify_res["reactions"],
        },
    }
    (eng.run_dir / "FINAL_SUMMARY.json").write_text(json.dumps(final, indent=2, default=str))
    print(json.dumps(final, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
