# Reaction Reconstruction Research

## Source-side (export) — complete

The canonical archive represents each reaction as a first-class object, never
flattened:

```
reactions: {
  reactions: [
    { reaction_type: "ReactionEmoji",     emoji: "👍",        count: 5, chosen: false },
    { reaction_type: "ReactionCustomEmoji", document_id: 123456, count: 1, chosen: true  },
    ...
  ]
}
```

Per the official reactions docs (https://core.telegram.org/api/reactions) the
archive keeps the reaction **type**, **emoji** or **custom-emoji document_id**,
**count**, and the **chosen** state. Reaction *voters / timestamps* are NOT
assumed to live in the Message object — if accurate voter lists are needed,
`messages.getMessageReactionsList` must be called per message (a separate
fetch with its own permissions/rate limits), and `messages.getMessagesReactions`
can augment per-message reaction info.

## Import-side — historical reaction restoration

Determined experimentally (see `docs/TIMELINE_MATERIALIZATION_EXPERIMENT.md` and
REACTION_FIDELITY_REPORT.html):

**The official history-import protocol does NOT carry reactions.** Imported
target messages show `reactions = null`. There is no parameter in
`initHistoryImport` / `startHistoryImport` for reactions.

Therefore every source reaction is classified:

- **ARCHIVAL_ONLY** for the historical reaction state (type / count / voters /
  custom-emoji id / timestamp). It lives completely in the archive.

## Post-import reconstruction — classification (NOT a silent over-claim)

Telegram exposes `messages.sendReaction`. It is possible in principle to re-apply
a reaction to an imported target message. However, doing so creates a **current
reaction event** on today's date/time under the importing account — it does NOT
recreate the original historical reaction timestamp, voter ordering, or which
users reacted historically (unless recast by whoever reacts today).

The fidelity classes are strict:

| Capability | Feasibility |
|---|---|
| Restore reaction count | PARTIAL (only via re-sending now) |
| Restore reaction type (emoji) | PARTIAL (sendReaction) |
| Restore custom-emoji reaction | PARTIAL (sendReaction with a custom emoji you may send) |
| Restore which user reacted | CURRENT_STATE_ONLY / NOT historical |
| Restore chosen state | PARTIAL (impersonates the current account) |
| Restore historical reaction timestamp | NOT POSSIBLE |
| Restore original reaction ordering | NOT POSSIBLE |

An optional **post-import reaction reconstruction** stage is therefore always
labeled one of `RESTORED_BY_IMPORT`, `RECONSTRUCTED_AFTER_IMPORT`,
`CURRENT_STATE_ONLY`, or `ARCHIVAL_ONLY`. It is never called "historical reaction
restoration."

## Honest reporting

REACTION_FIDELITY_REPORT.html lists, per source reaction: message id, type,
emoji / document_id, count, chosen, and the final status (currently
`ARCHIVAL_ONLY` for import; `RECONSTRUCTED_AFTER_IMPORT` if the optional
re-apply stage runs).