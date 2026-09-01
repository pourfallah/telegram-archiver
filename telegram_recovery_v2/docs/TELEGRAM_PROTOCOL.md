# Telegram protocol reality (the authority)

All operations are official MTProto methods. This engine does **not** promise
restored original Telegram message IDs or a server-side database restore —
Telegram's history import is documented as importing history from a foreign
chat app, and imported messages carry an *imported forward header*. The goal is
**maximum possible fidelity** using legitimate capabilities.

## Read (SOURCE A) — `messages.getHistory`

Paged `getHistory` streaming (newest → oldest), paced. Preserves the full
source `Message` (`id, peer_id, date, edit_date, from_id, message, entities,
media, reply_to (MessageReplyHeader), fwd_from (MessageFwdHeader), grouped_id,
reactions, views, forwards, flags`).

## Clear (TARGET B only, test) — `messages.deleteHistory`

Always `just_clear=true, revoke=false`. `revoke` is **never** set to true, so
A's copy is untouched and B's copy is cleared per the test fixture.

## Import — the five official operations

Per [core.telegram.org/api/import](https://core.telegram.org/api/import):

1. `messages.checkHistoryImport(import_head)` — determine if the file is importable.
2. `messages.checkHistoryImportPeer(peer)` — is this peer allowed to receive?
3. `messages.initHistoryImport(peer, file, media_count)` — start; returns the `import_id`.
4. `messages.uploadImportedMedia(peer, import_id, file_name, media)` — per media file.
5. `messages.startHistoryImport(peer, import_id)` — commit.

Import package = `_chat.txt` (the only file format `checkHistoryImport` accepts)
+ referenced media. It is generated **directly from the canonical archive** —
no WhatsApp converter in the path, no extra serialization layers.

`import_id` is persisted; a crash never re-inits (rule #54).

> ⚠️ Exact response semantics and the right `InputMedia` inputs are
> version/runtime dependent. That is why they are exercised only by the live
> E2E ([E2E_TEST.md](E2E_TEST.md)) — never assumed from docs alone.

## Calls made (per module)

| Method | Where |
|---|---|
| `messages.getHistory` | source_reader / engine snapshot |
| `messages.getMessagesReactions` | reactions.verify |
| `messages.getMessageReactionsList` | reactions.archive |
| `messages.sendReaction` | reactions.reconstruct (per actor) |
| `messages.deleteHistory` | engine.clear_target |
| `messages.checkHistoryImport(_Peer)` | importer |
| `messages.initHistoryImport` | importer |
| `messages.uploadImportedMedia` | importer |
| `messages.startHistoryImport` | importer |

## Building blocks used

Exact Telethon constructors (verified against the installed TL schema):
`Message`, `MessageReplyHeader` (`reply_to_msg_id, reply_to_top_id,
reply_to_peer_id, quote, quote_text, quote_entities, quote_offset`),
`MessageFwdHeader` (`date, imported, from_id, from_name, channel_post,
post_author, saved_from_*`), `DocumentAttributeSticker/Audio/Video/Animated/
Filename`, `MessageEntityCustomEmoji`, reactions (`ReactionEmoji,
ReactionCustomEmoji, ReactionPaid`), `MessagePeerReaction`.