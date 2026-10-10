"""Consumer-visible edges of the sparse quote service + execution frontier.

These guard the contract other actors depend on: no future quote at any arrival
latency, UNKNOWN never silently becoming a zero outcome, integer fee-funded
quantities at the round-lot boundary, supplements never overwriting ranked
evidence, and the corroboration gate that is the only way a stale leg is priced.
"""

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_quote_audit import clock_us
from alpha_sparse_execution_frontier import (
    BASE_LATENCY_MS,
    LATENCIES,
    annual_block,
    case_stats,
    detect_missing_legs,
    fetch_missing_legs,
    price_cohort,
)
from alpha_sparse_quote_service import (
    NO_ORDER_STATUS,
    day_quote_path,
    load_day_quotes,
    quote_at,
    supported_round_trip,
)

DAY = "2025-02-04"


def frame(ticker, stamps, bid, ask, size, conds=None):
    return pl.DataFrame(
        {
            "symbol": [ticker] * len(stamps),
            "ts_utc": pl.Series(stamps).cast(pl.Datetime("us", "UTC")),
            "bid_price": bid,
            "bid_size": size,
            "ask_price": ask,
            "ask_size": size,
            "bid_exchange": ["K"] * len(stamps),
            "ask_exchange": ["Q"] * len(stamps),
            "conditions": conds or [["R"] for _ in stamps],
            "tape": ["C"] * len(stamps),
        }
    )


def trade_frame(ticker, stamps, cond="@"):
    return pl.DataFrame(
        {
            "symbol": [ticker] * len(stamps),
            "ts_utc": pl.Series(stamps).cast(pl.Datetime("us", "UTC")),
            "price": [4.0] * len(stamps),
            "size": [10.0] * len(stamps),
            "exchange": ["P"] * len(stamps),
            "conditions": [[cond]] * len(stamps),
            "trade_id": list(range(len(stamps))),
            "tape": ["C"] * len(stamps),
        }
    )


def write_day(root, day, frames, kind="quotes"):
    d = root / "sip" / "net" / kind
    d.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).sort("symbol", "ts_utc").write_parquet(d / f"{day}.parquet")


def steady(ticker, start_us, n, bid=10.0, ask=10.01, size=100.0):
    stamps = [start_us + i * 1_000_000 for i in range(n)]
    return frame(ticker, stamps, [bid] * n, [ask] * n, [size] * n)


def trades_rows(day, ticker_minutes, horizon=60):
    return [
        {
            "day": day,
            "ticker": tk,
            "t": m,
            "entry_et": m,
            "entry_open": 10.0,
            "exit_et": m + horizon,
            "horizon": horizon,
            "gross": 0.02,
            "net": 0.015,
            "cost_bps": 100.0,
            "order_budget": 1000.0,
            "score": 0.04,
            "status": "known_open_proxy",
            "exit_open_proxy": 10.2,
        }
        for tk, m in ticker_minutes
    ]


def price(trades, root, **kw):
    opts = {
        "sizes": (1000.0,),
        "residuals": (0.0,),
        "latencies": (BASE_LATENCY_MS,),
        "max_age_s": 2.0,
        "scenario_max_age_s": 15.0,
        "scenario_on": True,
    }
    opts.update(kw)
    return price_cohort("late143", trades, data_root=root, supplemental_root=None, **opts)[0]


@pytest.mark.parametrize("latency", LATENCIES)
def test_no_arrival_latency_can_use_a_future_quote(latency):
    t = clock_us(DAY, 600, latency)
    f = frame("AAA", [t - 100_000, t + 1], [10.0, 20.0], [10.01, 20.01], [5.0, 5.0])
    q, status = quote_at(f, t, 2.0)
    assert status == "quoted"
    assert (q["bid"], q["ask"], q["quote_us"]) == (10.0, 10.01, t - 100_000)


def test_stale_quote_is_unknown_never_a_zero_fill():
    t = clock_us(DAY, 600, 250)
    f = frame("AAA", [t - 3_000_000], [10.0], [10.01], [5.0])
    assert quote_at(f, t, 2.0) == (None, "stale_quote")


