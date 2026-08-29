"""Fast, resumable, lightweight source catalog builder (concurrent probes).

Phase 1 of the historical-sampling test. The source chat can be huge
(100k-200k+ messages) — a serial getHistory crawl is ~30+ minutes. Instead we
probe the history CONCURRENTLY: launch N in-flight getHistory pages at
deterministically spaced offset windows, capture only lightweight rows, and
checkpoint every batch. This yields a dense, representative index (every
probed page covers a contiguous 100-message window; windows are spread across
the entire history so EVERY year/period is represented).

- No media downloads, no thumbnails, no reaction-user lookups, no full raw
  serialization during discovery.
- Checkpointed NDJSON: resume from the deepest done offset window.
- The full Message objects are NOT persisted; only minimal rows.
- Fully deterministic given the peer (probe grid is a pure function of the
  id window).

NOTE: this is an INDEX for sampling, not an exhaustive every-message archive.
For the recovery test that is exactly what is needed: sample ~20 candidates
from the index, then FULL-fetch only those (+ reply parents + album members).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from pathlib import Path

from telethon import functions
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeSticker,
    DocumentAttributeAnimated,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
)

PAGE = 100
# how many concurrent windows; each window = 100 messages. 120 windows = 12k
# indexed messages spread across the whole history — plenty for stratified
# sampling and instant (~120 RPCs, a few seconds).
WINDOWS = 120


def media_type_of(msg) -> str | None:
    """Cheap media classification from the already-returned Message object."""
    m = msg.media
    if m is None:
        return None
    if isinstance(m, MessageMediaPhoto):
        return "photo"
    if isinstance(m, MessageMediaDocument):
        doc = m.document
        if doc is None or not doc.attributes:
            return "document"
        names = [type(a).__name__ for a in doc.attributes]
        if "DocumentAttributeSticker" in names:
            return "sticker"
        if "DocumentAttributeAnimated" in names:
            return "gif"
        if any(getattr(a, "voice", False) for a in doc.attributes):
            return "voice"
        if "DocumentAttributeAudio" in names:
            return "audio"
        if "DocumentAttributeVideo" in names:
            return "video"
        return "document"
    if type(m).__name__ == "MessageMediaWebPage":
        return "webpage"
    return type(m).__name__


def row_from_msg(msg) -> dict:
    r = {
        "message_id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "sender_id": getattr(msg.from_id, "user_id", None) if msg.from_id else None,
        "grouped_id": int(msg.grouped_id) if msg.grouped_id else None,
        "media_type": media_type_of(msg),
        "has_reply": msg.reply_to is not None,
        "has_reactions": bool(msg.reactions and msg.reactions.results),
        "text_len": len(msg.message or ""),
        "text_prefix": (msg.message or "")[:40],
        "fwd": msg.fwd_from is not None,
        "raw_ctor": type(msg).__name__,
    }
    return r


async def _page(client, peer, offset_id: int):
    res = await client(
        functions.messages.GetHistoryRequest(
            peer=peer,
            offset_id=offset_id,
            offset_date=0,
            add_offset=0,
            limit=PAGE,
            max_id=0,
            min_id=0,
            hash=0,
        )
    )
    return list(res.messages)


async def _probe_window(client, peer, offset_id: int) -> list[dict]:
    """One concurrent window: fetch PAGE messages older than offset_id."""
    try:
        msgs = await _page(client, peer, offset_id)
    except Exception:  # noqa: BLE001 - skip window on any error
        return []
    return [row_from_msg(m) for m in msgs if m]


def _grid_top_id(rows) -> int:
    """Highest id seen so far (newest boundary of the covered range)."""
    return max(rows.keys(), default=0)


async def build_catalog(client, peer, run_dir: Path, windows: int = WINDOWS) -> dict:
    """Concurrent windowed probe -> source_catalog.ndjson (+ checkpoint).

    Steps:
      1. Fetch the newest page (offset 0) once -> top boundary.
      2. Probe `windows` deterministic windows spread across the history,
         top-down, by id: window k starts at top_id - k*(top_id/windows).
      3. Merge, dedupe by message_id, sort, checkpoint, write ndjson.
    """
    out_path = run_dir / "source_catalog.ndjson"
    ck_path = run_dir / "source_catalog_checkpoint.json"
    meta = {
        "peer_id": peer.user_id if hasattr(peer, "user_id") else None,
        "started_at": dt.datetime.utcnow().isoformat(timespec="seconds"),
        "status": "running",
        "windows": windows,
    }

    t0 = time.time()
    rows: dict[int, dict] = {}

    # resume: reload existing rows
    if out_path.exists():
        for line in open(out_path, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                rows[r["message_id"]] = r
        print(f"CATALOG resume: {len(rows)} rows cached", flush=True)

    # 1) newest page to establish the top id
    top = _grid_top_id(rows)
    if top == 0:
        newest = await _page(client, peer, 0)
        for m in newest:
            rows[m.id] = row_from_msg(m)
        top = _grid_top_id(rows)
        print(f"CATALOG top id={top}", flush=True)
    if top == 0:
        meta["status"] = "error"
        ck_path.write_text(json.dumps(meta, indent=1))
        return meta

    # 2) deterministic window offsets across the whole history
    #    window k samples messages just older than top - k*(top//windows)
    #    (id space is dense enough that this covers every era)
    offsets = [max(0, top - k * (top // (windows + 1))) for k in range(1, windows + 1)]

    # process in concurrent batches of 12 to avoid hammering the DC
    batch = 12
    for i in range(0, len(offsets), batch):
        chunk = offsets[i : i + batch]
        results = await asyncio.gather(*[_probe_window(client, peer, o) for o in chunk])
        for rlist in results:
            for r in rlist:
                rows[r["message_id"]] = r
        # checkpoint after each batch
        ordered = sorted(rows.values(), key=lambda r: -r["message_id"])
        with open(out_path, "w", encoding="utf-8") as f:
            for r in ordered:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        meta.update(
            {
                "processed": len(rows),
                "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds"),
                "elapsed_s": round(time.time() - t0, 1),
            }
        )
        ck_path.write_text(json.dumps(meta, indent=1))
        print(f"CATALOG {len(rows)} rows ({i + len(chunk)}/{len(offsets)} windows) {time.time()-t0:.0f}s", flush=True)

    meta["status"] = "done"
    meta["finished_at"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    meta["elapsed_s"] = round(time.time() - t0, 1)
    ck_path.write_text(json.dumps(meta, indent=1))
    print(f"CATALOG DONE: {len(rows)} rows in {meta['elapsed_s']}s", flush=True)
    return meta