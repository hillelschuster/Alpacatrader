"""Behavioral guards for causal feature extraction and non-hindsight fill accounting."""

import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_open_panel import FEATURES, allowed, symbol_rows


def tape(end=959):
    et = np.arange(570, end + 1)
    px = 10 + (et - 570) * 0.01
    return pl.DataFrame(
        {
            "et": et,
            "open": px,
            "high": px + 0.02,
            "low": px - 0.02,
            "close": px + 0.005,
            "volume": np.full(len(et), 1000.0),
        }
    )


def row(frame, t=585, end=959):
    rows = symbol_rows("2021-02-01", "XYZ", frame, [(585, 0.1)], {585: {"XYZ": 1}}, end)
    return next(r for r in rows if r["t"] == t)


def test_future_prices_and_volume_do_not_change_decision_features():
    base = tape()
    shocked = base.with_columns(
        [
            pl.when(pl.col("et") >= 585).then(pl.col(c) * 10).otherwise(pl.col(c)).alias(c)
            for c in ("open", "high", "low", "close", "volume")
        ]
    )
    before, after = row(base), row(shocked)
    assert {f: before[f] for f in FEATURES} == {f: after[f] for f in FEATURES}
    assert after["entry_open"] == 10 * before["entry_open"]


def test_missing_entry_is_expired_cash_not_a_later_hindsight_fill():
    r = row(tape().filter(pl.col("et") != 585))
    assert r["entry_status"] == "unfilled_expired"
    assert r["entry_open"] is None
    assert r["gross_60"] == 0
    assert r["exit_status_60"] == "unfilled_cash"


def test_halt_delays_exit_and_early_close_caps_target():
    halted = tape().filter(~pl.col("et").is_between(600, 610))
    r = row(halted)
    assert r["exit_et_15"] == 611
    assert r["gross_15"] == (10 + 0.41) / (10 + 0.15) - 1
    early = row(tape(779), t=775, end=779)
    assert early["exit_et_60"] == 779
    censored = row(tape(778), t=775, end=779)
    assert censored["exit_status_60"] == "unknown_pending"
    assert censored["gross_60"] is None


def test_protected_periods_are_not_development_dates():
    assert allowed("2021-02-01") and allowed("2026-05-29")
    assert not allowed("2024-03-01")
    assert not allowed("2025-01-31")
    assert not allowed("2026-06-01")
