"""PHASE 1+2 of the historical sampling experiment: lightweight resumable
catalog + stratified sampling. Does NOT modify any chat.

Usage:
  python -m recovery_v2.sample_history \
      --source-peer +989353114546 [--target-peer <A-phone>] \
      --count 20 --years 3 [--seed S] [--run-id ID] [--no-resume]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from recovery.config import RecoveryConfig, load_dotenv
from recovery.pipeline import Abort, run_sample_history


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recovery_v2.sample_history",
                                description="P1 resumable catalog + P2 stratified sampling (read-only)")
    p.add_argument("--source-peer", required=True, help="the C contact of A<->C, e.g. +989353114546")
    p.add_argument("--target-peer", default=None, help="A's phone / the A<->B chat (default RECOVERY_PHONE_A)")
    p.add_argument("--count", type=int, default=20, help="logical sample size (default 20)")
    p.add_argument("--years", type=int, default=3, help="distinct historical years to prefer")
    p.add_argument("--seed", default=None, help="deterministic seed (default SHA256(run_id))")
    p.add_argument("--run-id", default=None, help="explicit run id (default auto 'sample_<ts>')")
    p.add_argument("--no-resume", action="store_true", help="do NOT resume an existing catalog")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()
    cfg = RecoveryConfig.from_env()
    if not (cfg.api_id_a and cfg.api_hash_a and cfg.session_a()):
        sys.exit("SOURCE A not configured (RECOVERY_API_ID_A/HASH_A/SESSION_A_*)")
    try:
        run_id = asyncio.run(run_sample_history(
            cfg, source_peer=args.source_peer, target_peer=args.target_peer,
            count=args.count, years=args.years, seed=args.seed,
            run_id=args.run_id, resume=not args.no_resume))
    except Abort as exc:
        print(f"ABORT: {exc}")
        return 2
    print(f"\nSAMPLE READY. Next:  python -m recovery_v2.full_sampled_recovery "
          f"--run-id {run_id} --source-peer {args.source_peer} "
          f"[--dry-run | --confirm]")
    return 0


if __name__ == "__main__":
    sys.exit(main())