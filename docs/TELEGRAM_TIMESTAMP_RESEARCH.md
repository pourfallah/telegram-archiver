# TELEGRAM TIMESTAMP RESEARCH — OFFICIAL SOURCES

All findings below are from primary sources only. URLs used:

## Official documentation

1. **Import API article** — <https://core.telegram.org/api/import>
   - `messages.checkHistoryImport(import_head)` — "up to 100 lines of the chat export file, starting from the beginning of the file"; returns `messages.historyImportParsed` with `pm`, `group`, `title`.
   - `messages.checkHistoryImportPeer(peer)` — eligibility; returns `confirm_text`. "Typically, history imports are allowed for private chats with a mutual contact or supergroups with change_info administrator rights."
   - `messages.initHistoryImport(peer, file, media_count)` — returns `messages.historyImport#1662af0b id:long`.
   - `messages.uploadImportedMedia(peer, import_id, file_name, media) → MessageMedia`.
   - `messages.startHistoryImport(peer, import_id) → Bool` — "importing all messages into the chat".
   - Key sentence: *"Imported messages will show in the chat history as messages containing a fwd_from messageFwdHeader constructor with the imported flag, and should be appropriately marked in the UI as messages imported from a foreign chat app."*
   - **The article nowhere documents any parameter or file syntax that controls the resulting server-side message date.** The only date-bearing constructor mentioned is the forwarded header.

2. **Schema (layer as bundled in current tdesktop dev)** —
   <https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/scheme/api.tl>
   (mirrors <https://core.telegram.org/schema>):
   ```
   messageFwdHeader#4e4df4bb flags:# imported:flags.7?true ... date:int ...
   messages.checkHistoryImport#43fe19f3 import_head:string = messages.HistoryImportParsed;
   messages.initHistoryImport#34090c3b peer:InputPeer file:InputFile media_count:int = messages.HistoryImport;
   ```
   The `message.date:int` field is a plain unix timestamp set by the server when a
   message is created. There is no client-supplied date field on any import method.

3. **Layers** — <https://core.telegram.org/api/layers> — no layer adds any
   historical-date parameter to the import methods.

## Telegram Desktop source (dev branch, commit `49a7f3a87363cdda122a9308df697f14ebee667f`, 2026-08-23)

4. `Telegram/SourceFiles/history/history_item_helpers.cpp:820`
   ```cpp
   QDateTime ItemDateTime(not_null<const HistoryItem*> item) {
       return base::unixtime::parse(item->date());
   }
   ```
   → The bubble/visible timestamp comes from the **server message date**.

5. `Telegram/SourceFiles/history/view/history_view_element.cpp:703-723`
   (`DateTooltipText`) — for forwarded+imported items the tooltip shows:
   - the server `dateTime()` first,
   - then `lng_forwarded_date` = `forwarded->originalDate` (the historical date),
   - prefixed by `lng_forwarded_imported`.

6. `Telegram/Resources/langs/lang.strings:3127`
   `"lng_forwarded_imported" = "This message was imported from another app. It may not be real.";`

7. `Telegram/SourceFiles/history/view/history_view_bottom_info.cpp:519-520, 739, 756-760`
   — the info line appends the literal word *“imported”* next to the server date;
   the original date is shown only via `Flag::ForwardedDate` in saved-sublist /
   self-chat contexts, not as the timeline position.

8. `Telegram/SourceFiles/history/history_item.cpp:270,303,2253,3874`
   — `CreateConfig.fillForwardedInfo` maps `fwd_from.is_imported()` to
   `config.imported`; imported forwards never skip notifications and are always
   shown “from sender”.

**Conclusion from source:** Telegram Desktop itself performs no date rewriting,
has no hidden historical-date handling, and displays imported messages at their
server-assigned (import-time) date, with the original date preserved only as
forward-header metadata. Since the reference client behaves this way, the
placement limitation is server-side, not client-side.

## Search performed

- GitHub code search for `checkHistoryImport` / `InitHistoryImportRequest` across
  `telegramdesktop/tdesktop`: the only hits are the scheme definition; the
  interactive WhatsApp-import UI is not present in current dev (it was removed/
  moved), so no alternative client-side date logic exists to copy.
- No official API exists to edit a message's `date` after creation
  (`messages.editMessage` has no date parameter).
