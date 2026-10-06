#!/usr/bin/env python
"""Behavioural regression tests for the ENTRY-EV-01 Stage-A readout.

These cover day/value alignment across null filtering, five-observation versus
five-day sensitivity, empty/small populations, and keeping retrospective labels
from filtering the observable proxy population.

Everything below runs on hand-built tiny frames -- no market data is read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from entry_ev.stage_a_read import (  # noqa: E402
    build_readout,
    event_stats,
)

H = 5


def frame(rows: list[tuple[str, float | None]]) -> pl.DataFrame:
    """(day, fwd_ret_5) rows; nulls are preserved as nulls."""
    return pl.DataFrame({"day": [r[0] for r in rows],
                         "fwd_ret_5": [r[1] for r in rows]}, schema={"day": pl.Utf8,
                                                                  "fwd_ret_5": pl.Float64})


def test_day_key_travels_with_its_value_across_null_filtering():
    # Values in row order: 0.10, 0.00, 0.02 (the null between them is dropped).
    sub = frame([("d1", 0.10), ("d1", None), ("d2", 0.00), ("d2", 0.02)])
    e = event_stats(sub, H)
    db = e["day_balanced"]
    assert e["n_events"] == 3 and e["n_days"] == 2
    # d1 keeps 0.10 alone; d2 keeps 0.00 and 0.02 -> mean of {0.10, 0.01}
    assert db["n_days"] == 2
    assert db["mean_day_balanced"] == 0.055
    assert db["best_day_mean"] == 0.10
    assert db["worst_day_mean"] == 0.01


def test_top5_day_excludes_five_DAYS_not_five_events():
    # One high day owning 10 identical events, five flat days with a single event each.
    rows = [("hot", 1.0)] * 10 + [(f"flat{i}", 0.0) for i in range(5)]
    e = event_stats(frame(rows), H)
    db = e["day_balanced"]
    assert e["n_events"] == 15
    assert e["top5_event_sum"] == 5.0                 # five EVENTS of the hot day
    assert e["top5_event_sum_share"] == 0.5
    # day scale: hot (1.0) + four flat days dropped -> one flat day left at 0.0
    assert db["n_days"] == 6
    assert db["top5_day_sum"] == 1.0
    assert db["mean_excl_top5_days"] == 0.0
    assert db["mean_day_balanced"] == 1.0 / 6.0


def test_event_exclusion_boundary_fewer_or_equal_five_events():
    e = event_stats(frame([("d1", 1.0), ("d1", 2.0), ("d2", -1.0)]), H)
    assert e["n_events"] == 3
    assert e["mean_excl_top5_events"] is None
    assert e["top5_event_sum_share"] == 2.0 / 2.0
    # five events exactly is still the boundary -> None, not a zero denominator
    five = frame([(f"d{i}", 0.1 * i) for i in range(5)])
    assert event_stats(five, H)["mean_excl_top5_events"] is None


def test_day_exclusion_boundary_fewer_than_five_days():
    sub = frame([("d1", 1.0), ("d2", 2.0), ("d3", 3.0), ("d4", 4.0)])
    db = event_stats(sub, H)["day_balanced"]
    assert db["n_days"] == 4
    assert db["mean_excl_top5_days"] is None
    assert db["top5_day_sum_share"] == 1.0  # every day is in the "top" five


def test_null_only_region_reports_empty_instead_of_dividing_by_zero():
    e = event_stats(frame([("d1", None), ("d2", None)]), H)
    assert e == {"n_events": 0, "n_days": 0, "day_balanced": {"n_days": 0}}


def test_degenerate_zero_sum_yields_null_share_not_a_crash():
    sub = frame([(f"d{i}", 0.0) for i in range(8)])
    e = event_stats(sub, H)
    assert e["exact_zero_return_share"] == 1.0
    assert e["top5_event_sum_share"] is None
    assert e["mean_excl_top5_events"] == 0.0
    assert e["day_balanced"]["top5_day_sum_share"] is None


def test_exact_zero_share_counts_non_null_events_only():
    sub = frame([("d1", 0.0), ("d1", None), ("d2", 0.5)])
    assert event_stats(sub, H)["exact_zero_return_share"] == 0.5


def test_readout_namespaces_stay_separate_and_state_retrospective_provenance():
    rows = []
    for t in range(4):
        rows.append({"day": "d1", "t": 600 + t, "ticker": "AAA", "rank_known": 7,
                     "gain": 0.5, "px": 10.0, "ret1": 0.01, "ret3": 0.02, "ret5": 0.03,
                     "ret15": 0.04, "dd_from_high": -0.01, "promo_age": -2 if t == 0 else None,
                     "vol30_ratio": 0.4, **{f"fwd_ret_{h}": 0.001 for h in (1, 3, 5, 10, 15, 30, 60)}})
    rows.append({"day": "d1", "t": 604, "ticker": "BBB", "rank_known": 5, "gain": 0.9,
                 "px": 10.0, "ret1": 0.01, "ret3": 0.02, "ret5": 0.03, "ret15": 0.04,
                 "dd_from_high": 0.0, "promo_age": 1, "vol30_ratio": 1.0,
                 **{f"fwd_ret_{h}": 0.002 for h in (1, 3, 5, 10, 15, 30, 60)}})
    out = build_readout(pl.DataFrame(rows))
    # the retroactively-labelled row (promo_age -2) is anatomy only; it is NOT removed from
    # the rank 6-10 proxy population, and the null promo_age rows stay in it as well
    assert out["hindsight"]["promo_age_-5..0"]["n_rows"] == 1
    assert out["causal"]["rank6-10_all"]["n_rows"] == 4  # 3 never-promoted (promo_age null) + 1 pre-promotion
    assert out["causal"]["rank6-10_ret5>0"]["n_rows"] == 4  # retro rows stay in the proxy pool

