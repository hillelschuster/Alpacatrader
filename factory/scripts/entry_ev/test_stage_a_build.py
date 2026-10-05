#!/usr/bin/env python
"""Unit tests for the ENTRY-EV-01 Stage-A producer's horizon / navigation semantics.

Everything below runs on hand-built tiny frames -- no market data is read.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from entry_ev.stage_a_build import (  # noqa: E402
    HORIZONS,
    NG,
    T0,
    assert_in_block,
    assert_month_in_block,
    board_coordinates,
    dense_bars,
    first_bar_at_or_after,
    forward_and_path,
    select_events,
    volume_ratio,
)


def empty_grid() -> dict[str, np.ndarray]:
    return {f: np.full(NG, np.nan) for f in ("open", "high", "low", "close", "volume")}


def put(grid: dict[str, np.ndarray], et: int, o, h, l, c, v) -> None:
    i = et - T0
    grid["open"][i], grid["high"][i], grid["low"][i] = o, h, l
    grid["close"][i], grid["volume"][i] = c, v


# --------------------------------------------------------------------------- #
# Bar-grid fixture: a small, gappy tape.
#   et  570 571 572  573 574 575 576
#   o   10  11  12   -   -  14  15
#   h   11  12  13   -   -  15  16
#   l    9  10  11   -   -  13  14
#   v  100 200  50   -   - 300  10
# --------------------------------------------------------------------------- #
@pytest.fixture()
def gappy() -> dict[str, np.ndarray]:
    g = empty_grid()
    for et, o, h, l, c, v in [(570, 10, 11, 9, 10, 100), (571, 11, 12, 10, 11, 200),
                              (572, 12, 13, 11, 12, 50), (575, 14, 15, 13, 14, 300),
                              (576, 15, 16, 14, 15, 10)]:
        put(g, et, o, h, l, c, v)
    return g


def test_first_bar_at_or_after_is_left_closed_and_skips_gaps(gappy):
    present = np.flatnonzero(~np.isnan(gappy["open"]))
    got = first_bar_at_or_after(present, np.array([570, 571, 572, 573, 574, 575, 577]) - T0)
    # 573/574 have no bar of their own -> the next bar at 575 is used
    assert list(got) == [0, 1, 2, 5, 5, 5, -1]


def test_forward_uses_first_bar_at_or_after_t_and_t_plus_h(gappy):
    out = forward_and_path(gappy["open"], gappy["high"], gappy["low"], np.array([570, 571, 575]))
    assert list(out["entry_et"]) == [570, 571, 575]
    assert list(out["entry_open"]) == [10.0, 11.0, 14.0]
    # t=570: exit(1)=571 -> 11/10-1 = +10%
    assert out["fwd_ret_1"][0] == pytest.approx(0.10)
    # t=571: exit(3)=575 (no bars at 572..574) -> 14/11-1
    assert out["fwd_ret_3"][1] == pytest.approx(14 / 11 - 1)
    assert out["exit_et_3"][1] == 575
    # t=575: exit(1)=576 -> 15/14-1
    assert out["fwd_ret_1"][2] == pytest.approx(15 / 14 - 1)
    # exit(5) from t=575 is beyond the tape
    assert np.isnan(out["fwd_ret_5"][2])


def test_entry_never_uses_the_bar_before_t(gappy):
    """t=573 has no bar of its own; entry is 575's open, never 572's."""
    out = forward_and_path(gappy["open"], gappy["high"], gappy["low"], np.array([573]))
    assert out["entry_et"][0] == 575 and out["entry_open"][0] == 14.0


def test_no_bar_at_or_after_t_is_null_not_zero(gappy):
    out = forward_and_path(gappy["open"], gappy["high"], gappy["low"], np.array([959, 577]))
    for h in HORIZONS:
        assert np.isnan(out[f"fwd_ret_{h}"]).all()
        assert np.isnan(out["entry_open"]).all()
    assert list(out["entry_et"]) == [-1, -1]


def test_path_is_half_open_entry_et_to_exit_et(gappy):
    """MAE/MFE cover et in [entry_et, exit_et): 570..574 -> bars 570,571,572."""
    out = forward_and_path(gappy["open"], gappy["high"], gappy["low"], np.array([570]),
                           horizons=(5,), path_horizons=(5,))
    assert out["exit_et_5"][0] == 575
    assert out["mae_5"][0] == pytest.approx(9 / 10 - 1)    # low(570)=9, not low(575)=13
    assert out["mfe_5"][0] == pytest.approx(13 / 10 - 1)   # high(572)=13, not high(575)=15


def test_path_is_null_when_incomplete(gappy):
    """t=573 enters at 575 and has no bar at/after 578 -> no complete path."""
    out = forward_and_path(gappy["open"], gappy["high"], gappy["low"], np.array([573]),
                           horizons=(5,), path_horizons=(5,))
    assert np.isnan(out["mae_5"][0]) and np.isnan(out["mfe_5"][0])


def test_path_spans_only_the_requested_window():
    """A dip before the entry must not leak into MAE; a spike after the exit must not leak into MFE."""
    g = empty_grid()
    for et, o, h, l, c, v in [(570, 50, 51, 0.5, 50, 1),      # the pre-entry dip
                              (571, 10, 10.5, 9.5, 10, 1),    # the entry bar
                              (576, 10, 1000, 10, 10, 1),      # the exit bar's high spike
                              (580, 10, 10, 10, 10, 1)]:
        put(g, et, o, h, l, c, v)
    out = forward_and_path(g["open"], g["high"], g["low"], np.array([571]),
                           horizons=(5,), path_horizons=(5,))
    assert out["entry_open"][0] == 10.0 and out["exit_et_5"][0] == 576
    assert out["mae_5"][0] == pytest.approx(9.5 / 10 - 1)      # bar 571 only, not 570's low 0.5
    assert out["mfe_5"][0] == pytest.approx(10.5 / 10 - 1)     # bar 571 only, not 576's high 1000


def test_volume_ratio_uses_bars_et_le_t_minus_1():
    g = empty_grid()
    g["volume"][(600 - T0):(650 - T0)] = 3.0
    g["volume"][: (600 - T0)] = 1.0
    g["volume"][(650 - T0):] = 1.0
    v = volume_ratio(g["volume"], np.array([650, 570, 575]))
    # V(650)=180, V(620)=90, V(590)=20  -> (180-90)/(90-20)
    assert v[0] == pytest.approx(90 / 70)
    # t=570: no bar is complete at t-1 -> denominator 0 -> null, never 0.0
    assert np.isnan(v[1]) and np.isnan(v[2])


def test_board_coordinates_ret_dd_and_promo_age():
    px = np.full(NG, np.nan)
    rk = np.full(NG, np.nan)
    px[:6] = [10, 12, 9, 11, 20, 22]
    rk[0], rk[3] = 3.0, 7.0
    t = np.array([570, 571, 572, 573, 575])
    c = board_coordinates(px, rk, t)
    assert c["promo_age"].tolist() == [0.0, 1.0, 2.0, 3.0, 5.0]
    np.testing.assert_allclose(c["ret1"], [np.nan, 0.20, -0.25, 11 / 9 - 1, 0.10], rtol=1e-9)
    assert c["ret3"][3] == pytest.approx(11 / 10 - 1)
    np.testing.assert_allclose(c["dd_from_high"], [0.0, 0.0, -0.25, -1 / 12, 0.0], rtol=1e-9)


def test_promo_age_null_when_never_ranked_top5():
    px = np.full(NG, np.nan)
    px[0] = 10.0
    rk = np.full(NG, np.nan)
    rk[0] = 8.0
    c = board_coordinates(px, rk, np.array([570]))
    assert np.isnan(c["promo_age"][0])


def test_dense_bars_never_prices_past_session_end(gappy):
    lane = pl.DataFrame({
        "ticker": ["X"] * 5,
        "etm": [570, 571, 572, 575, 576],
        "open": [10.0, 11.0, 12.0, 14.0, 15.0],
        "high": [11.0, 12.0, 13.0, 15.0, 16.0],
        "low": [9.0, 10.0, 11.0, 13.0, 14.0],
        "close": [10.0, 11.0, 12.0, 14.0, 15.0],
        "volume": [100.0, 200.0, 50.0, 300.0, 10.0],
    })
    full = dense_bars(lane, "X", 959)
    assert full["open"][576 - T0] == 15.0
    short = dense_bars(lane, "X", 575)
    assert short["open"][576 - T0] != short["open"][576 - T0]   # NaN: bar is past session_end
    assert short["open"][575 - T0] == 14.0


def test_dense_bars_unknown_ticker_is_all_nan(gappy):
    lane = pl.DataFrame({"ticker": ["X"], "etm": [570], "open": [1.0], "high": [1.0],
                         "low": [1.0], "close": [1.0], "volume": [1.0]})
    d = dense_bars(lane, "Y", 959)
    assert np.isnan(d["open"]).all()


# --------------------------------------------------------------------------- #
# Guard + block guard
# --------------------------------------------------------------------------- #
def _board_rows(n=10, **over):
    base = dict(
        day=["2021-03-01"] * n, t=[570] * n, ticker=["X"] * n,
        px=[10.0] * n, px_et=[569] * n, rank_known=[1] * n, gain=[1.0] * n,
        prev_close=[10.0] * n, prev_close_stale=[False] * n,
        prev_close_floor_qualified=[False] * n, flag_prevclose_discrepancy=[False] * n,
        flag_extreme_gain=[False] * n, session_end=[959] * n,
    )
    base.update(over)
    return pl.DataFrame(base)

def test_guard_drops_each_reason_and_keeps_extreme_gain():
    rows = _board_rows(
        prev_close=[10.0, None, 0.508, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
        prev_close_stale=[False] * 3 + [True] + [False] * 6,
        prev_close_floor_qualified=[False] * 4 + [True] + [False] * 5,
        flag_prevclose_discrepancy=[False] * 5 + [True] + [False] * 4,
        flag_extreme_gain=[False] * 9 + [True],
    )
    kept, counts, n_sel = select_events(rows, 959)
    assert n_sel == 10
    assert counts == {
        "prev_close_null": 1, "prev_close_lt_1": 1, "prev_close_stale": 1,
        "prev_close_floor_qualified": 1, "flag_prevclose_discrepancy": 1,
        "any": 5, "flag_extreme_gain_reported": 1,
    }
    assert kept.height == 5
    assert kept["flag_extreme_gain"].sum() == 1          # reported, never dropped


def test_selection_is_rank_and_rth_only():
    rows = _board_rows(n=5, t=[570, 600, 959, 960, 569], rank_known=[1, 10, 5, 1, 1])
    kept, counts, n_sel = select_events(rows, 959)
    # 960 is past session_end, 569 is before the open, and rank 11 is off-board
    assert n_sel == 3 and kept.height == 3
    assert sorted(kept["t"].to_list()) == [570, 600, 959]


def test_block_guard_refuses_protected_days():
    assert assert_in_block("2021-02-01") == dt.date(2021, 2, 1)
    assert assert_in_block("2023-03-14") == dt.date(2023, 3, 14)
    for bad in ("2021-01-29", "2023-03-15", "2023-12-29", "2024-06-03", "2026-06-01"):
        with pytest.raises(AssertionError):
            assert_in_block(bad)
    for bad_month in ("2021-01", "2023-04", "2023-12", "2024-06", "2026-06"):
        with pytest.raises(AssertionError):
            assert_month_in_block(bad_month)
    assert_month_in_block("2021-02") and assert_month_in_block("2023-03")
