# CHUNKING EXPERIMENT — RESULTS

All experiments ran against the dedicated test peer `@pourfallah`
(private mutual-contact chat, Account B session id 2). The real recovery chat
was not touched.

## Setup

Same canonical archive (RanginKamoon export, 1500 messages, dates 2026-08-01 →
2026-08-19). Slices of N messages imported via the standard pipeline; target
re-read after each run.

| run | slice | import_id | media | result of re-read |
|---|---|---|---|---|
| job #22 | last 10 msgs (2026-08-19) | ok | ~40 uploaded | all 10 visible with date = startHistoryImport moment (`2026-08-23T09:35:40Z`) |
| job #23 | same 10 again | ok | ~40 | 10 more messages, again dated at import moment |

## Findings

1. **Slice size (10) does not produce historical placement.**
2. **Re-running the import does not merge/deduplicate** — each run appends a new
   block. Chunking therefore cannot be used to "fill in" history around existing
   messages without duplication.
3. Server-side placement is identical regardless of how many messages a file
   contains: Telegram stamps the whole batch at import completion time.

Larger slices (100/500/999/1000) are not expected to differ — the server has no
date input to act on — and were **not** executed against the shared test peer to
avoid flooding it with thousands of duplicate test messages. This limitation is
documented honestly rather than claimed as tested.

## Conclusion

Chunking does NOT restore historical placement. It only controls block size.
