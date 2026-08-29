# Architecture — telegram_recovery_v2

## Design principle

```
ACCOUNT A (source of truth)
   |
   v  DIRECT MTProto READ (messages.getHistory via Telethon)
LOSSLESS ARCHIVE   (canonical JSON + raw MTProto + media bytes + SHA256)
   |
   v  DIRECT render of the ONLY format the official import parser accepts
IMPORT PACKAGE     (foreign-app text file + media tokens)
   |
   v  OFFICIAL MTProto IMPORT (check/init/upload/start)
ACCOUNT B (same A<->B peer)
   |
   v  REAL MTProto VERIFICATION (actual target Message objects)
FINAL REPORT / CAPABILITY MATRIX
```

ONE engine. `TelegramRecoveryEngine` (src/recovery/engine.py) is called by
the CLI, the scripts, the tests — and later the web app. There is no second
implementation.

## Modules (all under src/recovery/)

| module | responsibility |
|---|---|
| config.py | accounts A/B (phone, api creds, session string), runs dir |
| telegram_client.py | ClientPool: one connected Telethon client per account; flood-wait helper |
| source_reader.py | paginated A-side history read; B-side target snapshots (target_before/after) |
| archive.py | canonical per-message record (media+caption+reply+reactions stay ONE record), raw MTProto NDJSON, media records + SHA256'd bytes, ArchiveReader for package building |
| media.py | attach-name scheme, InputMedia construction rules (incl. proven .tgs handling) |
| importer.py | build_import_file (canonical archive -> foreign-app text file) + ImportEngine: the official import RPC sequence with persisted import_id (resumable, no double import) |
| mapper.py | source->target mapping (sender/date/text/media/sequence signals — never text alone) |
| reactions.py | reaction plan (who reacted, from getMessageReactionsList), reconstruction via each reactor's own session, verification via getMessagesReactions |
| verifier.py | archive consistency checks + per-message classification against REAL target objects + FINAL_REPORT |
| engine.py | TelegramRecoveryEngine: orchestration, run ids, artifacts |

## Run artifacts (test_runs/<run_id>/)

- run.log — timestamped engine log
- archive/ — messages.ndjson, raw_messages.ndjson, reactions.ndjson,
  reactions_plan.json, media/media_index.json, media/files/*, archive_meta.json
- import_file.txt — the import payload
- package.json, media_attach_map.json
- import_state.json — persisted import_id / uploaded files / started flag
- media_import_trace.json — per-media upload trace (NOT treated as success)
- target_before.json / target_after.json / target_poll_*.json
- source_to_target.json — mapping with confidence + reason
- reactions_reconstruction.json, reactions_verification.json
- verify_export.json
- FINAL_REPORT.json — per-message classifications

Run ids are `recovery_v2_YYYYMMDD_HHMMSS_xxxxxx`; artifacts are never reused
or deleted between runs.

## Security

- Session strings and api_hash live in `secrets/` (git-ignored) or env vars.
- Nothing prints OTP/2FA/session material. Reports contain peer ids and user
  ids only, never credentials.
