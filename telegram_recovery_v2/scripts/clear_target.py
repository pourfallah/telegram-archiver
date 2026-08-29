#!/usr/bin/env python3
"""Clear B-side history of the A<->B chat (test only).

Uses messages.deleteHistory with just_clear=True, revoke=False — A keeps
everything. NEVER revokes.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.config import load_config
from recovery.telegram_client import ClientPool
from telethon import functions


async def main() -> int:
    cfg = load_config()
    async with ClientPool(cfg) as pool:
        cb = pool.client("B")
        peer = await cb.get_input_entity(pool.tg_id("A"))
        res = await cb(
            functions.messages.DeleteHistoryRequest(
                peer=peer, max_id=0, just_clear=True, revoke=False
            )
        )
        print(json.dumps({"cleared": True, "pts": getattr(res, "pts", None)}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
