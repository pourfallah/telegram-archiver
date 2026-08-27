"""Phase 3 — real E2E via the NORMAL PRODUCTION APPLICATION (HTTP API + worker).

The full production path, driven through the live app endpoints exactly as the
UI does:
  login -> GET /api/accounts & /api/exports -> start export (Account A) via
  POST /api/exports -> wait worker export.run -> confirm export.verified=PASS
  -> POST /api/import/start-real (Account B) -> poll worker run_import ->
  read verification/reaction reports + MEDIA_IMPORT_TRACE.json.

This is NOT a test-only importer — it uses the same endpoints/worker/DB.

Usage: run inside backend container with a token from /tmp
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
RUN_DIR = Path("/data/e2e_run")
RUN_DIR.mkdir(parents=True, exist_ok=True)

SOURCE_ACCOUNT = 1   # +989 (First Dev., A)
TARGET_ACCOUNT = 3   # +551 (David, B)
TARGET_PEER = 165649921  # First Dev. as seen from B (= A me.id)

MARK = "RECOVERY_FINAL_20260827_"


def _req(method, path, body=None, timeout=120):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _req_plain(method, path, body=None, timeout=120):
    try:
        return _req(method, path, body, timeout)
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code, "__body__": e.read().decode()[:1000]}


def main():
    print("BASE:", BASE)
    # --- accounts & exports ---
    accts = _req("GET", "/api/accounts", None, 60)
    print("accounts:", [{"id": a["id"], "phone": a.get("phone")} for a in accts])
    exports = _req("GET", "/api/exports")
    print("existing exports ids:", [e["id"] for e in exports][-6:])

    # --- 1) start a NEW export of the A<->David private chat from Account A ---
    # find David chat id in A's view = 7768075024
    chat_id = 7768075024
    body = {
        "chat_id": chat_id,
        "format": "json",
        "include_media": True,
    }
    # NOTE: create endpoint lives at /api/accounts/{account_id}/exports
    exp = _req_plain("POST", f"/api/accounts/{SOURCE_ACCOUNT}/exports", body)
    print("EXPORT START:", json.dumps({k: exp.get(k) for k in ("id", "status", "chat_title", "message")}, default=str))
    if exp.get("__http_error__"):
        print("  ->", exp["__http_error__"], exp["__body__"])
        return

    export_id = exp.get("id")
    (RUN_DIR / "run_id.txt").write_text(str(export_id))

    # --- 2) poll export until it completes & is verified ---
    print("waiting export", export_id, "...")
    verified = False
    for i in range(60):  # up to 10 min
        time.sleep(10)
        e = _req("GET", f"/api/exports/{export_id}", None, 60)
        st = e.get("status")
        msg = e.get("messages_processed")
        # verification is a separate endpoint (ExportPublic omits it)
        v = _req_plain("GET", f"/api/exports/{export_id}/verification")
        ver = (v.get("verification") or {}).get("status") if isinstance(v, dict) else None
        vok = v.get("verified")
        print(f"  t+{(i+1)*10}s status={st} msgs={msg} verified={vok} ({ver})")
        if st == "completed":
            if vok is True and ver == "PASS":
                verified = True
            break
        if st in ("failed", "cancelled"):
            print("  EXPORT FAILED:", e.get("error"))
            return
    if not verified:
        print("  export did not verify PASS")
        return
    print("EXPORT VERIFIED PASS")

    # --- 3) start REAL import into Account B via production endpoint ---
    imp_body = {
        "export_id": export_id,
        "target_account_id": TARGET_ACCOUNT,
        "target_peer_id": TARGET_PEER,
        "message_limit": 9999,
        "contact_identifier": str(TARGET_PEER),
    }
    imp = _req_plain("POST", "/api/import/start-real", imp_body)
    print("IMPORT START:", json.dumps({k: imp.get(k) for k in ("id", "status", "message")}, default=str))
    if imp.get("__http_error__"):
        print("  ->", imp["__http_error__"], imp["__body__"])
        return
    job_id = imp.get("id")
    (RUN_DIR / "import_job_id.txt").write_text(str(job_id))

    # --- 4) poll import job ---
    final = None
    for i in range(60):
        time.sleep(10)
        j = _req("GET", f"/api/import/jobs/{job_id}")
        st = j.get("status")
        prog = (j.get("progress") or {})
        phase = prog.get("phase")
        err = j.get("error")
        print(f"  t+{(i+1)*10}s job={job_id} status={st} phase={phase}{' ERR='+err if err else ''}")
        if st in ("completed", "partial", "failed"):
            final = j
            break
    if final is None:
        print("  import did not finish")
        return
    print("IMPORT DONE status:", final.get("status"))
    (RUN_DIR / "import_job_final.json").write_text(json.dumps(final, default=str, indent=2))

    # --- 5) copy key artifacts into run dir via backend container volume ---
    print("DONE")
    return final


if __name__ == "__main__":
    main()