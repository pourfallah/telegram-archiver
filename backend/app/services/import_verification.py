"""Import verification engine.

After a real Telegram import completes, re-read the target conversation and
compare against the source archive. Produces a detailed verification report
(IMPORT_VERIFICATION_REPORT.json + .html) with per-message comparison.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.canonical_archive import _load_messages as load_canonical_messages


def _normalize_text(text: str | None) -> str:
    if text is None:
        return ""
    return " ".join(text.split())


def _msg_key(m: dict) -> tuple:
    """Deterministic key for matching messages (sender + text + date prefix)."""
    sender = m.get("sender") or {}
    sid = sender.get("id")
    date = str(m.get("date") or "")[:10]  # date only
    text = _normalize_text(m.get("text") or "")
    return (sid, date, text[:80])


class ImportVerification:
    """Compare source archive against imported target chat."""

    def __init__(self, source_messages: list[dict], target_messages: list[dict]):
        self.source = source_messages
        self.target = target_messages
        self.source_by_key = {_msg_key(m): m for m in source_messages}
        self.target_by_key = {_msg_key(m): m for m in target_messages}

    def compare(self) -> dict[str, Any]:
        matched = 0
        sender_seq_ok = True
        timestamp_ok = True
        text_ok = True
        media_ok = True
        details = {
            "source_count": len(self.source),
            "target_count": len(self.target),
            "matched": 0,
            "missing_in_target": [],
            "extra_in_target": [],
            "sender_mismatches": [],
            "timestamp_mismatches": [],
            "text_mismatches": [],
            "media_mismatches": [],
        }

        # Check each source message has a match in target
        for key, src in self.source_by_key.items():
            tgt = self.target_by_key.get(key)
            if tgt is None:
                details["missing_in_target"].append({
                    "source_key": key,
                    "source_preview": src.get("text", "")[:80],
                })
                continue

            matched += 1

            # Sender
            src_sender = (src.get("sender") or {}).get("id")
            tgt_sender = (tgt.get("sender") or {}).get("id")
            if src_sender != tgt_sender:
                details["sender_mismatches"].append({
                    "key": key,
                    "source_sender": src_sender,
                    "target_sender": tgt_sender,
                })
                sender_seq_ok = False

            # Timestamp
            src_date = str(src.get("date") or "")
            tgt_date = str(tgt.get("date") or "")
            if src_date[:16] != tgt_date[:16]:  # compare to minute
                details["timestamp_mismatches"].append({
                    "key": key,
                    "source": src_date,
                    "target": tgt_date,
                })
                timestamp_ok = False

            # Text
            src_text = _normalize_text(src.get("text") or "")
            tgt_text = _normalize_text(tgt.get("text") or "")
            if src_text != tgt_text:
                details["text_mismatches"].append({
                    "key": key,
                    "source": src_text[:100],
                    "target": tgt_text[:100],
                })
                text_ok = False

            # Media presence (not byte-for-byte)
            src_media = len(src.get("media") or [])
            tgt_media = len(tgt.get("media") or [])
            if src_media != tgt_media:
                details["media_mismatches"].append({
                    "key": key,
                    "source_media_count": src_media,
                    "target_media_count": tgt_media,
                })
                media_ok = False

        # Check for extra messages in target
        for key, tgt in self.target_by_key.items():
            if key not in self.source_by_key:
                details["extra_in_target"].append({
                    "target_key": key,
                    "target_preview": tgt.get("text", "")[:80],
                })

        details["matched"] = matched

        # Overall classification
        if matched == len(self.source) and matched == len(self.target):
            overall = "FULL_MATCH"
        elif matched == len(self.source):
            overall = "SOURCE_COVERED_EXTRA_IN_TARGET"
        elif matched > 0:
            overall = "PARTIAL"
        else:
            overall = "NO_MATCH"

        return {
            "overall": overall,
            "counts": {
                "source": len(self.source),
                "target": len(self.target),
                "matched": matched,
            },
            "checks": {
                "count": matched == len(self.source) == len(self.target),
                "sender_order": sender_seq_ok,
                "timestamp": timestamp_ok,
                "text": text_ok,
                "media": media_ok,
            },
            "details": details,
            "generated_at": datetime.now(UTC).isoformat(),
        }


def run_verification(
    source_archive_dir: Path,
    target_chat_messages: list[dict],
) -> dict[str, Any]:
    """High-level verification entry point."""
    source_msgs = load_canonical_messages(source_archive_dir)
    verifier = ImportVerification(source_msgs, target_chat_messages)
    return verifier.compare()


def write_report(report: dict, out_dir: Path) -> Path:
    """Write JSON + HTML report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "IMPORT_VERIFICATION_REPORT.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    html_path = out_dir / "IMPORT_VERIFICATION_REPORT.html"
    html = _render_html(report)
    html_path.write_text(html, encoding="utf-8")
    return json_path


