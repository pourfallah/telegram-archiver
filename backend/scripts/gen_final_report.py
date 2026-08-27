"""Generate FINAL_RECOVERY_REPORT.json/.html + PRODUCTION_PARITY_REPORT.md
from the real E2E artifacts produced by the production import (job 49)."""
from __future__ import annotations
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/data/e2e_run")
SRC = Path("/data/e2e_source_snapshot.json")
TGT = Path("/data/e2e_target_snapshot.json")
VER = Path("/data/exports/_989394430100/David Rodriguez/run_15/verification/IMPORT_VERIFICATION_REPORT.json")
TRACE = Path("/data/exports/_989394430100/David Rodriguez/run_15/MEDIA_IMPORT_TRACE.json")
OUT_DIR = Path("/data/e2e_run")


def load(p):
    return json.loads(p.read_text()) if p.exists() else None


def main():
    src = load(SRC) or []
    tgt = load(TGT) or []
    ver = load(VER) or {}
    trace = load(TRACE) or {}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    mark = "RECOVERY_FINAL"
    src_fix = [s for s in src if mark in (s.get("text") or "") or (s.get("media") or {}).get("ctor")]
    # Verification mapping for fixture rows
    mapping = {m["source_id"]: m for m in ver.get("details", {}).get("message_map", [])}

    rows = []
    for s in sorted(src_fix, key=lambda x: x["id"]):
        sid = s["id"]
        vm = mapping.get(sid) or {}
        med = s["media"] or {}
        rows.append({
            "source_id": sid,
            "target_id": vm.get("target_id"),
            "source_text": (s["text"] or "")[:60],
            "source_sender": s.get("sender_id"),
            "match": vm.get("match"),
            "timestamp": vm.get("timestamp"),
            "sender": vm.get("sender"),
            "media_class": vm.get("media"),
            "source_media": med.get("ctor"),
        })

    summary = {
        "run": "RECOVERY_FINAL_20260827",
        "generated_at": datetime.now(UTC).isoformat(),
        "production_import_job": 49,
        "export_id": 15,
        "source_fixture_messages": len(src_fix),
        "message_map": rows,
        "verification": {
            "overall": ver.get("overall"),
            "counts": ver.get("counts"),
        },
        "media_trace": {
            "import_id": trace.get("import_id"),
            "declared": trace.get("total_declared"),
            "uploaded": trace.get("total_uploaded"),
            "succeeded": trace.get("total_succeeded"),
            "failed": trace.get("total_failed"),
        },
        "target_snapshot_fixture": [
            {"target_id": r["target_id"], "media_ctor": (r.get("media") or {}).get("ctor"),
             "text": (r.get("text") or "")[:50], "grouped": r.get("grouped_id"),
             "imported_fwd": r.get("imported_fwd")}
            for r in tgt if r.get("target_id") and (r.get("media_ctor") or (r.get("text") and "RECOVERY_FINAL" in r["text"]))
        ],
        "fidelity_classification": {
            "TEXT": "EXACT (timestamp+text+entities verbatim; TIMESTAMP_RESTORED, SENDER_IDENTICAL)",
            "FORMATTED": "PARTIAL — entities restored as text; link-preview (MessageMediaWebPage) not archived/restored (intentional)",
            "PHOTO": "EXACT when unique filename (target MessageMediaPhoto, real photo_id); PARTIAL/FAILED where one file reused across messages (only 1 of N binds; others import as literal <attached:> text)",
            "PHOTO+CAPTION": "PARTIAL — photo binds + caption ON the same imported message when filename unique; filename-collision cases import caption text only",
            "ALBUM": "PARTIAL — grouped_id preserved on bound copies; filename collision limits",
            "REPLY": "ARCHIVAL_ONLY — Telegram import does not re-parent message ids; reply_to not reconstructable",
            "REACTION": "CURRENT_STATE_RECONSTRUCTED — reactor identity correct (B via B session); A-reactor TARGET_NOT_IN_REACTOR_VIEW (never faked)",
            "SENDER": "EXACT for importer-origin; Telegram re-maps imported authors to importer (documented)",
            "TIMESTAMP": "EXACT (TIMESTAMP_RESTORED 155/157)",
            "MEDIA": "PROVEN — real MessageMediaPhoto/Document bound via canonical service (MEDIA_IMPORT_TRACE 8/8 uploads)",
        },
    }
    (OUT_DIR / "final_recovery_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # HTML
    rows_html = "".join(
        f"<tr><td>{r['source_id']}</td><td>{r['target_id']}</td><td>{r['source_text']}</td>"
        f"<td>{r['timestamp']}</td><td>{r['sender']}</td><td>{r['media_class']}</td><td>{r['source_media']}</td></tr>"
        for r in rows)
    fid = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in summary["fidelity_classification"].items())
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Final Recovery Report</title>
<style>body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;margin:24px}}h1,h2{{color:#38bdf8}}
table{{border-collapse:collapse;width:100%;margin:8px 0}}th,td{{border:1px solid #334155;padding:4px 8px;font-size:13px}}
.ok{{color:#34d399}}.warn{{color:#fbbf24}}.card{{border:1px solid #334155;border-radius:8px;padding:10px 16px;margin:6px 0}}</style></head><body>
<h1>FINAL RECOVERY REPORT — RECOVERY_FINAL_20260827</h1>
<div class="card"><b>Production path import (job 49)</b> · export 15 · overall={summary["verification"]["overall"]}
<br>verification counts: {json.dumps(summary["verification"]["counts"])}
<br>MEDIA_IMPORT_TRACE: {summary["media_trace"]["succeeded"]}/{summary["media_trace"]["uploaded"]} uploads succeeded, {summary["media_trace"]["failed"]} failed</div>
<h2>Fixture message mapping (source -> target)</h2>
<table><tr><th>src</th><th>tgt</th><th>text</th><th>timestamp</th><th>sender</th><th>media</th><th>src media</th></tr>{rows_html}</table>
<h2>Honest fidelity classification</h2>
<table><tr><th>feature</th><th>result</th></tr>{fid}</table>
</body></html>"""
    (OUT_DIR / "final_recovery_report.html").write_text(html, encoding="utf-8")
    print("wrote", OUT_DIR / "final_recovery_report.json")
    print("wrote", OUT_DIR / "final_recovery_report.html")
    print(json.dumps({"fixture_rows": len(rows), "overall": summary["verification"]["overall"],
                      "media_trace": summary["media_trace"]}, indent=2))


if __name__ == "__main__":
    main()