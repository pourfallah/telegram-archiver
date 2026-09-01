# HISTORICAL SAMPLE RECOVERY TEST (A<->C -> A<->B)

One controlled experiment in phases. Its single question:

> Can the recovery engine take REAL, genuinely-old Telegram history from an
> unrelated source chat (A<->C) and place it into the existing A<->B peer with
> the original historical dates and maximum fidelity?

## Hard invariants

- **SOURCE A<->C is read-only.** Only `getHistory` (catalog) and `getMessages`
  (re-fetch selected ids). Never send/delete/edit/react/forward. A post-run
  immutability check re-reads the sampled ids.
- **TARGET A<->B is the only writable peer**, and only with `--confirm`.
- **No fabrication**: no invented dates, no WhatsApp round-trip, real source bytes.
- **Import = official history import** (checkHistoryImport -> checkHistoryImportPeer
  -> initHistoryImport -> uploadImportedMedia -> startHistoryImport); no
  sendMessage/sendMedia/forwardMessages substitute.
- **No redundant full scan**: the 198k-message-source is never fully
  materialized, downloaded, reacted, or serialized.

## Phases (`src/recovery/pipeline.py`)

| Phase | Work | Cost |
|---|---|---|
| P1 | lightweight resumable catalog (`source_catalog.ndjson` + checkpoint): id/date/sender/media-type/reply/group/reactions-flags | 1 lightweight pass, resume-safe, reused |
| P2 | stratified year-bucket sampling, deterministic `seed=SHA256(run_id)`, type-diverse | in-memory |
| P3 | LAZY full fetch of ONLY selected ids (+ reply parents + full groups) via `getMessages` | small |
| P4 | media download + reactions for the sample only | small |
| P5 | package from snapshot => roundtrip verified (abort on mismatch) | small |
| P6 | official import into A<->B (import_id + package_hash persisted) | small |
| P7 | read real target objects; map; reconstruct reactions; per-field fidelity report + decision | small |

## Run

```bash
python -m recovery_v2.sample_history --source-peer +989****4546 \
    --target-peer <A-phone> --count 20 --years 3
# ...prints RUN_ID, catalog range/years, sampled ids, SAMPLE_PREVIEW.html

python -m recovery_v2.full_sampled_recovery --run-id <ID> \
    --source-peer +989****4546 --dry-run      # P3-P5, B untouched
python -m recovery_v2.full_sampled_recovery --run-id <ID> \
    --source-peer +989****4546 --confirm      # P6-P7, clears + imports into B
```

Artifacts per run under `test_runs/<run_id>/`: source_catalog(.ndjson + checkpoint),
sample_manifest.json, SAMPLE_PREVIEW.html, archive/, package/, source_sample_snapshot.json,
target_before.json, target_after.json, source_to_target.json,
FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.{json,html}.

## Decision (`pick_decision` in pipeline)

- `IMPORT_FAILED` — no new target messages.
- `HISTORICAL_IMPORT_VERIFIED` — ≥1 TIMESTAMP_EXACT (`target.message.date ≈ source.date`).
- `PARTIAL_HISTORICAL_IMPORT` — ≥1 IMPORTED_METADATA_ONLY (`fwd_from.date` historical, `imported=true`, visible date not).
- `HISTORICAL_TIMELINE_NOT_RESTORED` — neither.