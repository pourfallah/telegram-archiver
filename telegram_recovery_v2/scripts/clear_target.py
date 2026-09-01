#!/usr/bin/env python3
"""Step 3: clear TARGET B's copy of the chat (just_clear=true, revoke NEVER).

After this, A must still hold the source. The engine always uses
``just_clear=True, revoke=False`` — revoke is never enabled.
"""
from __future__ import annotations

import asyncio
import json

from _common import build_engine


async def main():
    eng = build_engine()
    try:
        await eng.connect()
        before = await eng.snapshot_target("before")
        cleared = await eng.clear_target()
        a_has = await eng.verify_source_still_has_history(want=5)
        print(json.dumps({"before": before, "clear": cleared,
                          "source_still_has_history": a_has}, indent=2))
    finally:
        await eng.close()


if __name__ == "__main__":
    asyncio.run(main())