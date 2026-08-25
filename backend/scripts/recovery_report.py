"""Final recovery report builder for the E2E run."""
from __future__ import annotations

import json
from pathlib import Path


def build(run_dir: Path) -> Path:
    src = json.loads((run_dir / "source" / "source_messages.json").read_text())
    summary = json.loads((run_dir / "source" / "source_summary.json").read_text())
    voters = json.loads((run_dir / "source" / "source_reaction_voters.json").read_text())
    samples = json.loads((run_dir / "target" / "target_after_samples.json").read_text())
    target = samples["T+300"]["records"]
    proto = json.loads((run_dir / "import_protocol_log.json").read_text())
    react = json.loads((run_dir / "reaction_reconstruction.json").read_text())
    clear_v = json.loads((run_dir / "target" / "target_after_clear_verification.json").read_text())

    # source -> target mapping by (date, text) for report purposes
    def norm(t):
        return " ".join((t or "").split())[:40]

    tgt_by_key = {}
    for r in target:
        tgt_by_key[f"{r['message_date'][:16]}|{norm(r['text'])}"] = r

    rows = []
    ts_exact = ts_meta = 0
    media_score = {"total": 0, "ok": 0}
    for m in src:
        key = f"{m['date'][:16]}|{norm(m['text'])}"
        t = tgt_by_key.get(key)
        if t is None:
            rows.append(f"<tr><td>{m['source_message_id']}</td><td>{m['date'][:16]}</td>"
                        f"<td>{m['text'][:40]}</td><td>—</td><td>NOT_FOUND</td></tr>")
            continue
        md = t["message_date"][:16]
        fd = (t.get("fwd_from_date") or "")[:16]
        src_d = m["date"][:16]
        if md == src_d:
            ts = "TIMESTAMP_EXACT"
            ts_exact += 1
        elif fd == src_d:
            ts = "IMPORTED_METADATA_ONLY"
            ts_meta += 1
        else:
            ts = "NOT_RESTORED"
        # media
        src_media = (m.get("media") or {}).get("ctor", "none")
        mstate = "—"
        if src_media != "none":
            media_score["total"] += 1
            # media may be a SEPARATE message from the caption (caption-split).
            # The media target is the same-date target whose media ctor matches.
            media_t = None
            for r in target:
                if r["message_date"][:16] == m["date"][:16] and r.get("media_ctor"):
                    media_t = r
                    break
            attrs = (media_t or {}).get("media_attrs") or []
            tgt_ctor = (media_t or t or {}).get("media_ctor")
            if src_media == "MessageMediaPhoto" and tgt_ctor == "MessageMediaPhoto":
                mstate = "PHOTO_EXACT"
                media_score["ok"] += 1
            elif src_media == "MessageMediaDocument" and tgt_ctor == "MessageMediaDocument":
                if "DocumentAttributeSticker" in attrs:
                    mstate = "STICKER_EXACT"
                    media_score["ok"] += 1
                elif "DocumentAttributeAnimated" in attrs:
                    mstate = "GIF_EXACT"
                    media_score["ok"] += 1
                elif "DocumentAttributeAudio" in attrs:
                    mstate = "AUDIO_EXACT"
                    media_score["ok"] += 1
                else:
                    mstate = "DOCUMENT_ONLY"
            else:
                mstate = "FAILED"
        rows.append(
            f"<tr><td>{m['source_message_id']}</td><td>{m['date'][:16]}</td>"
            f"<td>{m['text'][:40]}</td><td>{t['target_id']}</td><td>{ts}</td>"
            f"<td>{mstate}</td></tr>")

    # reaction rows
    react_rows = []
    for r in react.get("results", []):
        emoji = r.get("emoji")
        reactor = r.get("reactor")
        st = r.get("status")
        cls = "ok" if "RECONSTRUCTED" in st or "NotModified" in st else "warn"
        react_rows.append(
            f"<tr><td>{r.get('source_message_id')}</td><td>{r.get('target_id','—')}</td>"
            f"<td>{reactor}</td><td>{emoji}</td><td class='{cls}'>{st}</td></tr>")

    total_src = len(src)
    matched = len([r for r in rows if 'NOT_FOUND' not in r])
    n_react_ok = sum(1 for r in react.get("results", [])
                     if "RECONSTRUCTED" in r.get("status", "")
                     or "NotModified" in r.get("status", ""))

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Final Recovery Report</title>
<style>body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;margin:24px}}
h1,h2{{color:#38bdf8}} table{{border-collapse:collapse;width:100%;margin:8px 0}}
th,td{{border:1px solid #334155;padding:4px 8px;font-size:12px;text-align:left}}
.ok{{color:#34d399}} .warn{{color:#fbbf24}} .bad{{color:#f87171}}
.card{{border:1px solid #334155;border-radius:8px;padding:10px 16px;margin:6px 0}}</style></head><body>
<h1>FINAL RECOVERY REPORT — E2E RUN e2e_20260825</h1>

<h2>Source snapshot</h2>
<div class="card">SOURCE: A (+989394430100, First Dev.) — TARGET: B (+5511991966422, David Rodriguez)<br>
TARGET PEER: A&lt;-&gt;B (existing private chat)<br>
Export: MESSAGES={summary['total_messages']} MEDIA={summary['media_items']}
REACTIONS={summary['reaction_items']} REPLIES={len(summary['replies'])}
FORWARDS={len(summary['forwards'])} GROUPED={summary['grouped'] or 'NO_GROUPED_MEDIA_IN_THIS_FIXTURE'}</div>

<h2>B-side clear verification</h2>
<div class="card">B-side clear: just_clear=true, revoke=false (pts={json.loads((run_dir/'checkpoint_clear.json').read_text()).get('pts')})<br>
A intact after clear: {str(clear_v.get('a_intact')).upper()} — B empty of source: {str(clear_v.get('b_empty_of_source')).upper()}</div>

<h2>Import protocol log</h2>
<div class="card">{'<br>'.join(f"{p.get('step')}: {p.get('result') or p.get('returned')} {p.get('file_name') or ''}" for p in proto)}</div>

<h2>Message fidelity ({matched}/{total_src} mapped)</h2>
<table><tr><th>source id</th><th>source date</th><th>text</th><th>target id</th><th>timestamp</th><th>media</th></tr>
{''.join(rows)}</table>

<h2>Timestamps</h2>
<div class="card">TIMESTAMP_EXACT (message.date == source): {ts_exact}/{total_src} ·
IMPORTED_METADATA_ONLY: {ts_meta}/{total_src}</div>

<h2>Media fidelity</h2>
<div class="card">restored: {media_score['ok']}/{media_score['total']}</div>

<h2>Reaction reconstruction (identity-preserving)</h2>
<table><tr><th>source msg</th><th>target msg</th><th>reactor</th><th>reaction</th><th>status</th></tr>
{''.join(react_rows)}</table>
<div class="card">reactions reconstructed with correct identity: {n_react_ok}/{len(voters)}
(REACTOR_SESSION identity rule: A's reactions by A, B's by B)</div>

<h2>Honest limitations</h2>
<ul>
<li>Replies: ARCHIVAL_ONLY — import protocol carries no reply syntax; no re-parenting RPC exists.</li>
<li>Forward provenance (channel music): ARCHIVAL_ONLY — target fwd_from holds IMPORT metadata, not the original channel source.</li>
<li>Caption of forwarded music: CAPTION_SEPARATE (import materializes it as an adjacent text message).</li>
<li>Reaction timestamps: intentionally not restored (product rule: WHO/WHAT/WHERE matter, date does not).</li>
<li>Imported messages are cloud messages in the shared A&lt;-&gt;B conversation.</li>
</ul>
<p><small>Generated from ACTUAL MTProto reads of the target chat (not from local DB).</small></p>
</body></html>"""
    out = run_dir / "FINAL_RECOVERY_REPORT.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    p = build(Path(sys.argv[1]))
    print("WROTE", p)
