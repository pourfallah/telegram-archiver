# RECOVERY FIDELITY — WHAT IS AND IS NOT RESTORED

Definitions (three distinct timestamps per message):

| concept | where it lives | restored by import? |
|---|---|---|
| **A. Original source timestamp** | canonical archive (`messages.ndjson`) | preserved offline, always |
| **B. Imported metadata timestamp** (`fwd_from.date`, `imported=true`) | Telegram server, per imported message | **YES** — verified live |
| **C. Visible timeline timestamp** (`message.date`) | Telegram server | **NO** — set to import moment by Telegram; no API input can change it |

## Verified against the real target chat

| property | status |
|---|---|
| text content | ✅ exact (7/10 matched in job #23 report; 3 unmatched were empty-text media-only lines) |
| sender names | ✅ via `fwd_from.from_name` |
| order within block | ✅ follows file order |
| historical date as metadata (B) | ✅ |
| historical date visible on bubble (C) | ❌ Telegram server-side limitation |
| original message IDs | ❌ new server IDs assigned |
| replies / reactions / edits / grouped-albums / sticker identity | ❌ not representable through the foreign-history import protocol; preserved in canonical archive only |

## Why C cannot be fixed

- No import method accepts a date parameter (schema: `initHistoryImport(peer, file, media_count)`).
- `messages.editMessage` cannot change dates of existing messages.
- The reference client (Telegram Desktop) shows the same behavior and labels
  these messages "This message was imported from another app. It may not be real."
- Any other route (sendMessage with fake dates, local DB edits) would be
  fabrication and is explicitly out of scope.

## Maximum-fidelity recovery = A + B

The system therefore guarantees:

1. Full-fidelity offline archive (canonical archive, all fields).
2. On-Telegram recovery with correct text, senders, ordering, media, and the
   original date embedded in each message's forwarded-header metadata.
3. Honest reporting that the visible timeline position is Telegram-assigned.
