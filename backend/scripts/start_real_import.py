"""Start + poll a real production import via the normal app API (start-real)."""
from __future__ import annotations
import json, sys, time, urllib.request

BASE = "http://localhost"
TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
EXPORT_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 15
TARGET_ACCOUNT = 3
TARGET_PEER = 165649921  # First Dev. as seen from B (= A me.id)


def r(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as x:
            return json.loads(x.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode()[:600]}


imp = r("POST", "/api/import/start-real", {
    "export_id": EXPORT_ID,
    "target_account_id": TARGET_ACCOUNT,
    "target_peer_id": TARGET_PEER,
    "message_limit": 1000,
    "contact_identifier": str(TARGET_PEER),
})
print("IMPORT START:", json.dumps({k: imp.get(k) for k in ("id", "status", "message", "__error__", "__body__")}, default=str))
if imp.get("__error__"):
    sys.exit(1)
job_id = imp.get("id")
with open("/tmp/import_job_id.txt", "w") as f:
    f.write(str(job_id))

for i in range(60):
    time.sleep(10)
    j = r("GET", f"/api/import/jobs/{job_id}")
    st = j.get("status")
    prog = j.get("progress") or {}
    phase = prog.get("phase")
    err = j.get("error")
    print(f"  t+{(i+1)*10}s job={job_id} status={st} phase={phase}{' ERR='+str(err) if err else ''}", flush=True)
    if st in ("completed", "partial", "failed"):
        with open("/tmp/import_job_final.json", "w") as f:
            json.dump(j, f, default=str, indent=2)
        print(json.dumps({"final_status": st, "verification_overall": (prog.get("verification") or {}).get("overall")}, default=str))
        sys.exit(0)
print("not finished in 10min")
sys.exit(2)