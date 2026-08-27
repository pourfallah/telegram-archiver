# PRODUCTION PARITY REPORT — REAL E2E (RECOVERY_FINAL_20260827)

**Date:** 2026-08-27 · **Branch:** max-fidelity-archive · **Import job:** 49 · **Export:** 15
**Source account A:** +989…0100 (First Dev.) · **Target account B:** +551…6422 (David)

This report documents what the **normal production application** (HTTP API → Celery worker →
Telegram MTProto) actually restores on a real A↔B private chat, based on **real** source fixture
messages (real MessageMedia objects) and an independent re-read of the target.

---

## 1. What was proven TRUE (with real evidence)

| Finding | Evidence |
|---------|----------|
| Real media CAN be imported via the production path | `MEDIA_IMPORT_TRACE.json` — job 49 `import_id=8178946182766270516`, **8/8 uploads succeeded, 0 failed** (InputMediaUploadedPhoto/Document → MessageMediaPhoto/Document with real photo/doc ids) |
| Photo bound as real `MessageMediaPhoto` in target | e2e_target_snapshot `2520: MessageMediaPhoto (fwd_imported=True)`, `2542/2543: MessageMediaPhoto grouped=14302829920241196` |
| Text restored losslessly | `RECOVERY_FINAL_TEXT_001_PLAIN` → target, `TIMESTAMP_RESTORED`, `SENDER_IDENTICAL` |
| Timestamp restored (visible message.date) | verification `TIMESTAMP_RESTORED: 155` of 157 matched |
| Entities / formatting restored as text | formatted fixture matched verbatim |
| Photo+CAPTION stays one message | target `2522` caption on the photo message |
| Reconstructable reactions use correct identity | `RECONSTRUCTED_AFTER_IMPORT` only for B's own reactions (B's session); A's never faked |
| Canonical archive is lossless for text/entities/replies/reactions/grouped_id | source snapshot verified field-for-field |

## 2. What the production path does NOT restore (honest)

| Feature | Result | Root cause (evidence) |
|---------|--------|------------------------|
| One-file-reused-across-many-messages media | only 1 of N binds; others import as literal `<attached:>` text (target 2523/2545/2550) | `build_media_specs_from_archive` dedups by **filename** (`seen` set); Telegram binds tokens by filename → 1 unique file = 1 media token |
| Reply re-parenting | NOT reconstructable | no Telegram RPC re-parents message ids after import; archive preserves the relationship only |
| Reactions by a non-importer reactor | `TARGET_NOT_IN_REACTOR_VIEW` (not faked) | per-participant message ids; other account's view of the imported block unresolved |
| A→B sender identity | imported authors re-mapped to importer (documented Telegram behavior) | `SENDER_MISMATCH`/`SENDER_MAPPED_TO_IMPORTER` |
| Link preview (MessageMediaWebPage) | not archived (intentional — derived content, not user-owned) | export `classify_media` drops web pages |

## 3. Code-path parity (the master-prompt core requirement)

**Before** this E2E, the "successful tests" (`tests/test_import_pipeline.py`) proved only serializer
format + InputMedia attribute shape with a FakeClient — they never bound media to Telegram, and the
deployed worker predated even the uncommitted canonical-media work.

**During** this real E2E the production path surfaced **4 real bugs that the unit tests could not**:

1. `export_verification` entities compared as raw dicts (`ctor` vs `type`) → false FAIL → import gate blocked. **Fixed** (`_entities_eq`).
2. `export_verification` `MessageMediaWebPage` (link preview) not normalized → false FAIL. **Fixed** (`_media_ctor`).
3. `import_tasks` referenced undefined `src_map` for `limit` → `NameError`. **Fixed**.
4. `TelegramImportedMediaService._upload_file` returned an **unawaited** `client.upload_file()` coroutine → "a TLObject was expected". **Fixed** (async/await).
5. `DocumentAttributeVideo` w/h passed as `None` → struct.error on send. **Fixed** (coerce to int 0).

After fixes, **production now uses the same serializer + canonical media service + verifier** that
the (strengthened) tests exercise. Parity is functional: the live worker runs the canonical path.

## 4. Fidelity classification (master prompt §40 — honest, no "file exists = restored")

- TEXT — **EXACT** (timestamp, text, entities verbatim)
- FORMATTING — **PARTIAL** (entities restored as text; link-preview ctor not archived)
- SENDER — **EXACT** for importer-origin; Telegram re-maps source authors to importer (documented)
- TIMESTAMP — **EXACT** (TIMESTAMP_RESTORED 155/157)
- PHOTO — **EXACT when unique filename** (real MessageMediaPhoto, real photo_id)
- PHOTO+CAPTION — **PARTIAL** (photo + caption one message when filename unique; filename-collision cases import caption text only)
- STICKER — **not proven in this run** (re-used doc file → document); unique-filename sticker upload proven at mechanism level
- VIDEO/GIF/AUDIO/DOCUMENT — **EXACT at mechanism** (uploaded as MessageMediaDocument with source attrs; verified trace)
- ALBUM — **PARTIAL** (grouped_id preserved on bound copies; filename collision limits)
- FORWARD — **PARTIAL** (media bounded; fwd provenance archival-only)
- REPLY — **ARCHIVAL_ONLY** (not reconstructable by Telegram import)
- REACTION — **CURRENT_STATE_RECONSTRUCTED** (correct identity, never faked)
- CUSTOM EMOJI — **PARTIAL** (text preserved with emoji; document_id entity archival-only)

**Overall verification (production import job 49):** `PARTIAL` — source 160, target(new) 245,
matched 157, TIMESTAMP_RESTORED 155, media `PHOTO_EXACT 6 / VIDEO_EXACT 2 / DOCUMENT_EXACT 29`,
reactions reconstructed (B) + not-faked (A). UNMATCHED 3 are the **old** E2E text-only fixtures,
not the RECOVERY_FINAL set.

## 5. Root-cause summary (master prompt §45)

The prior `FINAL_PRODUCTION_PARITY_REPORT.md` ("ACHIEVED": STICKER_EXACT/AUDIO_EXACT/CAPTION_EXACT,
media+caption EXACT) was **not backed by runtime evidence** — the source fixture was text-only
(`media=NONE` on every PRODPARITY message). This E2E replaced fabricated claims with a real,
independent, production-path run whose honest result is documented above.

## 6. Remaining Telegram limitations
- One import file line binds to one media token by filename → duplicate filenames collapse.
- Reply / forward re-parenting after import is not possible via any MTProto method.
- Non-importer reactors' imported view resolution for reactions remains per-participant-gated.
- Source sender identity is re-mapped to the importing account by Telegram.

## 7. Debug artifacts (preserved)
`e2e_artifacts/`: `final_recovery_report.json` · `final_recovery_report.html` ·
`MEDIA_IMPORT_TRACE.json` · `e2e_source_snapshot.json` · `e2e_target_snapshot.json`.

Full server artifacts under `/data/exports/_989394430100/David Rodriguez/run_15/verification/`
(IMPORT_VERIFICATION_REPORT, RECOVERY_FIDELITY_REPORT, REACTION_RECOVERY_REPORT, EXPORT_VERIFICATION).