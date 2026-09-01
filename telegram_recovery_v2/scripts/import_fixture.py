#!/usr/bin/env python3
"""Step 4: run the direct Telegram history import into the A<->B peer.

Uses the official import flow (checkHistoryImport -> checkHistoryImportPeer
-> initHistoryImport -> uploadImportedMedia x N -> startHistoryImport). The
import_id is persisted in run state so a crash never re-inits the package.
"""
from __future__ import annotations

import asyncio
import json

from _common import build_engine


async def main():
    eng = build_engine()
    try:
        await eng.connect()
        print(json.dumps(await eng.import_package(), indent=2))
    finally:
        await eng.close()


if __name__ == "__main__":
    asyncio.run(main())