# CANONICAL ARCHIVE AUDIT

Determines whether the canonical archive (`archive/`) is lossless — i.e. preserves every
MTProto-readable field of a source Telegram message.

## Source of truth chain

```
LIVE TELEGRAM MESSAGE  →  message_to_dict()  →  messages.jsonl →  build_canonical_archive() →  archive/messages/messages.ndjson
```

- `message_to_dict` in `backend/app/services/telegram_utils.py` is the canonical JSON shape (schema v2/v3).
- `build_canonical_archive` in `backend/app/services/canonical_archive.py` copies journal rows verbatim and downloads media.

## Field audit

| Telegram field | Stored? | Where | Format | Lossy? | Source fn |
|----------------|---------|-------|--------|--------|-----------|
| id | YES | messages.ndjson `id` | int | no | `message_to_dict` |
| date | YES | `date` | ISO 8601 (UTC) | no | `message_to_dict` |
| edit_date | YES | `edited` | ISO 8601 | no | `message_to_dict` |
| from_id / sender | YES | `sender.{id,name,username}` | dict | **PARTIAL** — full PeerProfile (avatar, etc.) not kept | `sender_info` |
| from_id raw | PARTIAL | `raw_message.from_id` | scrubbed VT dict | partial (scrub) | `to_raw_json` |
| message (text) | YES | `text` | str | no | `message_to_dict` |
| entities | YES | `entities[]` | `{type,offset,length,url,user_id,document_id}` | no (offsets UTF-16 preserved) | `serialize_entities` |
| reply_to | YES | `reply_to.{reply_to_msg_id,top_msg_id,quote,quote_entities,reply_to_peer_id}` | dict | **PARTIAL** (nested reply peer limited) | `serialize_reply` |
| forward / fwd_from | YES | `forwarded_from.{from_id,name,date,from_name,channel_post,post_author,saved_from_*}` | dict | **PARTIAL** (media attrs of forwarded item inherited from media) | `serialize_forward` |
| reactions | YES | `reactions.{reactions:[{reaction_type,count,emoji,document_id,chosen}], voters:[{peer_id,...}]}` | dict | no | `serialize_reactions` + `enrich_reaction_users` |
| grouped_id | YES | `grouped_id` | int/long | no (album relation kept) | `message_to_dict` |
| media type | YES | `media[].type` | str | **NO in DB for e2e** — see issue | `classify_media` |
| media ctor | PARTIAL | `media[].type` only; ctor not explicit | | **lossy** | `classify_media` |
| photo/document id | PARTIAL | only in `raw_message` (scrubbed) | | partial | |
| access_hash | PARTIAL | only in `sender`/`raw_message` | | partial | |
| mime_type | YES | `media[].mime_type` | str | no | `classify_media` |
| size_bytes | YES | `media[].size_bytes` | int | no | `classify_media` |
| filename | YES | `media[].original_filename` + `filename` | str | no | `classify_media` + `_media_entry` |
| width/height | YES | `media[].extra.{width,height}` | int | no (documents only) | `classify_media` (DocumentAttributeVideo/Sticker) |
| duration | YES | `media[].extra.duration` | float | no | `classify_media` |
| performer/title | YES | `media[].extra.{performer,title}` | str | no | `classify_media` |
| sticker alt / set | PARTIAL | `media[].extra.sticker_emoji`, `animated` | str/bool | set/alt not fully | `classify_media` |
| custom emoji | YES | entity `{type:custom_emoji, document_id}` | dict | no | `serialize_entities` |
| thumbnails | NO | not stored | | **lossy** | n/a |
| file_reference | NO (scrubbed) | `raw_message.<bytes>` | `<bytes>` | **lossy** | `to_raw_json` scrub |
| SHA256 | YES post-arch | `archive/checksums.json` | hex | no | `canonical_archive._sha256` |
| views / forwards / replies | YES | `views`, `forwards`, `replies_count` | int | no | `message_to_dict` |
| media file bytes | YES | `archive/media/<type>/<file>` | file | no | `media_downloader` + `canonical_archive` copy |

## Critical lossy finding (root cause)

**`media[].type` was recorded as `document` for every media item** in the real E2E exports
(`store-check` on export 14): `media_type=document, mime=application/octet-stream, size=326,
original_filename=unnamed`. Real photos / stickers / audio / albums were all collapsed to a
generic 326-byte `document_326.bin`.

This is caused by how the **fixture/test** uploaded the media (raw bytes with no filename / no
`DocumentAttribute*`) **and** by `classify_media` mapping to `document` when it cannot detect a
distinguishing attribute. Because `message_to_dict` uses `classify_media` from the message's
`DocumentAttribute*` list, an upload that lacks those attributes is archived as a plain document.
Once the type is `document` in the archive, import can never restore `STICKER_EXACT`/`PHOTO_EXACT`/
`AUDIO_EXACT` — it only ever re-uploads a generic document.

## Verdict

- Text, entities, reply headers, reactions (+voters), grouped_id, forward provenance, rich
  document metadata (duration/performer/title/width/height) **are** preserved losslessly.
- **Media type fidelity and photo/sticker/audio semantics are NOT guaranteed**: they depend on
  the archive's `media[].type`, which degrades to `document` when the source upload lacks
  `DocumentAttribute*` — exactly what happened in the last two real runs.
- IDs/access_hash/file_reference are only best-effort (scrubbed in `raw_message`).

=> The archive is NOT fully lossless for media today. Fixing the **export/classify_media**
detection (or preserving the source media ctor + attributes explicitly) is a prerequisite for
restoring real media types on import.