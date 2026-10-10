"""Subsecond chronology guards: no future flow features or favorable halt exit choice."""

import copy
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_micro_core import (
    LATENCY_US,
    admissions,
    coverage_is_complete,
    coverage_kind,
    coverage_unknown_reason,
    evaluate,
    execution,
    load_day,
    states,
)
from alpha_quote_audit import clock_us


def stream():
    start = clock_us("2021-03-01", 575, 0)
    stamps = np.arange(start - 60_000_000, start + 180_000_000, 100_000)
    px = 10 + np.arange(len(stamps)) * 0.0001
    quotes = {
        "ts": stamps - 1,
        "bid": px - 0.001,
        "ask": px + 0.001,
        "bs": np.full(len(stamps), 1000.0),
        "az": np.full(len(stamps), 1000.0),
        "regular": np.ones(len(stamps), dtype=bool),
        "valid": np.ones(len(stamps), dtype=bool),
    }
    return {
        "admit_t": 575,
        "trades": {"ts": stamps, "px": px, "size": np.full(len(stamps), 100.0)},
        "quotes": quotes,
    }


def test_future_ticks_and_quotes_cannot_change_current_signal_state():
    original = stream()
    changed = copy.deepcopy(original)
    target = clock_us("2021-03-01", 576, 0)
    for field in ("px", "size"):
        a = changed["trades"]
        a[field][a["ts"] >= target] *= 10
    for field in ("bid", "ask", "bs", "az"):
        a = changed["quotes"]
        a[field][a["ts"] >= target] *= 10
    before = states("2021-03-01", "XYZ", original, 959)
    after = states("2021-03-01", "XYZ", changed, 959)
    b = before.filter(before["signal_us"] == target).row(0, named=True)
    a = after.filter(after["signal_us"] == target).row(0, named=True)
    assert a == b
    e1 = execution("2021-03-01", "XYZ", target, original, 959, 60)
    e2 = execution("2021-03-01", "XYZ", target, changed, 959, 60)
    assert e2["entry_open"] == pytest.approx(10 * e1["entry_open"])


def test_depth_columns_are_past_only_displayed_depth():
    """bid_shares/depth_imbalance are strictly-prior NBBO quantities, never future."""
    start = clock_us("2021-03-01", 575, 0)
    stamps = np.array([start - 5_000_000, start + 2_000_000], dtype=np.int64)
    px = np.array([10.0, 10.0])
    quotes = {
        "ts": stamps,
        "bid": px - 0.001,
        "ask": px + 0.001,
        "bs": np.array([2000.0, 1000.0]),
        "az": np.array([1000.0, 2000.0]),
        "regular": np.ones(2, dtype=bool),
        "valid": np.ones(2, dtype=bool),
    }
    frame = states(
        "2021-03-01",
        "XYZ",
        {
            "admit_t": 575,
            "trades": {
                "ts": np.array([start], dtype=np.int64),
                "px": np.array([10.0]),
                "size": np.array([100.0]),
            },
            "quotes": quotes,
        },
        959,
    )
    row = frame.filter(frame["signal_us"] == start).row(0, named=True)
    # At signal_us the strictly-prior quote is the earlier one; the later quote
    # is still in the future and must not contribute.
    assert row["bid_shares"] == pytest.approx(2000.0)
    assert row["ask_shares"] == pytest.approx(1000.0)
    assert row["depth_imbalance"] == pytest.approx((2000.0 - 1000.0) / 3000.0)
    # A future-only depth change must not move the past-only depth columns.
    quotes["bs"] = np.array([2000.0, 5000.0])
    quotes["az"] = np.array([1000.0, 9000.0])
    frame2 = states(
        "2021-03-01",
        "XYZ",
        {
            "admit_t": 575,
            "trades": {
                "ts": np.array([start], dtype=np.int64),
                "px": np.array([10.0]),
                "size": np.array([100.0]),
            },
            "quotes": quotes,
        },
        959,
    )
    same = frame2.filter(frame2["signal_us"] == start).row(0, named=True)
    assert same["bid_shares"] == row["bid_shares"]
    assert same["ask_shares"] == row["ask_shares"]
    assert same["depth_imbalance"] == row["depth_imbalance"]


