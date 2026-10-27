"""Regression tests for the depth-universe producer's quote read path and guards.

The defect these pin (2026-10-27, P1): ``build_day_cells`` passed the *staging root* to
``quote_day_frame`` as a supplement root, but ``alpha_quote_universe_acquire.merge_day``
writes ``<root>/quotes/<day>.parquet`` while ``supplement_day_path`` resolves a root as
``<root>/<day>.parquet``. Every pair this study acquired was therefore invisible to the very
run that acquired it, and its intents were recorded as ``quote_not_acquired``.

Stubbed frames only: no network, no credentials, no corpus file.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")

import alpha_depth_universe as adu  # noqa: E402
from alpha_quote_audit import clock_us  # noqa: E402
from alpha_quote_aware_frequency import quote_day_frame, symbol_quotes  # noqa: E402
from alpha_sparse_execution_frontier import QUOTE_SCHEMA_LOCAL  # noqa: E402
from alpha_sparse_quote_service import supplement_day_path  # noqa: E402

DAY = "2023-04-24"
TICKER = "INAB"


def _quote_frame(day: str) -> pl.DataFrame:
    stamps = [
        datetime.fromtimestamp(clock_us(day, minute, 0) / 1e6, tz=UTC) for minute in (680, 681)
    ]
    return pl.DataFrame(
        {
            "symbol": [TICKER, TICKER],
            "ts_utc": stamps,
            "bid_price": [2.80, 2.82],
            "bid_size": [900.0, 900.0],
            "ask_price": [2.85, 2.86],
            "ask_size": [800.0, 800.0],
            "bid_exchange": ["V", "V"],
            "ask_exchange": ["V", "V"],
            "conditions": [["R"], ["R"]],
            "tape": ["C", "C"],
        },
        schema=QUOTE_SCHEMA_LOCAL,
    )


@pytest.fixture()
def staged(tmp_path: Path) -> tuple[Path, Path]:
    """A staging day laid out exactly as ``merge_day`` leaves it."""
    quotes_root = tmp_path / "staging" / DAY / "quotes"
    merged = quotes_root / "quotes" / f"{DAY}.parquet"
    merged.parent.mkdir(parents=True)
    _quote_frame(DAY).write_parquet(merged)
    return quotes_root, merged


def test_quote_roots_points_at_the_merged_directory(staged):
    quotes_root, merged = staged
    roots = adu.quote_roots(quotes_root)
    # the study's own acquisition is the LAST supplement, i.e. the weakest precedence
    assert roots[-1] == quotes_root / "quotes"
    assert tuple(roots[: len(adu.frozen.SUPPLEMENT_ROOTS)]) == tuple(adu.frozen.SUPPLEMENT_ROOTS)
    # and that root resolves to the file merge_day actually wrote
    assert supplement_day_path(roots[-1], DAY) == merged
    assert merged.exists()


def test_the_old_wiring_could_not_have_read_the_file(staged):
    """Pin the defect: the bare staging root resolves to a file that does not exist."""
    quotes_root, merged = staged
    assert not supplement_day_path(quotes_root, DAY).exists()
    assert supplement_day_path(quotes_root, DAY) != merged


def test_freshly_acquired_quote_is_read_and_reaches_the_intent(staged, tmp_path):
    quotes_root, _ = staged
    empty_data_root = tmp_path / "no_ranked_cache"
    frame = quote_day_frame(empty_data_root, DAY, {TICKER}, adu.quote_roots(quotes_root))
    assert frame is not None and frame.height == 2
    assert set(frame["symbol"].to_list()) == {TICKER}

    streams = symbol_quotes(frame, DAY)
    assert TICKER in streams

    intent_us = clock_us(DAY, 681, 0)
    facts = adu.frozen.intent_facts(streams[TICKER], intent_us)
    assert facts["intent_status"] == "quoted"
    # the latest RAW print at the clock is the 681 one
    assert facts["intent_ask"] == pytest.approx(2.86)
    assert facts["intent_bid"] == pytest.approx(2.82)
    assert facts["intent_reason"] == "eligible"


def test_protected_dates_are_refused():
    for day in ("2024-03-01", "2025-01-15", "2026-06-02", "2026-07-15", "2026-08-31"):
        with pytest.raises(ValueError):
            adu.guard_day(day)
    for day in ("2023-05-15", "2025-06-10", "2026-05-29"):
        adu.guard_day(day)
