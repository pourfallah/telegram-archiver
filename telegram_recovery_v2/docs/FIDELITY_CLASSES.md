# Fidelity classes

Labels used in every per-field classification and the final report. The ONLY
authoritative validation is the actual Telegram target message object read
after the recovery operation.

| Label | Meaning |
|---|---|
| `EXACT` | target object matches the source for this field. |
| `RECONSTRUCTED` | reproduced deliberately after import (e.g. reactions re-sent per actor), not carried by the import itself. |
| `PARTIAL` | partially preserved (e.g. text matches loosely, timestamp shifted, reply parent wrong). |
| `ARCHIVAL_ONLY` | preserved in the archive but NOT restored on the target (e.g. reply survived only in archived quote). |
| `FAILED` | expected but absent/wrong on the target. |
| `NONE` / `NOT_RESTORED` / `SENDER_MISMATCH` … | per-field specifics (see below). |

Specific sub-classifications:

- **SENDER**: `SENDER_EXACT` (same peer id) · `SENDER_METADATA_ONLY`
  (`fwd_from.from_name` matches a display name) · `SENDER_MISMATCH`.
- **TIMESTAMP**: `EXACT` (<60s) · `PARTIAL` · `IMPORTED_METADATA_ONLY`
  (only `fwd_from.date`/import header) · `NOT_RESTORED`. `fwd_from.date` is
  never called the restored visible message date.
- **CAPTION**: `CAPTION_ATTACHED` (media + text in the SAME target record) ·
  `CAPTION_SEPARATE` (split into a separate text message) · `CAPTION_LOST`.
  Only `CAPTION_ATTACHED` is exact.
- **MEDIA**: `EXACT` (`MessageMediaPhoto` / attributes match the requested
  type) · `DOCUMENT_ONLY` (a generic document/webp where a photo/sticker/
  video/etc. was expected).
- **STICKER**: `EXACT` requires `DocumentAttributeSticker`; a WEBP document is
  `DOCUMENT_ONLY`.
- **REPLY**: `REPLY_EXACT` (mapped parent == target `reply_to_msg_id`) ·
  `REPLY_PARTIAL` · `REPLY_ARCHIVAL_ONLY` · `REPLY_FAILED`.
- **GROUP**: `GROUP_EXACT` (`grouped_id` preserved) · `GROUP_PARTIAL` ·
  `GROUP_FLATTENED` (album flattened) · `GROUP_FAILED`.
- **REACTION**: `REACTION_EXACT` · `REACTION_RECONSTRUCTED` ·
  `REACTION_PARTIAL` · `REACTION_ARCHIVAL_ONLY` · `REACTION_FAILED`.
  Reaction date may differ; reactor identity may not.