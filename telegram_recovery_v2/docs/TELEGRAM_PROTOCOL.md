# Telegram Protocol Notes (v2 engine)

Authority: https://core.telegram.org/schema and the method pages listed in
the project brief. Everything below was also checked against the live API.

## Official history import flow

```
messages.checkHistoryImport(import_head)      # validate file format (first ~100 lines)
messages.checkHistoryImportPeer(peer)         # MUST be called by the TARGET account (B)
messages.initHistoryImport(peer, file, media_count) -> import_id
messages.uploadImportedMedia(peer, import_id, file_name, media)   # once per media
messages.startHistoryImport(peer, import_id)  # begins server-side processing
```

- The import file format is the WhatsApp export syntax the server parser
  accepts: `[DD/MM/YYYY, HH:MM:SS] Name: text`. Live-verified: ISO timestamps
  are rejected with `IMPORT_FORMAT_TIME_INVALID`; DD/MM/YYYY, HH:MM:SS passes.
- Media binds ONLY to a bare `<attached: FILENAME>` line, matched by exact
  filename. `media_count` passed to initHistoryImport must equal the number
  of `<attached:>` lines.
- `imported` messages arrive at B with a forward header
  (`fwd_from.imported = true`); the visible sender is the importing account.
- startHistoryImport returns immediately; the server materializes messages
  over the following minutes. Only re-reads of the target decide success.

## Resumability / no duplicate imports

`import_state.json` persists `import_id`, uploaded attach names, and the
`started` flag after each RPC. A crashed run resumes: init is skipped if
import_id exists, already-uploaded media are skipped, start is idempotent.

## B-side clear

`messages.deleteHistory(peer, max_id=0, just_clear=True, revoke=False)` —
clears B's view only; A retains the full conversation. `revoke=True` is
never used by this engine.

## Reactions

- `messages.getMessageReactionsList` (from A, on the source) identifies WHO
  reacted; `chosen` flags alone are insufficient.
- Reconstruction: `messages.sendReaction` from EACH reactor's own session.
  Never impersonate. `MessageNotModifiedError` = reaction already present.
- A and B see DIFFERENT message ids for the same physical message. Mapping
  ids live in B's view; when A reacts, the id must be translated (pair
  table) or resolved from A's own view.
- Verification: `messages.getMessagesReactions` on the target.

## Message ids across accounts

For a 1:1 chat, message id spaces are per-account. All code that reacts or
verifies must know which account's view an id belongs to.