def test_integer_fee_funded_quantity_and_l1_capacity_unknown():
    t = clock_us(DAY, 600, 250)
    quote = {
        "ask": 10.0,
        "bid": 9.99,
        "ask_shares": 40,
        "bid_shares": 40,
        "age_s": 0.1,
        "quote_us": t,
        "spread_bps": 10.0,
    }
    rt = supported_round_trip(quote, quote, 1000.0, 0.0)
    assert rt["quantity"] == 100  # int(1000 // 10.00)
    assert rt["supported"] is False  # 100 shares > 40 displayed
    assert rt["unknown"] == "unknown_l1_capacity_entry_exit"
    assert rt["status"] == "unknown_l1_capacity"
    assert rt["net"] is not None  # measured, but flagged unsupported
    small = supported_round_trip(quote, quote, 250.0, 0.0)
    assert small["quantity"] == 25 and small["supported"] is True
    # residual cost is charged per side and shrinks the fee-funded quantity
    half = supported_round_trip(quote, quote, 250.0, 25.0)
    assert half["quantity"] == 24
    assert half["net"] < small["net"]


def test_quarter_budget_below_one_share_is_known_no_order_not_zero_fill():
    """TCGL 2026-01-30-like boundary: entry ASK 306.00 with a $250 budget funds
    ZERO whole shares (250 // 306.00 == 0). Pre-fix this priced as SUPPORTED
    with net 0.0, inflating n_l1_supported and diluting the covered mean; the
    honest reading is that NO order was ever sent, so the cash stays unfilled."""
    t = clock_us(DAY, 600, 250)
    entry = {
        "ask": 306.0,
        "bid": 305.9,
        "ask_shares": 100,
        "bid_shares": 100,
        "age_s": 0.1,
        "quote_us": t,
        "spread_bps": 3.3,
    }
    exit_ = {
        "ask": 306.1,
        "bid": 305.95,
        "ask_shares": 100,
        "bid_shares": 100,
        "age_s": 0.1,
        "quote_us": t,
        "spread_bps": 3.3,
    }
    for res in (0.0, 10.0, 25.0, 50.0):
        rt = supported_round_trip(entry, exit_, 250.0, res)
        assert rt["quantity"] == 0 and rt["exit_quantity"] == 0
        assert rt["status"] == NO_ORDER_STATUS == "known_no_order_min_capital"
        assert rt["supported"] is False
        assert rt["entry_supported"] is False and rt["exit_supported"] is False
        assert rt["unfilled_known_cash"] is True
        assert rt["unknown"] is None  # KNOWN, not UNKNOWN
        assert rt["net"] is None and rt["net_usd"] is None  # no priced return
        assert rt["entry_cost_usd"] == 0.0 and rt["exit_proceeds_usd"] == 0.0
    # The same quoted pair at a budget that funds shares is bit-for-bit the old
    # behaviour: the stock is never re-selected and no fractional share invented.
    bigger = supported_round_trip(entry, exit_, 1000.0, 0.0)
    assert bigger["quantity"] == 3  # int(1000 // 306.00)
    assert bigger["supported"] is True
    assert bigger["unfilled_known_cash"] is False
    assert bigger["net"] is not None


def test_exact_one_share_affordability_and_fee_funded_boundary():
    """$200.00 at a $200.00 ASK funds exactly one whole share; +50bps round-trip
    residual lifts the fee-funded entry price to 200.50 and drops the same
    budget to a known no-order (never a borrowed fraction)."""
    t = clock_us(DAY, 600, 250)
    entry = {
        "ask": 200.0,
        "bid": 199.9,
        "ask_shares": 100,
        "bid_shares": 100,
        "age_s": 0.1,
        "quote_us": t,
        "spread_bps": 5.0,
    }
    exit_ = {
        "ask": 200.1,
        "bid": 199.9,
        "ask_shares": 100,
        "bid_shares": 100,
        "age_s": 0.1,
        "quote_us": t,
        "spread_bps": 5.0,
    }
    one = supported_round_trip(entry, exit_, 200.0, 0.0)
    assert one["quantity"] == 1 and one["supported"] is True
    assert one["entry_cost_usd"] == 200.0  # fee-funded: <= budget
    assert one["unfilled_known_cash"] is False
    assert supported_round_trip(entry, exit_, 200.25, 0.0)["quantity"] == 1
    fees = supported_round_trip(entry, exit_, 200.25, 50.0)
    assert fees["quantity"] == 0  # 200.25 // 200.50 == 0
    assert fees["unfilled_known_cash"] is True
    assert fees["status"] == NO_ORDER_STATUS