def test_zero_displayed_depth_cannot_divide():
    start = clock_us("2021-03-01", 575, 0)
    stamps = np.array([start], dtype=np.int64)
    quotes = {
        "ts": stamps - 1,
        "bid": np.array([10.0]),
        "ask": np.array([10.01]),
        "bs": np.array([0.0]),
        "az": np.array([0.0]),
        "regular": np.ones(1, dtype=bool),
        "valid": np.ones(1, dtype=bool),
    }
    frame = states(
        "2021-03-01",
        "XYZ",
        {
            "admit_t": 575,
            "trades": {"ts": stamps, "px": np.array([10.0]), "size": np.array([100.0])},
            "quotes": quotes,
        },
        959,
    )
    row = frame.row(0, named=True)
    assert row["depth_imbalance"] == 0.0
    assert np.isfinite(row["depth_imbalance"])


def test_pending_exit_uses_first_regular_quote_not_a_later_recovery():
    target = clock_us("2021-03-01", 576, 0)
    exit_target = target + LATENCY_US + 60_000_000
    q = {
        "ts": np.array([target - 1, exit_target + 1_000_000, exit_target + 2_000_000]),
        "bid": np.array([10.0, 9.0, 12.0]),
        "ask": np.array([10.01, 9.01, 12.01]),
        "bs": np.full(3, 1000.0),
        "az": np.full(3, 1000.0),
        "valid": np.ones(3, dtype=bool),
        "regular": np.ones(3, dtype=bool),
    }
    e = execution("2021-03-01", "XYZ", target, {"quotes": q}, 959, 60)
    assert e["exit_us"] == exit_target + 1_000_000
    assert e["exit_bid"] == 9
    assert e["gross_60"] == pytest.approx(9 / 10.01 - 1)
    assert e["horizon_seconds"] == 60


def test_horizon_keys_follow_the_declared_horizon():
    """Required horizon_seconds: exit target, keys and replay all move with it."""
    s = stream()
    start = clock_us("2021-03-01", 576, 0)
    e5 = execution("2021-03-01", "XYZ", start, s, 959, 5)
    e15 = execution("2021-03-01", "XYZ", start, s, 959, 15)
    assert e5["horizon_seconds"] == 5 and e15["horizon_seconds"] == 15
    assert e5["exit_target_us"] - e5["entry_et"] == 5_000_000
    assert e15["exit_target_us"] - e15["entry_et"] == 15_000_000
    assert "gross_5" in e5 and "exit_et_5" in e5 and "gross_15" not in e5
    assert "gross_15" in e15 and "exit_et_15" in e15 and "gross_5" not in e15
    assert e5["gross_5"] != e15["gross_15"]
    # No default horizon: a caller must state it explicitly.
    with pytest.raises(TypeError):
        execution("2021-03-01", "XYZ", start, s, 959)


def test_evaluate_moves_the_shared_replay_horizon():
    rows = [
        {
            "day": "2021-03-01",
            "ticker": "XYZ",
            "t": 576,
            "score": 0.0,
            "entry_et": 0,
            "entry_open": 10.0,
            "entry_status": "filled_proxy",
            "session_end": 1,
            "exit_target_us": 0,
            "exit_us": 1,
            "exit_et_5": 1,
            "gross_5": 0.01,
            "exit_et_15": 1,
            "gross_15": 0.02,
            "exit_et_60": 1,
            "gross_60": 0.03,
            "horizon_seconds": 5,
        }
    ]
    m5, _ = evaluate(rows, ["2021-03-01"], 150, 5)
    m15, _ = evaluate(rows, ["2021-03-01"], 150, 15)
    m60, _ = evaluate(rows, ["2021-03-01"], 150, 60)
    assert m5["horizon"] == 5 and m15["horizon"] == 15 and m60["horizon"] == 60
    # The declared horizon selects the gross key, so the fill outcome moves with it.
    assert m5["known_fills"] == m15["known_fills"] == m60["known_fills"] == 1
    assert m5["mean_net_known_fill"] < m15["mean_net_known_fill"] < m60["mean_net_known_fill"]
    assert m60["daily"][0]["lower_bound_pnl"] == pytest.approx(250 * m60["mean_net_known_fill"])
    # No default horizon: the caller must state it.
    with pytest.raises(TypeError):
        evaluate(rows, ["2021-03-01"], 150)


