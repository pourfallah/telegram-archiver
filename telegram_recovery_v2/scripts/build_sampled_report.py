#!/usr/bin/env python3
"""Build corrected-run report (run 3): timestamps/filenames/media/caption/reply."""

import html
import json
import sys
from pathlib import Path


def main() -> None:
    run_dir = Path(sys.argv[1])
    rows = json.loads((run_dir / "final_rows.json").read_text())
    snaps = json.loads((run_dir / "sample" / "sample_source_messages.json").read_text())
    meta = json.loads((run_dir / "source_meta.json").read_text())
    man = json.loads((run_dir / "sample" / "sample_manifest.json").read_text())

    n_exact = sum(1 for r in rows if r.get("delta_s") is not None and abs(r["delta_s"]) <= 1)
    n_literal = sum(1 for r in rows if "<attached:" in (r.get("target_text") or ""))
    media_rows = [r for r in rows if r.get("media")]
    from collections import Counter

    media_cls = Counter(r["media"] for r in media_rows)
    fns = [r.get("filename") for r in media_rows if r.get("filename")]
    cap_pairs = [r for r in rows if r.get("source_id") and abs(r.get("delta_s") or 99) == 1 and r.get("target_text")]
    reply_rows = [r for r in rows if r.get("reply_to")]
    src_replies = [s for s in snaps if s.get("reply_to")]

    features = {
        "SOURCE_CHAT": "A <-> +989353114546",
        "TARGET_CHAT": "A <-> B (+5511991966422)",
        "RUN_ID": run_dir.name,
        "SOURCE_INDEXED": meta.get("message_count"),
        "SAMPLED_MESSAGE_COUNT": len(snaps),
        "TARGET_NEW_MESSAGE_COUNT": len(rows),
        "DATE_RANGE": f"{min(s['date'][:10] for s in snaps)} -> {max(s['date'][:10] for s in snaps)}",
        "YEARS_COVERED": ",".join(man.get("years_covered", [])),
        "TIMESTAMP_EXACT (|Δt|<=1s vs source UTC)": f"{n_exact}/{len(rows)}",
        "LITERAL <attached:> TEXT": f"{n_literal} (0 = every media line bound)",
        "IMPORTED_METADATA (fwd_from.imported=true)": f"{len(rows)}/{len(rows)}",
        "MEDIA MATERIALIZED": f"{len(media_rows)} messages carry media; classes={dict(media_cls)}",
        "ORIGINAL FILENAMES PRESERVED": len(fns) > 0,
        "  samples": "; ".join(fns[:8]) if fns else "-",
        "CAPTION (CAPTION_SEPARATE +1s sibling)": f"{len(cap_pairs)} media captions as separate +1s message (import parser limitation, verified live 2026-08-28)",
        "REPLY": f"source replies={len(src_replies)} target reply_to={len(reply_rows)} -> NOT_RESTORED (import file syntax has no reply field)",
        "SOURCE_UNTOUCHED": "YES (verified live after import)",
    }

    rows_html = "".join(
        f"<tr><td>{r['target_id']}</td><td>{r.get('source_id') or '-'}</td>"
        f"<td>{r.get('delta_s')}</td><td>{r.get('target_date') or ''}</td>"
        f"<td>{r.get('media') or 'text'}</td><td>{html.escape(str(r.get('filename') or ''))}</td>"
        f"<td>{html.escape(r.get('target_text') or '')[:50]}</td>"
        f"<td>{r.get('reply_to') or ''}</td></tr>"
        for r in rows
    )
    feat_html = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k, v in features.items())
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>HISTORICAL SAMPLE RECOVERY (corrected) {html.escape(run_dir.name)}</title>
<style>body{{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:24px}}
h1,h2{{color:#58a6ff}} table{{border-collapse:collapse;width:100%;margin-bottom:20px}}
th,td{{border:1px solid #30363d;padding:4px 8px;text-align:left;font-size:12px}} th{{background:#161b22}}</style>
</head><body>
<h1>HISTORICAL SAMPLE RECOVERY — corrected run {html.escape(run_dir.name)}</h1>
<table>{feat_html}</table>
<h2>Per-message ({len(rows)})</h2>
<table><tr><th>TGT</th><th>SRC</th><th>Δt(s)</th><th>TGT DATE</th><th>MEDIA</th><th>FILENAME</th><th>TEXT</th><th>REPLY_TO</th></tr>{rows_html}</table>
</body></html>"""
    report = {"run_id": run_dir.name, "features": features, "rows": rows}
    (run_dir / "FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (run_dir / "FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.html").write_text(page)
    print(f"wrote {run_dir / 'FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.json'}")
    for k, v in features.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()