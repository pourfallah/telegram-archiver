# CURRENT IMPLEMENTATION AUDIT

Branch: `feature/real-telegram-import`
Date: 2026-08-19
Scope: Ground-truth review of the existing repository before any behavior change.

This audit is based on reading the current `backend/app`, `backend/tests`, `frontend/src`,
`docs/`, `README.md` and `CHANGELOG.md`. It records what the software **actually does** today,
separately from what its documentation says or a reviewer might assume. It is the Phase-1
deliverable for the "Real Telegram import / recovery-style migration" redesign.

---

## 1. Current architecture

Containerized self-hosted app (Docker Compose): `nginx` (edge, port 80) -> `frontend`
(React + Vite, static SPA) and `backend` (FastAPI + uvicorn) -> `postgres` + `redis`; a
`worker` (Celery) executes long-running jobs. Shared `exports/` volume.

Data model (`app/models/`): `UserAccount` (dashboard auth), `TelegramSession` (one per
Telegram account, Fernet-encrypted session + api_hash), `ChatExport` (export job + crash
checkpoint state), `Message` (export ledger row), `MediaFile` (per-media download state),
`MigrationJob`, `ImportPackage`, `AuditLog`.

Backend layers:
- `app/api/` — routers: auth, accounts, accounts_exports (chat search + create export),
  exports, migrations, stats, health.
- `app/services/` — `session_manager` (login flow + client pool), `export_engine`
  (checkpointed message iteration), `export_writers` (JSON / NDJSON / HTML / SQLite),
  `media_downloader`, `converter` (WhatsApp package), `test_builder`, `import_assistant`,
  `task_runner`, `telegram_utils` (message normalization).
- `app/workers/tasks.py` — Celery `export.run`.

## 2. Current export pipeline

`POST /api/accounts/{id}/exports {chat_id, format, include_media}`:
- resolves the chat via the live Telethon client and stores a serialized input peer
- creates a `ChatExport` row (status `queued`) and dispatches Celery `export.run`
- the engine (`export_engine.run`) iterates `client.get_messages(chat, offset_id=…)`,
  normalizes each message via `telegram_utils.message_to_dict`, writes `messages.jsonl`,
  bulk-inserts `Message` rows, persists a durable offset checkpoint each batch, downloads
  media (`media_downloader`) to `media/<type>/<file>`, and at finalize writes canonical
  `messages.json` (oldest-first), `database.sqlite`, and paged HTML.
- Live progress (counts, speed, ETA, percent when total known) with pause/resume/cancel;
  crash-resume is exact (no duplicate `Message` rows).

Strengths: real MTProto iteration used for **export**; robust checkpointing; media hashed
(SHA-256); multiple writers.
Weakness (for this redesign): the export ledger (`Message` rows) keeps the original
`message_id`, `date`, `edit_date`, `sender_id/name/username`, `text`, `entities`,
`reply_to_message_id`, `forwarded_from`, `reactions`, `views`, `media_types` — but **drops**
grouped-media/album membership, sticker/animation/document sub-metadata, and original media
references. The canonical archive is a JSON/NDJSON/HTML/SQLite family, not the loss-minimizing
structured spec requested.

## 3. Current conversion pipeline

`POST /api/migrations {export_id}`:
- requires `export.export_dir` non-empty (relaxed from "must be completed"; partial allowed)
- calls `converter.build_whatsapp_package(export_dir, out_dir)` which reads `messages.json`
  (or `messages.jsonl` fallback), maps distinct `sender_id -> display name` (never merges),
  and emits WhatsApp `_chat.txt` lines `DD/MM/YYYY, HH:mm - Sender: message` plus
  `<Attached: file>` lines, copies media, and writes `manifest.json`.
- also `POST /api/migrations/test` builds a small package (10/50/100/500/1000) — either
  from the first N real messages of an export (`export_id` given) or synthetic sample data.

`import_assistant` validates the **WhatsApp text package** (`_chat.txt`, `manifest.json`,
`media/`) and emits `INSTRUCTIONS.md` telling the user to use Telegram Desktop's
"Import from WhatsApp".

## 4. Current import flow

There is **no direct Telegram MTProto import**. "Import" in the current app means:
1. Convert a Telegram export to a WhatsApp-style package (`_chat.txt`).
2. Validate that package (structure).
3. Print instructions to use Telegram Desktop → Settings → Advanced → Import from WhatsApp.
4. Manual fallback (re-share media).

The actual history insertion is left entirely to Telegram's official importer, invoked by
the human in the Telegram Desktop client. The app never calls `messages.checkHistoryImportPeer`,
`checkHistoryImport`, `initHistoryImport`, `uploadImportedMedia`, or `startHistoryImport`.

## 5. What is actually implemented (working, tested)

- Account login (phone → OTP → 2FA) + encrypted sessions + client pool.
- Export engine (JSON / HTML / SQLite / media) + pause/resume/cancel + crash-resume.
- Media download with SHA-256 + retries/concurrency cap.
- WhatsApp-package converter + test builder (from real export) + package validation + instructions.
- Dashboard (login, accounts, exports w/ progress + preview, migration, import assistant,
  package preview).
