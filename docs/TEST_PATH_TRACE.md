# TEST PATH TRACE

Traces the "successful" isolated tests and compares them to the real production import path.

## The unit tests (`backend/tests/test_import_pipeline.py`)

All pass (9/9 in that file; full suite green). These are **serializer + attribute-construction** unit tests — they do NOT call Telegram.

| Test | What it proves | Telegram? |
|------|----------------|-----------|
| `test_import_text_only` | serializer emits `[01/01/2024, 10:00:00] - A: REPLY_PARENT` | NO — asserts file text |
| `test_import_photo` | serializer emits `<attached: photo_test.jpg>`, media_refs=1 | NO |
| `test_import_photo_caption_one_block` | photo+caption = ONE block (2 physical lines, caption has no ts prefix) | NO |
| `test_import_sticker_no_caption` | serializer emits `<attached: sticker.webp>` | NO |
| `test_upload_media_attributes` | `_build_input_media` returns `DocumentAttributeSticker(alt, InputStickerSetEmpty)`, `DocumentAttributeAudio(performer/title)`, `DocumentAttributeAnimated` | FakeClient.upload_file only |
| `test_mapping_never_positional_for_media` | empty-text sticker not positionally mapped to a photo target | NO |

**Critical limitation:** these tests never run `initHistoryImport` / `uploadImportedMedia` /
`startHistoryImport` against real Telegram. They prove the *import file text* and *InputMedia
attribute objects* are shaped correctly — they do NOT prove Telegram restores a real
MessageMediaPhoto/Document, and they do NOT execute against a real target peer.

## The scripts (`backend/scripts/*.py`)

| Script | Purpose | Real Telegram? |
|--------|---------|----------------|
| `caption_final_test.py` | Standalone: upload tiny JPEG + `InputMediaUploadedPhoto`, media+caption | YES (uses session id 3, peer 165649921) |
| `minimal_import_test.py` | Standalone rich attributes | YES |
| `matrix_media.py` / `media_matrix.py` | media binding matrix | YES |
| `recovery_e2e.py` / `recovery_e2e_import.py` | full E2E (snapshot/clear/import/verify/report) | YES |
| `independent_import.py` | independent import | YES |

These scripts use their OWN `TelegramImporter`-style RPC calls against live accounts. They are
**not** invoked by the production UI path and are not part of the running application.

## Comparison: SUCCESSFUL TEST vs PRODUCTION

| Feature | Test path | Production path | Same serializer? | Same import service? | Same media uploader? | Same session? | Same target peer? | Same package? | Same code path? | Same result? |
|---------|-----------|-----------------|------------------|----------------------|----------------------|---------------|-------------------|---------------|-----------------|--------------|
| TEXT | serializer unit test | import_tasks → build_import_file | YES (`import_serializer.build_import_file`) | YES | n/a | YES | YES | YES | YES | PROVEN (job 44/45 text matched) |
| PHOTO+CAPTION | `caption_final_test.py` standalone | canonical media service (uncommitted) | YES format | — | NO (test uses raw telethon; prod uses TelegramImportedMediaService) | YES | YES | YES | PARTIAL | job 45: photo+caption `UNMATCHED` — **FAILED in prod** |
| STICKER | `minimal_import_test.py` attrs | TelegramImportedMediaService attributes | — | — | attrs same shape | YES | YES | — | PARTIAL | job 44: sticker `UNMATCHED`; job 45 all media generic `DOCUMENT_EXACT` |
| GROUPED ALBUM | `recovery_e2e.py` capture only | serializer media_refs + specs | — | — | — | YES | YES | — | **NO** | job 45: album messages `UNMATCHED` |
| REACTION | `recovery_e2e_import.py` sendReaction | reconstruction.reconstruct_reactions (multi-session) | — | — | — | resolver | per-participant | — | YES | job 44: `PLAN_ONLY_DISABLED`; job 45: A→❤️ 1 ok, B→2 `TARGET_NOT_IN_REACTOR_VIEW` |

## ROOT DIVERGENCE (evidence-backed)

1. **The unit tests never bind media to Telegram.** Passing `test_import_pipeline` does not
   mean the running worker restores real media. The parity report's `STICKER_EXACT`/`AUDIO_EXACT`/
   `CAPTION_EXACT` claims are **not** produced by the real production runs.

2. **Live production runs contradict the parity report:**
   - job 44 (David Rodriguez, the real A↔B recovery): `overall=PARTIAL`, sticker message **UNMATCHED**, reactions **PLAN_ONLY_DISABLED**.
   - job 45 (E2E Test Chat): `overall=PARTIAL`, 3 UNMATCHED (photo+caption + both albums), and EVERY media item classified `DOCUMENT_EXACT` (`ctor=MessageMediaDocument`, attr=`DocumentAttributeFilename` only, mime=`application/octet-stream`) — **no sticker/photo/audio/album semantics at all**.

3. **The deployed worker predates the fixes.** The running `telegram-archiver-worker-1` image
   is 11h old (built from committed code), while `telegram_imported_media.py` and the serializer/media_count
   fixes are **uncommitted**. So even if the new canonical media service is correct, **the live
   application is not running it** → the real UI/worker still uses the old lossy media path.

4. **Source archive itself was lossy for the e2e runs** — store-check shows every fixture media
   message was written to the DB as `media_type=document, mime=application/octet-stream, size=326`
   with `original_filename=unnamed`. The photo/album/sticker fixture items were all collapsed to
   generic documents at export time. This is the primary reason media can never come back as a
   real photo/sticker: **the type information is gone before import even begins.**

## Conclusion

The claimed parity ("ACHIEVED") is not supported by real runtime evidence. The **true** state:
text/timestamp restoration works; **media type fidelity and grouped/photo+caption restoration
are not proven and in fact failed** in the last two live production runs. The new canonical media
service + serializer fixes exist only as uncommitted work and have never been deployed or
live-verified.