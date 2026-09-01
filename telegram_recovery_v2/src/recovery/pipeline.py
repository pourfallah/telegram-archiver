"""Phased, resumable, lazy sampling pipeline for the A<->C -> A<->B experiment.

Phase model (see docs/E2E_TEST.md and the experiment brief):
  P1 lightweight catalog   (resumable, checkpointed, NO media/reactions/full-raw)
  P2 stratified sampling   (year buckets, deterministic seed, diversity)
  P3 LAZY full fetch        (only the ~selected ids + reply parents + group members)
  P4 media/reactions LAZY   (only for the selected set)
  P5 package from snapshot  (only the sample; roundtrip-verified)
  P6 import (5 official methods) -> A<->B only
  P7 verify against real target objects -> report + decision

SOURCE A<->C is read-only at every phase. Only TARGET A<->B may be modified,
and only after --confirm (or unless --dry-run).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from telethon.tl import functions as f

from .config import RecoveryConfig, load_dotenv
from .engine import Run  # noqa: F401  (re-export for entry points)
from .telegram_client import RecoveryClient, default_connect, tl_to_plain
from .archive import Archive, build_canonical_record
from .media import MediaDownloader
from .importer import ImportEngine, build_import_package
from .mapper import map_source_to_target
from . import verifier as V

SCAN_BATCH = 500
CLOSURE_CATALOG_NEEDED = True  # group/reply-parent expansion uses the catalog


class Abort(Exception):
    """Controlled experiment aborted with a reason."""


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def seed_for(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()


def shorten(s, n=40):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# clients / peers
# ---------------------------------------------------------------------------
async def build_clients(cfg: RecoveryConfig):
    src = RecoveryClient(cfg.api_id_a, cfg.api_hash_a, cfg.phone_a, connect=default_connect)
    tgt = RecoveryClient(cfg.api_id_b, cfg.api_hash_b, cfg.phone_b, connect=default_connect)
    await src.connect(cfg.session_a())
    await tgt.connect(cfg.session_b())
    return src, tgt


async def resolve_peer(client: RecoveryClient, target: str) -> dict:
    inp = await client.get_peer(target)
    full = await client.client.get_entity(target)
    props = {}
    for k in ("first_name", "last_name", "username", "title", "phone"):
        v = getattr(full, k, None)
        if v:
            props[k] = v
    return {"input_peer": str(inp), "id": getattr(full, "id", None),
            "type": type(full).__name__, "props": props}


async def identify(peer_plain: dict) -> str:
    return f"peer_{peer_plain['type']}:{peer_plain['id']}"


def assert_target_is_ab(src, tgt_peer_desc: dict) -> None:
    """The TARGET peer must be the A<->B private chat, never the C chat.

    A private chat peer, as seen by its owner, carries the OTHER user's id. So
    the A<->B chat resolved via B has id == A's user id. If someone passes C's
    phone as --target-peer, B would resolve the B<->C chat whose id == C's id,
    != A's id -> refused. This guarantees a source C number can never become the
    recovery target."""
    a_id = src.my_id
    tgt_id = tgt_peer_desc.get("id")
    if a_id is not None and tgt_id is not None and tgt_id != a_id:
        raise Abort(
            f"target peer ({tgt_peer_desc.get('type')}:{tgt_id}) is NOT the A<->B "
            f"private chat (its id must equal SOURCE A's user id {a_id}). Refusing.")


# ---------------------------------------------------------------------------
# P1 — lightweight resumable catalog (NO media, NO reactions, NO full raw)
# ---------------------------------------------------------------------------
def _peer_uid(peer_id) -> int | None:
    if peer_id is None:
        return None
    for n in ("user_id", "channel_id", "chat_id"):
        v = getattr(peer_id, n, None)
        if v is not None:
            return v
    return getattr(peer_id, "id", None)


def _cheap_media_type(media) -> list[str]:
    """Media subtypes WITHOUT building full descriptors (cheap for 198k msgs)."""
    if media is None:
        return []
    name = type(media).__name__
    if name == "MessageMediaPhoto":
        return ["photo"]
    if name != "MessageMediaDocument":
        return [name.replace("MessageMedia", "").lower()] or ["other"]
    doc = getattr(media, "document", None)
    out = []
    for a in (getattr(doc, "attributes", None) or []):
        an = type(a).__name__
        if an == "DocumentAttributeSticker":
            out.append("sticker")
        elif an == "DocumentAttributeVideo":
            out.append("video")
        elif an == "DocumentAttributeAudio":
            out.append("voice" if getattr(a, "voice", False) else "audio")
        elif an == "DocumentAttributeAnimated":
            out.append("animation")
        elif an == "DocumentAttributeFilename":
            out.append("document")
    return out or ["document"]


def _dt_str(dt) -> str | None:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:  # noqa: BLE001
        return str(dt)


def lightweight_record(m) -> dict:
    """Minimal metadata per message — the ONLY thing stored for the whole chat."""
    r = getattr(m, "reply_to", None)
    reactions = getattr(m, "reactions", None)
    media_types = _cheap_media_type(getattr(m, "media", None))
    return {
        "id": int(getattr(m, "id", 0)),
        "date": _dt_str(getattr(m, "date", None)),
        "sender_id": _peer_uid(getattr(m, "from_id", None)),
        "grouped_id": getattr(m, "grouped_id", None),
        "has_media": bool(media_types),
        "media_types": media_types,
        "has_reply": r is not None,
        "reply_to_id": getattr(r, "reply_to_msg_id", None) if r is not None else None,
        "has_reactions": bool(reactions and getattr(reactions, "results", None)),
        "has_forward": getattr(m, "fwd_from", None) is not None,
        "text_len": len(getattr(m, "message", "") or ""),
    }


async def discover_catalog(client: RecoveryClient, src_peer,
                           catalog_path: Path, checkpoint_path: Path,
                           resume: bool = True) -> dict:
    """Resumable lightweight catalog (newest -> oldest, dedup on resume)."""
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[int] = set()
    if resume and catalog_path.exists():
        for line in catalog_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    seen.add(int(json.loads(line)["id"]))
                except Exception:  # noqa: BLE001
                    pass

    checkpoint = {"peer_id": str(src_peer), "offset_id": 0, "processed_count": len(seen),
                  "status": "running", "updated_at": now_iso()}
    offset_id = 0
    added = 0
    with catalog_path.open("a", encoding="utf-8") as fh:
        while True:
            res = await client.call(f.messages.GetHistoryRequest(
                peer=src_peer, offset_id=offset_id, offset_date=None, add_offset=0,
                limit=SCAN_BATCH, max_id=0, min_id=0, hash=0))
            msgs = getattr(res, "messages", None) or []
            if not msgs:
                break
            for m in msgs:
                mid = int(getattr(m, "id", 0))
                if mid in seen:
                    continue
                fh.write(json.dumps(lightweight_record(m), ensure_ascii=False) + "\n")
                seen.add(mid)
                added += 1
            offset_id = msgs[-1].id
            checkpoint.update({"offset_id": offset_id,
                               "processed_count": len(seen), "updated_at": now_iso()})
            checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
            # NOTE: do NOT break on len(msgs) < SCAN_BATCH — messages.getHistory
            # caps each page at 100 for this peer even when limit=500 is asked, so
            # a partial first page does NOT mean the history ended. Only an EMPTY
            # page (handled above) terminates discovery.
    checkpoint.update({"status": "complete", "updated_at": now_iso()})
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
    return {"messages": len(seen), "added_this_run": added}


def load_catalog(catalog_path: Path) -> list[dict]:
    out = []
    if not catalog_path.exists():
        return out
    for line in catalog_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# P2 — stratified sampling (from the lightweight catalog only)
# ---------------------------------------------------------------------------
def msg_kind(rec: dict) -> str:
    if rec.get("grouped_id") is not None:
        return "album"
    if rec.get("has_reply"):
        return "reply"
    if rec.get("has_forward"):
        return "forward"
    if rec.get("has_media"):
        return (rec.get("media_types") or ["media"])[0]
    if rec.get("has_reactions"):
        return "reaction"
    if rec.get("text_len", 0) > 0 and rec.get("media_types") == []:
        return "text"
    return "text"


def time_buckets(catalog: list[dict], years_target: int) -> list[tuple[str, list[dict]]]:
    by_year: dict[str, list[dict]] = {}
    for r in catalog:
        y = str(r.get("date") or "")[:4]
        if y.isdigit():
            by_year.setdefault(y, []).append(r)
    if len(by_year) >= years_target or not by_year:
        return sorted(by_year.items(), key=lambda kv: kv[0])
    by_mo: dict[str, list[dict]] = {}
    for r in catalog:
        d = str(r.get("date") or "")
        if len(d) >= 7:
            by_mo.setdefault(d[:7], []).append(r)
    return sorted(by_mo.items(), key=lambda kv: kv[0])


def select_ids(catalog: list[dict], count: int, seed: str,
               years_target: int) -> list[dict]:
    """Deterministic, time-bucketed, type-diverse selection of lightweight recs.
    Returns the RECORDS (ids) whose complete groups/reply-parents are added by
    apply_closures."""
    rng = random.Random(seed)
    buckets = [b for b in time_buckets(catalog, years_target) if b[1]]
    n = len(buckets)
    if n == 0:
        return []
    per = [max(1, count // n) for _ in range(n)]
    rem = count - sum(per)
    for _ in range(rem):
        per[rng.randrange(n)] += 1
    if sum(per) < count:
        per[-1] += count - sum(per)

    chosen: dict[int, dict] = {}
    for (label, recs), k in zip(buckets, per):
        pool_ids = [r["id"] for r in recs if r["id"] not in chosen]
        pool = [r for r in recs if r["id"] in pool_ids]
        if not pool:
            continue
        types: dict[str, list[dict]] = {}
        for r in pool:
            types.setdefault(msg_kind(r), []).append(r)
        order = []
        for t, members in types.items():
            order.append(rng.choice(members))
        picked = 0
        for r in order:
            chosen[r["id"]] = r
            picked += 1
        while picked < k and pool:
            cand = rng.choice(pool)
            if cand["id"] in chosen:
                pool = [x for x in pool if x["id"] != cand["id"]]
                continue
            chosen[cand["id"]] = cand
            picked += 1
    return list(chosen.values())


def apply_closures(catalog: list[dict], selected: list[dict]) -> list[dict]:
    """Add complete grouped-media members + required reply parents (#10/#11)."""
    by_id = {r["id"]: r for r in catalog}
    ids = set(r["id"] for r in selected)
    groups = [r.get("grouped_id") for r in selected if r.get("grouped_id")]
    for g in groups:
        for r in catalog:
            if r.get("grouped_id") == g:
                ids.add(r["id"])
    parents = [r.get("reply_to_id") for r in selected if r.get("reply_to_id")]
    for p in parents:
        if p in by_id:
            ids.add(p)
    out = [by_id[i] for i in ids]
    out.sort(key=lambda r: str(r.get("date") or ""))
    return out


def years_covered(recs: list[dict]) -> list[int]:
    ys = set()
    for r in recs:
        y = str(r.get("date") or "")[:4]
        if y.isdigit():
            ys.add(int(y))
    return sorted(ys)


def date_range(recs: list[dict]):
    dates = sorted(str(r.get("date") or "") for r in recs if r.get("date"))
    return (dates[0], dates[-1]) if dates else (None, None)


def catalog_stats(catalog: list[dict]) -> dict:
    years = years_covered(catalog)
    dmin, dmax = date_range(catalog)
    kinds: dict[str, int] = {}
    for r in catalog:
        kinds[msg_kind(r)] = kinds.get(msg_kind(r), 0) + 1
    return {"messages": len(catalog), "date_min": dmin, "date_max": dmax,
            "years": years, "kind_distribution": kinds}


# ---------------------------------------------------------------------------
# P3 — LAZY full fetch for the selected ids only (+ replies/groups closure)
# ---------------------------------------------------------------------------
def _msg_peer_id(m) -> int | None:
    p = getattr(m, "peer_id", None)
    if p is None:
        return None
    for n in ("user_id", "chat_id", "channel_id"):
        v = getattr(p, n, None)
        if v is not None:
            return v
    return getattr(p, "id", None)


def _input_peer_id(peer) -> int | None:
    for n in ("user_id", "chat_id", "channel_id"):
        v = getattr(peer, n, None)
        if v is not None:
            return v
    return getattr(peer, "id", None)


async def fetch_by_ids(client: RecoveryClient, src_peer, ids: list[int]) -> list:
    """Fetch full Messages ONLY for the given ids, via messages.getMessages(id=...).

    Telethon's GetMessagesRequest accepts ONLY ``id`` (no peer). We keep the
    source-peer guarantee explicit: resolve the source peer's real id once and
    drop any returned message whose peer_id is not that peer, so a wrong peer
    can never be silently fetched."""
    if not ids:
        return []
    expected_peer = _input_peer_id(src_peer)
    out: list = []
    for i in range(0, len(ids), 90):
        chunk = ids[i:i + 90]
        res = await client.call(f.messages.GetMessagesRequest(id=chunk))
        for m in (getattr(res, "messages", None) or []):
            pid = _msg_peer_id(m)
            if expected_peer is not None and pid is not None and pid != expected_peer:
                continue  # not from the source A<->C peer — never fetch the wrong chat
            out.append(m)
    return out


def closures_from_catalog(catalog: list[dict], ids: list[int]) -> list[int]:
    """Expand ids with full group members + direct reply parents (catalog-based)."""
    by_id = {r["id"]: r for r in catalog}
    out = set(ids)
    for i in ids:
        r = by_id.get(i)
        if not r:
            continue
        if r.get("grouped_id"):
            for x in catalog:
                if x.get("grouped_id") == r["grouped_id"]:
                    out.add(x["id"])
        p = r.get("reply_to_id")
        if p in by_id:
            out.add(p)
    return sorted(out)


async def lazy_materialize(run: Run, cfg: RecoveryConfig, src, src_peer,
                           final_ids: list[int],
                           source_peer_id: str, target_peer_id: str) -> list[dict]:
    """Fetch FULL objects only for final_ids, download media, write snapshot+package."""
    tls = await fetch_by_ids(src, src_peer, final_ids)
    tl_by_id = {int(getattr(m, "id", 0)): m for m in tls}
    recs: list[dict] = []
    for i in final_ids:
        m = tl_by_id.get(i)
        if m is None:
            continue  # deleted / moved — skip, snapshot will match by id
        rec = build_canonical_record(m)
        if rec.get("media"):
            dl = MediaDownloader(src, run.archive.media_dir, resume=False)
            rec["media"] = await dl.download_all(m, rec["media"])
        rec["raw"] = tl_to_plain(m)
        recs.append(rec)

    for rec in recs:
        run.archive.append_canonical({k: v for k, v in rec.items() if k != "raw"})
        if rec.get("raw"):
            run.archive.append_raw(rec["raw"])
    run.archive.write_manifest({"run_id": run.run_id, "source_peer": source_peer_id,
                                "target_peer": target_peer_id, "messages": len(recs),
                                "generated_at": now_iso()})
    build_import_package(run.archive, run.package_dir)
    snapshot = {"run_id": run.run_id, "source_peer": source_peer_id,
                "target_peer": target_peer_id, "seed": seed_for(run.run_id),
                "source_message_ids": final_ids, "messages": recs}
    (run.root / "source_sample_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return recs


def fingerprint(package_dir: Path) -> str:
    h = hashlib.sha256()
    chat = package_dir / "_chat.txt"
    if chat.exists():
        h.update(b"chat:" + hashlib.sha256(chat.read_bytes()).digest())
    media_dir = package_dir / "media"
    ents = []
    if media_dir.is_dir():
        for p in sorted(media_dir.iterdir()):
            if p.is_file():
                ents.append((p.name, hashlib.sha256(p.read_bytes()).hexdigest()))
    for name, sha in ents:
        h.update(f"media:{name}={sha}\n".encode())
    return h.hexdigest()[:64]


async def verify_roundtrip(run: Run, recs: list[dict]) -> dict:
    manifest = json.loads((run.package_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = manifest.get("rows", [])
    chat_txt = (run.package_dir / "_chat.txt").read_text(encoding="utf-8")
    pkg_media = set()
    for p in (run.package_dir / "media").glob("*"):
        if p.is_file():
            pkg_media.add(hashlib.sha256(p.read_bytes()).hexdigest())
    issues = []
    if len(rows) != len(recs):
        issues.append(f"count mismatch: package={len(rows)} snapshot={len(recs)}")
    snap_media = {m.get("sha256") for r in recs for m in (r.get("media") or []) if m.get("sha256")}
    if snap_media != pkg_media:
        issues.append("media set mismatch")
    for r in recs:
        t = (r.get("text") or "").strip()
        if t and t not in chat_txt:
            issues.append(f"text missing: {shorten(t)}")
    if issues:
        raise Abort("PACKAGE ROUNDTRIP MISMATCH: " + "; ".join(issues[:5]))
    return {"ok": True, "package_hash": fingerprint(run.package_dir),
            "messages": len(recs), "media": len(pkg_media)}


# ---------------------------------------------------------------------------
# sample artifacts + human preview
# ---------------------------------------------------------------------------
def write_sample_artifacts(run: Run, selected_recs: list[dict], seed: str,
                           count_requested: int, catalog: list[dict],
                           source_peer_id: str, target_peer_id: str) -> dict:
    ids = [r["id"] for r in selected_recs]
    manifest = {
        "run_id": run.run_id, "source_peer": source_peer_id, "target_peer": target_peer_id,
        "sample_count_requested": count_requested, "sample_count_actual": len(ids),
        "years_covered": years_covered(selected_recs),
        "date_range": date_range(selected_recs),
        "seed": seed, "message_ids": ids,
        "media_types": sorted({mt for r in selected_recs
                               for mt in (r.get("media_types") or [])}),
        "reaction_count": sum(1 for r in selected_recs if r.get("has_reactions")),
        "reply_count": sum(1 for r in selected_recs if r.get("has_reply")),
        "group_count": len({r.get("grouped_id") for r in selected_recs if r.get("grouped_id")}),
        "catalog_stats": catalog_stats(catalog),
    }
    p = run.root / "sample_manifest.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    preview = ["<!doctype html><html><meta charset='utf-8'><title>Sample preview</title>",
               "<style>table{border-collapse:collapse;font:12px monospace}td,th{border:1px solid #999;padding:3px 6px;text-align:left}</style>",
               "<body><h1>SAMPLE PREVIEW</h1><table><tr><th>#</th><th>date</th><th>type</th>"
               "<th>media</th><th>reply</th><th>grp</th><th>fwd</th><th>rx</th><th>text</th></tr>"]
    for i, r in enumerate(selected_recs, 1):
        preview.append(f"<tr><td>{i}</td><td>{r.get('date')}</td><td>{msg_kind(r)}</td>"
                       f"<td>{','.join(r.get('media_types') or [])}</td><td>{r.get('reply_to_id')}</td>"
                       f"<td>{r.get('grouped_id')}</td><td>{r.get('has_forward')}</td>"
                       f"<td>{r.get('has_reactions')}</td><td>{r.get('text_len')}ch</td></tr>")
    preview += ["</table></body></html>"]
    (run.root / "SAMPLE_PREVIEW.html").write_text("\n".join(preview), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# P6/P7 — target (A<->B) operations, verification, report
# ---------------------------------------------------------------------------
def target_record(m) -> dict:
    rec = build_canonical_record(m)
    tid = rec["source_message_id"]
    rec["source_message_id"] = 0
    rec["target_message_id"] = tid
    return rec


async def read_target_records(tgt, tgt_peer, limit: int = 5000) -> list[dict]:
    res = await tgt.call(f.messages.GetHistoryRequest(peer=tgt_peer, offset_id=0,
                                                      offset_date=None, add_offset=0,
                                                      limit=min(limit, 500), max_id=0,
                                                      min_id=0, hash=0))
    msgs = getattr(res, "messages", None) or []
    return [target_record(m) for m in msgs]


async def snapshot_target(run: Run, label: str, tgt, tgt_peer) -> dict:
    recs = await read_target_records(tgt, tgt_peer)
    data = {"label": label, "time": now_iso(), "count": len(recs), "records": recs}
    path = run.target_before if label == "before" else run.target_after
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"label": label, "count": len(recs)}


async def check_target_peer(tgt, tgt_peer) -> str:
    res = await tgt.call(f.messages.CheckHistoryImportPeerRequest(peer=tgt_peer))
    return str(res)[:300]


async def clear_target(tgt, tgt_peer) -> dict:
    await tgt.call(f.messages.DeleteHistoryRequest(peer=tgt_peer, max_id=0,
                                                   just_clear=True, revoke=False))
    return {"just_clear": True, "revoke": False}


async def run_import(run: Run, src, tgt, tgt_peer, import_state: dict):
    eng = ImportEngine(src, tgt, tgt_peer, run.root)
    return await eng.run_import(run.package_dir, import_id_state=import_state)


async def reconstruct_react(cfg, src, tgt, src_peer, tgt_peer, recs, mapping) -> list[dict]:
    from telethon.tl import types as tl
    from .reactions import reaction_to_tl
    me = {src.my_id: src, tgt.my_id: tgt}
    out = []
    tgt_of = {m.source_message_id: m.target_message_id for m in mapping}
    for s in recs:
        tid = tgt_of.get(s["source_message_id"])
        if tid is None or tid < 0:
            continue
        try:
            res = await src.call(f.messages.GetMessageReactionsListRequest(
                peer=src_peer, id=s["source_message_id"], limit=100, reaction=None, offset=""))
        except Exception as exc:  # noqa: BLE001
            out.append({"source": s["source_message_id"], "status": "REACTOR_READ_FAILED",
                        "error": str(exc)[:120]})
            continue
        for r in getattr(res, "reactions", None) or []:
            rid = _peer_uid(r.peer_id)
            sess = me.get(rid)
            if sess is None:
                out.append({"source": s["source_message_id"], "target": tid, "reactor": rid,
                            "status": "REACTOR_SESSION_REQUIRED"})
                continue
            rxn = {"__tl__": type(r.reaction).__name__}
            if hasattr(r.reaction, "emoticon"):
                rxn["emoticon"] = r.reaction.emoticon
            if hasattr(r.reaction, "document_id"):
                rxn["document_id"] = r.reaction.document_id
            try:
                await sess.call(f.messages.SendReactionRequest(
                    peer=tgt_peer, msg_id=tid, big=False, add_to_recent=True,
                    reaction=[reaction_to_tl(rxn)]))
                out.append({"source": s["source_message_id"], "target": tid,
                            "reactor": rid, "reaction": rxn, "status": "RECONSTRUCTED"})
            except Exception as exc:  # noqa: BLE001
                out.append({"source": s["source_message_id"], "target": tid,
                            "reactor": rid, "status": "FAILED", "error": str(exc)[:120]})
    return out


async def read_target_reactions(tgt, tgt_peer, target_ids: list[int]) -> dict:
    from telethon.tl import types as tl
    if not target_ids:
        return {}
    res = await tgt.call(f.messages.GetMessagesReactionsRequest(peer=tgt_peer, id=target_ids))
    result = {}
    for up in getattr(res, "updates", None) or []:
        if isinstance(up, tl.UpdateMessageReactions):
            rows = [{"reaction": {"__tl__": type(rc.reaction).__name__,
                                  **({"emoticon": rc.reaction.emoticon} if hasattr(rc.reaction, "emoticon") else {}),
                                  **({"document_id": rc.reaction.document_id} if hasattr(rc.reaction, "document_id") else {})},
                     "count": getattr(rc, "count", 0)} for rc in
                    (getattr(getattr(up, "reactions", None), "results", None) or [])]
            result[int(up.msg_id)] = rows
    return result


def reaction_verify_for(target_rows: dict, mapping) -> dict:
    out = {}
    for m in mapping:
        rows = target_rows.get(m.target_message_id)
        if rows is not None:
            out[m.source_message_id] = rows
    return out


async def source_immutability(cfg, src, src_peer, recs) -> dict:
    """Re-read ONLY the sampled source ids; compare to snapshot (#source immutability)."""
    ids = [r["source_message_id"] for r in recs]
    tls = await fetch_by_ids(src, src_peer, ids)
    cur = {int(getattr(m, "id", 0)): build_canonical_record(m) for m in tls}
    diffs = []
    for r in recs:
        c = cur.get(r["source_message_id"])
        if c is None:
            diffs.append(f"source msg {r['source_message_id']} missing"); continue
        if (c.get("text") or "") != (r.get("text") or ""):
            diffs.append(f"msg {r['source_message_id']} text changed")
        if (c.get("date") or "") != (r.get("date") or ""):
            diffs.append(f"msg {r['source_message_id']} date changed")
    return {"SOURCE_UNTOUCHED": "YES" if not diffs else "NO", "diffs": diffs[:10]}


def pick_decision(target_new: int, ts: dict) -> str:
    if target_new == 0:
        return "IMPORT_FAILED"
    if ts.get("TIMESTAMP_EXACT", 0) > 0:
        return "HISTORICAL_IMPORT_VERIFIED"
    if ts.get("IMPORTED_METADATA_ONLY", 0) > 0:
        return "PARTIAL_HISTORICAL_IMPORT"
    return "HISTORICAL_TIMELINE_NOT_RESTORED"


def build_report(run: Run, *, source_peer_id, target_peer_id, recs, after_recs,
                 delta, matrix, evidence, untouched, recon, rt, seed, decision) -> dict:
    features = {}
    for f in ("text", "formatting", "sender", "timestamp", "caption", "photo",
              "video", "gif", "audio", "voice", "document", "sticker",
              "reply", "forward", "reaction", "group"):
        c = matrix.get(f, {})
        features[f] = {"exact": c.get("EXACT", 0), "total": sum(c.values()), "counts": c}
    report = {
        "run_id": run.run_id, "source_peer": source_peer_id, "target_peer": target_peer_id,
        "source_count": len(recs), "target_new": len(delta),
        "date_range": date_range(recs), "years": years_covered(recs),
        "seed": seed, "package_hash": rt["package_hash"],
        "source_untouched": untouched["SOURCE_UNTOUCHED"],
        "reaction_reconstruction": recon, "features": features,
        "evidence": evidence, "decision": decision,
    }
    write_reports(run, report)
    return report


def write_reports(run: Run, report: dict) -> None:
    (run.root / "FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    feat = report["features"]
    frows = "".join(f"<tr><td>{k}</td><td>{v['exact']}/{v['total']}</td>"
                    f"<td>{v['counts']}</td></tr>" for k, v in feat.items())
    ev = report["evidence"]
    erows = "".join(f"<tr><td>{e['src']}</td><td>{e.get('src_date')}</td>"
                    f"<td>{e['tgt']}</td><td>{e.get('tgt_date')}</td>"
                    f"<td>{e.get('ts_class')}</td><td>{e.get('fwd_date')}</td></tr>" for e in ev)
    html = f"""<!doctype html><html><meta charset="utf-8"><title>Historical Sample Recovery</title>
<style>table{{border-collapse:collapse;font:12px monospace}}td,th{{border:1px solid #999;padding:3px 6px}}</style>
<body><h1>FINAL HISTORICAL SAMPLE RECOVERY REPORT</h1>
<p>run_id <b>{report['run_id']}</b> &nbsp; SOURCE {report['source_peer']} &nbsp; TARGET {report['target_peer']}</p>
<p>SOURCE COUNT {report['source_count']} &nbsp; TARGET NEW {report['target_new']} &nbsp;
DATE RANGE {report['date_range']} &nbsp; YEARS {report['years']}</p>
<p>DECISION <b>{report['decision']}</b> &nbsp; SOURCE_UNTOUCHED {report['source_untouched']}</p>
<h2>FEATURES</h2><table><tr><th>feature</th><th>exact/total</th><th>breakdown</th></tr>{frows}</table>
<h2>RAW EVIDENCE</h2><table><tr><th>src</th><th>src date</th><th>tgt</th><th>tgt date</th><th>class</th><th>fwd date</th></tr>{erows}</table>
</body></html>"""
    (run.root / "FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.html").write_text(html, encoding="utf-8")


def build_evidence(recs, after_recs, mapping) -> list[dict]:
    t_by = {t["target_message_id"]: t for t in after_recs}
    tgt_of = {m.source_message_id: m.target_message_id for m in mapping}
    pairs = []
    for s in recs:
        tid = tgt_of.get(s["source_message_id"])
        t = t_by.get(tid) if tid and tid >= 0 else None
        if t is None:
            continue
        pairs.append({"src": s["source_message_id"], "src_date": s.get("date"),
                      "tgt": tid, "tgt_date": t.get("date"),
                      "fwd_date": (t.get("forward") or {}).get("date"),
                      "ts_class": V._timestamp(s, t)["class"]})
    pairs.sort(key=lambda p: str(p["src_date"]))
    return pairs[:8]


# ---------------------------------------------------------------------------
# top-level phase orchestrators (called by the CLI entry points)
# ---------------------------------------------------------------------------
def make_run(cfg: RecoveryConfig, run_id: str) -> Run:
    root = Path(cfg.run_dir) / run_id
    root.mkdir(parents=True, exist_ok=True)
    archive = Archive(root / "archive")
    archive.create()
    return Run(run_id=run_id, root=root, archive=archive, package_dir=root / "package",
               source_to_target=root / "source_to_target.json",
               target_before=root / "target_before.json",
               target_after=root / "target_after.json",
               media_trace=root / "media_import_trace.json")


async def run_sample_history(cfg: RecoveryConfig, *, source_peer: str,
                             target_peer: str, count: int, years: int,
                             seed: str | None, run_id: str | None,
                             resume: bool = True) -> str:
    """P1 discovery (resumable) + P2 stratified sampling. Writes run artifacts."""
    if not source_peer:
        raise Abort("--source-peer required (the C contact, e.g. +989353114546)")
    run_id = run_id or ("sample_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    seed = seed or seed_for(run_id)
    run = make_run(cfg, run_id)

    src, tgt = await build_clients(cfg)
    try:
        src_peer_desc = await resolve_peer(src, source_peer)
        tgt_default = target_peer or cfg.phone_a or cfg.peer
        tgt_peer_desc = await resolve_peer(tgt, tgt_default)
        src_id = await identify(src_peer_desc)
        tgt_id = await identify(tgt_peer_desc)
        if src_id == tgt_id:
            raise Abort("source peer == target peer; refusing")
        assert_target_is_ab(src, tgt_peer_desc)

        print(f"SOURCE A<->C: {src_peer_desc}\nTARGET A<->B: {tgt_peer_desc}")

        # P1 lightweight resumable catalog
        cat_path = run.root / "source_catalog.ndjson"
        cp_path = run.root / "source_catalog_checkpoint.json"
        disc = await discover_catalog(src, await src.get_peer(source_peer), cat_path, cp_path, resume=resume)
        catalog = load_catalog(cat_path)
        stats = catalog_stats(catalog)
        print(f"catalog messages: {stats['messages']} (added this run {disc['added_this_run']})")
        print(f"catalog range: {stats['date_min']} -> {stats['date_max']}  years={stats['years']}")
        print(f"kind distribution: {stats['kind_distribution']}")
        if not catalog:
            raise Abort("catalog empty — nothing to sample")

        # P2 stratified sampling
        selected = select_ids(catalog, count, seed, years)
        closed = apply_closures(catalog, selected)
        manifest = write_sample_artifacts(run, closed, seed, count, catalog, src_id, tgt_id)
        print(f"\nRUN_ID {run_id}")
        print(f"sampled {len(closed)} message(s) (requested {count}; +group/reply closures) "
              f"across years {manifest['years_covered']}")
        print(f"preview: {run.root / 'SAMPLE_PREVIEW.html'}")
        return run_id
    finally:
        await src.close()
        await tgt.close()


async def run_full_recovery(cfg: RecoveryConfig, *, run_id: str,
                            source_peer: str | None, target_peer: str | None,
                            dry_run: bool, confirm: bool) -> int:
    """P3..P7 for an existing sample run (discover/sample already done)."""
    root = Path(cfg.run_dir) / run_id
    cat_path = root / "source_catalog.ndjson"
    man_path = root / "sample_manifest.json"
    if not man_path.exists():
        raise Abort(f"no sample for run {run_id!r} (run sample_history first)")
    catalog = load_catalog(cat_path)
    ids = json.loads(man_path.read_text(encoding="utf-8"))["message_ids"]
    final_ids = closures_from_catalog(catalog, ids)

    src, tgt = await build_clients(cfg)
    try:
        if not source_peer:
            raise Abort("--source-peer required to re-resolve the source peer")
        src_peer_desc = await resolve_peer(src, source_peer)
        tgt_default = target_peer or cfg.phone_a or cfg.peer
        tgt_peer_desc = await resolve_peer(tgt, tgt_default)
        src_id, tgt_id = await identify(src_peer_desc), await identify(tgt_peer_desc)
        src_peer = await src.get_peer(source_peer)
        tgt_peer = await tgt.get_peer(tgt_default)
        if src_id == tgt_id:
            raise Abort("source peer == target peer; refusing")
        assert_target_is_ab(src, tgt_peer_desc)

        run = make_run(cfg, run_id)
        # P3+P4+P5: lazy fetch + media + package (B untouched)
        recs = await lazy_materialize(run, cfg, src, src_peer, final_ids, src_id, tgt_id)
        rt = await verify_roundtrip(run, recs)
        print(f"package: {len(recs)} messages, {rt['media']} media, hash={rt['package_hash']}")
        if dry_run:
            print("\nDRY RUN: package built + roundtrip verified; TARGET A<->B untouched.")
            return 0
        if not confirm:
            print("\nABORT: pass --confirm to modify TARGET A<->B.")
            return 1

        print(f"\ntarget peer check: {await check_target_peer(tgt, tgt_peer)}")
        before = await snapshot_target(run, "before", tgt, tgt_peer)
        print(f"cleared target: {await clear_target(tgt, tgt_peer)} "
              f"(before had {before['count']} messages)")
        outcome = await run_import(run, src, tgt, tgt_peer,
                                   {"import_id": None, "package_hash": rt["package_hash"]})
        print(f"import rpc order: {outcome.rpc_order}")
        await asyncio.sleep(1.0)

        after = await snapshot_target(run, "after", tgt, tgt_peer)
        after_recs = after["records"]
        before_ids = {r["target_message_id"] for r in before["records"]}
        delta = {r["target_message_id"] for r in after_recs} - before_ids
        print(f"target new messages: {len(delta)} (of {len(after_recs)})")

        mapping = map_source_to_target(recs, after_recs, delta_target_ids=delta)
        recon = await reconstruct_react(cfg, src, tgt, src_peer, tgt_peer, recs, mapping)
        mapped_ids = [m.target_message_id for m in mapping if m.target_message_id >= 0]
        tr = await read_target_reactions(tgt, tgt_peer, mapped_ids)
        rverify = reaction_verify_for(tr, mapping)

        verifier = V.Verifier(mapping)
        result = verifier.verify(recs, after_recs, delta_target_ids=delta,
                                 reaction_verify=rverify)
        matrix = result["matrix"]
        untouched = await source_immutability(cfg, src, src_peer, recs)
        evidence = build_evidence(recs, after_recs, mapping)
        ts = {c: matrix["timestamp"].get(c, 0)
              for c in ("TIMESTAMP_EXACT", "IMPORTED_METADATA_ONLY", "NOT_RESTORED")}
        decision = pick_decision(len(delta), ts)
        build_report(run, source_peer_id=src_id, target_peer_id=tgt_id, recs=recs,
                     after_recs=after_recs, delta=delta, matrix=matrix, evidence=evidence,
                     untouched=untouched, recon=recon, rt=rt, seed=seed_for(run_id),
                     decision=decision)
        print(f"\n==== DECISION: {decision} (SOURCE_UNTOUCHED={untouched['SOURCE_UNTOUCHED']}) ====")
        for f, c in matrix.items():
            print(f"  {f:<14} {c.get('EXACT',0)}/{sum(c.values())}  {c}")
        print(f"\nreport: {run.root / 'FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.json'}")
        return 0
    finally:
        await src.close()
        await tgt.close()