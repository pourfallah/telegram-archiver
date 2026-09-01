#!/usr/bin/env python3
"""Verify the target after import: snapshot, map, reconstruct reactions,
read real reaction counts, and produce the fidelity report.

Reads ACTUAL Telegram target message objects — the only authoritative check.
"""
from __future__ import annotations

import asyncio
import json

from recovery.mapper import Mapping

from _common import build_engine


def _load_mapping(path):
    import json as _j
    return [Mapping(m["source_message_id"], m["target_message_id"],
                    m.get("confidence", "NONE"), m.get("reason", ""))
            for m in _j.loads(path.read_text()).get("mappings", [])]


async def main():
    eng = build_engine()
    try:
        await eng.connect()
        await eng.snapshot_target("after")
        mapping = await eng.map_source_to_target()
        applied = await eng.reconstruct_reactions(mapping)
        rv = await eng.verify_reactions(mapping)
        report = await eng.verify(mapping, reaction_verify=_rv_by_target(rv))
        print(json.dumps({"run_id": eng.run.run_id, "mapping": eng.run.inventory["mapping"],
                          "reaction_reconstruction": applied,
                          "reaction_verify": rv.get("checked"),
                          "summary": report["summary"]},
                         indent=2, ensure_ascii=False))
    finally:
        await eng.close()


def _rv_by_target(rv: dict) -> dict:
    return rv.get("messages", {})


if __name__ == "__main__":
    asyncio.run(main())