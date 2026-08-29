#!/usr/bin/env python3
"""Create a fresh run dir from an existing pristine archive (new run_id).

Use when the live source chat has been mutated by earlier experiments but a
lossless archive snapshot exists: copy the archive into a NEW run dir so the
package/import get deterministic fresh artifacts (spec 53: no artifact reuse).
"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from recovery.config import load_config
from recovery.engine import TelegramRecoveryEngine


def make_run_id(prefix: str = "recovery_v2") -> str:
    import datetime
    import secrets

    return f"{prefix}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-run", required=True, help="existing run dir name")
    ap.add_argument("--test-runs", default="test_runs")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    runs = root / args.test_runs
    src = runs / args.from_run
    if not (src / "archive").is_dir():
        sys.exit(f"no archive in {src}")

    run_id = make_run_id()
    dst = runs / run_id
    dst.mkdir(parents=True)
    shutil.copytree(src / "archive", dst / "archive")

    cfg = load_config(root)
    eng = TelegramRecoveryEngine(cfg, run_id)
    (dst / "archive" / "archive_meta.json").write_text(
        json.dumps({**json.loads((dst / "archive" / "archive_meta.json").read_text()),
                    "run_id": run_id, "cloned_from": args.from_run}, indent=2)
    )
    res = asyncio.run(eng.build_package())
    print(json.dumps({"run_id": run_id, **res}, indent=2))


if __name__ == "__main__":
    main()