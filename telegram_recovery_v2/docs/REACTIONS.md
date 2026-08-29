# Reactions — v2 engine

## What is archived

For every source message with reactions (read from A):

- reaction (emoji emoticon OR custom-emoji document_id)
- count, reaction_order
- WHO reacted (reactor label A/B + telegram user id), resolved live via
  `messages.getMessageReactionsList` and written to
  `archive/reactions_plan.json`.

Reaction dates are NOT archived — they are not needed for recovery.

## Reconstruction

After import and source->target mapping:

1. For each planned reaction, resolve target_message_id via the map.
2. Pick the reactor's OWN session (A or B). Never cross-account.
3. Because A and B see different message ids for the same message, translate
   the mapped target id (B's view) into the reactor's view using
   `target_message_pairs.json` when the reactor is A.
4. `messages.sendReaction(peer, msg_id, reaction)`.
   `MessageNotModifiedError` is treated as already-present (success).

## Verification

`messages.getMessagesReactions` on the target message (B's view). A reaction
is VERIFIED only when the expected emoticon/document_id is present on the
target object. Statuses:

- REACTION_EXACT — archived reaction present on target after send
- REACTION_RECONSTRUCTED — sent by engine and verified
- REACTION_PARTIAL — some reactions of a message verified, some not
- REACTION_ARCHIVAL_ONLY — archived but could not be sent
- REACTION_FAILED — send failed and not present
