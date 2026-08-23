# TIMESTAMP EXPERIMENT — CONTROLLED RESULT (2026-08-23)

## Setup

Dedicated test peer `@pourfallah` (Account B session id 2 — NOT the user's
recovery chat). 10 messages with obviously distinct dates spanning 2019–2025,
built by `backend/app/services/timestamp_experiment.py`:

| src id | source date (UTC) | text |
|---|---|---|
| 10 | 2019-01-01T00:00:00Z | msg 2019 earliest |
| 1 | 2020-01-01T10:00:00Z | msg 2020 new-year |
| 2 | 2020-01-01T10:01:00Z | msg 2020 one minute later |
| 3 | 2020-01-02T15:30:00Z | msg 2020 next day |
| 4 | 2021-06-10T23:59:59Z | msg 2021 june |
| 5 | 2022-11-15T08:22:41Z | msg 2022 november |
| 6 | 2023-07-04T12:00:00Z | msg 2023 july |
| 7 | 2024-02-29T06:30:00Z | msg 2024 leap day |
| 8 | 2025-05-05T18:45:12Z | msg 2025 may |
| 9 | 2025-12-31T23:59:59Z | msg 2025 new year eve |

Protocol executed live: checkHistoryImport → checkHistoryImportPeer →
initHistoryImport (id `920678871353048778`) → startHistoryImport → re-read chat.

Raw result: `/data/exports/experiments/TIMESTAMP_EXPERIMENT_RESULT.json`.

## Observed target messages (read AFTER materialization delay)

| target msg id | visible date (server) | imported flag | fwd_date metadata | matches source? |
|---|---|---|---|---|
| 500177 | **2018-12-31T20:30** | true | 2018-12-31T20:30 | ✅ (= 2019-01-01T00:00Z in UTC+3:30) |
| 500178 | **2020-01-01T06:30** | true | 2020-01-01T06:30 | ✅ (= 10:00Z − 3.5 h) |
| 500179 | 2020-01-01T06:31 | true | 2020-01-01T06:31 | ✅ |
| 500180 | 2020-01-02T12:00 | true | 2020-01-02T12:00 | ✅ |
| 500181 | 2021-06-10T20:29 | true | 2021-06-10T20:29 | ✅ |
| 500182 | 2022-11-15T04:52 | true | 2022-11-15T04:52 | ✅ |
| 500183 | 2023-07-04T08:30 | true | 2023-07-04T08:30 | ✅ |
| 500184 | 2024-02-29T03:00 | true | 2024-02-29T03:00 | ✅ |
| 500185 | 2025-05-05T15:15 | true | 2025-05-05T15:15 | ✅ |
| 500186 | 2025-12-31T20:29 | true | 2025-12-31T20:29 | ✅ |

Chronological order preserved across five years. All ten placed in history at
their original dates.

## Key discoveries

### 1. Historical placement WORKS — but materializes with a delay
Immediately after `startHistoryImport` returns, a naive re-read shows the batch
at import time (`2026-08-23T09:53:31`). Roughly **2 minutes later** Telegram
re-materializes the block at its historical dates. Earlier E2E runs read too
early and concluded (wrongly) that placement was impossible. The verification
phase must wait/poll for materialization before comparing.

### 2. The importer parses naive timestamps in the TARGET account's timezone
Written `10:00` (UTC wall clock) → stored `06:30` = 10:00 − 3.5 h.
The test account sits at UTC+3:30, so Telegram read our string as local time and
stored the corresponding UTC instant. Consequence:
- To display the TRUE instant, write import-file timestamps converted into the
  target account's local timezone.
- Implemented: `build_import_file(..., tz_offset_minutes=<target offset>)`;
  worker auto-detects the offset or accepts `options.tz_offset_minutes`.

With correct tz conversion: `visible == fwd_date == true source instant`.
The earlier "import-time dates" observation on job #22/#23 was a combination of
(a) reading before materialization and (b) no tz conversion.

## Answers to the task's experiment matrix

- message count / chunk size / multiple imports: do not affect placement; the
  whole block is placed historically regardless of size (10-message proof).
- existing target history: unaffected; imported block slots in chronologically.
- supported syntaxes: dot format `DD.MM.YYYY HH:MM`, naive, parsed as target-local.
