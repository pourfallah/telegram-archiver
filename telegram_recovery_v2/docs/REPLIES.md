# Replies — v2 engine

## Archiving

Reply headers (`messageReplyHeader`) are archived verbatim:
reply_to_msg_id, top_msg_id, quote_text, quote_entities. Replies are NEVER
inferred from chronology.

## Import reality

The official history-import file format (WhatsApp export syntax) has NO
reply syntax. Therefore an imported message cannot carry its reply
relationship from the import itself.

## Post-import reconstruction (legitimate option)

A reply link CAN be created after import by: deleting the imported child
(`messages.deleteMessages`, revoke=False, from B's session) and re-sending
its content with `reply_to=target_parent_id`. Costs: the re-sent message has
a NEW id and a NEW (now) timestamp — so reply-true but identity/metadata
lost for that child.

## Classification

- REPLY_EXACT — target child's reply_to points at mapped target parent
- REPLY_PARTIAL — reply exists but parent mapping uncertain
- REPLY_ARCHIVAL_ONLY — reply preserved in archive, not restorable
- REPLY_FAILED — expected reply but target child lost/failed

The default v2 flow archives replies and reports REPLY_ARCHIVAL_ONLY unless
the optional reconstruct step is enabled; it never fakes replies with text.
