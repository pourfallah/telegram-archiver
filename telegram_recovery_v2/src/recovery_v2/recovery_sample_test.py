"""One-command REAL historical recovery test harness (A<->C -> A<->B).

Default is --dry-run: it only resolves peers, builds a lightweight resumable
catalog, samples 20-30 multi-year messages, lazily full-fetches them, downloads
media, builds + roundtrip-verifies the package, and prints SAMPLE_PREVIEW —
it NEVER touches target B. Only --execute (after explicit interactive
confirmation) clears B and performs the real import + verification.

INVARIANTS:
  SOURCE A<->C is read-only (getHistory/getMessages only; no state-changing call).
  TARGET A<->B is the only peer ever modified, and only with --execute + confirm.
  Uses the canonical pipeline engine (P1-P7) in recovery.pipeline — no fake importer.
  No full 198k-message body scan (lightweight catalog + lazy fetch only).

Run:
  python -m recovery_v2.recovery_sample_test \
      --source-peer +989****4546 --target-peer +989****0100 --count 25        # dry (default)
  python -m recovery_v2.recovery_sample_test \
      --source-peer +989****4546 --count 25 --execute                          # confirm->clear B->import->verify
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from recovery.config import RecoveryConfig, load_dotenv
from recovery.pipeline import Abort, run_full_recovery, run_sample_history

from . import login_accounts as L

A_PHONE = os.environ.get("RECOVERY_PHONE_A") or "+989394430100"
B_PHONE = os.environ.get("RECOVERY_PHONE_B") or "+5511991966422"
C_PHONE = os.environ.get("RECOVERY_SOURCE_PEER") or "+989353114546"


def load_session_string(phone: str, env_key: str) -> str | None:
    """Prefer the RECOVERY_SESSION_*_STRING env, else the login-saved session file."""
    v = os.environ.get(env_key)
    if v:
        return v.strip() or None
    spath = L.SESSIONS_DIR / f"account_{phone.replace('+', 'p')}.session"
    if spath.exists():
        return spath.read_text(encoding="utf-8").strip()
    return None


def prepare_config(args) -> RecoveryConfig:
    load_dotenv()
    cfg = RecoveryConfig.from_env()
    src_phone = args.source_phone or A_PHONE
    tgt_phone = args.target_phone or B_PHONE
    # ONE shared app credential for BOTH accounts (as the login tool does)
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if api_id and api_id.isdigit():
        cfg.api_id_a = cfg.api_id_b = int(api_id)
    if api_hash:
        cfg.api_hash_a = cfg.api_hash_b = api_hash
    cfg.session_a_string = load_session_string(src_phone, "RECOVERY_SESSION_A_STRING")
    cfg.session_b_string = load_session_string(tgt_phone, "RECOVERY_SESSION_B_STRING")
    return cfg


def require_sessions(cfg: RecoveryConfig) -> None:
    missing = []
    if not cfg.session_a_string:
        missing.append("SOURCE A")
    if not cfg.session_b_string:
        missing.append("TARGET B")
    if missing:
        print("ERROR: no authenticated session for " + " and ".join(missing) + ".")
        print("  The OTP is sent to the phone and must be entered by the account holder,")
        print("  so this server cannot log them in by itself.")
        print("  Run the menu first to create the sessions (saved to data/sessions/):")
        print("      python -m recovery_v2.login_accounts   # option 1 (Add account) for each number")
        print(f"  Expected: {L.SESSIONS_DIR/'account_<phone>.session'}")
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recovery_v2.recovery_sample_test",
                                description="Real historical recovery test A<->C -> A<->B (dry by default)")
    p.add_argument("--source-peer", default=C_PHONE, help="the C contact that forms A<->C")
    p.add_argument("--target-peer", default=A_PHONE, help="A's phone -> the A<->B peer")
    p.add_argument("--source-phone", default=A_PHONE, help="the SOURCE account A phone")
    p.add_argument("--target-phone", default=B_PHONE, help="the TARGET account B phone")
    p.add_argument("--count", type=int, default=25)
    p.add_argument("--years", type=int, default=3)
    p.add_argument("--run-id", default=None)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="execute", action="store_false",
                   help="discover+sample+package ONLY, target B untouched (DEFAULT)")
    g.add_argument("--execute", dest="execute", action="store_true",
                   help="after confirmation: clear B only, import, verify")
    p.set_defaults(execute=False)
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    cfg = prepare_config(args)
    require_sessions(cfg)
    try:
        run_id = asyncio.run(run_sample_history(
            cfg, source_peer=args.source_peer, target_peer=args.target_peer,
            count=args.count, years=args.years, seed=None, run_id=args.run_id))
    except Abort as exc:
        print(f"ABORT: {exc}"); return 2
    if not args.execute:
        rc = asyncio.run(run_full_recovery(
            cfg, run_id=run_id, source_peer=args.source_peer,
            target_peer=args.target_peer, dry_run=True, confirm=False))
        print("\nDRY RUN complete: sample + package ready; TARGET B untouched. "
              "Inspect test_runs/<run_id>/SAMPLE_PREVIEW.html, then re-run with --execute.")
        return rc
    answer = input("\nI have verified the sample and want to modify target B. "
                   "Type 'yes' to proceed: ").strip()
    if answer.lower() != "yes":
        print("Aborted (no target changes)."); return 1
    rc = asyncio.run(run_full_recovery(
        cfg, run_id=run_id, source_peer=args.source_peer,
        target_peer=args.target_peer, dry_run=False, confirm=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())