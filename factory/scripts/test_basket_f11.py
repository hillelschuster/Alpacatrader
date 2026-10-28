from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isclose
from pathlib import Path
import sys

import polars as pl

sys.path.insert(0, str(Path(__file__).parent))

from basket_f11_pilot import EntryCandidate, entry_timestamp, observe_entry
from basket_f11_sensitivity import daily_capacity


def test_entry_observation_when_quote_trade_and_halt_gap_exist() -> None:
    entry_time = entry_timestamp("2021-02-01", 570)
    candidate = EntryCandidate(
        architecture="A_pm_N3_R2_10_5",
        day="2021-02-01",
        ticker="TEST",
        expected_et=570,
        entry_et=570,
        entry_px=10.0,
        blocked=False,
        gap_minutes=0,
    )
    quotes = pl.DataFrame(
        {
            "symbol": ["TEST", "TEST"],
            "ts_utc": [entry_time - timedelta(seconds=30), entry_time + timedelta(seconds=5)],
            "bid_price": [9.9, 9.8],
            "ask_price": [10.1, 10.2],
        }
    )
    trades = pl.DataFrame(
        {
            "symbol": ["TEST", "TEST"],
            "ts_utc": [entry_time + timedelta(seconds=1), entry_time + timedelta(seconds=20)],
            "price": [10.1, 10.2],
            "size": [100.0, 200.0],
        }
    )
    bars = pl.DataFrame(
        {
            "ticker": ["TEST", "TEST", "TEST"],
            "et": [570, 571, 577],
            "close": [10.0, 10.1, 10.2],
            "volume": [100.0, 200.0, 300.0],
        }
    )

    observation = observe_entry(candidate, quotes, trades, bars)

    assert observation.pre_quote_covered
    assert observation.next_print_covered
    assert isclose(observation.spread_bps or 0.0, 200.0)
    assert isclose(observation.half_spread_bps or 0.0, 100.0)
    assert isclose(observation.next_print_slippage_bps or 0.0, 100.0)
    assert observation.halt_like_gap_count == 1


def test_daily_capacity_when_equal_slots_use_the_bottleneck_volume() -> None:
    capacity = daily_capacity((100.0, 200.0), slots=3, participation=0.05)

    assert isclose(capacity, 15.0)


if __name__ == "__main__":
    test_entry_observation_when_quote_trade_and_halt_gap_exist()
    test_daily_capacity_when_equal_slots_use_the_bottleneck_volume()
