"""Tests for path_features: causality first, correctness second."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from scripts.path_features import (  # noqa: E402
    build_vector, zseries_30m, dtw, is_common_stock, FEATURES)


def _bars(n=40, start_et=570):
    import numpy as np
    rng = np.random.default_rng(7)
    px = 10 * (1 + rng.normal(0, 0.004, n).cumsum() / 10)
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-05-05 13:30", periods=n, freq="min", tz="UTC"),
        "ticker": "TST", "open": px, "high": px * 1.003, "low": px * 0.997,
        "close": px + rng.normal(0, 0.01, n), "volume": rng.integers(1000, 9000, n),
        "et": list(range(start_et, start_et + n))})


def test_causality_append_future_invariant():
    full = _bars(120)   # et 570..689
    early = full.iloc[:30].copy()  # et 570..599: identical prefix, future truncated
    a = build_vector(early, None, 9.5, t_et=600)
    b = build_vector(full, None, 9.5, t_et=600)
    assert a is not None and b is not None
    assert a == b, "future bars changed features = lookahead"


def test_zseries_causal_and_scaled():
    z = zseries_30m(_bars(120), t_et=600)
    assert z is not None and len(z) == 29  # 30 bars -> 29 returns
    import statistics as st
    assert abs(st.mean(z)) < 0.05 and 0.9 < st.stdev(z) < 1.1


def test_dtw_identity_and_order():
    a = [0.1, 0.5, 1.0, 0.4]
    assert dtw(a, a) == 0.0
    assert dtw(a, list(reversed(a))) > 0.0  # order matters


def test_universe_definition():
    assert is_common_stock("AAPL")
    assert not is_common_stock("ARBEW")     # warrant
    assert not is_common_stock("EONR.WS")   # unit/warrant dot
    assert not is_common_stock("BRK-B")     # dash class
    assert not is_common_stock("TQQQ", "ETF")


def test_feature_count_and_keys():
    v = build_vector(_bars(40), None, 9.5, t_et=600)
    assert sorted(v.keys()) == sorted(FEATURES) and len(v) == 28
