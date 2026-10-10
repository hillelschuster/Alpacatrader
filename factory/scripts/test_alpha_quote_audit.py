"""Prevent future quote use and 100x historical depth errors in money estimates."""

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_quote_audit import asof_quote, clock_us


def quotes(stamps, bids, asks, sizes, conditions=None):
    return pl.DataFrame(
        {
            "ts_utc": pl.Series(stamps).cast(pl.Datetime("us", "UTC")),
            "bid_price": bids,
            "ask_price": asks,
            "bid_size": sizes,
            "ask_size": sizes,
            "conditions": conditions or [["R"] for _ in stamps],
        }
    )


def test_asof_does_not_use_the_next_quote_even_if_it_has_a_better_price():
    t = clock_us("2023-03-01", 600, 250)
    frame = quotes([t - 100_000, t + 1], [10.0, 20.0], [10.01, 20.01], [5.0, 5.0])
    q, status = asof_quote(frame, t, "2023-03-01")
    assert status == "quoted"
    assert (q["bid"], q["ask"], q["quote_us"]) == (10.0, 10.01, t - 100_000)


def test_round_lot_boundary_preserves_economic_share_depth():
    old_t, new_t = clock_us("2025-10-31", 600, 0), clock_us("2025-11-03", 600, 0)
    old, _ = asof_quote(quotes([old_t], [10.0], [10.01], [5.0]), old_t, "2025-10-31")
    new, _ = asof_quote(quotes([new_t], [10.0], [10.01], [500.0]), new_t, "2025-11-03")
    assert old["ask_shares"] == new["ask_shares"] == 500
    assert old["bid_shares"] == new["bid_shares"] == 500


def test_invalid_latest_quote_cannot_fall_back_to_old_regular_depth():
    t = clock_us("2023-03-01", 600, 250)
    frame = quotes([t - 100_000, t], [10.0, 0.0], [10.01, 0.0], [5.0, 0.0])
    q, status = asof_quote(frame, t, "2023-03-01")
    assert q is None and status == "invalid_or_crossed_quote"
    aged = quotes([t - 3_000_000], [10.0], [10.01], [5.0])
    q, status = asof_quote(aged, t, "2023-03-01")
    assert q is None and status == "stale_quote"
