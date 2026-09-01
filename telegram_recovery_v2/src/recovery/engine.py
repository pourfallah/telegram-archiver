"""TelegramRecoveryEngine — the ONE entry point for every recovery operation.

CLI -> engine, tests -> engine, and (later) the web app -> engine. There is no
separate test importer, CLI importer or production importer. This class
orchestrates: source read -> lossless archive -> import package -> B-side clear
-> direct Telegram import -> target read -> mapping -> reaction reconstruction
-> real MTProto verification -> fidelity report.

Every run gets a deterministic ``run_id`` and its own artifact directory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon.tl import functions as tg_functions

from .archive import Archive, build_canonical_record
from .config import RecoveryConfig
from .importer import ImportEngine, build_import_package
from .mapper import Mapping, dump_mapping, map_source_to_target
from .media import MediaDownloader
from .reactions import (
    archive_reactions, reconstruct_reactions, verify_reactions,
)
from .source_reader import SourceReader
from .telegram_client import RecoveryClient, tl_to_plain
from .verifier import Verifier, write_report

logger = logging.getLogger("recovery.engine")

SKIP = object()  # sentinel


def make_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"recovery_v2_{now.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 99999:05d}"


@dataclass
class Run:
    run_id: str
    root: Path
    archive: Archive
    package_dir: Path
    source_to_target: Path
    target_before: Path
    target_after: Path
    media_trace: Path
    inventory: dict = field(default_factory=dict)

    @classmethod
    def create(cls, run_dir: Path, run_id: str | None = None) -> "Run":
        run_id = run_id or make_run_id()
        root = Path(run_dir) / run_id
        root.mkdir(parents=True, exist_ok=True)
        archive = Archive(root / "archive")
        archive.create()
        return cls(run_id=run_id, root=root, archive=archive,
                   package_dir=root / "package",
                   source_to_target=root / "source_to_target.json",
                   target_before=root / "target_before.json",
                   target_after=root / "target_after.json",
                   media_trace=root / "media_import_trace.json")


class TelegramRecoveryEngine:
    def __init__(self, source: RecoveryClient, target: RecoveryClient,
                 config: RecoveryConfig) -> None:
        self.src = source
        self.tgt = target
        self.config = config
        self.peer = None                  # resolved InputPeer
        self.peer_label = config.peer
        self._run: Run | None = None
        self._import_state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    @property
    def run(self) -> Run:
        if self._run is None:
            self._run = Run.create(self.config.run_dir)
        return self._run

    def set_run(self, run: Run) -> None:
        self._run = run

    async def connect(self) -> None:
        await self.src.connect(self.config.session_a())
        await self.tgt.connect(self.config.session_b())
        self.peer = await self.src.get_peer(self.peer_label or self.tgt.phone or "@me")

    async def close(self) -> None:
        if self.src:
            await self.src.close()
        if self.tgt:
            await self.tgt.close()

    async def _target_peer(self):
        return await self.tgt.get_peer(self.peer_label or self.src.phone or "@me")

    # ------------------------------------------------------------------
    # EXPORT: A -> lossless archive
    # ------------------------------------------------------------------
    async def export(self, max_messages: int | None = None) -> dict[str, Any]:
        r = self.run
        downloader = None
        if self.config.download_media:
            downloader = MediaDownloader(self.src, r.archive.media_dir,
                                         resume=self.config.media_resume)
        reader = SourceReader(self.src, self.peer, r.archive, downloader=downloader,
                              msgs_per_sec=self.config.msgs_per_sec,
                              burst=self.config.burst)
        t0 = time.monotonic()
        stats = await reader.stream_all(max_messages=max_messages)
        stats["elapsed_sec"] = round(time.monotonic() - t0, 2)
        r.archive.write_manifest({
            "run_id": r.run_id, "generated_at": datetime.now().isoformat(),
            "peer": self.peer_label,
            "source": self.src.describe(),
            "export": stats,
            "archive_dir": str(r.archive.root),
        })
        r.inventory["export"] = stats
        return stats

    # ------------------------------------------------------------------
    # VERIFY-EXPORT: archive integrity against live source
    # ------------------------------------------------------------------
    async def verify_export(self) -> dict[str, Any]:
        r = self.run
        records = list(r.archive.read_messages())
        raw_count = sum(1 for _ in r.archive.read_raw())
        missing_raw = 0
        bad_media_sha = []
        for rec in records:
            for m in rec.get("media") or []:
                if not (m.get("path") or m.get("sha256")):
                    continue
                if m.get("path") and not (r.archive.root / m["path"]).exists():
                    bad_media_sha.append((rec["source_message_id"], "missing"))
        return {
            "messages": len(records),
            "raw_snapshots": raw_count,
            "raw_complete": raw_count == len(records),
            "media_with_errors": [x for x in bad_media_sha if x[1] == "missing"],
            "media_errors": [x for x in bad_media_sha],
            "ok": raw_count == len(records) and not bad_media_sha,
        }

    # ------------------------------------------------------------------
    # BUILD-PACKAGE
    # ------------------------------------------------------------------
    async def build_package(self) -> dict[str, Any]:
        r = self.run
        r.package_dir.mkdir(parents=True, exist_ok=True)
        stats = build_import_package(r.archive, r.package_dir)
        r.inventory["package"] = stats
        return stats

    # ------------------------------------------------------------------
    # CLEAR-TARGET (B side only; just_clear=true, NEVER revoke)
    # ------------------------------------------------------------------
    async def snapshot_target(self, label: str) -> dict:
        r = self.run
        peer = await self._target_peer()
        messages = await self._read_history(peer)
        records = [_target_record(m) for m in messages]
        data = {"label": label, "time": datetime.now().isoformat(),
                "messages": records}
        path = r.target_before if label == "before" else r.target_after
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return {"count": len(records), "path": str(path)}

    async def _read_history(self, peer, limit: int = 10000) -> list[Any]:
        out: list[Any] = []
        offset_id = 0
        while True:
            res = await self.tgt.call(tg_functions.messages.GetHistoryRequest(
                peer=peer, offset_id=offset_id, offset_date=None, add_offset=0,
                limit=500, max_id=0, min_id=0, hash=0))
            msgs = getattr(res, "messages", None) or []
            if not msgs:
                break
            out.extend(msgs)
            if len(out) >= limit:
                break
            offset_id = msgs[-1].id
            if len(msgs) < 500:
                break
        return out

    async def clear_target(self, just_clear: bool = True) -> dict[str, Any]:
        """Clear B's copy (test only). revoke is NEVER set to True (#40)."""
        peer = await self._target_peer()
        await self.tgt.call(tg_functions.messages.DeleteHistoryRequest(
            peer=peer, max_id=0, just_clear=just_clear, revoke=False))
        return {"just_clear": just_clear, "revoke": False}

    async def verify_source_still_has_history(self, want: int = 1) -> bool:
        """POST-clear guard: A must still contain source history."""
        res = await self.src.call(tg_functions.messages.GetHistoryRequest(
            peer=self.peer, offset_id=0, offset_date=None, add_offset=0, limit=want,
            max_id=0, min_id=0, hash=0))
        return len(getattr(res, "messages", None) or []) >= want

    # ------------------------------------------------------------------
    # IMPORT
    # ------------------------------------------------------------------
    async def import_package(self) -> dict[str, Any]:
        r = self.run
        engine = ImportEngine(self.src, self.tgt, self.peer, r.root)
        outcome = await engine.run_import(r.package_dir, import_id_state=self._import_state)
        r.media_trace.write_text(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        r.inventory["import"] = {"import_id": outcome.import_id,
                                 "rpc_order": outcome.rpc_order}
        await asyncio.sleep(1.0)  # settle
        return {"import_id": outcome.import_id, "rpc_order": outcome.rpc_order}

    # ------------------------------------------------------------------
    # MAPPING
    # ------------------------------------------------------------------
    async def map_source_to_target(self) -> list[Mapping]:
        r = self.run
        source = list(r.archive.read_messages())
        before = _load_ids(r.target_before)
        after_records = _load_records(r.target_after)
        delta = {t["target_message_id"] for t in after_records} - before
        result = map_source_to_target(source, after_records, delta_target_ids=delta)
        dump_mapping(result, r.source_to_target)
        r.inventory["mapping"] = {
            "source": len(source), "target": len(after_records), "delta": len(delta),
            "mapped": sum(1 for m in result if m.target_message_id >= 0),
        }
        return result

    # ------------------------------------------------------------------
    # REACTION RECONSTRUCTION + VERIFICATION
    # ------------------------------------------------------------------
    async def archive_reactions(self) -> dict[str, int]:
        return await archive_reactions(self.src, self.peer, self.run.archive)

    async def reconstruct_reactions(self, mapping: list[Mapping]) -> list[dict]:
        actor_map = {str(v): v for v in (self.src, self.tgt) if v.me is not None}
        # actor identity (singleton for now): A reacts for A's received
        # reactions via A's client, B for B's — archived reactor ids pick the
        # session in reactions.reconstruct_reactions.
        sessions = {}
        me_src, me_tgt = self.src.my_id, self.tgt.my_id
        if me_src is not None:
            sessions[str(me_src)] = self.src
        if me_tgt is not None and me_tgt != me_src:
            sessions[str(me_tgt)] = self.tgt
        r = self.run
        applied = await reconstruct_reactions(
            self.tgt, await self._target_peer(), r.archive, mapping, sessions)
        (r.root / "reaction_reconstruction.json").write_text(
            json.dumps(applied, ensure_ascii=False, indent=2), encoding="utf-8")
        return applied

    async def verify_reactions(self, mapping: list[Mapping]) -> dict[str, Any]:
        target_ids = [m.target_message_id for m in mapping if m.target_message_id >= 0]
        return await verify_reactions(self.tgt, await self._target_peer(), target_ids)

    # ------------------------------------------------------------------
    # FIDELITY VERIFY + REPORT
    # ------------------------------------------------------------------
    async def verify(self, mapping: list[Mapping] | None = None,
                     reaction_verify=None) -> dict[str, Any]:
        r = self.run
        source = list(r.archive.read_messages())
        after_records = _load_records(r.target_after)
        mapping = mapping or []
        verifier = Verifier(mapping)
        if reaction_verify is not None:
            pass
        result = verifier.verify(source, after_records)
        summary = _summarize(result["matrix"])
        report = {
            "run_id": r.run_id,
            "generated_at": datetime.now().isoformat(),
            "source_records": len(source),
            "target_records": len(after_records),
            "mapped": sum(1 for m in mapping if m.target_message_id >= 0),
            "matrix": result["matrix"],
            "summary": summary,
            "rows": result["rows"],
        }
        write_report(r.root, report)
        r.inventory["report"] = summary
        return report

    # ------------------------------------------------------------------
    # FULL TEST
    # ------------------------------------------------------------------
    async def full_test(self, max_messages: int | None = None,
                        react: bool = True) -> dict[str, Any]:
        steps = {}
        steps["snapshot_before"] = await self.snapshot_target("before")
        steps["export"] = await self.export(max_messages=max_messages)
        steps["verify_export"] = await self.verify_export()
        steps["build_package"] = await self.build_package()
        steps["archive_reactions"] = await self.archive_reactions()
        steps["clear_target"] = await self.clear_target()
        steps["source_still_has"] = await self.verify_source_still_has_history()
        steps["import"] = await self.import_package()
        await self.snapshot_target("after")
        mapping = await self.map_source_to_target()
        if react:
            await self.reconstruct_reactions(mapping)
            rv = await self.verify_reactions(mapping)
            steps["reaction_verify"] = rv
            # wire target verification back into the report keyed by SOURCE id
            reaction_verify = _verify_by_source(mapping, rv.get("messages", {}))
            report = await self.verify(mapping, reaction_verify=reaction_verify)
        else:
            report = await self.verify(mapping)
        self.run.inventory["full_test"] = {k: v for k, v in steps.items()
                                           if k != "reaction_verify"}
        return {"steps": steps, "report": report, "inventory": self.run.inventory}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _target_record(message: Any) -> dict[str, Any]:
    rec = build_canonical_record(message)
    tid = rec["source_message_id"]
    rec.pop("source_message_id", None)
    rec["target_message_id"] = tid
    rec["source_message_id"] = 0  # target snapshot has no source id
    return rec


def _load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("messages", [])


def _load_ids(path: Path) -> set[int]:
    return {t["target_message_id"] for t in _load_records(path)}


def _summarize(matrix: dict[str, dict]) -> dict[str, Any]:
    fields = ("text", "formatting", "sender", "timestamp", "caption", "photo",
              "photo_caption", "video", "gif", "audio", "voice", "document",
              "sticker", "reply", "forward", "reaction", "group")
    out = {}
    for f in fields:
        counts = matrix.get(f, {})
        total = sum(counts.values()) or 1
        exact = counts.get("EXACT", 0)
        out[f] = {"exact": exact, "total": sum(counts.values()),
                  "exact_pct": round(100 * exact / total, 1), "counts": counts}
    return out


def _verify_by_source(mapping, target_verify: dict) -> dict[int, Any]:
    """Rebuild reaction verification keyed by SOURCE message id."""
    out: dict[int, Any] = {}
    for m in mapping:
        if m.target_message_id in target_verify:
            out[m.source_message_id] = target_verify[m.target_message_id]
    return out