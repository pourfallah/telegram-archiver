# E2E test procedure (real Telegram)

The hermetic suite proves the engine's structure and logic. Live correctness is
proven ONLY by running against real Telegram credentials.

## Prerequisites

1. Accounts A (source) and B (target) that share an A↔B conversation.
2. `my.telegram.org` `api_id`/`api_hash` for both.
3. Logged-in sessions saved via `recovery-v2 accounts login --actor a|b`,
   or set `RECOVERY_SESSION_*_STRING/FILE`.
4. Fresh fixture wanted (default) or `--use-existing`.

## Run

```bash
cd telegram_recovery_v2
source .venv/bin/activate
python scripts/generate_media_fixtures.py      # real fixture bytes
python scripts/create_fixture.py               # build the A->B fixture (A reacts)
python scripts/full_recovery_test.py           # the exact full command
# or piecewise:
python scripts/export_fixture.py
python scripts/clear_target.py
python scripts/import_fixture.py
python scripts/verify_target.py
```

Also as pytest (live marker):

```bash
pytest -m live tests/e2e/
```

## What the live run must verify (project rule #58 — NOT_AVAILABLE, not skip)

1. text                       2. formatted text       3. emoji
4. custom emoji                5. photo                6. photo + caption
7. sticker (a REAL sticker)    8. video                9. GIF/animation
10. audio                     11. voice               12. document
13. reply                     14. reaction by A       15. reaction by B
16. two-photo album           17. album item + caption 18. forwarded audio
19. media w/o caption        20. text adjacent to media

Every capability this account pair cannot produce is logged `NOT_AVAILABLE`.

## Fidelity report

`test_runs/<run_id>/FINAL_REPORT.json` + `.html`:
- source↔target rows: source_id, target_id, sender, date, text, media, caption,
  reply, forward, reaction, grouped_id;
- a per-feature matrix (`EXACT / RECONSTRUCTED / PARTIAL / ARCHIVAL_ONLY / FAILED`);
- reaction verification from `messages.getMessagesReactions`.

Only after the live run passes do we integrate the engine into the existing
web app (thin adapter — no duplicated logic).