# Maximum-Fidelity Capability Matrix

Branch: `max-fidelity-archive` — reflects ARCHIVE SCHEMA v3.

Columns:
- **SOURCE AVAILABLE** — does Account A expose the field through MTProto?
- **IMPORT SUPPORT** — does Telegram's history-import protocol carry it?
- **POST-IMPORT RECONSTRUCTION** — can Phase B legitimately rebuild it?
- **TARGET VERIFIED** — has it been confirmed by reading the actual target object?
- **STATUS** — honest overall classification.

| Feature | SOURCE AVAILABLE | IMPORT SUPPORT | POST-IMPORT RECONSTRUCTION | TARGET VERIFIED | STATUS |
|---|---|---|---|---|---|
| Text | YES | YES | — | YES | FULL |
| Formatting (entities) | YES | NO | PARTIAL (sendMessage can re-format text only for *new* messages; imported messages can't be re-typed without creating duplicates) | NO | ARCHIVAL_ONLY |
| Entities (UTF-16 offsets) | YES | NO | NO | NO | ARCHIVAL_ONLY |
| Custom Emoji | YES (document_id) | NO | NO (cannot re-attach entity to imported message) | NO | ARCHIVAL_ONLY (fallback text preserved) |
| Timestamp (fwd_from.date metadata) | YES | YES | — | YES | IMPORTED_METADATA_ONLY |
| Timeline position (message.date) | YES | NO | NO (no protocol method) | YES (proven unchanged at 600s) | NOT_RESTORABLE |
| Sender identity | YES | NO (re-mapped to importing account; name in fwd_from.from_name) | NO (server-side identity) | YES | SENDER_METADATA_ONLY |
| Photo | YES | YES | — | YES | PHOTO_EXACT |
| Video | YES | YES | — | YES | VIDEO_EXACT |
| GIF / animation | YES | YES | — | YES | ANIMATION_EXACT |
| Document | YES | YES | — | YES | DOCUMENT_EXACT |
| Voice | YES | YES (as document w/ audio attr) | — | PARTIAL | VOICE_EXACT/PARTIAL |
| Audio | YES | YES | — | PARTIAL | AUDIO_EXACT/PARTIAL |
| Sticker (file) | YES | YES (as document) | NO | YES | STICKER_DOCUMENT_ONLY |
| Sticker set / emoji identity | YES | NO | NO | NO | ARCHIVAL_ONLY |
| Caption | YES | PARTIAL | NO (separate adjacent message) | YES | CAPTION_SEPARATE |
| Reply | YES | NO | NO (no re-parenting RPC) | NO | ARCHIVAL_ONLY |
| Forward provenance | YES | NO (import fwd_from is import metadata, distinct) | NO | NO | ARCHIVAL_ONLY |
| Album (grouped) | YES | UNKNOWN | NO | NO | EXPERIMENTAL |
| Reaction (counts/types) | YES | NO | PARTIAL (sendReaction by the reactor's own session) | PARTIAL | RECONSTRUCTED_AFTER_IMPORT (if session available) |
| Reaction identity (who) | YES (voter lists where allowed) | NO | SESSION-DEPENDENT (strict reactor-identity rule) | NO | REACTOR_SESSION_REQUIRED otherwise |
| Reaction custom emoji | YES (document_id) | NO | PARTIAL (sendReaction w/ ReactionCustomEmoji) | NO | RECONSTRUCTED / SESSION_REQUIRED |
| Message flags | YES | PARTIAL | NO | NO | ARCHIVAL_ONLY |
| Service messages | PARTIAL | NO | NO | NO | ARCHIVAL_ONLY |

## Notes

1. Imported messages appear in the shared A↔B cloud conversation — normal
   Telegram private-chat visibility semantics apply; no client-side cache
   tricks are used.
2. Reaction timestamps are intentionally NOT preserved (per product requirement:
   WHO/WHAT/WHERE matters, date does not). sendReaction creates a current event.
3. The canonical archive (schema v3) retains every field above regardless of
   import/reconstruction capability — nothing is downgraded or discarded.