def test_locked_nbbo_does_not_invent_buy_aggression():
    s = stream()
    s["quotes"]["ask"] = s["quotes"]["bid"].copy()
    f = states("2021-03-01", "XYZ", s, 959)
    assert f["classified_dv60s"].max() == 0
    assert f["imbalance60s"].max() == 0


def test_no_signals_preserves_cash_and_calendar():
    m, _ = evaluate([], ["2021-03-01", "2021-03-02"], 150, 60)
    assert m["mean_daily_lower_bound"] == 0
    assert [r["lower_bound_pnl"] for r in m["daily"]] == [0, 0]


def _write_day(
    root: Path,
    day: str,
    quotes: list[dict],
    trades: list[dict],
    top: list[dict] | None = None,
) -> None:
    (root / "sip" / "candidates").mkdir(parents=True, exist_ok=True)
    (root / "sip" / "net" / "trades").mkdir(parents=True, exist_ok=True)
    (root / "sip" / "net" / "quotes").mkdir(parents=True, exist_ok=True)
    if top is None:
        top = [
            {"symbol": q["symbol"], "score": 0.2, "px_575": q["bid_price"], "px_575_et": 575}
            for q in quotes
        ]
    (root / "sip" / "candidates" / f"{day}.json").write_text(
        json.dumps({"snapshots": [{"T": 575, "pop": "B", "top": top}]})
    )
    pl.DataFrame(quotes).write_parquet(root / "sip" / "net" / "quotes" / f"{day}.parquet")
    pl.DataFrame(trades).write_parquet(root / "sip" / "net" / "trades" / f"{day}.parquet")


def _toy_quote(num: int, cond: str, symbol: str = "XYZ") -> dict:
    ts_us = clock_us("2021-03-01", 575, 0) + num * 1_000_000
    return {
        "symbol": symbol,
        "ts_utc": ts_us,
        "bid_price": 10.0,
        "ask_price": 10.01,
        "bid_size": 1000.0,
        "ask_size": 1000.0,
        "bid_exchange": "P",
        "ask_exchange": "P",
        "conditions": [cond],
        "tape": "C",
    }


def _toy_trade(num: int, symbol: str = "XYZ") -> dict:
    return {
        "symbol": symbol,
        "ts_utc": clock_us("2021-03-01", 575, 0) + num * 1_000_000,
        "price": 10.0,
        "size": 100.0,
        "conditions": ["@"],
        "tape": "A",
    }


def test_unknown_quote_conditions_are_reported_never_booked_as_cash(tmp_path):
    """A '?' quote flag is data-UNKNOWN, so the day is never 'complete' cash."""
    quotes = [_toy_quote(n, "?") for n in range(0, 300)] + [
        _toy_quote(n, "R") for n in range(300, 600)
    ]
    trades = [_toy_trade(n) for n in range(0, 600)]
    _write_day(tmp_path, "2021-03-01", quotes, trades)
    streams, cov = load_day(tmp_path, "2021-03-01")
    assert cov["watch_names"] == 1 and cov["covered_names"] == 1
    assert cov["quote_events"] == 600
    # The unknown flag counts the symbol; it is not a generic provider failure.
    assert cov["quote_condition_unknown_symbols"] == 1
    # Some regular quotes exist, so this is not a no-regular-quote day.
    assert cov["no_regular_quote_symbols"] == 0
    kind = coverage_kind(
        cov["watch_names"],
        cov.get("missing_symbol_streams", []),
        cov.get("missing_day_file", False),
        cov["no_regular_quote_symbols"],
        cov["quote_condition_unknown_symbols"],
    )
    assert kind == "quote_condition_unknown"
    assert not coverage_is_complete(kind)
    reason = coverage_unknown_reason({**cov, "watch_names": cov["watch_names"]})
    assert reason == "quote_condition_unknown"