def test_zero_displayed_depth_is_unknown_but_no_order_is_known_cash():
    """Both outcomes are unsupported, and they are NOT the same class: zero
    displayed depth with an affordable quantity is an UNKNOWN capacity
    shortfall (deeper book unmeasured), while a budget below one share is a
    KNOWN no-order (nothing was ordered, cash retained)."""
    t = clock_us(DAY, 600, 250)
    deep = {
        "ask": 10.0,
        "bid": 9.99,
        "ask_shares": 40,
        "bid_shares": 40,
        "age_s": 0.1,
        "quote_us": t,
        "spread_bps": 10.0,
    }
    shallow = dict(deep, ask_shares=0, bid_shares=0)
    unknown_rt = supported_round_trip(shallow, shallow, 1000.0, 0.0)
    assert unknown_rt["quantity"] == 100
    assert unknown_rt["supported"] is False
    assert unknown_rt["status"] == "unknown_l1_capacity"
    assert unknown_rt["unknown"] == "unknown_l1_capacity_entry_exit"
    assert unknown_rt["net"] is not None  # measured, flagged
    assert unknown_rt["unfilled_known_cash"] is False
    no_order_rt = supported_round_trip(deep, deep, 5.0, 0.0)
    assert no_order_rt["quantity"] == 0
    assert no_order_rt["status"] == NO_ORDER_STATUS
    assert no_order_rt["unknown"] is None
    assert no_order_rt["net"] is None
    assert no_order_rt["unfilled_known_cash"] is True


def test_round_lot_boundary_preserves_economic_depth():
    old_t = clock_us("2025-10-31", 600, 0)
    new_t = clock_us("2025-11-03", 600, 0)
    old, _ = quote_at(frame("AAA", [old_t], [10.0], [10.01], [5.0]), old_t, 2.0)
    new, _ = quote_at(frame("AAA", [new_t], [10.0], [10.01], [500.0]), new_t, 2.0)
    assert old["ask_shares"] == new["ask_shares"] == 500.0
    assert old["bid_shares"] == new["bid_shares"] == 500.0


def test_supplement_rows_merge_without_overwriting_ranked_evidence(tmp_path):
    t0 = clock_us(DAY, 600, 0)
    write_day(tmp_path, DAY, [frame("AAA", [t0 - 1_000_000], [10.0], [10.01], [7.0])])
    supp = tmp_path / "supp"
    supp.mkdir()
    # the supplement claims a different size for the SAME print: it must not win
    frame("AAA", [t0 - 1_000_000], [10.0], [10.01], [999.0]).write_parquet(supp / f"{DAY}.parquet")
    merged = load_day_quotes(tmp_path, DAY, {"AAA"}, supp)
    assert merged.height == 1 and merged["ask_size"][0] == 7.0
    # a supplement-only print is added, never dropped
    frame("AAA", [t0 + 10_000_000], [10.0], [10.02], [5.0]).write_parquet(supp / f"{DAY}.parquet")
    merged = load_day_quotes(tmp_path, DAY, {"AAA"}, supp)
    assert merged.height == 2
    assert merged["ask_size"].to_list() == [7.0, 5.0]
    assert merged["ts_utc"].is_sorted()


def test_missing_symbol_stream_is_precise_unknown_and_never_fetched_by_default(tmp_path):
    t0 = clock_us(DAY, 600, 0)
    write_day(tmp_path, DAY, [steady("AAA", t0, 60)])
    trades = trades_rows(DAY, [("ZZZ", 600)])
    legs = detect_missing_legs(trades, data_root=tmp_path, supplemental_root=None)
    assert [(leg["leg"], leg["ticker"]) for leg in legs] == [("entry", "ZZZ"), ("exit", "ZZZ")]
    rows = price(trades, tmp_path, sizes=(250.0, 1000.0))
    assert rows[0]["entry_status_250"] == "missing_symbol_stream"
    assert "missing_symbol_stream" in rows[0]["pair_status_250"]
    assert rows[0].get("p_net_250_1000_0") is None
    assert "p_q_250_1000_0" not in rows[0]  # unpriced legs emit no ladder cells
    # no legs means no request is built and no network path is entered
    assert fetch_missing_legs([], tmp_path / "unused", tmp_path / "no.env", 250, 0.0) == []


