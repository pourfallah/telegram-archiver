# Timeline Materialization Experiment

Live test against real Telegram servers. Tag: `exp3`, 2026-08.

## Question

Does Telegram's history import ever set an imported message's **`message.date`**
(the visible bubble timestamp) to the original historical instant, or does the
historical date survive only in `fwd_from.date` (import metadata)?

## Setup

- Importing account: target (account in the suite)
- Target peer resolved to the existing private chat
- Source picks (imported via the real history-import RPC):
  - id 5669210, source date `2026-08-19T09:25:25`, text `ممنوووونم`
  - id 5669281, source date `2026-08-19T16:01:52`, text `چطوری خوبی؟`
- Procedure: snapshot target before → `initHistoryImport`(media_count=0) →
  `startHistoryImport` → read target at t = 0, 30, 60, 120, 180, 300, 600 s,
  recording for each new message id: `message.date`, `fwd_from.date`, `imported`.

## Result (identical at every sample point)

At **all** sample times (0s … 600s) the freshly imported messages showed:

| Field | Value |
|---|---|
| `message.date` (visible) | `2026-08-25T09:30:55` (import moment) — **unchanged** |
| `fwd_from.date` (metadata) | `2026-08-19T09:25:25` / `16:01:52` (historical source) |
| `imported` | true |

The visible date did **not** move toward the historical date at 30, 60, 120, 180,
300 or 600 seconds.

## Conclusion (honest, not inferred)

1. **`message.date` is NOT restored to the historical instant by waiting.**
   The earlier "$1-3$ min materialization makes the historical date visible"
   claim is **disproven** by this experiment.
2. **The historical timestamp survives only as `fwd_from.date` import metadata.**
   The correct classification is **`IMPORTED_METADATA_ONLY`**, never
   `TIMESTAMP_RESTORED`.
3. There is **no documented hidden date parameter** on
   `initHistoryImport`/`startHistoryImport` that restores a visible historical
   `message.date`. Telegram's public import protocol preserves the original time
   as metadata, not as the bubble timestamp.

## Consequence for the verifier

- `checks.timestamp` must treat `IMPORTED_METADATA_ONLY` as "metadata preserved,
  visible date not restored" — it is a *partial*, not a full timestamp restore.
- The recovery report and README must **not** claim "timestamps restored" based on
  `fwd_from.date`. Visible timeline placement does not match the source timeline.