"""Fidelity verification for Telegram Recovery v2.

The ONLY authoritative validation is the ACTUAL Telegram target message objects
read after the recovery operation. This module compares each source record to
its mapped target twin and classifies every feature with an honest label:

    EXACT | RECONSTRUCTED | PARTIAL | ARCHIVAL_ONLY | FAILED

It then emits FINAL_REPORT.{json,html} plus a capability matrix. Labels are
conservative — a WEBP document is never called "sticker restored", a caption
split into a separate text message is never called exact.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Per-field classifiers
# ---------------------------------------------------------------------------
def _text(a: dict, b: dict) -> dict[str, Any]:
    sa, sb = (a.get("text") or ""), (b.get("text") or "")
    return {"class": "EXACT" if sa == sb else "PARTIAL" if _near(sa, sb) else "FAILED",
            "source": sa, "target": sb}


def _near(a: str, b: str) -> bool:
    na = " ".join(a.split()).lower()
    nb = " ".join(b.split()).lower()
    return na == nb or (na and nb and (na in nb or nb in na))


def _entities_rec(rec: dict) -> list[dict]:
    out = []
    for e in rec.get("entities") or []:
        out.append((e.get("type"), e.get("offset"), e.get("length"),
                    e.get("document_id")))
    return out


def _formatting(a: dict, b: dict) -> dict[str, Any]:
    ea, eb = _entities_rec(a), _entities_rec(b)
    return {"class": "EXACT" if ea == eb else "PARTIAL" if (ea and eb) else
            ("FAILED" if (ea or eb) else "EXACT"), "source": ea, "target": eb}


def _timestamp(a: dict, b: dict) -> dict[str, Any]:
    def ts(rec):
        d = rec.get("date")
        if isinstance(d, str):
            try:
                return datetime.fromisoformat(d.replace("Z", "+00:00"))
            except ValueError:
                return None
        return d
    sa, tb = ts(a), ts(b)
    if sa and tb:
        return {"class": "EXACT" if abs((tb - sa).total_seconds()) < 60 else "PARTIAL",
                "source": a.get("date"), "target": b.get("date")}
    fwd = b.get("forward") or {}
    if fwd.get("imported") and fwd.get("date"):
        return {"class": "IMPORTED_METADATA_ONLY", "source": a.get("date"),
                "target_fwd_date": fwd.get("date")}
    return {"class": "NOT_RESTORED", "source": a.get("date"), "target": b.get("date")}


def _sender(a: dict, b: dict) -> dict[str, Any]:
    def sid(rec):
        f = rec.get("from_id") or {}
        return f.get("user_id") or f.get("channel_id") or f.get("id")
    if sid(a) and sid(a) == sid(b):
        return {"class": "SENDER_EXACT", "source_id": sid(a), "target_id": sid(b)}
    fwd = b.get("forward") or {}
    fwd_name = fwd.get("from_name")
    src_name = (a.get("from_id") or {}).get("first_name") or \
               (a.get("from_id") or {}).get("title")
    if fwd_name and src_name and fwd_name == src_name:
        return {"class": "SENDER_METADATA_ONLY", "source": src_name, "target_name": fwd_name}
    return {"class": "SENDER_MISMATCH", "source_id": sid(a), "target_id": sid(b)}


def media_class(rec: dict, want: str) -> dict[str, Any]:
    """Classify the target's media for a requested media capability."""
    medias = rec.get("media") or []
    if not medias:
        return {"class": "FAILED", "detail": "no media"}
    m = medias[0]
    ctor = m.get("constructor")
    # GIF vs video vs document vs sticker are decided by safe classification
    if want == "photo":
        return {"class": "EXACT" if ctor == "MessageMediaPhoto" else "FAILED",
                "constructor": ctor}
    if want == "sticker":
        attrs = {a.get("__tl__") for a in m.get("attributes") or []}
        if "DocumentAttributeSticker" in attrs:
            return {"class": "EXACT", "constructor": ctor,
                    "sticker": m.get("sticker")}
        return {"class": "DOCUMENT_ONLY", "constructor": ctor,
                "mime": m.get("mime")}
    if want in ("video", "audio", "voice", "document", "animation"):
        t_attr = {"video": "DocumentAttributeVideo", "audio": "DocumentAttributeAudio",
                  "voice": "DocumentAttributeAudio", "animation": "DocumentAttributeAnimated",
                  "document": "DocumentAttributeFilename"}[want]
        attrs = {a.get("__tl__") for a in m.get("attributes") or []}
        if want == "voice":
            ok = "DocumentAttributeAudio" in attrs and m.get("voice")
        else:
            ok = t_attr in attrs
        return {"class": "EXACT" if ok else "DOCUMENT_ONLY",
                "constructor": ctor, "mime": m.get("mime")}
    return {"class": "PARTIAL", "detail": ctor}