def _render_html(report: dict) -> str:
    overall = report.get("overall", "UNKNOWN")
    counts = report.get("counts", {})
    checks = report.get("checks", {})
    details = report.get("details", {})

    def badge(ok: bool) -> str:
        return f'<span class="{"pass" if ok else "fail"}">{"PASS" if ok else "FAIL"}</span>'

    missing = len(details.get("missing_in_target", []))
    extra = len(details.get("extra_in_target", []))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Import Verification Report</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
h1 {{ color: #1f2937; }}
.badge {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 0.375rem; font-weight: 600; }}
.pass {{ background: #dcfce7; color: #166534; }}
.fail {{ background: #fee2e2; color: #991b1b; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
th, td {{ border: 1px solid #e5e7eb; padding: 0.5rem; text-align: left; }}
th {{ background: #f3f4f6; }}
.mismatch {{ color: #dc2626; }}
.ok {{ color: #16a34a; }}
pre {{ background: #1f2937; color: #e5e7eb; padding: 1rem; overflow: auto; }}
</style>
</head><body>
<h1>Import Verification Report</h1>
<p>Generated: {report.get("generated_at", "")}</p>

<h2>Overall: <span class="badge">{overall}</span></h2>

<h3>Counts</h3>
<table>
<tr><th>Source messages</th><td>{counts.get("source", 0)}</td></tr>
<tr><th>Target messages</th><td>{counts.get("target", 0)}</td></tr>
<tr><th>Matched</th><td>{counts.get("matched", 0)}</td></tr>
<tr><th>Missing in target</th><td class="{'mismatch' if missing else 'ok'}">{missing}</td></tr>
<tr><th>Extra in target</th><td class="{'mismatch' if extra else 'ok'}">{extra}</td></tr>
</table>

<h3>Checks</h3>
<table>
<tr><th>Check</th><th>Result</th></tr>
<tr><td>Message count (exact)</td><td>{badge(checks.get("count", False))}</td></tr>
<tr><td>Sender order</td><td>{badge(checks.get("sender_order", False))}</td></tr>
<tr><td>Timestamp (minute precision)</td><td>{badge(checks.get("timestamp", False))}</td></tr>
<tr><td>Text content</td><td>{badge(checks.get("text", False))}</td></tr>
<tr><td>Media count per message</td><td>{badge(checks.get("media", False))}</td></tr>
</table>

<h3>Details</h3>
<details><summary>Missing in target ({missing})</summary>
<pre>{json.dumps(details.get("missing_in_target", []), ensure_ascii=False, indent=2)}</pre>
</details>
<details><summary>Extra in target ({extra})</summary>
<pre>{json.dumps(details.get("extra_in_target", []), ensure_ascii=False, indent=2)}</pre>
</details>
<details><summary>Sender mismatches ({len(details.get('sender_mismatches', []))})</summary>
<pre>{json.dumps(details.get("sender_mismatches", []), ensure_ascii=False, indent=2)}</pre>
</details>
<details><summary>Timestamp mismatches ({len(details.get('timestamp_mismatches', []))})</summary>
<pre>{json.dumps(details.get("timestamp_mismatches", []), ensure_ascii=False, indent=2)}</pre>
</details>
<details><summary>Text mismatches ({len(details.get('text_mismatches', []))})</summary>
<pre>{json.dumps(details.get("text_mismatches", []), ensure_ascii=False, indent=2)}</pre>
</details>
<details><summary>Media mismatches ({len(details.get('media_mismatches', []))})</summary>
<pre>{json.dumps(details.get("media_mismatches", []), ensure_ascii=False, indent=2)}</pre>
</details>
</body></html>"""
    return html
