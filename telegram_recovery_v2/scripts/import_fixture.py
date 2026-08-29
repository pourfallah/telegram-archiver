#!/usr/bin/env python3
"""Import a built package into the A<->B peer (as B) via the official API."""

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
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    cfg = load_config()
    eng = TelegramRecoveryEngine(cfg, args.run_id)
    async with ClientPool(cfg) as pool:
        peer_b = await pool.resolve_peer("B", pool.tg_id("A"))
        res = await eng.run_import(peer_b)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