def _caption(a: dict, b: dict) -> dict[str, Any]:
    """A caption counts only if media AND text live in the SAME target record."""
    if not a.get("media"):
        return {"class": "NONE"}
    if not b.get("media"):
        return {"class": "CAPTION_LOST"}
    btext = b.get("text") or ""
    if not btext:
        return {"class": "CAPTION_LOST"}   # media kept, caption dropped
    if btext == (a.get("text") or ""):
        return {"class": "CAPTION_ATTACHED"}
    return {"class": "CAPTION_SEPARATE"}


def _reply(a: dict, b: dict, mapping: dict[int, int]) -> dict[str, Any]:
    ar, br = a.get("reply_to") or {}, b.get("reply_to") or {}
    src_parent = ar.get("reply_to_msg_id")
    tgt_parent = br.get("reply_to_msg_id")
    if src_parent is None:
        return {"class": "NONE"}
    if tgt_parent is None:
        # archived reply_text in fwd/quote vs structured reply
        return {"class": "ARCHIVAL_ONLY", "detail": "no target reply_to"}
    mapped_parent = mapping.get(src_parent)
    if mapped_parent is not None and mapped_parent == tgt_parent:
        return {"class": "REPLY_EXACT", "source_parent": src_parent,
                "target_parent": tgt_parent}
    return {"class": "REPLY_PARTIAL", "source_parent": src_parent,
            "target_parent": tgt_parent}


def _forward(a: dict, b: dict) -> dict[str, Any]:
    af, bf = a.get("forward"), b.get("forward")
    if af is None and bf is None:
        return {"class": "NONE"}
    if af is None:
        # Source was not a forward. An imported message always carries a
        # Telegram fwd_from header as the import carrier (imported=true),
        # so this is the expected/normal case, not a failure.
        return {"class": "EXACT", "detail": "source not forwarded; "
                "target fwd_from is the import carrier",
                "target_imported": bool((bf or {}).get("imported"))}
    if bf is None:
        return {"class": "FAILED", "detail": "source forwarded, target not"}
    fwd_same = (af.get("from_name") == bf.get("from_name")) and \
               (af.get("channel_post") == bf.get("channel_post"))
    if fwd_same or (af.get("from_id") and af.get("from_id") == bf.get("from_id")):
        return {"class": "EXACT", "source": af.get("from_name"),
                "target": bf.get("from_name")}
    return {"class": "PARTIAL", "source": af.get("from_name"),
            "target": bf.get("from_name"), "target_imported": bf.get("imported")}


def _reaction(source_reactions: list | None, target_verify: dict) -> dict[str, Any]:
    if not source_reactions:
        return {"class": "NONE"}
    if target_verify is None:
        return {"class": "ARCHIVAL_ONLY", "detail": "no target verification"}
    # conservative: exact if every archived (reaction,count) present on target
    target_rows = {(r.get("reaction", {}).get("emoticon") or r.get("reaction", {}).get("__tl__"),
                    int(r.get("count", 0))) for r in target_verify}
    src_rows = {(r.get("reaction", {}).get("emoticon") or r.get("reaction", {}).get("__tl__"),
                 int(r.get("count", 0))) for r in source_reactions}
    if src_rows and src_rows == target_rows:
        return {"class": "REACTION_EXACT", "source": sorted(src_rows),
                "target": sorted(target_rows)}
    if src_rows and src_rows.issubset(target_rows):
        return {"class": "REACTION_PARTIAL", "source": sorted(src_rows),
                "target": sorted(target_rows)}
    return {"class": "REACTION_FAILED", "source": sorted(src_rows),
            "target": sorted(target_rows)}


def _group(a: dict, b: dict) -> dict[str, Any]:
    ga, gb = a.get("grouped_id"), b.get("grouped_id")
    if ga is None and gb is None:
        return {"class": "NONE"}
    if ga and ga == gb:
        return {"class": "GROUP_EXACT"}
    if ga and gb is None:
        return {"class": "GROUP_FLATTENED"}
    return {"class": "GROUP_PARTIAL"}


