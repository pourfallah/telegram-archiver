# Architecture

## Component diagram

```
                    RECOVERY_CONFIG (.env / env)
                    api_id/hash/session for A and B, peer, run_dir, pacing
                                     │
        ┌────────────────────────────┴────────────────────────────┐
        ▼                                                          ▼
   SOURCE CLIENT (A)                                        TARGET CLIENT (B)
   RecoveryClient → Telethon TelegramClient                 RecoveryClient → Telethon TelegramClient
        │                                                          │
        ▼                                                          ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │                     TelegramRecoveryEngine (ONE class)              │
   │   export · verify-export · build-package · clear-target · import    │
   │   snapshot · map · reconstruct-reactions · verify · full-test       │
   └─────────────┬──────────────────────────────┬────────────────────────┘
                 │ reads/writes                 │ calls the 5 official
                 ▼                              ▼  history-import RPCs
        LOSSLESS ARCHIVE                       ImportEngine
        <run>/archive/                         checkHistoryImport
          manifest.json                        checkHistoryImportPeer
          messages.ndjson                      initHistoryImport
          raw/raw.ndjson                       uploadImportedMedia x N
          media/〈…fixtures…/                   startHistoryImport
          reactions/archive.json
        <run>/package/ (_chat.txt+media+manifest.json)
        <run>/source_to_target.json · target_before.json · target_after.json
        <run>/media_import_trace.json · reaction_reconstruction.json
        <run>/FINAL_REPORT.{json,html}
```

Everything the CLI does, the tests do, and (later) the web app will do goes
through `TelegramRecoveryEngine`. `ImportEngine` is its single import
implementation. There is **no** separate test importer / CLI importer /
production importer.

## Canonical archive (lossless, streaming)

One source Telegram message = **one record**, never flattened into a transcript.

`messages.ndjson` (one JSON line per message) keeps the complete property set:
`source_message_id, peer_id, date, edit_date, from_id, text, entities,
caption, media[], reply_to (reply_to_msg_id/top_id/peer, quote, quote_text,
quote_entities), forward (fwd header incl. `imported`), grouped_id, reactions
summary, views, forwards_count, flags`.

`raw/raw.ndjson` holds a **sanitized raw MTProto snapshot** per message
(produced by `tl_to_plain`). Only session/API/authorization secrets are ever
excluded — ordinary message properties are preserved. Bytes (`file_reference`,
`waveform`) become base64. This raw feed is the ultimate recovery source.

Memory-safe: both feeds are appended via NDJSON, so arbitrarily large
histories never load into RAM.

## Run model

Each run gets a deterministic `run_id` (`recovery_v2_YYYYMMDD_HHMMSS_xxxxx`) and
its own directory under `RECOVERY_RUN_DIR`. No artifact is reused between runs;
nothing is deleted, so every recovery is reproducible.

Import is **resume-safe**: the `import_id` is persisted in run state, so a
crashed import is resumed/verified, never blindly re-initialized (no accidental
double import).

## Fidelity verification

`Verifier` compares each source record to its mapped target twin and classifies
every feature. The only authoritative input is the **actual target message
objects** read from Telegram after recovery. See
[FIDELITY_CLASSES.md](FIDELITY_CLASSES.md) for the label semantics and
[LIMITATIONS.md](LIMITATIONS.md) for the capability matrix.

## Pacing / safety

Source reads are paced (`RECOVERY_MSGS_PER_SEC`, burst) to avoid `FloodWait`.
`clear_target` always uses `just_clear=true, revoke=false` (project rule #40).