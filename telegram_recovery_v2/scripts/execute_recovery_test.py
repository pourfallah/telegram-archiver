#!/usr/bin/env python3
"""REAL end-to-end recovery execute (A<->C -> A<->B).

Fresh RUN_ID + fresh archive/package/media every run. Source A<->C is read-only.
The A<->C message index (recently completed, 1.7M rows, 2015-2026) is streamed
and caps a deterministic per-year-month candidate pool so memory stays bounded;
sampling is then the usual deterministic select_ids + group/reply closure, and
everything from lazy full-fetch onward runs live against Telegram.

Only TARGET A<->B is modified, and only via the official history-import flow
after a safe B-side clear (just_clear=true, revoke=false).

Usage:  python scripts/execute_recovery_test.py
        (feed 'yes' on stdin to confirm the target modification, or pass --yes)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from recovery.config import RecoveryConfig  # noqa: E402
from recovery import pipeline as P  # noqa: E402
from recovery.importer import (tehran_local_str, tehran_timestamp_checks,
                               verify_timestamp_encoding)  # noqa: E402
from recovery_v2 import recovery_sample_test as H  # noqa: E402
from recovery_v2 import login_accounts as L  # noqa: E402

A = "+989394430100"
B = "+5511991966422"
C = "+989353114546"
SEED_CATALOG = Path("test_runs/sample_20260901_192951/source_catalog.ndjson")
MONTH_CAP = 150  # deterministic per-year-month candidate cap (memory-bounded)


def stream_bounded_candidates(src: Path, cap: int, out: Path) -> list[dict]:
    """Stream the big index; keep the first `cap` rows per year-month (bounded)."""
    seen: dict[str, int] = {}
    rows: list[dict] = []
    with src.open(encoding="utf-8") as fh, out.open("w", encoding="utf-8") as oh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            ym = (r.get("date") or "")[:7]
            if ym and seen.get(ym, 0) >= cap:
                continue
            if ym:
                seen[ym] = seen.get(ym, 0) + 1
            rows.append(r)
            oh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=25)
    ap.add_argument("--years", type=int, default=4)
    ap.add_argument("--yes", action="store_true", help="confirm target modification non-interactively")
    args = ap.parse_args(argv)
    from recovery.config import load_dotenv as _ld
    _ld()
    cfg = H.prepare_config(H._parser().parse_args(["--count", str(args.count)]))
    H.require_sessions(cfg)

    run_id = "e2e_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"RUN_ID {run_id}   source A<->C -> target A<->B")

    if not SEED_CATALOG.exists():
        sys.exit(f"catalog index not found: {SEED_CATALOG} (run discovery first)")
    run = P.make_run(cfg, run_id)
    cat_out = run.root / "source_catalog.ndjson"
    stream_bounded_candidates(SEED_CATALOG, MONTH_CAP, cat_out)

    catalog = P.load_catalog(cat_out)
    print(f"candidate pool: {len(catalog)} rows (bounded per year-month)")
    seed = P.seed_for(run_id)
    selected = P.select_ids(catalog, args.count, seed, args.years)
    closed = P.apply_closures(catalog, selected)
    P.write_sample_artifacts(run, closed, seed, args.count, catalog, "A<->C", "A<->B")
    ids = [r["id"] for r in closed]
    print(f"selected {len(ids)} source ids: {ids}")
    print(f"years covered: {P.years_covered(closed)}  range: {P.date_range(closed)}")
    print(f"preview: {run.root / 'SAMPLE_PREVIEW.html'}")

    # --- PRE-EXECUTION GATES: nothing runs/imports until these pass ---------
    checks = tehran_timestamp_checks()
    print("\n[TIMESTAMP ROUND-TRIP PRE-CHECK]")
    for c in checks:
        print("  %-30s -> tehran %-8s offset %+.2fh  file='%s'  minute_exact=%s"
              % (c["source_utc"], c["tehran_local"][11:16], c["offset_hours"],
                 c["file_timestamp"], c["minute_exact"]))
    if not verify_timestamp_encoding():
        sys.exit("PRE-EXECUTION GATE FAILED: timestamp round-trip is not minute-exact "
                 "(timezone shift present). Explicitly ABORTING — no target changes.")

    print("\n[PRE-IMPORT REPORT — THE ONLY CHAT MUTATED IS A<->B]")
    media_rows = 0
    reply_closures = 0
    album_closures = 0
    for r in sorted(closed, key=lambda x: x.get("date") or ""):
        media_types = ",".join(r.get("media_types") or [])
        if (r.get("media_types") or []):
            media_rows += 1
        if r.get("has_reply"):
            reply_closures += 1
        print("  src=%s utc=%s tehran=%s sender_id=%s media[%s] grouped=%s reply_to=%s "
              "react=%s fwd=%s"
              % (r["id"], r.get("date") or "-", tehran_local_str(r["date"]) if r.get("date") else "-",
                 r.get("sender_id"), media_types, r.get("grouped_id"), r.get("reply_to_id"),
                 r.get("has_reactions"), r.get("has_forward")))
        if r.get("grouped_id"):
            album_closures += 1
    print("  selected_message_count=%d  media_bearing_source_rows=%d  "
          "reply_closure_rows=%d  album_grouped_rows=%d"
          % (len(closed), media_rows, reply_closures, album_closures))

    # MEDIA GATE: a real media-path E2E is meaningless without source media.
    if media_rows == 0:
        sys.exit("PRE-EXECUTION GATE FAILED: sample contains no media-bearing source "
                 "rows — would import text-only. ABORTING, no target changes.")

    if not args.yes:
        ans = input("\nI have verified the sample and want to modify target B. Type 'yes': ").strip()
        if ans.lower() != "yes":
            print("Aborted (no target changes)."); return 1
    rc = asyncio.run(P.run_full_recovery(
        cfg, run_id=run_id, source_peer=C, target_peer=A, dry_run=False, confirm=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())