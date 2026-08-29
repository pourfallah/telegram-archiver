#!/usr/bin/env python3
"""Render FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.json -> HTML."""

import html
import json
import sys
from pathlib import Path


def main() -> None:
    run_dir = Path(sys.argv[1])
    report = json.loads((run_dir / "FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.json").read_text())

    def esc(x):
        return html.escape(str(x or ""))

    meta_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>"
        for k, v in report.items()
        if k not in ("rows", "aggregate", "feature_results")
    )
    agg_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(json.dumps(v))}</td></tr>"
        for k, v in report.get("aggregate", {}).items()
    )
    feat_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>"
        for k, v in report.get("feature_results", {}).items()
    )
    msg_rows = "".join(
        f"<tr><td>{esc(r['source_id'])}</td><td>{esc(r.get('target_id'))}</td>"
        f"<td>{esc(r['source_date'][:19])}</td><td>{esc(r['target_date'][:19] if r.get('target_date') else '')}</td>"
        f"<td>{esc((r['source_text'] or '')[:50])}</td>"
        f"<td>{esc(r['source_media'] or 'text')}</td><td>{esc(r['media'])}</td>"
        f"<td>{esc(r['timestamp'])}</td><td>{esc(r['sender'])}</td></tr>"
        for r in report.get("rows", [])
    )
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Sampled History Recovery {esc(run_dir.name)}</title>
<style>
body {{ background:#0d1117; color:#c9d1d9; font-family:monospace; padding:24px; }}
h1,h2 {{ color:#58a6ff; }} table {{ border-collapse:collapse; width:100%; margin-bottom:24px; }}
th,td {{ border:1px solid #30363d; padding:4px 8px; text-align:left; font-size:12px; }}
th {{ background:#161b22; }}
</style></head><body>
<h1>FINAL SAMPLED HISTORY RECOVERY — {esc(run_dir.name)}</h1>
<h2>Run metadata</h2><table>{meta_rows}</table>
<h2>Feature results</h2><table><tr><th>FEATURE</th><th>RESULT</th></tr>{feat_rows}</table>
<h2>Aggregates</h2><table><tr><th>DIMENSION</th><th>COUNTS</th></tr>{agg_rows}</table>
<h2>Per-message ({len(report.get('rows', []))})</h2>
<table><tr><th>SRC</th><th>TGT</th><th>SRC DATE</th><th>TGT DATE</th><th>SRC TEXT</th><th>SRC MEDIA</th><th>MEDIA CLS</th><th>TS CLS</th><th>SENDER CLS</th></tr>{msg_rows}</table>
</body></html>"""
    (run_dir / "FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.html").write_text(page)
    print(f"wrote {run_dir / 'FINAL_SAMPLED_HISTORY_RECOVERY_REPORT.html'}")


if __name__ == "__main__":
    main()