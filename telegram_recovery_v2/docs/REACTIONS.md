# Reactions

Reaction fidelity is about **WHO reacted, WITH WHAT, ON WHICH message** — not
the reaction timestamp (which may differ after recovery).

- Archive: `archive_reactions` fetches per-message reactor identity via
  `messages.getMessageReactionsList` and stores `{source_message_id: [
  {reactor_id, reaction{ReactionEmoji/ReactionCustomEmoji document_id}} ]}`.
  The source's count summary is also kept inline on each canonical record
  (`MessageReactions` → `results`).
- Reconstruct: `reconstruct_reactions` maps `source_message_id →
  target_message_id`, then for each archived reaction uses **the reactor's own
  session** — A reactions go out via A's client, B reactions via B's client,
  never impersonation — through `messages.sendReaction`. Custom emoji are kept
  as `ReactionCustomEmoji(reacted by ...)`.
- Verify: `verify_reactions` reads the target with
  `messages.getMessagesReactions` (bulk counts) and cross-checks against the
  archived (reaction, count) set.

Classification (verifier): `REACTION_EXACT` when every archived (reaction,
count) is present on the target; `REACTION_PARTIAL` when a strict subset;
`REACTION_RECONSTRUCTED` when re-applied per actor but target re-read is not
available; `ARCHIVAL_ONLY` when only the archive knows the reactions;
`REACTION_FAILED` otherwise. Reaction date is never part of the match.