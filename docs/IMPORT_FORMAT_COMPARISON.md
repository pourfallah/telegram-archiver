# IMPORT FORMAT COMPARISON

Compares our generated import file with the format Telegram's own importer
parses, based on the official article and real parser feedback.

## Reference formats accepted by `messages.checkHistoryImport`

Telegram documents no strict grammar; the parser recognizes common chat-export
line styles. Known-accepted (confirmed by docs examples + live parse):

| style | example | source |
|---|---|---|
| WhatsApp dot | `19.08.2026, 16:01 - Name: text` | WhatsApp .txt export |
| Telegram-style | `19.08.2026 16:01 - Name: text` | our current output |
| bracket media | `<attached: photo.jpg>` | docs / WhatsApp `[attached]` analog |

Live evidence: our file (style 2 + `<attached: …>`) parsed successfully —
`checkHistoryImport → {pm:true, group:false, title:'RanginKamoon'}` and
`initHistoryImport` accepted the full file.

## Side-by-side: ours vs a canonical WhatsApp export

```
OURS (import.txt)                       WHATSAPP EXPORT (_chat.txt)
19.08.2026 16:01 - A: hi                [19.08.2026, 16:01:52] A: hi
19.08.2026 12:08 - B: <attached: s.webm>[19.08.2026, 12:08:00] B: <attached: s.webm>
```

Differences:

1. **Date separator/brackets** — cosmetic; both parse.
2. **Seconds** — WhatsApp includes them; we truncate to minutes. The importer
   accepts both; seconds do not influence placement (server re-dates anyway).
3. **Media marker** — `<attached: X>` matches what the parser expects for
   associating later-uploaded media by filename (`uploadImportedMedia`'s
   `file_name` must equal the name in the marker).

## Multi-line messages

Newlines inside one message would break line-based parsing. Our serializer
flattens newlines to spaces (`_escape`). Alternative used by WhatsApp exports:
continuation lines without date prefix are treated as part of the previous
message. Both work; flattening is lossy in archive terms but exact text is kept
in the canonical archive. Logged as fidelity trade-off F-1.

## Conclusion

Our format is **not** the cause of the timestamp placement behavior — an
accepted, well-formed file still yields import-time server dates (see
TIMESTAMP_IMPORT_FORENSIC_AUDIT.md §M). Format changes cannot alter placement
because no field of the file maps to the server-side message date.