def _gap_day(root, minute_a=600, minute_b=660):
    """AAAA quotes every second; BBBB has a >2s gap before each arrival clock."""
    t0 = clock_us(DAY, minute_a, 0)
    exit_clock = clock_us(DAY, minute_b, 250)
    aaaa = steady("AAAA", t0 - 2_000_000, (minute_b - minute_a) * 60 + 5)
    bbbb = frame(
        "BBBB",
        [t0 - 3_000_000, exit_clock - 1_000_000, exit_clock],
        [4.0, 4.0, 4.0],
        [4.02, 4.02, 4.02],
        [100.0, 100.0, 100.0],
    )
    write_day(root, DAY, [aaaa, bbbb])
    return t0


def test_stale_scenario_prices_a_corroborated_stale_leg_and_leaves_the_rest_alone(tmp_path):
    t0 = _gap_day(tmp_path)
    prints = trade_frame("BBBB", [clock_us(DAY, 600, 250) + i * 100_000 for i in range(3)])
    write_day(tmp_path, DAY, [prints], kind="trades")
    trades = trades_rows(DAY, [("AAAA", 600), ("BBBB", 600)])
    rows = {r["ticker"]: r for r in price(trades, tmp_path)}
    aaaa = rows["AAAA"]
    assert aaaa["pair_status_250"] == "priced_primary_2s"
    # no stale leg: the scenario is out of scope, not a second estimate
    assert aaaa["s_applicable_250"] is False
    assert aaaa["s_entry_source_250"] == "not_applicable_no_stale_leg"
    assert "s_q_250_1000_0" not in aaaa
    bbbb = rows["BBBB"]
    assert bbbb["entry_status_250"] == "stale_quote"
    assert bbbb["exit_status_250"] == "quoted"
    assert bbbb["s_entry_source_250"] == "scenario_15s_corroborated"
    assert bbbb["s_entry_age_s_250"] > 2.0  # the wider assumption is explicit
    assert bbbb["s_exit_source_250"] == "primary_2s_unchanged"
    assert bbbb["s_q_250_1000_0"] is not None
    assert t0 < bbbb["exit_quote_us_250"]


def test_stale_leg_without_a_corroborating_print_stays_unknown_in_the_scenario(tmp_path):
    _gap_day(tmp_path)
    trades = trades_rows(DAY, [("BBBB", 600)])
    row = price(trades, tmp_path)[0]
    assert row["entry_status_250"] == "stale_quote"
    assert row["s_entry_prints_250"] == 0
    assert row["s_entry_source_250"] == "unknown_stale_no_corroborating_print"
    assert row["s_applicable_250"] is False
    assert "s_q_250_1000_0" not in row


def test_annual_math_separates_covered_contribution_from_conditional_whole_case():
    block = annual_block(
        n_trades=143,
        n_supported=77,
        n_known_no_order=0,
        mean_net=0.023,
        size=1000.0,
        period_days=332,
    )
    assert block["trades_per_year_whole_case"] == pytest.approx(143 / 332 * 252)
    assert block["trades_per_year_covered_case"] == pytest.approx(77 / 332 * 252)
    assert block["observed_covered_contribution_usd_per_year"] == pytest.approx(
        0.023 * (77 / 332 * 252) * 1000.0
    )
    assert (
        block["conditional_whole_case_usd_per_year_if_unknown_matched_covered"]
        > block["observed_covered_contribution_usd_per_year"]
    )
    assert block["shared_live_sip_opex_usd_per_year"] == 1188.0
    assert block["observed_covered_contribution_net_of_sip_opex_usd_per_year"] == pytest.approx(
        block["observed_covered_contribution_usd_per_year"] - 1188.0
    )


def test_annual_whole_case_excludes_known_no_order_from_traded_density():
    # A min-capital no-order is never one of the traded signals in the
    # if-all-eligible whole case, and its unfilled cash contributes 0 USD.
    block = annual_block(
        n_trades=143,
        n_supported=77,
        n_known_no_order=1,
        mean_net=0.023,
        size=1000.0,
        period_days=332,
    )
    assert block["trades_per_year_whole_case"] == pytest.approx(142 / 332 * 252)
    assert block["trades_per_year_covered_case"] == pytest.approx(77 / 332 * 252)
    assert block["n_known_no_order_excluded_from_whole_case"] == 1
    assert block["known_no_order_cash_contribution_usd_per_year"] == 0.0


