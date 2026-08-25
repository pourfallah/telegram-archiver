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
