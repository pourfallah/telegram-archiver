"""Offline tests for tz-shifted timestamp serialization + materialization rules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from recovery.importer import _wa_ts, _plus_1s


class TestTimezoneSemantics:
    """Live-proven (run recovery_v2_20260829_091021_548599): Telegram parses
    naive file timestamps in the TARGET account's local tz (UTC+3:30).
    Writing UTC wall-clock shifted every visible date by -3:30h."""

    def test_utc_shifted_to_account_tz(self):
        # 07:52:20 UTC must be written as 11:22:20 (+3:30) so the target shows 07:52:20
        assert _wa_ts("2026-08-29T07:52:20") == "29/08/2026, 11:22:20"

    def test_day_rollover(self):
        # 22:00 UTC -> 01:30 next day in +3:30
        assert _wa_ts("2026-08-29T22:00:00") == "30/08/2026, 01:30:00"

    def test_caption_plus_1s_survives_tz(self):
        ts = "2026-08-29T07:52:44"
        cap = _plus_1s(ts)
        assert _wa_ts(ts) == "29/08/2026, 11:22:44"
        assert _wa_ts(cap) == "29/08/2026, 11:22:45"  # 1s apart, same day

    def test_midnight_caption_rollover(self):
        cap = _plus_1s("2026-08-29T23:59:59")
        assert cap == "2026-08-30T00:00:00"