def test_unknown_outcomes_are_counted_with_causes_not_zeroed():
    rows = [
        {
            "day": "2025-02-04",
            "priced_250": True,
            "p_sup_250_1000_0": True,
            "p_net_250_1000_0": 0.02,
            "p_unknown_250_1000": None,
            "pair_status_250": "priced_primary_2s",
        },
        {
            "day": "2025-02-05",
            "priced_250": True,
            "p_sup_250_1000_0": False,
            "p_net_250_1000_0": None,
            "p_unknown_250_1000": "unknown_l1_capacity_entry",
            "pair_status_250": "priced_primary_2s",
        },
        {
            "day": "2025-02-06",
            "priced_250": False,
            "p_sup_250_1000_0": False,
            "p_net_250_1000_0": None,
            "p_unknown_250_1000": None,
            "pair_status_250": "entry:stale_quote;exit:quoted",
        },
    ]
    stats = case_stats(
        rows,
        priced_of=lambda r: bool(r["priced_250"]),
        supported_of=lambda r: bool(r["p_sup_250_1000_0"]),
        net_of=lambda r: r["p_net_250_1000_0"],
        unknown_of=lambda r: r["p_unknown_250_1000"],
        unpriced_of=lambda r: r["pair_status_250"],
        size=1000.0,
        period_days=332,
        boot_n=0,
        seed=1,
    )
    assert stats["n_l1_supported"] == 1
    assert stats["n_unknown"] == 2
    assert stats["unknown_causes"] == {
        "unknown_l1_capacity_entry": 1,
        "entry:stale_quote;exit:quoted": 1,
    }
    assert stats["covered_mean_net_fraction"] == pytest.approx(0.02)
    assert stats["coverage_fraction_supported"] == pytest.approx(1 / 3)
    assert stats["unknown_outcomes_unmeasured_not_zero"] is True


def test_no_order_pairs_aggregate_as_known_cash_never_unknown_or_fill():
    """Mixed priced-supported / known-no-order / capacity-UNKNOWN / unpriced rows:
    the no-order is counted as a signal intent, excluded from the supported set,
    the covered mean and unknown_causes, and its cash contributes 0 USD to the
    total (never a per-fill zero, never an UNKNOWN loss)."""
    rows = [
        {
            "day": "2025-02-04",
            "priced_250": True,
            "p_sup_250_250_0": True,
            "p_net_250_250_0": 0.02,
            "p_noorder_250_250_0": False,
            "p_unknown_250_250": None,
            "pair_status_250": "priced_primary_2s",
        },
        {
            "day": "2025-02-05",
            "priced_250": True,
            "p_sup_250_250_0": False,
            "p_net_250_250_0": None,
            "p_noorder_250_250_0": True,
            "p_unknown_250_250": None,
            "pair_status_250": "priced_primary_2s",
        },
        {
            "day": "2025-02-06",
            "priced_250": True,
            "p_sup_250_250_0": False,
            "p_net_250_250_0": None,
            "p_noorder_250_250_0": False,
            "p_unknown_250_250": "unknown_l1_capacity_entry",
            "pair_status_250": "priced_primary_2s",
        },
        {
            "day": "2025-02-07",
            "priced_250": False,
            "p_sup_250_250_0": False,
            "p_net_250_250_0": None,
            "p_noorder_250_250_0": False,
            "p_unknown_250_250": None,
            "pair_status_250": "entry:stale_quote;exit:quoted",
        },
    ]
    stats = case_stats(
        rows,
        priced_of=lambda r: bool(r["priced_250"]),
        supported_of=lambda r: bool(r["p_sup_250_250_0"]),
        net_of=lambda r: r["p_net_250_250_0"],
        noorder_of=lambda r: bool(r["p_noorder_250_250_0"]),
        unknown_of=lambda r: r["p_unknown_250_250"],
        unpriced_of=lambda r: r["pair_status_250"],
        size=250.0,
        period_days=4,
        boot_n=0,
        seed=1,
    )
    assert stats["n_signal_intents"] == 4 == stats["n_trades"]
    assert stats["n_priced"] == 3
    assert stats["n_known_no_order"] == 1
    assert stats["n_l1_supported"] == 1
    assert stats["n_unknown"] == 2  # capacity shortfall + data gap only
    assert stats["unknown_causes"] == {
        "unknown_l1_capacity_entry": 1,
        "entry:stale_quote;exit:quoted": 1,
    }
    assert stats["covered_mean_net_fraction"] == pytest.approx(0.02)
    assert stats["coverage_fraction_supported"] == pytest.approx(1 / 4)
    ann = stats["annual"]
    assert ann["trades_per_year_whole_case"] == pytest.approx(3 / 4 * 252)
    assert ann["trades_per_year_covered_case"] == pytest.approx(1 / 4 * 252)
    assert ann["n_known_no_order_excluded_from_whole_case"] == 1
    assert ann["known_no_order_cash_contribution_usd_per_year"] == 0.0


