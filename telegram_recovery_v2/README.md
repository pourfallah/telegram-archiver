# telegram_recovery_v2

A self-contained Telegram recovery engine: account **A** still has the full
conversation, account **B** lost it. v2 reads A directly over MTProto into a
lossless archive, builds the import package the official Telegram history
import API accepts, imports into the same A<->B peer as B, reconstructs what
can legitimately be reconstructed (reactions), and verifies everything
against the REAL target MTProto message objects.

Not a WhatsApp converter. Not a generic migration framework. One job:
maximum-fidelity recovery, honestly classified.

## Quick start

```bash
pip install -e .
# credentials: secrets/account_{a,b}.{api.json,session_string} or env (see .env.example)

recovery-v2 accounts                 # verify both sessions
recovery-v2 full-test --peer-id-a <B_user_id> --peer-id-b <A_user_id>
```

## One engine

`TelegramRecoveryEngine` (src/recovery/engine.py) is the single
implementation. The CLI (`recovery-v2 ...`), the scripts in scripts/, the
tests, and (later) the web application all drive the same engine. There is
no separate test importer.

## Commands

accounts / resolve-peer / inspect-chat / export / verify-export /
build-package / clear-target / import / reconstruct / verify / full-test

See docs/E2E_TEST.md for the exact sequence.

## Layout

- src/recovery/ — engine modules (config, telegram_client, source_reader,
  archive, media, importer, mapper, reactions, verifier, engine, cli)
- scripts/ — fixture creation, clear, import, verify, full test
- tests/ — unit / integration / e2e
- docs/ — ARCHITECTURE, TELEGRAM_PROTOCOL, MEDIA, REPLIES, REACTIONS,
  LIMITATIONS (capability matrix), E2E_TEST, OLD_SYSTEM_AUDIT
- test_runs/ — immutable per-run artifacts (archive, package, traces,
  target before/after, mapping, reports)

## Honesty rules

- Only actual target Telegram objects decide success — never upload tokens,
  never file existence.
- Classifications: EXACT / RECONSTRUCTED / PARTIAL / ARCHIVAL_ONLY / FAILED.
- Limitations are documented, not hidden: docs/LIMITATIONS.md.
- No "full restore" claims. Reaction/reply/caption/timestamp fidelity is
  reported per message.

## Security

Session strings and API hashes stay in secrets/ (git-ignored) or env vars.
Nothing prints credentials. Reports contain user ids only.
