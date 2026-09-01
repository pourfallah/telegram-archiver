# Limitations & capability matrix

This table is kept honest: "VERIFIED ON TARGET" is only true after the live E2E
reads real Telegram target objects. Until then every cell is `PENDING` or
`PLANNED` — no claims are made from hermetic (mocked) tests alone.

| Capability | Source available | Import possible | Post-import reconstruction | Verified on target | Status |
|---|---|---|---|---|---|
| Text | yes | yes (`_chat.txt`) | n/a | pending | hermetic-tested |
| Formatted text (entities, UTF-16 offsets) | yes (raw entities) | depends on importer | n/a | pending | hermetic-tested |
| Emoji / custom emoji | yes (`MessageEntityCustomEmoji.document_id`) | depends | n/a | pending | custom-emoji needs a real doc id |
| Photo | yes (all PhotoSize) | yes (`uploadImportedMedia`) | n/a | pending | |
| Photo + caption (one record) | yes | yes | n/a | pending | mandatory test (`RECOVERY_V2_PHOTO_CAPTION`) |
| Video | yes | yes | n/a | pending | fixture is minimal container |
| GIF / animation | yes (attributes) | yes | n/a | pending | |
| Audio | yes (title/performer/duration) | yes | n/a | pending | |
| Voice | yes (waveform) | yes | n/a | pending | fixture is minimal container |
| Document | yes | yes | n/a | pending | |
| Sticker | yes (`DocumentAttributeSticker`) | yes? | n/a | pending | WEBP uploads are **documents**, not stickers — needs a real sticker test |
| Reply + quote | yes (full header) | **likely not** via import | **no** | pending | see REPLIES.md |
| Forward provenance | yes (`fwd_from`) | partial (imported header) | no | pending | |
| Reactions (who/what/which) | yes (`getMessageReactionsList`) | n/a (import drops) | **yes** (per-actor `sendReaction`) | pending | date may differ |
| Album (`grouped_id`) | yes (real grouped_id) | ymmv | no | pending | verifier flags `GROUP_FLATTENED` if lost |
| Message order | yes | yes (sequence) | n/a | pending | |

## Hard platform limits (documented, never worked around)

- Imported messages carry an **imported forward header** — they are not a
  server-side restore of the original message IDs.
- Telegram's import path (`checkHistoryImport` / `initHistoryImport`) only
  accepts the chat-export text format.
- Replies, edits, reaction dates, forward provenance and original
  grouped/IDs are typically **not** restored by the import itself.
- `messages.deleteHistory` `revoke=true` is never used (would delete A's side).

## Terminology

We never say "full restore" unless target verification proves it. Correct
labels: `EXACT RESTORED`, `RECONSTRUCTED`, `PARTIAL`, `ARCHIVAL_ONLY` (see
[FIDELITY_CLASSES.md](FIDELITY_CLASSES.md)).