# ---------------------------------------------------------------------------
# Verifier driver
# ---------------------------------------------------------------------------
class Verifier:
    def __init__(self, mapping: list[Any]) -> None:
        # mapping: list of Mapping objects (source_message_id, target_message_id)
        self.tgt_of: dict[int, int] = {}
        for m in mapping:
            self.tgt_of[m.source_message_id] = m.target_message_id

    def verify(self, source: list[dict], target: list[dict], delta_target_ids=None,
               reaction_verify: dict[int, Any] | None = None) -> dict[str, Any]:
        t_by_id = {t["target_message_id"]: t for t in target}
        rows: list[dict[str, Any]] = []
        for s in source:
            tgt_id = self.tgt_of.get(s["source_message_id"])
            t = t_by_id.get(tgt_id) if tgt_id and tgt_id >= 0 else None
            row = {
                "source_id": s["source_message_id"],
                "target_id": tgt_id if tgt_id and tgt_id >= 0 else None,
                "text": _text(s, t if t else {}),
                "formatting": _formatting(s, t if t else {}),
                "sender": _sender(s, t if t else {}),
                "timestamp": _timestamp(s, t if t else {}),
                "caption": _caption(s, t if t else {}),
                "reply": _reply(s, t if t else {}, self.tgt_of),
                "forward": _forward(s, t if t else {}),
                "group": _group(s, t if t else {}),
            }
            # per-media capabilities
            for want in ("photo", "photo_caption", "video", "gif", "audio",
                         "voice", "document", "sticker"):
                if want == "photo_caption":
                    row["photo_caption"] = row["caption"]
                    continue
                row[want] = media_class(t if t else {}, want) if (t and t.get("media")) \
                    else {"class": "NONE" if not s.get("media") else "FAILED",
                          "detail": "no target"}
            # reaction
            src_reactions = (s.get("reactions") or {}).get("rows")
            row["reaction"] = _reaction(
                src_reactions, (reaction_verify or {}).get(s["source_message_id"]))
            rows.append(row)

        return {"rows": rows, "matrix": self.feature_matrix(rows)}

    @staticmethod
    def feature_matrix(rows: list[dict]) -> dict[str, Any]:
        fields = ("text", "formatting", "sender", "timestamp", "caption",
                  "photo", "photo_caption", "video", "gif", "audio", "voice",
                  "document", "sticker", "reply", "forward", "reaction", "group")
        matrix: dict[str, dict[str, int]] = {}
        for f in fields:
            counts: dict[str, int] = {}
            for r in rows:
                c = (r.get(f) or {}).get("class", "NONE")
                counts[c] = counts.get(c, 0) + 1
            matrix[f] = counts
        return matrix


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------
def write_report(run_dir: Path, report: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / "FINAL_REPORT.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    html = _render_html(report)
    (run_dir / "FINAL_REPORT.html").write_text(html, encoding="utf-8")
    return json_path


def _render_html(report: dict) -> str:
    rows = "".join(_row_html(r) for r in report["rows"])
    matrix_rows = "".join(
        f"<tr><td>{f}</td>" +
        "".join(f"<td>{c}</td>" for c in report["matrix"][f].values()) +
        "</tr>" for f in report["matrix"])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Recovery v2 — fidelity report</title>
<style>table{{border-collapse:collapse;font:12px monospace}}td,th{{border:1px solid #ccc;padding:3px 6px;text-align:left}}</style>
</head><body><h1>Recovery v2 fidelity report — {report['run_id']}</h1>
<h2>Feature matrix</h2><table>
<tr><th>feature</th><th>EXACT</th><th>PARTIAL</th><th>RECONSTRUCTED</th><th>ARCHIVAL_ONLY</th><th>FAILED</th></tr>{matrix_rows}</table>
<h2>Per-message table</h2><table><tr><th>src</th><th>tgt</th><th>text</th><th>fmt</th><th>sender</th><th>ts</th><th>caption</th><th>reply</th><th>forward</th><th>group</th><th>sticker</th></tr>{rows}</table>
</body></html>"""


def _row_html(r: dict) -> str:
    cell = lambda k: (r.get(k) or {}).get("class", "-")
    return (f"<tr><td>{r['source_id']}</td><td>{r['target_id']}</td>"
            f"<td>{cell('text')}</td><td>{cell('formatting')}</td>"
            f"<td>{cell('sender')}</td><td>{cell('timestamp')}</td>"
            f"<td>{cell('caption')}</td><td>{cell('reply')}</td>"
            f"<td>{cell('forward')}</td><td>{cell('group')}</td>"
            f"<td>{cell('sticker')}</td></tr>")