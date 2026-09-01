# Old system audit

Read of the existing `backend/` + `frontend/` application before building the
new engine. Audit is the basis for what to reuse and what to avoid.

## What the old system does

- **Export** (`services/export_engine.py`, `telegram_utils.py`,
  `media_downloader.py`): Telethon → `messages.json` / HTML / sqlite +
  media, checkpointed, paced. The normalize step is `telegram_utils
  .message_to_dict(...)` (schema v1).
- **Migration** is a **WhatsApp converter** (`services/converter.py`): it
  turns an export into `_chat.txt` + `media/` + `manifest.json` for Telegram
  Desktop's manual *"Import from WhatsApp"*. It **never calls the Telegram
  history-import MTProto methods**. The "Import Assistant"
  (`import_assistant.py`) only validates the local package and prints
  instructions. `api/migrations.py` wires converter/test-builder/assistant to
  HTTP routes.

## Where it transforms data (and where it loses it)

`telegram_utils.message_to_dict` drops or flattens several fields that are
critical to faithful recovery:

| Field | Old behavior | Lost |
|---|---|---|
| `grouped_id` | not serialized | album grouping |
| reply quote (`quote_text`/`quote_entities`/`quote_offset`, `top_msg_id`) | only `reply_to_msg_id` kept | quote/reply richness |
| reaction **reactors** | only `emoji → count` tallies (`{❤: 3}`) | WHO reacted |
| custom emoji `document_id` | `MessageEntityCustomEmoji` not in entity map → falls through to raw class name, `document_id` dropped | custom emoji fidelity |
| sticker metadata (`alt`/`stickerset`) | only classified as `"sticker"` via attr type name | sticker identity |
| forward `imported` flag, `channel_post`, `post_author` | only `from_id`/name/date kept | forward provenance |
| waveform, performer/title, spoiler, ttl, etc. | not kept | deep media metadata |
| raw MTProto object | none | the ultimate recovery source |

The old WhatsApp converter then re-flattens still further: it turns
media + caption into **separate** `_chat.txt` lines (`<Attached: file>` and a
text line), breaking the "one message = media + caption" invariant that the new
engine treats as canonical.

## Why previous tests were inconsistent

- Old tests validated the **WhatsApp package build** and its JSON shape — never
  an actual Telegram import/reconstruction against a real target. "Success" was
  package-on-disk, so real Telegram behavior was never covered.
- Multiple separate layers (converter, import_assistant, test_builder,
  serializer) encode overlapping logic that can drift apart and produce
  divergent "correct" shapes.
- Reconstruction/re-import is essentially **absent** in the old code — the
  pipeline stops at producing a package the user manually imports. There was
  therefore nothing consistent to assert about target fidelity.

## Safe to reuse

- `session_manager.py` pattern: Telethon client factory + `StringSession`,
  one-asyncio-loop model, per-account serialization, encrypt-at-rest with
  Fernet. The v2 engine keeps the Espoused `RecoveryClient` with the same
  connect/session-string model (without the web/ORM coupling).
- `safe_filename` sanitizer and the `entity type` name map ideas.
- `sender_info` best-effort descriptor.

## Should NOT be reused

- `telegram_utils.message_to_dict` flattening serializer (data loss above).
- The WhatsApp-converter-as-import-path design.
- The manual instructions-only "import" path.
- Any tester that asserts package-build == success.

The v2 engine therefore builds a new canonical archive (one message = one
record, plus sanitized raw MTProto), a direct history-import `ImportEngine`
(the five official methods), per-field fidelity verifier, and real-Telegram E2E.
The old application is untouched.