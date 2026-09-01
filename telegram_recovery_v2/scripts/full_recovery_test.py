#!/usr/bin/env python3
"""Full end-to-end REAL recovery test (the exact command of the project brief).

Performs, in order:
  1. (optionally) build a brand-new A<->B fixture — a fresh one by default
  2. read source / export into a lossless archive
  3. verify the archive
  4. build the import package (directly from the canonical archive)
  5. clear ONLY B (just_clear=true, revoke never)
  6. verify A still contains the source
  7. direct Telegram history import into the existing A<->B peer
  8. wait for a stable state
  9. read the target, map source->target
 10. reconstruct reactions per-actor (A with A, B with B)
 11. verify reactions on the target
 12. verify every field against ACTUAL target message objects
 13. write FINAL_REPORT.json / .html

Every run is reproducible (deterministic run_id, its own artifact directory).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from _common import build_engine, get_config

ROOT = Path(__file__).resolve().parent


def main() -> int:
    build_new = "--use-existing" not in sys.argv
    if build_new:
        print("==> building a NEW fixture")
        subprocess.check_call([sys.executable, str(ROOT / "create_fixture.py"),
                               *(["--react-b"] if "--react-b" in sys.argv else [])])
    asyncio.run(run(react="--no-react" not in sys.argv))
    return 0


async def run(react: bool) -> None:
    config = get_config()
    eng = build_engine(config)
    try:
        await eng.connect()
        result = await eng.full_test(max_messages=None, react=react)
        summary = result["report"]["summary"]
        print("\n==== FULL RECOVERY TEST ====")
        print(f"run_id        : {eng.run.run_id}")
        print(f"archive dir   : {eng.run.archive.root}")
        print(f"package dir   : {eng.run.package_dir}")
        print(f"report        : {eng.run.root / 'FINAL_REPORT.json'}")
        print("--- capability summary ---")
        for feature, v in summary.items():
            if v["total"] == 0:
                print(f"  {feature:<14} n/a")
            else:
                print(f"  {feature:<14} EXACT {v['exact']}/{v['total']} "
                      f"({v['exact_pct']}%)  {v['counts']}")
        print("\nPer-field classification docs: docs/FIDELITY_CLASSES.md")
    finally:
        await eng.close()


if __name__ == "__main__":
    raise SystemExit(main())