#!/usr/bin/env python3
"""Export A-side history of the A<->B chat into a lossless archive run."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.config import load_config
from recovery.engine import TelegramRecoveryEngine
from recovery.telegram_client import ClientPool


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config()
    eng = TelegramRecoveryEngine(cfg)
    async with ClientPool(cfg) as pool:
        peer = await pool.resolve_peer("A", pool.tg_id("B"))
        meta = await eng.export(peer, limit=args.limit)
        await eng.build_reaction_plan(peer)
    print(json.dumps({"run_id": eng.run_id, "run_dir": str(eng.run_dir), **meta}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
