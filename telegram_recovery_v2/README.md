# Telegram Recovery v2

A self-contained, **lossless Telegram recovery/reconstruction engine** — a fresh
subproject built WITHOUT touching the existing `backend/` + `frontend/`
application.

```
ACCOUNT A (source)  ──READ──►  LOSSLESS ARCHIVE  ──direct import──►  ACCOUNT B /
   (has the history)          (canonical + raw MTProto)    │          same A<->B peer
                                                           └──► REAL MTProto VERIFICATION ──► FINAL_REPORT
```

This is **not** a WhatsApp converter and **not** a generic migration framework.
It is a Telegram-native recovery engine with one job: move a real conversation
from account A (which still has it) back into the A↔B private chat with maximum
possible fidelity, and prove — from actual Telegram target message objects —
what was restored exactly, reconstructed, left partial, or archived only.

## Why a new subproject?

The existing repo is a WhatsApp-export *converter* (it builds `_chat.txt`
packages for Telegram Desktop's manual importer) plus a web dashboard. Repeated
patching produced inconsistent behavior around import/reconstruction. This
engine is built from scratch, self-contained, and does the official Telegram
history-import flow directly. The old application is untouched until this
engine has a proven baseline. See [docs/OLD_SYSTEM_AUDIT.md](docs/OLD_SYSTEM_AUDIT.md).

## Timeline / status

- [x] Source (module + CLI + config + tests): reads a Telegram chat into a
      lossless archive (canonical one-message-per-record + sanitized raw MTProto).
- [x] Package builder: import package generated directly from the canonical archive.
- [x] ImportEngine: the five official history-import RPCs + reactions + verifier.
- [x] Hermetic tests: 36 pass, 1 live test skips offline.
- [ ] **Live real-Telegram E2E** — requires RECOVERY_* credentials + sessions.
      Nothing in this repo claims a real restore worked until that runs.

## Layout

```
telegram_recovery_v2/
  README.md  pyproject.toml  requirements.txt  .env.example
  src/recovery/         engine, reader, archive, media, mapper, reactions,
                        importer, verifier, telegram_client, config, cli
  tests/unit            hermetic unit tests
  tests/integration     full offline pipeline test
  tests/e2e             live Telegram test (marker=live, skips without creds)
  scripts/              fixture/media/export/clear/import/verify/full-test
  docs/                 architecture, protocol, media, reactions, replies,
                        limitations, e2e, old-system audit
  test_runs/            per-run artifacts (gitignored, never deleted)
```

## Quick start

```bash
cd telegram_recovery_v2
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                          # 36 hermetic tests (live one skips)
python -m recovery.cli --help   # CLI surface
```

## Configure & authenticate

```bash
cp .env.example .env
# edit RECOVERY_API_ID_A/HASH_A/PHONE_A and ..._B
python -m recovery.cli accounts login --actor a   # interactive OTP + 2FA
python -m recovery.cli accounts login --actor b
```

Session strings are saved to files (mode 0600) and are **never printed**.
Secrets live only in `.env` / environment.

## CLI

```
recovery-v2 accounts          list A/B sessions, interactive login
recovery-v2 resolve-peer      resolve RECOVERY_PEER to an InputPeer
recovery-v2 inspect-chat      newest message ids
recovery-v2 export            A -> lossless archive
recovery-v2 verify-export     archive integrity (raw snapshots, media hashes)
recovery-v2 build-package     package directly from canonical archive
recovery-v2 clear-target      clear B only (just_clear, revoke NEVER)
recovery-v2 import            official history import
recovery-v2 reconstruct       per-actor reaction reconstruction
recovery-v2 verify            fidelity report (reads real target objects)
recovery-v2 full-test         end-to-end recovery (creates a fresh fixture)

Sampled real-history experiment (SOURCE A<->C  ->  TARGET A<->B), phased + resumable:

```

python -m recovery_v2.sample_history \
    --source-peer +989****4546 --target-peer <A-phone> \
    --count 20 --years 3                 # P1 lightweight resumable catalog + P2 sample
python -m recovery_v2.full_sampled_recovery \
    --run-id <ID> --source-peer +989****4546 --dry-run   # P3-P5: fetch~20 + media + package
python -m recovery_v2.full_sampled_recovery \
    --run-id <ID> --source-peer +989****4546 --confirm   # P6-P7: clear B + import + verify
```

Phase model (`src/recovery/pipeline.py`): P1 catalog is lightweight
(id/date/media-type/reply/group/reactions flags only; resumable + checkpointed,
never full message bodies for the whole chat); P2 stratified year-bucket
sampling (deterministic seed); P3 LAZY full fetch of only the ~selected ids +
reply parents + full groups; P4 media/reactions for the sample only; P5 package
+ roundtrip; P6 official import (5 methods); P7 real-target verification +
`FINAL_HISTORICAL_SAMPLE_RECOVERY_REPORT.{json,html}`. SOURCE A<->C is read-only
throughout; only TARGET A<->B is modified, and only with `--confirm`.

## One honest rule

No claim of "restored" until the **actual Telegram target message objects**,
read after recovery, prove it. [docs/FIDELITY_CLASSES.md](docs/FIDELITY_CLASSES.md)
defines the labels (`EXACT / RECONSTRUCTED / PARTIAL / ARCHIVAL_ONLY / FAILED`).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flows, run artifacts
- [docs/TELEGRAM_PROTOCOL.md](docs/TELEGRAM_PROTOCOL.md) — the official MTProto operations used
- [docs/MEDIA.md](docs/MEDIA.md) — media classification + real fixtures
- [docs/REACTIONS.md](docs/REACTIONS.md) — who/what/which-message reaction fidelity
- [docs/REPLIES.md](docs/REPLIES.md) — reply/quote preservation
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — capability matrix (no hiding)
- [docs/REAL_IMPORT_VS_NEW_MESSAGE_AUDIT.md](docs/REAL_IMPORT_VS_NEW_MESSAGE_AUDIT.md) — proves the import path is genuine history import, not send-new-messages
- [docs/FINAL_IMPORT_TRUTH_REPORT.md](docs/FINAL_IMPORT_TRUTH_REPORT.md) — feature×result table (TIMESTAMP/SENDER/MEDIA/…), target results live-pending
- [docs/E2E_TEST.md](docs/E2E_TEST.md) — exact live test procedure
- [docs/OLD_SYSTEM_AUDIT.md](docs/OLD_SYSTEM_AUDIT.md) — what the old system does / loses