# Old System Audit

This audit covers the existing `backend/` application in this repository,
written before `telegram_recovery_v2/` was built. Its purpose is to decide
what is safe to reuse and what must not be reused, per the project reset
brief.

## What the old system does

The old system is a FastAPI + Celery + Postgres web application:

- `export_engine.py` reads chat history from a source account via Telethon
  (`messages.getHistory` through Telethon's high-level iterator), converts
  each message to a JSON "canonical" record, downloads media, and writes
  export directories.
- `whatsapp_format.py` / `import_serializer.py` convert the export into a
  WhatsApp line-format text file (`[DD/MM/YYYY, HH:MM:SS] - Name: text` with
  `<attached: FILE>` markers) because Telegram's official history-import API
  (`messages.initHistoryImport`) only accepts a foreign-app text file plus
  media tokens.
- `telegram_import.py` + worker `import_worker.py` run the official import:
  `checkHistoryImportPeer` (from the target account) → `checkHistoryImport` →
  `initHistoryImport` → `uploadImportedMedia` per file → `startHistoryImport`.
- `reconstruction.py` runs post-import reconstruction: reaction
  reconstruction via per-reactor sessions (`messages.sendReaction`), reply
  reconstruction via delete+resend.
- `import_verification.py` re-reads the target chat and compares it with the
  source export, producing fidelity reports.

## Where it transforms data (and what is lost)

1. **Message flattening** — the serializer renders each Telegram Message to
   one or more WhatsApp text lines. A Message that is media + caption stays
   together in the *old* serializer, but the text line format cannot carry
   entities, so formatting (bold/italic/spoiler/custom emoji) is lost on
   import; it survives only in the archive.
2. **Captions** — live-proven 2026-08-28: a caption-continuation line after
   `<attached: X>` makes Telegram import the whole block as literal text and
   the media does NOT bind. The workaround emits the caption as a separate
   message line with a +1s timestamp. That means captions are never attached
   to the same target Message; they always import as a separate message.
3. **Timestamps** — same-day timestamps restore exactly; older dates keep
   import-time on the target Message and only `fwd_from.date` carries the
   original date. The old system exposed raw `fwd_from.date` as "restored"
   in some reports — misleading.
4. **Replies** — WhatsApp format has no reply syntax. The old system
   reconstructs replies by deleting the imported child and re-sending with
   `reply_to` (revoke=False), which creates a NEW message with a NEW date —
   the reply link is real but the child message identity is not preserved.
5. **Reactions** — reconstructed post-import by the correct reactor session
   (never cross-account). Date is not preserved (not needed).
6. **Stickers** — live-proven: `.tgs` animated stickers with
   `DocumentAttributeSticker` + `application/x-tgsticker` cause the target
   message to materialize EMPTY. The old code now imports `.tgs` as plain
   documents (DOCUMENT_ONLY). Static webp stickers import with the sticker
   attribute.
7. **Albums** — the text-file import format has no grouping concept; each
   album item imports as a separate message. `grouped_id` cannot survive the
   official import path. Never reconstructed from timestamps.
8. **Sender identity** — imported messages are sent by the importing account
   (B), with an imported forward header. Sender fidelity is metadata-only by
   protocol design.

## Why previous tests were inconsistent

- Several verification passes trusted `uploadImportedMedia`'s return token
  (`MessageMediaEmpty` for everything) as diagnostic — it is not; the only
  truth is the target Message after materialization (minutes later).
- Media binding depends on exact filename matching between the `<attached:>`
  line and uploaded tokens; duplicate filenames silently broke binding until
  unique attach names were introduced.
- Timestamp behavior differed by date (same-day vs old), so single-shot test
  fixtures produced contradictory conclusions.
- Earlier verification mixed `fwd_from.date` into "timestamp restored"
  results.
- Multiple serializer variants (test vs production paths) drifted apart.

## Safe to reuse

- Session storage model (Fernet-encrypted session strings) and the live
  authenticated sessions for accounts A and B (we extract session strings
  once; the v2 engine reads them from disk).
- Hard-won live-proven facts (documented in the team skill):
  - `uploadImportedMedia` returning `MessageMediaEmpty` is not diagnostic;
  - media binds only on bare `<attached: FILE>` lines;
  - one uploaded token per exact filename;
  - `.tgs` sticker attribute kills the media;
  - same-day timestamps restore exactly, old dates do not.
- The official import call sequence itself (it is the protocol; v2
  re-implements it cleanly).

## NOT to reuse

- The WhatsApp text-file serialization layer as the *primary* archive: v2's
  archive is canonical MTProto JSON; the text file is generated only as the
  import payload the Telegram parser requires.
- Verification code that trusted upload tokens or `fwd_from.date`.
- Positional/timestamp-based message matching fallbacks.
- The multiple divergent importer/serializer code paths (test vs prod).
- Anything that would couple the new engine to FastAPI/Celery/Postgres.

## V2 design consequence

`telegram_recovery_v2` keeps ONE engine (`TelegramRecoveryEngine`) used by
tests, CLI, and (later) the web app. Archive = canonical per-message JSON +
raw MTProto snapshot + media bytes with SHA256. Import = official MTProto
import API, minimal text payload. Verification = actual target Message
objects only.
