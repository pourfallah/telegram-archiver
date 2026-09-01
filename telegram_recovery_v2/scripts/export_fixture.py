#!/usr/bin/env python3
"""Export the source chat into a lossless recovery archive (Step: READ A)."""
from __future__ import annotations

import asyncio
import json

from _common import build_engine, get_config


async def main():
    eng = build_engine()
    try:
        await eng.connect()
        print(json.dumps({"run_id": eng.run.run_id, "export": await eng.export()},
                         indent=2, ensure_ascii=False))
    finally:
        await eng.close()


if __name__ == "__main__":
    asyncio.run(main())