def test_all_unknown_conditions_are_quote_condition_unknown_not_cash(tmp_path):
    quotes = [_toy_quote(n, "?") for n in range(0, 600)]
    trades = [_toy_trade(n) for n in range(0, 600)]
    _write_day(tmp_path, "2021-03-01", quotes, trades)
    streams, cov = load_day(tmp_path, "2021-03-01")
    assert cov["quote_condition_unknown_symbols"] == 1
    assert cov["no_regular_quote_symbols"] == 1
    kind = coverage_kind(
        cov["watch_names"],
        [],
        cov.get("missing_day_file", False),
        cov["no_regular_quote_symbols"],
        cov["quote_condition_unknown_symbols"],
    )
    assert kind == "quote_condition_unknown"
    assert not coverage_is_complete(kind)
    # The stream keeps the invalid quotes; no fallback to an older favourable quote.
    q = streams["XYZ"]["quotes"]
    assert q["ts"].size == 600 and not q["valid"].any()


def test_known_non_regular_only_quotes_are_regular_absent_not_unknown(tmp_path):
    """O/Y style availability marks are data-KNOWN; absence of R is regular-absent."""
    quotes = [_toy_quote(n, "O") for n in range(0, 300)] + [
        _toy_quote(n, "Y") for n in range(300, 600)
    ]
    trades = [_toy_trade(n) for n in range(0, 600)]
    _write_day(tmp_path, "2021-03-01", quotes, trades)
    streams, cov = load_day(tmp_path, "2021-03-01")
    assert cov["quote_condition_unknown_symbols"] == 0
    assert cov["no_regular_quote_symbols"] == 1
    kind = coverage_kind(
        cov["watch_names"],
        [],
        cov.get("missing_day_file", False),
        cov["no_regular_quote_symbols"],
        cov["quote_condition_unknown_symbols"],
    )
    assert kind == "no_regular_quotes"
    assert not coverage_is_complete(kind)
    reason = coverage_unknown_reason({**cov, "watch_names": 1})
    assert reason == "no_regular_quotes"


def test_missing_day_file_is_unknown_when_names_are_watched(tmp_path):
    quotes = [_toy_quote(n, "R") for n in range(0, 600)]
    trades = [_toy_trade(n) for n in range(0, 600)]
    _write_day(tmp_path, "2021-03-01", quotes, trades)
    (tmp_path / "sip" / "net" / "quotes" / "2021-03-01.parquet").unlink()
    streams, cov = load_day(tmp_path, "2021-03-01")
    assert cov["missing_day_file"] is True
    assert not coverage_is_complete(coverage_kind(1, [], True, 0, 0))


def test_no_watch_names_day_is_legitimate_cash(tmp_path):
    quotes = [_toy_quote(n, "R") for n in range(0, 600)]
    trades = [_toy_trade(n) for n in range(0, 600)]
    _write_day(tmp_path, "2021-03-01", quotes, trades)
    (tmp_path / "sip" / "candidates" / "2021-03-01.json").write_text(
        json.dumps({"snapshots": []})
    )
    streams, cov = load_day(tmp_path, "2021-03-01")
    assert cov["watch_names"] == 0
    assert coverage_kind(0, [], False, 0, 0) == "no_watch_names"
    assert coverage_is_complete("no_watch_names")


def test_missing_watched_symbol_stream_is_unknown(tmp_path):
    quotes = [_toy_quote(n, "R") for n in range(0, 600)]
    trades = [_toy_trade(n) for n in range(0, 600)]
    _write_day(tmp_path, "2021-03-01", quotes, trades)
    (tmp_path / "sip" / "candidates" / "2021-03-01.json").write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "T": 575,
                        "pop": "B",
                        "top": [
                            {"symbol": "XYZ", "score": 0.2, "px_575": 10.0, "px_575_et": 575},
                            {"symbol": "ABC", "score": 0.3, "px_575": 20.0, "px_575_et": 575},
                        ],
                    }
                ]
            }
        )
    )
    streams, cov = load_day(tmp_path, "2021-03-01")
    assert cov["watch_names"] == 2
    assert cov["missing_symbol_streams"] == ["ABC"]
    kind = coverage_kind(
        cov["watch_names"], cov["missing_symbol_streams"], cov.get("missing_day_file", False), 0, 0
    )
    assert kind == "missing_symbol_streams"
    assert not coverage_is_complete(kind)


