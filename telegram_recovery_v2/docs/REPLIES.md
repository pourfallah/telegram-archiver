# Replies

Replies are preserved structurally — **never inferred from chronology**.

`MessageReplyHeader` archive fields: `reply_to_msg_id`, `reply_to_top_id`,
`reply_to_peer_id`, `quote` (whether it is a quote reply), `quote_text`,
`quote_entities`, `quote_offset`.

- The canonical archive keeps these exactly as Telegram reported them.
- The verifier classifies a reply as `REPLY_EXACT` only when the child's
  `reply_to_msg_id` maps (source→target) to the same parent as the target's
  `reply_to_msg_id`. `REPLY_ARCHIVAL_ONLY` when the reply survived only in the
  archive/quote text but Telegram re-import did not restore a structured
  `reply_to`. `REPLY_PARTIAL` / `REPLY_FAILED` otherwise.

If the official import path cannot create the structured reply relationship on
the target, that fact is documented — reply restoration is never faked with
text.