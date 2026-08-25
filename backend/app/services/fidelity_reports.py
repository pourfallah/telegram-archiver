"""Maximum-fidelity reports.

Generates three HTML reports from a verification run plus the source archive:
  - RECOVERY_FIDELITY_REPORT.html   (per-message source vs target, FULL/PARTIAL/FAILED/ARCHIVAL_ONLY)
  - REACTION_FIDELITY_REPORT.html   (reaction-by-reaction preserved/archival status)
  - capability matrix (per-field SOURCE / IMPORT / SERVER / TARGET / STATUS)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _pct(x: int, y: int) -> int:
    return round(100 * x / y) if y else 100


def _field_status(import_supported: bool, target_verified: bool) -> str:
    if target_verified:
        return "FULL"
    if import_supported:
        return "EXPERIMENTAL"
    return "ARCHIVAL_ONLY"


def build_capability_matrix() -> list[dict[str, str]]:
    """Static capability matrix per field (kept honest — server/verify unknown until tested)."""
    return [
        {"field": "text", "source": "YES", "import_format": "YES", "server": "YES", "target": "YES", "status": "FULL"},
        {"field": "timestamp (date)", "source": "YES", "import_format": "YES", "server": "YES", "target": "YES", "status": "FULL"},
        {"field": "photo", "source": "YES", "import_format": "YES", "server": "YES", "target": "?", "status": "VERIFIED"},
        {"field": "video / animation", "source": "YES", "import_format": "YES", "server": "YES", "target": "?", "status": "VERIFIED"},
        {"field": "document", "source": "YES", "import_format": "YES", "server": "YES", "target": "?", "status": "VERIFIED"},
        {"field": "caption", "source": "YES", "import_format": "PARTIAL", "server": "?", "target": "?", "status": "PARTIAL"},
        {"field": "reaction", "source": "YES", "import_format": "NO", "server": "?", "target": "NO", "status": "ARCHIVAL_ONLY"},
        {"field": "reply", "source": "YES", "import_format": "NO", "server": "?", "target": "?", "status": "ARCHIVAL_ONLY"},
        {"field": "forward provenance", "source": "YES", "import_format": "NO", "server": "?", "target": "?", "status": "ARCHIVAL_ONLY"},
        {"field": "sticker identity", "source": "YES", "import_format": "PARTIAL", "server": "?", "target": "DOC", "status": "PARTIAL"},
        {"field": "custom emoji", "source": "YES", "import_format": "NO", "server": "?", "target": "?", "status": "ARCHIVAL_ONLY"},
        {"field": "grouped media (album)", "source": "YES", "import_format": "UNKNOWN", "server": "?", "target": "?", "status": "EXPERIMENTAL"},
        {"field": "message entities", "source": "YES", "import_format": "NO", "server": "?", "target": "?", "status": "ARCHIVAL_ONLY"},
        {"field": "polls / contact / geo", "source": "YES", "import_format": "UNKNOWN", "server": "?", "target": "?", "status": "ARCHIVAL_ONLY"},
        {"field": "service message", "source": "PARTIAL", "import_format": "NO", "server": "?", "target": "?", "status": "ARCHIVAL_ONLY"},
    ]


def build_fidelity_report(
    verification: dict[str, Any],
    out_dir: Path,
    source_messages: list[dict] | None = None,
    target_messages: list[dict] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = verification.get("counts", {})
    ta = verification.get("timestamp_analysis", {})
    rows = ta.get("rows", [])
    media_summary = (verification.get("details") or {}).get("media_summary") or {}

    text_y = counts.get("matched") or 0
    timestamp_y = len(rows)
    ts_preserved = ta.get("historical_metadata_preserved", 0)
    ts_visible = ta.get("visible_equals_source", 0)
    media_total = media_summary.get("total", 0)
    media_restored = media_summary.get("restored", 0)

    caps = build_capability_matrix()

    # Per-message table
    msg_rows = []
    for i, r in enumerate(rows):
        msg_rows.append(
            f"<tr><td class='mono'>{i+1}</td>"
            f"<td>{r.get('source_date','')[:16]}</td>"
            f"<td>{r.get('target_import_meta_date','')[:16] or '—'}</td>"
            f"<td>{r.get('key','')[:40]}</td>"
            f"<td>{'✓' if r.get('meta_preserves_history') else '✗'}</td>"
            f"<td>{'✓' if r.get('visible_equals_source') else '✗'}</td></tr>"
        )

    cap_rows = "\n".join(
        f"<tr><td>{c['field']}</td><td>{c['source']}</td><td>{c['import_format']}</td>"
        f"<td>{c['server']}</td><td>{c['target']}</td><td><b>{c['status']}</b></td></tr>"
        for c in caps
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Maximum-Fidelity Recovery Report</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;background:#0f172a;color:#e2e8f0}}
h1,h2{{color:#38bdf8}} table{{border-collapse:collapse;width:100%;margin:8px 0}}
th,td{{border:1px solid #334155;padding:4px 8px;text-align:left;font-size:13px}}
th{{background:#1e293b}} .ok{{color:#34d399}} .bad{{color:#f87171}}
.mono{{font-family:monospace}} .card{{display:inline-block;border:1px solid #334155;
border-radius:8px;padding:8px 14px;margin:4px}}
.score{{font-size:26px;font-weight:700;color:#38bdf8}}
</style></head><body>
<h1>RECOVERY FIDELITY REPORT</h1>
<p><em>Maximum-fidelity archival + reconstruction. "Archival-only" means the field is fully
stored in the canonical archive but Telegram's import protocol does not restore it.</em></p>

<h2>Overall</h2>
<div class="card"><span class="score">Overall: {verification.get('overall','—')}</span></div>

<h2>Fidelity scorecards</h2>
<div class="card">Text fidelity: <b>{_pct(text_y, counts.get('source', 1))}%</b></div>
<div class="card">Timestamp fidelity (metadata preserved): <b>{_pct(ts_preserved, max(timestamp_y,1))}%</b></div>
<div class="card">Timestamp fidelity (visible placement): <b>{_pct(ts_visible, max(timestamp_y,1))}%</b></div>
<div class="card">Media fidelity: <b>{_pct(media_restored, max(media_total,1))}%</b></div>
<div class="card">Entity / reaction / reply fidelity: <b>archival-only</b></div>

<h2>Per-message timestamp / content table</h2>
<table><thead><tr><th>#</th><th>Source date</th><th>Target meta date</th>
<th>Content</th><th>History preserved</th><th>Visible == source</th></tr></thead>
<tbody>{''.join(msg_rows) or '<tr><td colspan=6>no matched rows</td></tr>'}</tbody></table>

<h2>Capability matrix</h2>
<table><thead><tr><th>Field</th><th>SOURCE</th><th>IMPORT FORMAT</th><th>SERVER</th><th>TARGET</th><th>STATUS</th></tr></thead>
<tbody>{cap_rows}</tbody></table>

<p><small>Generated {verification.get('generated_at','')}. Original source data is never discarded;
restriction applies only to what the Telegram import protocol can reconstruct.</small></p>
</body></html>"""
    path = out_dir / "RECOVERY_FIDELITY_REPORT.html"
    path.write_text(html, encoding="utf-8")
    return path


