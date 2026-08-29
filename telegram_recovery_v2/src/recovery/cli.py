"""CLI for the recovery v2 engine. Every command calls TelegramRecoveryEngine
(or a module function it also uses) — no duplicate logic.

Usage: recovery-v2 <command> ...   (or python -m recovery.cli <command>)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import load_config
from .engine import TelegramRecoveryEngine
from .telegram_client import ClientPool


def _resolve_peer_arg(pool: ClientPool, label: str, identifier: str):
    """Resolve 'A<->B' style peer: the 1:1 chat between the two accounts."""
    if identifier == "self":
        return types.InputPeerSelf() if False else None
    if identifier.isdigit():
        return asyncio.get_event_loop().run_until_complete(
            pool.resolve_peer(label, int(identifier))
        )
    return asyncio.get_event_loop().run_until_complete(pool.resolve_peer(label, identifier))


async def cmd_accounts(cfg) -> dict:
    async with ClientPool(cfg) as pool:
        out = {}
        for label in ("A", "B"):
            me = await pool.client(label).get_me()
            out[label] = {"tg_id": me.id, "name": f"{me.first_name or ''} {me.last_name or ''}".strip(), "phone": me.phone}
        return out


async def cmd_resolve_peer(cfg, identifier: str) -> dict:
    async with ClientPool(cfg) as pool:
        out = {}
        for label in ("A", "B"):
            try:
                peer = await pool.resolve_peer(label, identifier)
                out[label] = str(peer)
            except Exception as e:
                out[label] = f"ERROR: {e}"
        return out


async def cmd_inspect_chat(cfg, peer_id: int, limit: int) -> dict:
    from .source_reader import SourceReader

    async with ClientPool(cfg) as pool:
        peer = await pool.resolve_peer("A", peer_id)
        reader = SourceReader(pool)
        n = 0
        first = None
        async for msg in reader.iter_history(peer, limit=limit):
            if first is None:
                first = {"id": msg.id, "date": msg.date.isoformat(), "text": (msg.message or "")[:80]}
            n += 1
        return {"peer": str(peer), "sampled": n, "newest": first}


async def cmd_export(cfg, run_id: str | None, peer_id: int, limit: int | None) -> dict:
    eng = TelegramRecoveryEngine(cfg, run_id)
    async with ClientPool(cfg) as pool:
        peer = await pool.resolve_peer("A", peer_id)
        meta = await eng.export(peer, limit=limit)
    return {"run_id": eng.run_id, "run_dir": str(eng.run_dir), **meta}


async def cmd_build_package(cfg, run_id: str) -> dict:
    eng = TelegramRecoveryEngine(cfg, run_id)
    pkg = await eng.build_package()
    return {"run_id": run_id, **pkg}


async def cmd_clear_target(cfg, peer_id_b: int) -> dict:
    """peer_id_b = B's OWN user id; the engine resolves A<->B from B's view."""
    eng = TelegramRecoveryEngine(cfg)
    async with ClientPool(cfg) as pool:
        peer = await pool.resolve_peer("B", pool.tg_id("A"))
        res = await eng.clear_target(peer)
    return res


async def cmd_import(cfg, run_id: str, peer_id: int) -> dict:
    eng = TelegramRecoveryEngine(cfg, run_id)
    async with ClientPool(cfg) as pool:
        peer = await pool.resolve_peer("B", pool.tg_id("A"))
        res = await eng.run_import(peer)
    return res


async def cmd_verify(cfg, run_id: str, peer_id: int) -> dict:
    eng = TelegramRecoveryEngine(cfg, run_id)
    async with ClientPool(cfg) as pool:
        peer = await pool.resolve_peer("B", peer_id)
        before_ids = set()
        tb = eng.run_dir / "target_before.json"
        if tb.exists():
            before_ids = {json.loads(l)["message_id"] for l in open(tb, encoding="utf-8")}
        res = await eng.verify(peer, before_ids)
    return res


async def cmd_full_test(cfg, peer_id_a: int, peer_id_b: int, limit: int | None) -> dict:
    eng = TelegramRecoveryEngine(cfg)
    async with ClientPool(cfg) as pool:
        peer_a = await pool.resolve_peer("A", peer_id_a)
        peer_b = await pool.resolve_peer("B", peer_id_b)
    result = await eng.run_full(peer_a, peer_b, limit=limit)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="recovery-v2")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("accounts")
    sp = sub.add_parser("resolve-peer"); sp.add_argument("identifier")
    sp = sub.add_parser("inspect-chat"); sp.add_argument("peer_id", type=int); sp.add_argument("--limit", type=int, default=20)
    sp = sub.add_parser("export"); sp.add_argument("--peer-id", type=int, required=True); sp.add_argument("--run-id"); sp.add_argument("--limit", type=int)
    sp = sub.add_parser("verify-export"); sp.add_argument("--run-id", required=True)
    sp = sub.add_parser("build-package"); sp.add_argument("--run-id", required=True)
    sp = sub.add_parser("clear-target"); sp.add_argument("--peer-id", type=int, required=True)
    sp = sub.add_parser("import"); sp.add_argument("--run-id", required=True); sp.add_argument("--peer-id", type=int, required=True)
    sp = sub.add_parser("verify"); sp.add_argument("--run-id", required=True); sp.add_argument("--peer-id", type=int, required=True)
    sp = sub.add_parser("full-test"); sp.add_argument("--peer-id-a", type=int, required=True); sp.add_argument("--peer-id-b", type=int, required=True); sp.add_argument("--limit", type=int)
    sp = sub.add_parser("reconstruct"); sp.add_argument("--run-id", required=True); sp.add_argument("--peer-id-a", type=int, required=True); sp.add_argument("--peer-id-b", type=int, required=True)

    args = p.parse_args(argv)
    cfg = load_config()

    async def dispatch():
        if args.cmd == "accounts":
            return await cmd_accounts(cfg)
        if args.cmd == "resolve-peer":
            return await cmd_resolve_peer(cfg, args.identifier)
        if args.cmd == "inspect-chat":
            return await cmd_inspect_chat(cfg, args.peer_id, args.limit)
        if args.cmd == "export":
            return await cmd_export(cfg, args.run_id, args.peer_id, args.limit)
        if args.cmd == "verify-export":
            eng = TelegramRecoveryEngine(cfg, args.run_id)
            return eng.verify_export()
        if args.cmd == "build-package":
            return await cmd_build_package(cfg, args.run_id)
        if args.cmd == "clear-target":
            return await cmd_clear_target(cfg, args.peer_id)
        if args.cmd == "import":
            return await cmd_import(cfg, args.run_id, args.peer_id)
        if args.cmd == "verify":
            return await cmd_verify(cfg, args.run_id, args.peer_id)
        if args.cmd == "full-test":
            return await cmd_full_test(cfg, args.peer_id_a, args.peer_id_b, args.limit)
        if args.cmd == "reconstruct":
            eng = TelegramRecoveryEngine(cfg, args.run_id)
            async with ClientPool(cfg) as pool:
                pa = await pool.resolve_peer("A", args.peer_id_a)
                pb = await pool.resolve_peer("B", args.peer_id_b)
                mapping = {"mappings": []}
                stt = eng.run_dir / "source_to_target.json"
                if stt.exists():
                    mapping = json.loads(stt.read_text())
                return await eng.reconstruct(pa, pb, mapping)
        raise SystemExit(f"unknown command {args.cmd}")

    result = asyncio.run(dispatch())
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