- Backend test suite (68 passing, ruff clean); frontend builds.

## 6. What is only documentation / UI (not real behavior)

- "Import **assistant**" is documentation/UI: it produces instructions, it does not import.
- The `docs/` claim about "fresh account" as a universal rule is documentation, and is
  **over-broad** (see §8).
- Package preview (modal) and export preview are real endpoints; the "official importer"
  handoff is documentation.
- `POST /api/exports/{id}/retry-failed` and `GET /api/jobs` are listed in docs but **not
  implemented** (docs mark them "not yet shipped").

## 7. Files responsible for import

- `backend/app/services/import_assistant.py` — validates WhatsApp package + returns
  instructions. **No Telegram import.**
- `backend/app/services/converter.py` — makes the WhatsApp `_chat.txt` package.
- `backend/app/api/migrations.py` — builds packages / validates / instructions.
- `backend/app/api/accounts_exports.py` + `exports.py` — export-side only.
- No file calls any Telegram history-import method.

## 8. Files that assume "fresh / empty target" (over-broad)

- `README.md` §Known-Leftserver limitation: frames the *only* legitimate move as the
  WhatsApp-format importer into a **fresh** account and repeatedly stress "fresh account".
- `PROJECT_PLAN.md` §2.2: "Import target must be a fresh account / empty chat".
- `docs/api.md` / `docs/architecture.md` repeat the WhatsApp-only / fresh-account framing.
- `import_assistant.instructions` step 1 says "use a fresh target account … brand-new empty chat".

There is no code that actually creates a new account or chat; the "fresh account" assumption
lives in docs + message copy. Fixing it is a docs + service task, not a model task.

## 9. Parts that use WhatsApp text generation only

- `converter.build_whatsapp_package` (line parse + `<Attached: file>`).
- `import_assistant.validate_package` (regex parse of `_chat.txt`).
- `test_builder` (synthetic Alice/Bob for the no-`export_id` fallback).
These are the only "import serialization" the app has; it is purely WhatsApp-line text.

## 10. Parts that use Telegram MTProto directly

- `session_manager.py`: send_code_request / sign_in / get_me / connect — live Telethon.
- `export_engine.py`: `get_messages` iteration, `get_entity`, flood-wait.
- `media_downloader.py`: `download_media`.
- `accounts_exports.py`: `get_entity`, `get_input_entity`, `get_dialogs`.
Used only for login/export. **Never** for import or history-import peer checking.

## 11. Tests: are they real MTProto or mock-only?

- All backend tests use **mocked Telethon clients** (fakes in `tests/fakes.py`) driving the
  API through an in-process runner / actual FastAPI + a scratch Postgres or SQLite.
- GitHub CI (`backend/.github/…`) runs lint + those tests.
- No live-Telegram smoke test exists (no `LIVE_TELEGRAM_*`-gated test file, no external
  account A/B E2E).

## 12. Is a true Account-A -> Account-B Telegram import currently tested?

No. There is:
- no target-account (Account B) login-in-import path,
- no existing A<->B peer selection,
- no `checkHistoryImport-Peer`, no import-media upload, no `startHistoryImport`,
- no post-import re-read + verification.
Nothing resembling the recovery scenario is exercised even by mocks.

---

## Appendix A — what the current app would do in the intended scenario

Given Account A has the surviving chat, and Account B deleted their history:
1. A authenticates, finds the A<->B private chat, and exports it (works today).
2. The app asks B to log in and then tells B to use Telegram Desktop
   "Import from WhatsApp" targeting B's account — **not** A/B’s existing A<->B peer with a
   real Telegram import.
It leaves it entirely to Telegram's official clients to route, timestamp, send, and merge.

## Appendix B — honest feasibility flags for the requested real MTProto import

- Telethon already exposes the low-level request classes (`messages.CheckHistoryImport`,
  `messages.InitHistoryImportPeers`, `messages.StartHistoryImport`, etc.) so the client
  calls are *implementable* in principle.
- The hard, documented-uncertain part is Telegram's **import archive format**: the byte
  layout that `messages.initHistoryImport(peer, import_file, media_count)` accepts is
  Telegram's own serialization used by the official importer (its `import_head` /
  `import_media` are produced by the official flow). It is **not** a WhatsApp `_chat.txt`
  and **not** publicly documented for third parties. Building a conforming archive is the
  primary technical risk and can only be validated by a real target account.
- Whether imported history merges into the **historical** timeline is server-side and
  depends on the target chat size (<1000 vs >1000 messages) per Telegram's documented
  behavior; that is the core thing the requested "Timeline Experiment" would measure.

(Cited sources: Telegram import API — https://core.telegram.org/api/import ;
 peer check — https://core.telegram.org/method/messages.checkHistoryImport/ ;
 "Move history" blog — https://telegram.org/blog/move-history .)