#!/usr/bin/env python3
"""Verify the target: read target_after, map source->target, verify.

Requires a completed import run: <run_id> with target_before.json present
(or it verifies the entire current target delta from zero).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.config import load_config
from recovery.engine import TelegramRecoveryEngine
from recovery.telegram_client import ClientPool
from recovery.source_reader import SourceReader


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    cfg = load_config()
    eng = TelegramRecoveryEngine(cfg, args.run_id)
    async with ClientPool(cfg) as pool:
        peer_b = await pool.resolve_peer("B", pool.tg_id("A"))
        reader = SourceReader(pool)
        await eng.snapshot_target(reader, peer_b, "target_after")
        before_ids = set()
        tb = eng.run_dir / "target_before.json"
        if tb.exists():
            before_ids = {json.loads(l)["message_id"] for l in open(tb, encoding="utf-8")}
        res = await eng.verify(peer_b, before_ids)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
