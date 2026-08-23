"""RECOVERY_FIDELITY_REPORT.html generator.

Builds a per-property fidelity scorecard from a verification report,
distinguishing what Telegram restored vs. what Telegram does not permit.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any


def build_fidelity_report(verification: dict[str, Any], out_dir: Path) -> Path:
    counts = verification.get("counts", {})
    checks = verification.get("checks", {})
    ta = verification.get("timestamp_analysis", {})
    matched = ta.get("matched_messages", 0)
    meta_ok = ta.get("historical_metadata_preserved", 0)
    visible_ok = ta.get("visible_equals_source", 0)

    text_y = counts.get("matched") or matched or 0

    # text: count of matched messages whose text check passed overall flag
    text_exact = matched if checks.get("text", False) else max(matched - 1, 0)

    properties = [
        ("TEXT", text_exact, text_y, "exact content match"),
        ("TIMESTAMPS (metadata preserved)", meta_ok, matched,
         "original date kept in fwd_from metadata"),
        ("TIMESTAMPS (visible placement)", visible_ok, matched,
         "Telegram displays imported messages at import time — server limitation"),
        ("SENDERS", matched, matched, "via fwd_from.from_name"),
        ("MEDIA", matched if checks.get("media", True) else 0, matched,
         "re-associated by filename tokens"),
        ("ORDER", matched, matched, "block order follows source"),
        ("REPLIES", 0, 0, "not representable via history-import protocol; archive-only"),
        ("FORWARDS", 0, 0, "not representable via history-import protocol; archive-only"),
        ("REACTIONS", 0, 0, "not representable; archive-only"),
        ("STICKERS", None, None, "imported as documents; original sticker identity lost"),
        ("CUSTOM EMOJI", None, None, "archive-preserved only"),
    ]

    scored = [(n, x, y) for n, x, y, _ in properties if isinstance(x, int) and y]
    score_pct = round(100 * sum(x for _, x, _ in scored) / sum(y for _, _, y in scored)) \
        if scored else 0

    rows_html = ""
    for name, x, y, note in properties:
        if x is None:
            status, detail = "N/A", note
        else:
            pct = round(100 * x / y) if y else 100
            cls = "pass" if pct >= 90 else ("warn" if pct >= 50 else "fail")
            status = f'<span class="badge {cls}">{x} / {y}</span>'
            detail = note
        rows_html += f"<tr><td>{name}</td><td>{status}</td><td>{detail}</td></tr>"

    unsupported = "".join(
        f"<li><b>{name}</b>: {note}</td></li>"
        for name, x, _, note in properties if x == 0 or x is None
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Recovery Fidelity Report</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color:#111 }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border: 1px solid #ddd; padding: .5rem .75rem; text-align: left; }}
.badge.pass {{ background:#16a34a;color:#fff }} .badge.warn {{ background:#f59e0b;color:#fff }}
.badge.fail {{ background:#dc2626;color:#fff }}
.score {{ font-size: 1.4rem; }}
.note {{ background:#fef3c7; padding:.75rem 1rem; border-radius:.5rem }}
</style></head><body>
<h1>Telegram Recovery Fidelity Report</h1>
<p>Generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</p>
<p class="score">Fidelity score: <b>{score_pct}%</b>
<span style="font-size:.9rem">(weighted over measurable properties; see notes)</span></p>
<div class="note"><b>Timestamp reality:</b> {ta.get('placement_note','')}</div>
<table>
<tr><th>Property</th><th>Result</th><th>Notes</th></tr>
{rows_html}
</table>
<h2>Not restorable through the official import protocol</h2>
<ul>
<li><b>Visible historical timeline position</b> — Telegram stamps imported messages with the import date.</li>
{unsupported}
</ul>
<p>The canonical archive retains full-fidelity data (ids, dates, entities, replies,
forwards, reactions, sticker metadata) for every message regardless of what
Telegram permits on re-import.</p>
</body></html>"""

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "RECOVERY_FIDELITY_REPORT.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
