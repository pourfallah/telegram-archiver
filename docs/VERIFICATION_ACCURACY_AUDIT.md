# Verification Accuracy Audit

Branch: `max-fidelity-archive`

## Why the old verifier was not a recovery verifier

The previous engine matched a source message to a target message **by normalized
text alone** (a multiset). Across a real 7-source / 21-target test it reported:

```
SOURCE_COVERED_EXTRA_IN_TARGET
Matched: 7/7
```

…even though:

- the target chat contained 14 unrelated/preexisting messages,
- imported senders were re-mapped by Telegram (source `7768075024` → target `165649921`),
- and the "7/7 matched" gave no information about whether timestamps, media
  objects, stickers, reactions or replies were actually faithful.

**Text-match coupled with 21 target messages is content-presence, not recovery.**
This audit replaces that behavior.

## Rules the new verifier enforces

1. **Only NEW messages are validated.** Before `startHistoryImport` the worker
   snapshots the target peer's IDs (`target_snapshot_before.json`). After
   materialization it reads again and computes the delta. Only `after − before`
   messages are compared against the source. Pre-existing target content is
   never counted as success (see `target_snapshot_before.json` + `is_new`).

2. **Multi-field identity, never text-only.** Each message is fingerprinted as
   `SHA256(timestamp | text | media-descriptors | grouped_id)` and matched by
   that key first (`matched_exact`). A same-text fallback exists and is flagged
   separately (`matched_text_only`) — it is never labeled exact recovery.

3. **Sender attribution is honest.** Telegram's history import re-maps every
   imported message's author to the importing account. The report distinguishes:
   - `SENDER_IDENTICAL` — author matched the source (rare),
   - `SENDER_MAPPED_TO_IMPORTER` — expected re-mapping (documented behavior),
   - `SENDER_MISMATCH` — something else authored the target message (flagged).

4. **`message.date` and `fwd_from.date` are never conflated.**
   - `TIMESTAMP_RESTORED` requires `message.date == source date`
   - `IMPORTED_METADATA_ONLY` = `fwd_from.date == source` but `message.date != source`
   - `NOT_RESTORED` = neither.

5. **Media/sticker classification is constructor-driven**, not
   "has_media_object". The worker records the target `MessageMedia` constructor,
   its `DocumentAttribute*` list and MIME; the verifier then labels
   `PHOTO_EXACT / VIDEO_EXACT / ANIMATION_EXACT / DOCUMENT_EXACT / VOICE_EXACT /
   AUDIO_EXACT / STICKER_EXACT / STICKER_DOCUMENT_ONLY / MEDIA_ABSENT`.

6. **Overall is conservative.** `FULL_RECOVERY` only if every source message is
   matched exactly AND no sender mismatch AND no `NOT_RESTORED` timestamp.
   Otherwise the report is honest about what is partial.

## Report fields (IMPORT_VERIFICATION_REPORT.json)

- `counts.matched_exact` / `matched_text_only` — strong vs weak matches
- `details.message_map[]` — per source→target: `{source_id, target_id, source_text,
  target_text, match, reason, sender, timestamp, media}`
- `details.sender_status` / `timestamp_status` — per-category tallies
- `details.wrong_sender` / `wrong_timestamp` — explicit failure lists
- `details.media_classification[]` — per-item honest constructor class
- `timestamp_analysis` — placed rows + the message.date vs fwd_from.date note

## Acceptance

A "recovery" claim now requires exact-fingerprint matches with verified
timestamps/media; anything less is reported as partial or honest-limitation, and
the source archive remains the complete source of truth regardless.