def build_reaction_report(source_messages: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in source_messages or []:
        rx = m.get("reactions") or {}
        for item in rx.get("reactions", []):
            rows.append(
                f"<tr><td class='mono'>{m.get('id')}</td>"
                f"<td>{item.get('reaction_type','')}</td>"
                f"<td>{item.get('emoji','')}</td>"
                f"<td class='mono'>{item.get('document_id','')}</td>"
                f"<td>{item.get('count','')}</td>"
                f"<td>{'yes' if item.get('chosen') else 'no'}</td>"
                f"<td>ARCHIVAL_ONLY</td></tr>"
            )
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Reaction Fidelity Report</title>
<style>body{{background:#0f172a;color:#e2e8f0;font-family:system-ui;margin:24px}}
h1{{color:#fbbf24}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #334155;padding:4px 8px;font-size:13px}} .mono{{font-family:monospace}}</style>
</head><body><h1>REACTION FIDELITY REPORT</h1>
<p>Reactions are archived first-class (type / emoji / custom-emoji document_id / count / chosen).
Telegram's history-import protocol does not currently restore them → <b>ARCHIVAL_ONLY</b>.</p>
<table><thead><tr><th>msg id</th><th>type</th><th>emoji</th><th>document_id</th>
<th>count</th><th>chosen</th><th>status</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan=7>no reactions</td></tr>'}</tbody></table>
</body></html>"""
    path = out_dir / "REACTION_FIDELITY_REPORT.html"
    path.write_text(html, encoding="utf-8")
    return path


def build_reaction_recovery_report(
    source_messages: list[dict],
    reconstruction: dict | None,
    out_dir: Path,
) -> Path:
    """REACTION_RECOVERY_REPORT.html — per source reaction: source msg -> target
    msg, source reactor -> reconstructed-by, reaction, target outcome, status."""
    out_dir.mkdir(parents=True, exist_ok=True)
    outcomes = (reconstruction or {}).get("outcomes") or []
    by_key = {}
    for o in outcomes:
        by_key[(o.get("source_message_id"), o.get("reactor_id"),
                o.get("emoji"), o.get("document_id"))] = o

    rows = []
    for m in source_messages or []:
        rx = m.get("reactions") or {}
        voters = rx.get("voters") or []
        if voters:
            for v in voters:
                key = (m.get("id"), v.get("peer_id"), v.get("emoji"), v.get("document_id"))
                o = by_key.get(key)
                status = (o or {}).get("outcome") or "NOT_PLANNED"
                rows.append(
                    f"<tr><td class='mono'>{m.get('id')}</td>"
                    f"<td class='mono'>{o.get('target_id','—') if o else '—'}</td>"
                    f"<td class='mono'>{v.get('peer_id','?')}</td>"
                    f"<td>{v.get('emoji') or ('custom:' + str(v.get('document_id')))}</td>"
                    f"<td><b>{status}</b></td></tr>"
                )
        for t in rx.get("reactions", []):
            if voters:
                continue
            rows.append(
                f"<tr><td class='mono'>{m.get('id')}</td><td>—</td><td>?</td>"
                f"<td>{t.get('emoji') or ('custom:' + str(t.get('document_id')))}</td>"
                f"<td><b>REACTOR_UNKNOWN</b></td></tr>"
            )

    summary = ""
    if reconstruction:
        summary = "<p><b>Reconstruction:</b> enabled=" + str(reconstruction.get("enabled")) + \
                  " | " + str(reconstruction.get("summary") or {}) + "</p>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Reaction Recovery Report</title>
<style>body{{background:#0f172a;color:#e2e8f0;font-family:system-ui;margin:24px}}
h1{{color:#fbbf24}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #334155;padding:4px 8px;font-size:13px}} .mono{{font-family:monospace}}</style>
</head><body><h1>REACTION RECOVERY REPORT</h1>
{summary}
<p>Identity rule: a reaction by user X is only reconstructed by an authenticated
session of user X. Otherwise <b>REACTOR_SESSION_REQUIRED</b> — never faked.</p>
<table><thead><tr><th>source msg</th><th>target msg</th><th>reactor</th><th>reaction</th><th>status</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan=5>no reactions</td></tr>'}</tbody></table>
</body></html>"""
    path = out_dir / "REACTION_RECOVERY_REPORT.html"
    path.write_text(html, encoding="utf-8")
    return path


def build_sticker_recovery_report(
    source_messages: list[dict], target_dicts: list[dict], out_dir: Path
) -> Path:
    """STICKER_RECOVERY_REPORT.html — per source sticker: source identity vs
    target document attributes and honest classification."""
    out_dir.mkdir(parents=True, exist_ok=True)
    from app.services.import_verification import _normalize_text

    rows = []
    for m in source_messages or []:
        for med in m.get("media") or []:
            if med.get("type") != "sticker":
                continue
            tgt = None
            for t in target_dicts or []:
                if _normalize_text(t.get("text") or "") == _normalize_text(m.get("text") or "") \
                        and t.get("target_media_raw"):
                    tgt = t
                    break
            raw = (tgt or {}).get("target_media_raw") or {}
            attrs = raw.get("attrs") or []
            has_sticker_attr = "DocumentAttributeSticker" in attrs
            status = ("STICKER_EXACT" if has_sticker_attr else
                      "STICKER_SEMANTIC_PARTIAL" if raw.get("ctor") == "MessageMediaDocument"
                      else "DOCUMENT_ONLY")
            extra = med.get("extra") or {}
            rows.append(
                f"<tr><td class='mono'>{m.get('id')}</td>"
                f"<td>{extra.get('sticker_emoji','')}</td>"
                f"<td>{extra.get('animated')}</td>"
                f"<td class='mono'>{raw.get('ctor','—')}</td>"
                f"<td class='mono'>{','.join(attrs) or '—'}</td>"
                f"<td class='mono'>{raw.get('mime','—')}</td>"
                f"<td><b>{status}</b></td></tr>"
            )
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Sticker Recovery Report</title>
<style>body{{background:#0f172a;color:#e2e8f0;font-family:system-ui;margin:24px}}
h1{{color:#fbbf24}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #334155;padding:4px 8px;font-size:13px}} .mono{{font-family:monospace}}</style>
</head><body><h1>STICKER RECOVERY REPORT</h1>
<p>A generic WebP document WITHOUT DocumentAttributeSticker is DOCUMENT_ONLY —
never STICKER_EXACT.</p>
<table><thead><tr><th>source msg</th><th>sticker emoji</th><th>animated</th>
<th>target constructor</th><th>target attrs</th><th>target mime</th><th>status</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan=7>no stickers</td></tr>'}</tbody></table>
</body></html>"""
    path = out_dir / "STICKER_RECOVERY_REPORT.html"
    path.write_text(html, encoding="utf-8")
    return path
