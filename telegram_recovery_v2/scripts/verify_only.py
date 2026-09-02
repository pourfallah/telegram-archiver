#!/usr/bin/env python3
"""Verify an ALREADY-imported A->B target WITHOUT re-importing.

Reads the live, paginated target A<->B history via B, maps it to the source
canonical archive of the given run, and produces the Fefore verification
report + decision. Source A<->C is read-only here. Used to complete a run
whose import succeeded but whose verification crashed on a bug we since fixed.

Usage:  python scripts/verify_only.py --run <run_id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from recovery import pipeline as P
from recovery import verifier as V
from recovery_v2 import recovery_sample_test as H


async def read_target_records_paginated(tgt, tgt_peer, cap=100000) -> list[dict]:
    """Paginate A<->B history via B (getHistory caps ~100/page for this peer)."""
    from telethon.tl import functions as f
    out: list[dict] = []
    offset_id = 0
    while True:
        res = await tgt.call(f.messages.GetHistoryRequest(
            peer=tgt_peer, offset_id=offset_id, offset_date=None, add_offset=0,
            limit=100, max_id=0, min_id=0, hash=0))
        msgs = getattr(res, "messages", None) or []
        if not msgs:
            break
        for m in msgs:
            out.append(P.target_record(m))
        if len(msgs) < 100:
            break
        offset_id = msgs[-1].id
        if len(out) >= cap:
            break
    return out


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--source-peer", required=True, help="A<->C contact (read only)")
    args = ap.parse_args(argv)

    from recovery.config import load_dotenv
    load_dotenv()
    cfg = H.prepare_config(H._parser().parse_args(["--count", "25"]))
    H.require_sessions(cfg)

    run = P.make_run(cfg, args.run)
    cat_path = run.root / "source_catalog.ndjson"
    man_path = run.root / "sample_manifest.json"
    catalog = P.load_catalog(cat_path)
    ids = json.loads(man_path.read_text(encoding="utf-8"))["message_ids"]
    final_ids = P.closures_from_catalog(catalog, ids)

    # Canonical source records were persisted by the run's lazy_materialize.
    recs = [json.loads(ln) for ln in
            (run.root / "archive" / "messages.ndjson").read_text(encoding="utf-8")
            .splitlines() if ln.strip()]
    print(f"source canonical records: {len(recs)} (closure ids: {len(final_ids)})")

    src, tgt = await P.build_clients(cfg)
    try:
        # target chat is A<->B, so the target peer resolves to account A. Find
        # A's real phone from the local account store (A shares +98 country code
        # with the +98...0100 identity; B is +55...). --source-peer is the C chat.
        from recovery_v2.login_accounts import AccountStore
        store = AccountStore()
        accounts = store.list()
        a_phone = next((x.get("phone") for x in accounts
                        if (x.get("phone") or "").startswith("+98")), None)
        if not a_phone:
            a_phone = next((x.get("phone") for x in accounts), None)
        print(f"A account phone for target peer: {a_phone}")
        src_peer_desc = await P.resolve_peer(src, args.source_peer)
        tgt_peer_desc = await P.resolve_peer(tgt, a_phone)
        src_id, tgt_id = await P.identify(src_peer_desc), await P.identify(tgt_peer_desc)
        P.assert_target_is_ab(src, tgt_peer_desc)
        src_peer = await src.get_peer(args.source_peer)
        tgt_peer = await tgt.get_peer(a_phone)
        print(f"source peer {src_id}  target peer {tgt_id}  (asserted A<->B)")

        after_recs = await read_target_records_paginated(tgt, tgt_peer)
        print(f"live target messages read (paginated): {len(after_recs)}")
        delta = {r["target_message_id"] for r in after_recs}

        mapping = P.map_source_to_target(recs, after_recs, delta_target_ids=delta)
        mapped_ids = [m.target_message_id for m in mapping if m.target_message_id >= 0]
        print(f"mapped source->target: {sum(1 for m in mapping if m.target_message_id >= 0)}/{len(recs)}")
        tr = await P.read_target_reactions(tgt, tgt_peer, mapped_ids)
        rverify = P.reaction_verify_for(tr, mapping)

        verifier = V.Verifier(mapping)
        result = verifier.verify(recs, after_recs, delta_target_ids=delta,
                                 reaction_verify=rverify)
        matrix = result["matrix"]

        untouched = await P.source_immutability(cfg, src, src_peer, recs)
        evidence = P.build_evidence(recs, after_recs, mapping)
        ts = {c: matrix["timestamp"].get(c, 0)
              for c in ("TIMESTAMP_EXACT", "IMPORTED_METADATA_ONLY", "NOT_RESTORED")}
        decision = P.pick_decision(len(delta), ts)
        P.build_report(run, source_peer_id=src_id, target_peer_id=tgt_id, recs=recs,
                       after_recs=after_recs, delta=delta, matrix=matrix,
                       evidence=evidence, untouched=untouched, recon={},
                       rt={"package_hash": "verify-only-replay"},
                       seed=P.seed_for(args.run), decision=decision)

        print(f"\n==== DECISION: {decision} (SOURCE_UNTOUCHED={untouched['SOURCE_UNTOUCHED']}) ====")
        for k, c in matrix.items():
            print(f"  {k:<14} EXACT={c.get('EXACT',0)} total={sum(c.values())}  {c}")
        print(f"\nreport: {run.root / 'FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.json'}")
        return 0
    finally:
        await src.close()
        await tgt.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))