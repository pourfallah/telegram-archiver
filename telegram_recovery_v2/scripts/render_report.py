#!/usr/bin/env python3
"""Render FINAL_REPORT.json -> FINAL_REPORT.html (self-contained, dark theme)."""

import html
import json
import sys
from pathlib import Path


def main() -> None:
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    report = json.loads((run_dir / "FINAL_REPORT.json").read_text())
    cap = report.get("capability", {})
    counts = report.get("counts", {})
    reactions = report.get("reactions", {})
    rows = report.get("rows", [])

    def esc(x):
        return html.escape(str(x or ""))

    cap_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in cap.items()
    )
    count_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(json.dumps(v))}</td></tr>"
        for k, v in counts.items()
    )
    react_rows = "".join(
        f"<tr><td>{esc(r.get('reactor'))}</td><td>{esc(r.get('emoji'))}</td>"
        f"<td>{esc(r.get('tgt'))}</td><td class='{'pass' if r.get('reactor_verified') else 'fail'}'>{'PASS' if r.get('reactor_verified') else 'FAIL'}</td></tr>"
        for r in reactions.get("details", [])
    )
    msg_rows = "".join(
        f"<tr><td>{esc(r['source_id'])}</td><td>{esc(r.get('target_id'))}</td>"
        f"<td>{esc((r['source_text'] or '')[:60])}</td><td>{esc((r['target_text'] or '')[:60])}</td>"
        f"<td>{esc(r['source_media'])}</td><td>{esc(r['target_media'])}</td>"
        f"<td>{esc(r['classifications']['caption'])}</td><td>{esc(r['classifications']['timestamp'])}</td></tr>"
        for r in rows
    )

    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>FINAL_REPORT {esc(run_dir.name)}</title>
<style>
body {{ background:#0d1117; color:#c9d1d9; font-family:monospace; padding:24px; }}
h1,h2 {{ color:#58a6ff; }} table {{ border-collapse:collapse; width:100%; margin-bottom:24px; }}
th,td {{ border:1px solid #30363d; padding:4px 8px; text-align:left; font-size:12px; }}
th {{ background:#161b22; }} .pass {{ color:#3fb950; }} .fail {{ color:#f85149; }}
</style></head><body>
<h1>FINAL_REPORT — {esc(run_dir.name)}</h1>
<h2>Capability matrix</h2><table><tr><th>FEATURE</th><th>STATUS</th></tr>{cap_rows}</table>
<h2>Counts</h2><table><tr><th>DIMENSION</th><th>COUNTS</th></tr>{count_rows}</table>
<h2>Reactions reconstruction</h2><table><tr><th>REACTOR</th><th>EMOJI</th><th>TARGET</th><th>VERIFIED</th></tr>{react_rows}</table>
<h2>Per-message fidelity ({len(rows)} rows)</h2>
<table><tr><th>SRC</th><th>TGT</th><th>SRC TEXT</th><th>TGT TEXT</th><th>SRC MEDIA</th><th>TGT MEDIA</th><th>CAPTION</th><th>TIMESTAMP</th></tr>{msg_rows}</table>
</body></html>"""
    (run_dir / "FINAL_REPORT.html").write_text(page)
    print(f"wrote {run_dir / 'FINAL_REPORT.html'}")


if __name__ == "__main__":
    main()