# Maximum-Fidelity Archive & Reconstruction

Branch: `max-fidelity-archive`

This project is **maximum-fidelity archival + reconstruction** — not a Telegram
database rollback. The canonical archive is the source of truth and preserves
every readable field, even those Telegram's current import protocol cannot
restore. The importer classifies each field honestly.

## Canonical message shape (schema v2)

`archive/messages/messages.ndjson` (oldest-first) now carries, per message:

| Field | Source | Restorable by import? |
|---|---|---|
| `id` | Telegram message ID | target gets a NEW ID |
| `grouped_id` | album group | EXPERIMENTAL |
| `date` / `edited` | timestamps (UTC) | date YES; edit-state NO |
| `sender {id,name,username,phone}` | author | name via fwd header |
| `text` | message text | FULL |
| `entities[]` | entities incl. `custom_emoji.document_id`, UTF-16 offsets | ARCHIVAL_ONLY |
| `reply_to {msg_id,peer_id,top,quote,quote_entities}` | reply header + quote | ARCHIVAL_ONLY |
| `forwarded_from {from_id,name,date,from_name,channel_post,post_author,saved_from_*}` | forward provenance | ARCHIVAL_ONLY |
| `reactions {reactions:[{type,emoji|document_id,count,chosen}]}` | reaction totals + selected | ARCHIVAL_ONLY |
| `views`, `forwards`, `replies_count` | counters | ARCHIVAL_ONLY |
| `via_bot`, `post_author`, `pinned`, `noforwards`, `silent`, `mentioned`, `media_unread`, `post` | flags | ARCHIVAL_ONLY |
| `media[]` | rich media descriptors | photo/video/document/sticker → real object |
| `has_media_object` | real media attached? | verification signal |
| `raw_message` | scrubbed raw MTProto snapshot | ARCHIVAL_ONLY |

## Custom emoji (mandatory)

A custom emoji is archived as its real entity:
- `text` = fallback emoji (e.g. `👍`)
- `entities[].type = custom_emoji`, `.document_id = <id>`, UTF-16 `offset`/`length`

It is NEVER downgraded to a plain emoji-only string. The source document ID is
always preserved.

## Reactions (first-class, not flattened)

`reactions[]` keeps per-reaction: `reaction_type` (emoji / custom-emoji / paid),
`emoji`, `document_id`, `count`, and `chosen`. Total counts are preserved without
flattening `👍×5` into a single `👍`. Voters are not assumed to be in the message
object; if needed they are fetched via `messages.getMessageReactionsList`.

## Stickers (identity preserved)

Archived as real document metadata (document ID, access hash, MIME, size,
dimensions, sticker emoji, set, animated/video flags, attributes, thumbnail,
SHA-256), never just `sticker_123.webp`. The importer attaches the file as a
document; the original Telegram sticker *entity* (set/emoji) is not recreated.

## Reports (generated at `verification/` after each import)

- `RECOVERY_FIDELITY_REPORT.html` — per-message source vs target + fidelity
  scorecards + capability matrix (SOURCE / IMPORT FORMAT / SERVER / TARGET / STATUS).
- `REACTION_FIDELITY_REPORT.html` — reaction-by-reaction preserved/archival state.
- `IMPORT_VERIFICATION_REPORT.{json,html}` — match counts, per-message rows.

## Honest classification

- Text, date, photo, video, animation, document → **FULL / VERIFIED**
- Caption → **PARTIAL** (separate adjacent text message)
- Sticker identity → **PARTIAL** (document, no set/emoji entity)
- Entities, reactions, replies, forwards, custom emoji, albums, service messages → **ARCHIVAL_ONLY**
- Original source message ID vs imported target message ID are distinct concepts.

Nothing that Telegram cannot restore is ever silently dropped or faked — it
stays in the archive.