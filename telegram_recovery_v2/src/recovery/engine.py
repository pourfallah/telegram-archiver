"""TelegramRecoveryEngine — the ONE implementation.

Tests, CLI and (later) the web app all drive recovery through this class.
Phases map 1:1 to the CLI commands:

    export        read A -> lossless archive
    verify-export archive consistency checks
    build-package archive -> official import file + media specs
    clear-target  delete B-side history (just_clear=True, revoke=False)
    import        official MTProto import (check/init/upload/start)
    reconstruct   reactions (+ any legitimate reconstructions)
    verify        read target, map, classify, FINAL_REPORT
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from telethon import functions, types

from .archive import ArchiveReader
from .config import RecoveryConfig
from .importer import ImportEngine, build_import_file
from .mapper import map_source_to_target
from .reactions import ReactionReconstructor, verify_reactions
from .source_reader import SourceReader
from .telegram_client import ClientPool
from .verifier import (
    build_fidelity_report,
    read_target_messages,
    verify_archive,
)


def new_run_id() -> str:
    return f"recovery_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class TelegramRecoveryEngine:
    def __init__(self, cfg: RecoveryConfig, run_id: str | None = None) -> None:
        self.cfg = cfg
        self.run_id = run_id or new_run_id()
        self.run_dir = cfg.runs_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_lines: list[str] = []
        # Resolved during run()
        self.peer_a = None   # A<->B peer as seen by A
        self.peer_b = None   # same peer as seen by B
        self.labels: dict[int, str] = {}

    # ------------------------------------------------------------------ log

    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log_lines.append(line)
        with open(self.run_dir / "run.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---------------------------------------------------------------- phases

    async def export(self, peer_a, limit: int | None = None) -> dict:
        """Read the full A-side history into the lossless archive."""
        async with ClientPool(self.cfg) as pool:
            self.labels = {pool.tg_id("A"): "A", pool.tg_id("B"): "B"}
            reader = SourceReader(pool)
            meta = await reader.read_full(
                peer_a, self.run_dir, sender_labels=self.labels, limit=limit
            )
            self.log(f"export: {meta['message_count']} messages, {meta['media_count']} media")
            return meta

    async def build_reaction_plan(self, peer_a) -> int:
        """Identify WHO reacted (A or B) for each archived reaction, using
        messages.getMessageReactionsList from A. Writes reactions_plan.json."""
        async with ClientPool(self.cfg) as pool:
            client = pool.client("A")
            archive_dir = self.run_dir / "archive"
            reacts = [json.loads(l) for l in open(archive_dir / "reactions.ndjson", encoding="utf-8")]
            if not reacts:
                return 0
            by_msg: dict[int, list[dict]] = {}
            for r in reacts:
                by_msg.setdefault(r["source_message_id"], []).append(r)
            plan = []
            for msg_id, items in by_msg.items():
                try:
                    res = await client(
                        functions.messages.GetMessageReactionsListRequest(
                            peer=peer_a, id=msg_id, limit=100
                        )
                    )
                except Exception:
                    # Fallback: assume 'chosen' flags tell us our own reaction;
                    # attribute the rest by count heuristics -> both A and B.
                    for r in items:
                        plan.append({"source_message_id": msg_id, "reactor": None, **r})
                    continue
                for rc in getattr(res, "reactions", []):
                    peer = getattr(rc, "peer_id", None)
                    uid = getattr(peer, "user_id", None) if peer else None
                    reactor = self.labels.get(uid)
                    reaction_obj = rc.reaction
                    inner = (
                        {"_": "ReactionEmoji", "emoticon": getattr(reaction_obj, "emoticon", None)}
                        if hasattr(reaction_obj, "emoticon")
                        else {"_": "ReactionCustomEmoji", "document_id": getattr(reaction_obj, "document_id", None)}
                    )
                    plan.append(
                        {
                            "source_message_id": msg_id,
                            "reactor": reactor,
                            "reaction": inner,
                            "count": 1,
                            "chosen": False,
                        }
                    )
            (archive_dir / "reactions_plan.json").write_text(json.dumps(plan, indent=2))
            self.log(f"reaction plan: {len(plan)} reactions")
            return len(plan)

    def verify_export(self) -> dict:
        res = verify_archive(self.run_dir / "archive")
        (self.run_dir / "verify_export.json").write_text(json.dumps(res, indent=2))
        return res

    async def build_package(self) -> dict:
        archive = ArchiveReader(self.run_dir)
        out = build_import_file(archive, self.run_dir / "import_file.txt")
        (self.run_dir / "package.json").write_text(
            json.dumps({**out, "run_id": self.run_id}, indent=2)
        )
        # attach map: attach name -> media record (consumed by run_import);
        # names MUST match importer.build_import_file's _ext_for exactly.
        from .importer import _ext_for

        attach_map = {}
        for m in archive.messages():
            media = m.get("media")
            if media and media.get("local_file"):
                attach = f"m{m['message_id']}{_ext_for(media)}"
                attach_map[attach] = {
                    "media_id": media["media_id"],
                    "source_message_id": m["message_id"],
                }
        (self.run_dir / "media_attach_map.json").write_text(json.dumps(attach_map, indent=2))
        self.log(f"package: {out['lines']} lines, {out['media_count']} media")
        return out

    async def clear_target(self, peer_b) -> dict:
        """Delete B-side history WITHOUT revoking (A keeps everything)."""
        client = self.pool_b.client("B") if hasattr(self, "pool_b") else None
        from .telegram_client import ClientPool as _CP

        async with _CP(self.cfg) as pool:
            res = await pool.call_with_flood_wait(
                pool.client("B"),
                functions.messages.DeleteHistoryRequest(
                    peer=peer_b, max_id=0, just_clear=True, revoke=False
                ),
            )
            self.log("clear-target: B history cleared (just_clear, no revoke)")
            return {"pts": getattr(res, "pts", None)}

    async def snapshot_target(self, reader: SourceReader, peer_b, name: str) -> dict:
        return await reader.read_target_snapshot(peer_b, self.run_dir / f"{name}.json")

    async def import_package(self, peer_b) -> dict:
        engine = ImportEngine(pool=None, peer=peer_b, run_dir=self.run_dir)  # replaced below
        raise NotImplementedError

    # The real import path used by CLI/scripts -----------------------------

    async def run_import(self, peer_b) -> dict:
        from .importer import MediaImportSpec

        async with ClientPool(self.cfg) as pool:
            engine = ImportEngine(pool, peer_b, self.run_dir)
            pkg = json.loads((self.run_dir / "package.json").read_text())
            # SAFETY: never re-run start after it already fired (no duplicate
            # imports). A started import can only be verified, not redone.
            if engine.state.started:
                return {
                    "already_started": True,
                    "import_id": engine.state.import_id,
                    "started_at": engine.state.started_at,
                    "note": "import already started for this run; verify instead of re-importing",
                }
            fmt = await engine.check_format(self.run_dir / "import_file.txt")
            peer_check = await engine.check_peer()
            specs = self._media_specs()
            # HARD RULE: every <attached:> line must have a token BEFORE start.
            if pkg["media_count"] != len(specs):
                raise RuntimeError(
                    f"package declares {pkg['media_count']} media but only "
                    f"{len(specs)} specs resolved — aborting before init (would corrupt import)"
                )
            await engine.init_import(self.run_dir / "import_file.txt", pkg["media_count"])
            trace = await engine.upload_media(specs)
            if len(trace) < len(specs):
                raise RuntimeError(
                    f"only {len(trace)}/{len(specs)} media uploaded — NOT starting import"
                )
            await engine.start()
            return {
                "format_check": fmt,
                "peer_check": peer_check,
                "import_id": engine.state.import_id,
                "media_uploaded": len(trace),
            }

    def _media_specs(self) -> list:
        from .importer import MediaImportSpec

        specs = []
        pkg = json.loads((self.run_dir / "package.json").read_text())
        idx = {
            m["media_id"]: m
            for m in json.loads(
                (self.run_dir / "archive" / "media" / "media_index.json").read_text()
            )
        }
        for attach, m in self._attach_map().items():
            rec = idx[m["media_id"]]
            specs.append(
                MediaImportSpec(
                    attach_name=attach,
                    local_path=self.run_dir / "archive" / rec["local_file"],
                    source_message_id=m["source_message_id"],
                    media_id=m["media_id"],
                    media_type=rec["type"],
                    is_photo=rec["type"] == "photo",
                    mime=rec.get("mime") or "application/octet-stream",
                    orig_filename=rec.get("filename"),
                    trace={"source_message_id": m["source_message_id"], "media_id": m["media_id"]},
                )
            )
        return specs

    def _attach_map(self) -> dict[str, dict]:
        path = self.run_dir / "media_attach_map.json"
        return json.loads(path.read_text()) if path.exists() else {}

    async def reconstruct(self, peer_a, peer_b, mapping: dict) -> dict:
        async with ClientPool(self.cfg) as pool:
            recon = ReactionReconstructor(pool, peer_b, self.run_dir)
            res = await recon.reconstruct(self.run_dir / "archive", mapping)
            self.log(f"reconstruct: {res['sent']}/{res['planned']} reactions sent")
            return res

    async def verify(self, peer_b, target_before_ids: set[int]) -> dict:
        mapping = map_source_to_target(
            self.run_dir / "archive",
            self.run_dir / "target_after.json",
            target_before_ids,
            self.run_dir,
        )
        id_map = {m["source_message_id"]: m["target_message_id"] for m in mapping["mappings"]}
        async with ClientPool(self.cfg) as pool:
            target_msgs = await read_target_messages(pool, peer_b, list(id_map.values()))
        report = build_fidelity_report(self.run_dir / "archive", self.run_dir, mapping, target_msgs)
        reactions = await verify_reactions(
            ClientPool(self.cfg), peer_b, self.run_dir, mapping
        ) if (self.run_dir / "archive" / "reactions_plan.json").exists() else {"status": "NO_REACTIONS"}
        return {"mapping": mapping, "report_counts": report["counts"], "reactions": reactions}

    # ----------------------------------------------------------------- full

    async def run_full(self, peer_a, peer_b, limit: int | None = None) -> dict:
        """The complete recovery pipeline, end to end."""
        export_meta = await self.export(peer_a, limit=limit)
        await self.build_reaction_plan(peer_a)
        ver = self.verify_export()
        pkg = await self.build_package()
        # write attach map for the importer
        attach_map = {}
        archive = ArchiveReader(self.run_dir)
        for m in archive.messages():
            media = m.get("media")
            if media and media.get("local_file"):
                from .media import attach_name_for

                attach = f"m{m['message_id']}{Path(media['local_file']).suffix}"
                attach_map[attach] = {
                    "media_id": media["media_id"],
                    "source_message_id": m["message_id"],
                }
        (self.run_dir / "media_attach_map.json").write_text(json.dumps(attach_map, indent=2))

        async with ClientPool(self.cfg) as pool:
            reader = SourceReader(pool)
            before = await self.snapshot_target(reader, peer_b, "target_before")
            before_ids = {
                json.loads(l)["message_id"]
                for l in open(self.run_dir / "target_before.json", encoding="utf-8")
            }
            await self.clear_target(peer_b)
            after_clear = await self.snapshot_target(reader, peer_b, "target_after_clear")
            import_res = await self.run_import(peer_b)
            self.log("import: started; waiting for materialization")
            # Materialization poll: target re-read until count stabilizes.
            prev = -1
            for attempt in range(30):
                await asyncio_sleep(20)
                snap = await self.snapshot_target(reader, peer_b, f"target_poll_{attempt}")
                n = json.loads((self.run_dir / f"target_poll_{attempt}.json").read_text())["messages"]
                self.log(f"poll {attempt}: {n} target messages")
                if n == prev and n > 0:
                    break
                prev = n
        # final target snapshot + verify
        async with ClientPool(self.cfg) as pool:
            reader = SourceReader(pool)
            await self.snapshot_target(reader, peer_b, "target_after")
            after_ids = {
                json.loads(l)["message_id"]
                for l in open(self.run_dir / "target_after.json", encoding="utf-8")
            }
        verify_res = await self.verify(peer_b, before_ids)
        final = {
            "run_id": self.run_id,
            "export": export_meta,
            "archive_check": ver,
            "package": pkg,
            "import": import_res,
            "verify": {"mapping_count": verify_res["mapping"]["mapped"], "counts": verify_res["report_counts"], "reactions": verify_res["reactions"]},
        }
        (self.run_dir / "FINAL_SUMMARY.json").write_text(json.dumps(final, indent=2))
        return final


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