# A snapshot whose ranks 4-10 the legacy top-three default never admits. Ranks 1-3
# carry a $10 print (inside the top-3 policy); rank 4 carries the $1 floor of the
# wider universe, so nothing but the rank cut keeps it out of the legacy default.
_WATCH_TOP = [
    {"symbol": "AAA", "score": 0.2, "px_575": 10.0, "px_575_et": 575},
    {"symbol": "BBB", "score": 0.2, "px_575": 10.0, "px_575_et": 575},
    {"symbol": "CCC", "score": 0.2, "px_575": 10.0, "px_575_et": 575},
    {"symbol": "DDD", "score": 0.2, "px_575": 1.0, "px_575_et": 575},
]


def test_explicit_watch_admits_rank_four_the_top3_default_does_not(tmp_path):
    """The loader's watch is a real policy parameter: an explicitly declared wider
    universe loads rank-4 names the legacy top-three default never admits, from the
    very same raw day."""
    quotes = [_toy_quote(n, "R", s["symbol"]) for s in _WATCH_TOP for n in range(0, 30)]
    trades = [_toy_trade(n, s["symbol"]) for s in _WATCH_TOP for n in range(0, 30)]
    _write_day(tmp_path, "2021-03-01", quotes, trades, top=_WATCH_TOP)

    legacy, legacy_cov = load_day(tmp_path, "2021-03-01")
    assert set(legacy) == {"AAA", "BBB", "CCC"}
    assert legacy_cov["watch_names"] == 3

    watch = {r["symbol"]: 575 for r in _WATCH_TOP}
    streams, cov = load_day(tmp_path, "2021-03-01", watch=watch)
    assert set(streams) == {"AAA", "BBB", "CCC", "DDD"}
    assert cov["watch_names"] == 4 and cov["covered_names"] == 4
    # the rank-4 stream is genuinely loaded, not a placeholder, and its state grid
    # starts at the admission minute the caller declared
    assert streams["DDD"]["admit_t"] == 575
    assert streams["DDD"]["quotes"]["ts"].size == 30
    assert streams["DDD"]["trades"]["px"].size == 30
    # the same day under the legacy default: the rank-4 name is simply not requested
    assert "DDD" not in admissions(tmp_path, "2021-03-01")


def test_coverage_counts_requested_watch_names_not_the_covered_intersection(tmp_path):
    """watch_names is the REQUESTED universe: a name whose raw streams are missing
    shrinks covered_names and stays data-UNKNOWN; it never shrinks the
    denominator, and the survived-intersection count is not the candidate count."""
    present = [r["symbol"] for r in _WATCH_TOP[:3]]
    quotes = [_toy_quote(n, "R", s) for s in present for n in range(0, 30)]
    trades = [_toy_trade(n, s) for s in present for n in range(0, 30)]
    _write_day(tmp_path, "2021-03-01", quotes, trades, top=_WATCH_TOP)

    watch = {r["symbol"]: 575 for r in _WATCH_TOP}
    streams, cov = load_day(tmp_path, "2021-03-01", watch=watch)
    assert set(streams) == set(present)
    assert cov["watch_names"] == 4
    assert cov["covered_names"] == 3
    assert cov["missing_symbol_streams"] == ["DDD"]
    kind = coverage_kind(
        cov["watch_names"],
        cov["missing_symbol_streams"],
        cov.get("missing_day_file", False),
        cov["no_regular_quote_symbols"],
        cov["quote_condition_unknown_symbols"],
    )
    assert kind == "missing_symbol_streams"
    assert not coverage_is_complete(kind)
    assert coverage_unknown_reason(cov) == "missing_symbol_streams"


