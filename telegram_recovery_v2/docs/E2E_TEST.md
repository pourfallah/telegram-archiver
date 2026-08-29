# E2E Test — how to run the real recovery

## Preconditions

- Two Telegram accounts: A (source of truth) and B (recovery target),
  each with a valid Telethon session string in `secrets/account_{a,b}.session_string`
  and API creds in `secrets/account_{a,b}.api.json` (git-ignored), or set the
  `RECOVERY_ACCOUNT_*` environment variables (see .env.example).
- Python 3.11+, `pip install -e .` inside telegram_recovery_v2/.

## Full pipeline

```bash
cd telegram_recovery_v2

# 1. create the real fixture in the A<->B chat (unique markers per day)
python scripts/create_fixture.py

# 2. export A's history to the lossless archive
recovery-v2 export --peer-id <B_USER_ID>

# 3. verify archive consistency
recovery-v2 verify-export --run-id <run_id>

# 4. build the import package from the archive
recovery-v2 build-package --run-id <run_id>

# 5. clear B only (just_clear=True, revoke=False) — A keeps everything
recovery-v2 clear-target --peer-id <B_USER_ID>

# 6. run the official import (check -> peer -> init -> upload -> start)
recovery-v2 import --run-id <run_id> --peer-id <B_USER_ID>

# 7. wait for server-side materialization (poll target counts), then map,
#    reconstruct reactions, verify against real target objects
recovery-v2 verify --run-id <run_id> --peer-id <B_USER_ID>
```

Or everything at once:

```bash
recovery-v2 full-test --peer-id-a <B_USER_ID> --peer-id-b <A_USER_ID>
```

## Rules baked into the flow

- Target delta: only messages in target_after not present in target_before
  are attributed to this run.
- Import is resumable via import_state.json; never re-init or re-upload on
  retry — inspect state first.
- Run artifacts are never deleted; every run gets a fresh run_id.
- The only success criteria are actual target MTProto Message objects.