def test_frontier_size_250_prices_tcgl_like_budget_as_no_order_not_supported(tmp_path):
    """End-to-end through price_cohort: a TCGL-like pair quoted ASK 306 with a
    $250 budget lands in the known no-order class at size 250 and is UNCHANGED
    at $1000 (three L1-supported shares) - no re-selected stock, no fraction."""
    t0 = clock_us(DAY, 600, 0)
    write_day(
        tmp_path,
        DAY,
        [
            frame("TCGL", [t0 - 100_000], [305.9], [306.0], [100]),
            frame("TCGL", [clock_us(DAY, 660, 0)], [305.95], [306.1], [100]),
        ],
    )
    trades = trades_rows(DAY, [("TCGL", 600)])
    rows = price(
        trades,
        tmp_path,
        sizes=(250.0, 1000.0),
        residuals=(0.0,),
        latencies=(BASE_LATENCY_MS,),
        scenario_on=False,
    )
    row = rows[0]
    assert row["p_q_250_250_0"] == 0 and row["p_noorder_250_250_0"] is True
    assert row["p_sup_250_250_0"] is False and row["p_net_250_250_0"] is None
    assert row["p_unknown_250_250"] is None
    assert row["p_q_250_1000_0"] == 3 and row["p_sup_250_1000_0"] is True
    assert row["p_noorder_250_1000_0"] is False
    assert row["p_net_250_1000_0"] is not None
    stats = case_stats(
        rows,
        priced_of=lambda r: bool(r["priced_250"]),
        supported_of=lambda r: bool(r.get("p_sup_250_250_0")),
        net_of=lambda r: r.get("p_net_250_250_0"),
        noorder_of=lambda r: bool(r.get("p_noorder_250_250_0")),
        unknown_of=lambda r: r.get("p_unknown_250_250"),
        unpriced_of=lambda r: r.get("pair_status_250"),
        size=250.0,
        period_days=1,
        boot_n=0,
        seed=1,
    )
    assert stats["n_known_no_order"] == 1 and stats["n_l1_supported"] == 0
    assert stats["covered_mean_net_fraction"] is None  # no real priced fill
    assert stats["n_unknown"] == 0


def test_latency_ladder_reports_every_arrival_without_picking_the_best(tmp_path):
    t0 = clock_us(DAY, 600, 0)
    # entry quotes end 1s before the clock, exit quotes resume for the 601:00 print
    stamps = [t0 - 3_000_000, t0 - 2_000_000, t0 - 1_000_000] + [t0 + 59_000_000, t0 + 60_000_000]
    write_day(
        tmp_path,
        DAY,
        [frame("AAA", stamps, [10.0] * len(stamps), [10.01] * len(stamps), [100.0] * len(stamps))],
    )
    trades = [
        {
            "day": DAY,
            "ticker": "AAA",
            "t": 600,
            "entry_et": 600,
            "entry_open": 10.0,
            "exit_et": 601,
            "horizon": 1,
            "gross": 0.0,
            "net": 0.0,
            "cost_bps": 100.0,
            "order_budget": 1000.0,
            "score": 0.04,
            "status": "known_open_proxy",
            "exit_open_proxy": 10.0,
        }
    ]
    rows = price(trades, tmp_path, latencies=(250, 1000, 2000, 5000), scenario_on=False)
    row = rows[0]
    assert row["priced_250"] is True and row["priced_1000"] is True
    assert row["priced_2000"] is False and row["priced_5000"] is False
    assert row["entry_ask_250"] == row["entry_ask_1000"] == pytest.approx(10.01)
    assert row["p_net_250_1000_0"] is not None and row.get("p_net_2000_1000_0") is None
    assert "p_net_2000_1000_0" not in row  # unpriced legs emit no ladder cells
    assert "entry:stale_quote" in row["pair_status_2000"]
    # the shared audit rule: age exactly equal to the ceiling is still fresh
    assert row["entry_age_s_1000"] == pytest.approx(2.0)
    assert row["entry_status_1000"] == "quoted"
    assert day_quote_path(tmp_path, DAY).exists()