def test_coverage_unknown_day_takes_full_book_lower_bound_not_cash():
    """The whole-portfolio bound: an unknown day is -100% of the book, never 0 cash."""
    import alpha_micro_flow as flow

    rows = [
        {
            "day": "2021-03-01",
            "ticker": "AAA",
            "t": 576,
            "score": 0.0,
            "entry_et": 0,
            "entry_open": 10.0,
            "entry_status": "filled_proxy",
            "session_end": 1,
            "exit_target_us": 0,
            "exit_us": 1,
            "exit_et_60": 1,
            "gross_60": 0.01,
            "horizon_seconds": 60,
        },
        {
            "day": "2021-03-02",
            "ticker": "BBB",
            "t": 576,
            "score": 0.0,
            "entry_et": 0,
            "entry_open": 10.0,
            "entry_status": "filled_proxy",
            "session_end": 1,
            "exit_target_us": 0,
            "exit_us": 1,
            "exit_et_60": 1,
            "gross_60": 0.01,
            "horizon_seconds": 60,
        },
    ]
    metrics, _ = evaluate(rows, ["2021-03-01", "2021-03-02"], 150, 60)
    coverage = {
        "2021-03-01": {
            "complete": False,
            "coverage_kind": "quote_condition_unknown",
            "unknown_reason": "quote_condition_unknown",
        },
        "2021-03-02": {"complete": True, "coverage_kind": "complete"},
    }
    out = flow.adjust_coverage(metrics, ["2021-03-01", "2021-03-02"], coverage)
    by_day = {r["day"]: r for r in out["daily"]}
    assert by_day["2021-03-01"]["lower_bound_return"] == -1.0
    assert by_day["2021-03-01"]["lower_bound_pnl"] == -750.0
    assert by_day["2021-03-01"]["coverage_unknown"] is True
    assert out["coverage_unknown_days"] == 1
    assert out["coverage_unknown_day_list"] == ["2021-03-01"]
    # The unknown day never joins the reported-known mean of complete days.
    assert out["known_complete_days"] == 1
    assert out["mean_daily_lower_bound_known_complete_days"] == pytest.approx(
        metrics["daily"][1]["lower_bound_return"]
    )
    assert out["mean_daily_lower_bound"] == pytest.approx(
        (metrics["daily"][0]["lower_bound_return"] + -1.0) / 2
    )


def test_reclaim_covers_unknown_reasons_without_booking_cash():
    """The reclaim label vocabulary keeps the 58 '?' days unknown, reason-aligned."""
    import alpha_micro_reclaim as reclaim

    unknown_q = {
        "watch_names": 7,
        "covered_names": 7,
        "missing_day_file": False,
        "no_regular_quote_symbols": 7,
        "quote_condition_unknown_symbols": 7,
        "names_with_firm_quotes": [],
    }
    assert reclaim._day_coverage_labels(unknown_q) == ("no_firm_quotes", "quote_condition_unknown")
    assert reclaim._day_coverage_labels(unknown_q)[0] != "complete"
    known_absent = dict(unknown_q, quote_condition_unknown_symbols=0)
    assert reclaim._day_coverage_labels(known_absent) == ("no_firm_quotes", "quote_regular_absent")
    partial = dict(unknown_q, covered_names=5, missing_symbol_streams=["ABC"])
    assert reclaim._day_coverage_labels(partial)[0] == "partial_streams"
    assert reclaim._day_coverage_labels(partial)[1] == "quote_condition_unknown"
    missing = dict(unknown_q, missing_day_file=True)
    assert reclaim._day_coverage_labels(missing) == ("missing_day_file", "missing_day_file")
    # Cash stays legitimate only for real no-watch days / coded no-signal days.
    assert reclaim._day_coverage_labels({"watch_names": 0}) == ("no_watch_names", None)
    complete = {
        "watch_names": 4,
        "covered_names": 4,
        "missing_day_file": False,
        "no_regular_quote_symbols": 0,
        "quote_condition_unknown_symbols": 0,
        "names_with_firm_quotes": ["AAA", "BBB"],
    }
    assert reclaim._day_coverage_labels(complete) == ("complete", None)
    for label, _ in (
        reclaim._day_coverage_labels(unknown_q),
        reclaim._day_coverage_labels(missing),
        reclaim._day_coverage_labels(partial),
    ):
        assert label not in ("no_watch_names", "complete")
