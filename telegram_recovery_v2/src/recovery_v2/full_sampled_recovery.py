"""PHASES 3-7 of the historical sampling experiment for an existing sample run:
lazy full fetch (~20 selected + closures), media, package+roundtrip,
(optional) B-side clear + import, target verification, report + decision.

Read-only on SOURCE A<->C. Modifies TARGET A<->B ONLY with --confirm.

Usage:
  python -m recovery_v2.full_sampled_recovery --run-id <ID> \
      --source-peer +989353114546 [--target-peer <A-phone>] [--dry-run | --confirm]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from recovery.config import RecoveryConfig, load_dotenv
from recovery.pipeline import Abort, run_full_recovery


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recovery_v2.full_sampled_recovery",
                                description="P3-P7 for an existing sample run")
    p.add_argument("--run-id", required=True, help="the run id from sample_history")
    p.add_argument("--source-peer", default=None, help="the C contact of A<->C")
    p.add_argument("--target-peer", default=None, help="A's phone / the A<->B chat")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--dry-run", action="store_true",
                   help="fetch+media+package+roundtrip ONLY; B untouched")
    m.add_argument("--confirm", action="store_true",
                   help="allow clearing + importing into TARGET A<->B")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()
    cfg = RecoveryConfig.from_env()
    if not (cfg.api_id_a and cfg.api_hash_a and cfg.session_a() and cfg.session_b()):
        sys.exit("SOURCE A and TARGET B sessions required (RECOVERY_*)")
    try:
        rc = asyncio.run(run_full_recovery(
            cfg, run_id=args.run_id, source_peer=args.source_peer,
            target_peer=args.target_peer, dry_run=args.dry_run, confirm=args.confirm))
    except Abort as exc:
        print(f"ABORT: {exc}")
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())