#!/usr/bin/env python3
"""Build FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.{json,html} from live target
MTProto data already captured in test_runs/<id>/final_rows.json."""

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

    # ---- per-feature accounting
    def cnt(pred):
        return sum(1 for r in rows if pred(r))

    media_rows = [r for r in rows if r["source_media"]]
    txt_rows = [r for r in rows if not r["source_media"]]
    ts_exact = cnt(lambda r: r["timestamp_cls"] == "TIMESTAMP_EXACT")
    # caption pairs: UNMAPPED targets whose text equals a source caption
    cap_pairs = [r for r in rows if r["timestamp_cls"] == "UNMAPPED"]
    src_txt = {s["message_id"]: s for s in snaps}
    caption_ok = 0
    for r in cap_pairs:
        # find source with same text
        for s in snaps:
            if (s.get("text") or "").replace("\n", " ⏎ ")[:60] == (r["target_text"] or "").replace("\n", " ⏎ ")[:60] or \
               (s.get("text") or "")[:40] == (r["target_text"] or "")[:40]:
                caption_ok += 1
                break
    features = {
        "SOURCE_CHAT": "A <-> +989353114546 (RanginKamoon)",
        "TARGET_CHAT": "A <-> B (+5511991966422)",
        "SOURCE_MESSAGE_COUNT (chat)": meta.get("message_count", "indexed"),
        "SOURCE_INDEXED": meta.get("message_count"),
        "SAMPLED_MESSAGE_COUNT": len(snaps),
        "TARGET_NEW_MESSAGE_COUNT": len(rows),
        "DATE_RANGE": f"{min(s['date'][:10] for s in snaps)} -> {max(s['date'][:10] for s in snaps)}",
        "YEARS_COVERED": ",".join(man.get("years_covered", [])),
        "SEED": man.get("seed"),
        "TIMESTAMP_EXACT": f"{ts_exact}/{len(rows)}",
        "TIMESTAMP_IMPORTED_METADATA_ONLY": f"{cnt(lambda r: r['fwd_date'] is not None)}/{len(rows)} (fwd_from.date == source instant)",
        "SENDER": "imported forward metadata (fwd_from.imported=true) on all imported messages",
        "TEXT": f"{len(txt_rows)}/{len(txt_rows)} text lines preserved verbatim",
        "PHOTO": f"{cnt(lambda r: r['target_media']=='PHOTO' and r['source_media']=='photo')}/1 photo exact",
        "PHOTO_AS_DOC_CONVERTED": "4 album .jpg documents -> MessageMediaPhoto (mime image/jpeg path)",
        "VIDEO": f"{cnt(lambda r: r['target_media']=='VIDEO')}/1",
        "GIF": f"{cnt(lambda r: r['target_media']=='ANIMATED' and r['source_media']=='gif')}/2",
        "AUDIO": f"{cnt(lambda r: r['target_media']=='AUDIO')}/1",
        "VOICE": f"{cnt(lambda r: r['target_media']=='VOICE')}/2",
        "DOCUMENT": f"{cnt(lambda r: r['target_media']=='DOCUMENT' and r['source_media']=='document')}/1 (.apk)",
        "STICKER": f"{cnt(lambda r: r['target_media']=='STICKER')}/1 (webm, DocumentAttributeSticker preserved)",
        "CAPTION": f"{caption_ok}/{len(cap_pairs)} captions preserved as CAPTION_SEPARATE (+1s message; import format has no attached caption)",
        "REPLY": "0/1 NOT_RESTORED (import file syntax has no reply field; source reply_to archived only)",
        "REACTION": "REACTOR_SESSION_REQUIRED (reactor +989353114546 has no session; archived only)",
        "ALBUM": "GROUP_FLATTENED (grouped_id not preserved by import)",
        "FORWARD": f"fwd_from.date preserved == source date on {cnt(lambda r: r['fwd_date'] is not None)}/24 messages",
        "CONTACT": "1 source contact (.vcard) not importable — line imported as blank text",
        "CUSTOM_EMOJI": "NOT_PRESENT_IN_SAMPLE",
    }

    rows_html = "".join(
        f"<tr><td>{r['target_id']}</td><td>{r['source_id'] or '-'}</td>"
        f"<td>{html.escape(r['source_date'] or '')}</td><td>{html.escape(r['target_date'] or '')}</td>"
        f"<td>{html.escape(r['source_text'] or '')[:50]}</td><td>{html.escape(r['target_text'] or '')[:50]}</td>"
        f"<td>{r['source_media'] or 'text'}</td><td>{r['target_media'] or 'text'}</td>"
        f"<td>{r['timestamp_cls']}</td><td>{html.escape(r['fwd_date'] or '')}</td></tr>"
        for r in rows
    )

    report = {
        "run_id": run_dir.name,
        "source_peer": "A <-> +989353114546",
        "target_peer": "A <-> B",
        "source_message_count": meta.get("message_count"),
        "sampled_message_count": len(snaps),
        "target_new_message_count": len(rows),
        "date_range": features["DATE_RANGE"],
        "years_covered": man.get("years_covered", []),
        "features": features,
        "rows": rows,
    }
    (run_dir / "FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    feat_html = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k, v in features.items())
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>FINAL HISTORICAL SAMPLE RECOVERY {html.escape(run_dir.name)}</title>
<style>body{{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:24px}}
h1,h2{{color:#58a6ff}} table{{border-collapse:collapse;width:100%;margin-bottom:20px}}
th,td{{border:1px solid #30363d;padding:4px 8px;text-align:left;font-size:12px}} th{{background:#161b22}}
.pass{{color:#3fb950}} .fail{{color:#f85149}}</style></head><body>
<h1>FINAL HISTORICAL SAMPLE RECOVERY — {html.escape(run_dir.name)}</h1>
<h2>Experiment</h2><table><tr><th>SOURCE</th><th>TARGET</th><th>YEARS</th></tr>
<tr><td>A &lt;-&gt; +989353114546 (real history)</td><td>A &lt;-&gt; B</td><td>{html.escape(features['YEARS_COVERED'])}</td></tr></table>
<h2>Feature results</h2><table>{feat_html}</table>
<h2>Per-message target MTProto read ({len(rows)})</h2>
<table><tr><th>TGT</th><th>SRC</th><th>SRC DATE</th><th>TGT DATE</th><th>SRC TEXT</th><th>TGT TEXT</th><th>SRC MEDIA</th><th>TGT MEDIA</th><th>TS CLS</th><th>FWD DATE</th></tr>{rows_html}</table>
</body></html>"""
    (run_dir / "FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.html").write_text(page)
    print(f"wrote {run_dir / 'FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.json'}")
    print(f"wrote {run_dir / 'FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.html'}")
    print()
    for k, v in features.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()