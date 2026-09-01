"""CLI for Telegram Recovery v2 — ``recovery-v2``.

Every subcommand drives the SAME engine the tests and (later) the web app use.
Pure debugging/drive interface; no business logic lives here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import RecoveryConfig, load_dotenv
from .engine import TelegramRecoveryEngine
from .telegram_client import RecoveryClient, default_connect

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("recovery.cli")

RUN_ID_ARG = "--run"

# ---------------------------------------------------------------------------
# engine construction
# ---------------------------------------------------------------------------
def _clients(config: RecoveryConfig):
    src = RecoveryClient(config.api_id_a, config.api_hash_a, config.phone_a,
                         connect=default_connect)
    tgt = RecoveryClient(config.api_id_b, config.api_hash_b, config.phone_b,
                         connect=default_connect)
    return src, tgt


async def _engine(config: RecoveryConfig) -> TelegramRecoveryEngine:
    src, tgt = _clients(config)
    engine = TelegramRecoveryEngine(src, tgt, config)
    try:
        await engine.connect()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"connect failed: {type(exc).__name__}: {exc}")
    return engine


async def _amain(args, config: RecoveryConfig) -> int:
    if args.command == "accounts":
        return await _cmd_accounts(config, args)
    engine = await _engine(config)
    try:
        if args.command == "resolve-peer":
            print(json.dumps({"peer": config.peer,
                              "input_peer": str(engine.peer)}, ensure_ascii=False))
        elif args.command == "inspect-chat":
            from telethon.tl import functions as f
            res = await engine.src.call(f.messages.GetHistoryRequest(
                engine.peer, offset_id=0, offset_date=None, add_offset=0,
                limit=min(getattr(args, "count", 5), 500), max_id=0, min_id=0, hash=0))
            msgs = getattr(res, "messages", None) or []
            print(json.dumps({"count": len(msgs),
                              "newest": [int(getattr(m, "id", 0)) for m in msgs]}))
        elif args.command == "export":
            print(json.dumps(await engine.export(max_messages=args.max)))
        elif args.command == "verify-export":
            print(json.dumps(await engine.verify_export()))
        elif args.command == "build-package":
            print(json.dumps(await engine.build_package()))
        elif args.command == "clear-target":
            print(json.dumps(await engine.clear_target()))
        elif args.command == "snapshot":
            print(json.dumps(await engine.snapshot_target(args.label)))
        elif args.command == "import":
            print(json.dumps(await engine.import_package()))
        elif args.command == "reconstruct":
            applied = await engine.reconstruct_reactions(_load_mapping(engine.run.source_to_target))
            print(json.dumps(applied, ensure_ascii=False))
        elif args.command == "verify":
            mapping = _load_mapping(engine.run.source_to_target)
            report = await engine.verify(mapping)
            print(json.dumps(report.get("summary", {}), ensure_ascii=False, indent=2))
        elif args.command == "full-test":
            report = await engine.full_test(max_messages=args.max, react=not args.no_react)
            print(json.dumps({"run_id": engine.run.run_id,
                              "steps": {k: ("ok" if isinstance(v, dict) else v)
                                        for k, v in report["steps"].items()},
                              "summary": report["report"]["summary"]},
                             ensure_ascii=False, indent=2))
        else:
            raise SystemExit(f"unknown command: {args.command}")
    finally:
        await engine.close()
    return 0


def _load_mapping(path: Path):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [m for m in data.get("mappings", [])]


async def _cmd_accounts(config: RecoveryConfig, args) -> int:
    if getattr(args, "action", "list") == "login":
        await _interactive_login("A" if args.actor == "a" else "B", config)
        return 0
    for tag, cfg in (("A", (config.api_id_a, config.phone_a, config.session_a())),
                     ("B", (config.api_id_b, config.phone_b, config.session_b()))):
        api_id, phone, sess = cfg
        print(f"account {tag}: phone={phone} session={'present' if sess else 'none'} "
              f"api_id={'set' if api_id else 'missing'}")
    return 0


async def _interactive_login(tag: str, config: RecoveryConfig) -> None:
    import getpass
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    api_id = config.api_id_a if tag == "A" else config.api_id_b
    api_hash = config.api_hash_a if tag == "A" else config.api_hash_b
    phone = config.phone_a if tag == "A" else config.phone_b
    if not (api_id and api_hash and phone):
        sys.exit(f"missing RECOVERY_API_ID_{tag}/HASH/PHONE for interactive login")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        await client.send_code_request(phone)
    except Exception as exc:  # noqa: BLE001
        await client.disconnect()
        sys.exit(f"code request failed: {type(exc).__name__}: {exc}")
    code = input(f"[{tag}] OTP code for {phone}: ").strip()
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        pw = getpass.getpass(f"[{tag}] 2FA password: ")
        await client.sign_in(password=pw)
    session_string = client.session.save()
    path = config.session_a_file if tag == "A" else config.session_b_file
    if not path:
        path = f"session_{tag.lower()}.session"
    Path(path).write_text(session_string, encoding="utf-8")
    os_chmod = getattr(__import__("os"), "chmod", None)
    if os_chmod:
        import os
        os.chmod(path, 0o600)
    print(f"[{tag}] logged in; session saved to {path} (mode 0600)")
    await client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recovery-v2",
                                description="Telegram Recovery v2 engine")
    p.add_argument("--env", default=None, help="path to an env file")
    sub = p.add_subparsers(dest="command", required=True)

    acc = sub.add_parser("accounts", help="list accounts / interactive login")
    acc.add_argument("action", nargs="?", choices=["list", "login"], default="list")
    acc.add_argument("--actor", choices=["a", "b"], default="a")

    sub.add_parser("resolve-peer", help="resolve RECOVERY_PEER to an InputPeer")
    sub.add_parser("inspect-chat").add_argument("--count", type=int, default=5)
    sub.add_parser("export").add_argument("--max", type=int, default=None)
    sub.add_parser("verify-export")
    sub.add_parser("build-package")
    sub.add_parser("snapshot").add_argument("label", choices=["before", "after"])
    sub.add_parser("clear-target")
    sub.add_parser("import")
    sub.add_parser("reconstruct")
    sub.add_parser("verify")
    ft = sub.add_parser("full-test")
    ft.add_argument("--max", type=int, default=None)
    ft.add_argument("--no-react", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "env", None):
        load_dotenv(args.env)
    config = RecoveryConfig.from_env()
    return asyncio.run(_amain(args, config))


if __name__ == "__main__":
    raise SystemExit(main())