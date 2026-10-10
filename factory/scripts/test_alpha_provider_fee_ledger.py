"""Money-boundary regressions for the provider fee ledger.

Consumer-visible fee behaviors that are easy to get wrong and expensive when
wrong: per-day per-TYPE up-cent aggregation (vs rounding every trade), sided
fees (SEC sells only, CAT both sides), the per-trade TAF cap and its holiday,
the two-sided-notional fee-bps denominator, per-order minimums, UNKNOWN
components, and the no-order / unpriced boundaries. No source pinning: every
assertion is about fee behavior a future model would rely on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpha_provider_fee_ledger import (  # noqa: E402
    FEE_COMPONENT_ORDER,
    PROVIDERS,
    allocate_posted_fees,
    build_day_ledger,
    fee_funded_quantity,
    fee_turnover_bps,
    order_fees,
    reprice_trade,
    round_trip_fee_bps,
    trade_fee_record,
    up_cent,
)

ALPACA = PROVIDERS["alpaca_current_2026q4"]
TAF27 = PROVIDERS["alpaca_taf_jan2027"]
LITE = PROVIDERS["ibkr_lite_current"]
TIERED = PROVIDERS["ibkr_pro_tiered_current"]
FIXED = PROVIDERS["ibkr_pro_fixed_current"]


def fill_row(day="2025-03-03", exit_day="2025-03-03", fee_total=0.01, cat=0.0006):
    row = {"known_fill": True, "day": day, "exit_day": exit_day}
    for comp in FEE_COMPONENT_ORDER:
        row[f"fee_{comp}_buy"] = None
        row[f"fee_{comp}_sell"] = None
    row["fee_cat_buy"] = cat / 2
    row["fee_cat_sell"] = cat / 2
    row["fee_sec_sell"] = 0.13
    row["fee_total_usd"] = fee_total
    return row


def test_daily_aggregation_groups_trades_on_one_day_before_up_cent():
    # three CAT accruals of $0.0002 on ONE day aggregate to $0.0006 -> $0.01,
    # while the same three trades on SEPARATE days post $0.01 each: daily
    # per-TYPE aggregation, never rounding every trade up individually.
    rows_one_day = [fill_row() for _ in range(3)]
    one = build_day_ledger(rows_one_day, ALPACA)
    assert len(one) == 1
    ledger = one["2025-03-03"]
    assert ledger["n_orders"] == 6  # a buy and a sell order per fill
    assert ledger["fee_posted_by_type_usd_eod"]["cat"] == 0.01

    rows_three_days = [fill_row(day=f"2025-03-0{i + 1}") for i in range(3)]
    multi = build_day_ledger(rows_three_days, ALPACA)
    assert len(multi) == 3
    assert all(v["fee_posted_by_type_usd_eod"]["cat"] == 0.01 for v in multi.values())
    assert abs(sum(v["fee_posted_total_usd_eod"] for v in multi.values()) - 3 * 0.14) < 1e-9


def test_up_cent_float_guard_and_rounds_up_not_nearest():
    assert up_cent(0.0001) == 0.01
    assert up_cent(0.02) == 0.02  # 0.02*100 is 2.0000000000000004 in binary
    assert up_cent(0.021) == 0.03
    assert up_cent(0.20) == 0.20


def test_sec_is_sell_only_while_cat_charges_both_sides():
    qty = 122
    ask, bid = 8.15, 5.67
    buy = order_fees(ALPACA, "buy", ask, qty)
    sell = order_fees(ALPACA, "sell", bid, qty)
    assert buy["sec"] == 0.0  # the SEC fee does not apply to the buy leg
    assert sell["sec"] > 0
    assert buy["cat"] == 0.000003 * qty
    assert sell["cat"] == 0.000003 * qty


def test_taf_holiday_is_zero_and_jan2027_caps_per_trade():
    qty = 122
    sell = order_fees(TAF27, "sell", 5.67, qty)
    assert sell["taf"] == 0.000195 * qty
    # the cap binds far above ~50k shares: raw $19.50 posts as the $9.79 cap
    capped = order_fees(TAF27, "sell", 5.67, 100_000)
    assert capped["taf"] == 9.79
    assert order_fees(ALPACA, "sell", 5.67, qty)["taf"] == 0.0


def test_round_trip_fee_bps_uses_entry_notional_not_two_sided_turnover():
    # A round-trip cost is charged on the capital committed to the ENTRY
    # ticket: $1.00 of fees on a $1000 entry is 10 RT bps. Dividing by the
    # two-sided turnover ($1000 + $1100) would report 4.76 bps and silently
    # understate the RT cost against the 25-150bps ladder.
    bps = round_trip_fee_bps(0.14, 1000.0)
    assert abs(bps - 1.4) < 1e-12
    assert abs(round_trip_fee_bps(1.0, 1000.0) - 10.0) < 1e-12
    assert round_trip_fee_bps(None, 1000.0) is None
    assert round_trip_fee_bps(1.0, None) is None
    assert round_trip_fee_bps(1.0, 0.0) is None
    # the two-sided fee/turnover rate survives only under its own name
    turnover = fee_turnover_bps(1.0, 1000.0, 1100.0)
    assert abs(turnover - 1.0 / 2100.0 * 10_000) < 1e-12
    assert abs(turnover - 4.7619047619) < 1e-9
    assert fee_turnover_bps(None, 100.0, 90.0) is None


def test_min_capital_no_order_sends_no_order_and_no_fee():
    record = {
        "day": "2025-03-03",
        "exit_day": "2025-03-03",
        "ticker": "PRXY",
        "entry_et": 600,
        "exit_et": 660,
        "entry_ask": 1200.0,  # budget cannot fund even one share
        "exit_bid": 1250.0,
        "entry_displayed_shares": 100.0,
        "exit_displayed_shares": 100.0,
        "legs_priced": True,
        "unpriced_cause": None,
        "stored_quantity_by_rung": {},
        "stored_supported_r0": True,
    }
    row = reprice_trade(record, 0, 1000.0)
    assert row["status"] == "known_no_order_min_capital"
    assert row["quantity"] == 0 and row["known_fill"] is False
    assert row["buy_notional_usd"] == 0.0 and row["sell_notional_usd"] == 0.0
    assert row["net_usd_slippage_only"] is None  # never a zero-return fill
    # an order that is not sent has no fee at all: the profile refuses to
    # price a zero-quantity order rather than reporting a zero-dollar fill
    import pytest

    with pytest.raises(ValueError):
        trade_fee_record(ALPACA, row["entry_price"], row["exit_price"], 0)


def test_unpriced_legs_stay_unknown_fee_never_zero_cash():
    record = {
        "day": "2025-03-03",
        "exit_day": "2025-03-03",
        "ticker": "PRXY",
        "entry_et": 600,
        "exit_et": 660,
        "entry_ask": None,
        "exit_bid": None,
        "entry_displayed_shares": None,
        "exit_displayed_shares": None,
        "legs_priced": False,
        "unpriced_cause": "entry:stale_quote;exit:quoted",
        "stored_quantity_by_rung": {},
        "stored_supported_r0": None,
    }
    row = reprice_trade(record, 25, 1000.0)
    assert row["status"] == "unknown_unpriced"
    assert row["unpriced_cause"] == "entry:stale_quote;exit:quoted"
    assert row["known_fill"] is False
    # an unpriced outcome carries no measured fee at all
    ledger = build_day_ledger([dict(row, known_fill=False)], ALPACA)
    assert all(v["fee_raw_total_usd"] == 0 for v in ledger.values())


def test_capacity_unknown_keeps_no_fill_and_no_fee():
    record = {
        "day": "2025-03-03",
        "exit_day": "2025-03-03",
        "ticker": "PRXY",
        "entry_et": 600,
        "exit_et": 660,
        "entry_ask": 8.15,
        "exit_bid": 5.67,
        "entry_displayed_shares": 10.0,  # displayed depth below the 122 funded
        "exit_displayed_shares": 400.0,
        "legs_priced": True,
        "unpriced_cause": None,
        "stored_quantity_by_rung": {},
        "stored_supported_r0": False,
    }
    row = reprice_trade(record, 0, 1000.0)
    assert row["status"] == "unknown_l1_capacity_entry"
    assert row["known_fill"] is False
    assert row["net_usd_slippage_only"] is None


def test_per_order_minimums_bind_on_small_orders():
    qty = 1
    # IBKR Lite is commission-FREE for qualifying US exchange-listed stock/ETF
    # retail RTH orders: a one-share order has no per-share commission, no
    # per-order minimum and no per-order maximum.
    buy = order_fees(LITE, "buy", 10.0, qty)
    assert buy["commission"] == 0.0
    assert LITE.conditional is True
    assert LITE.conditional_assumption  # labeled scenario, not a fee fact
    commission = next(c for c in LITE.components if c.name == "commission")
    assert commission.rate == 0.0 and commission.per_order_min_usd == 0.0
    # fixed plan: the $1.00 minimum is itself capped at 1% of trade value,
    # so a tiny order posts its capped commission, never more
    buy = order_fees(FIXED, "buy", 10.0, qty)
    assert buy["commission"] == 0.10
    buy = order_fees(FIXED, "buy", 100.0, 100)
    assert buy["commission"] == 1.00  # 0.005 x 100 = $0.50 below the minimum
    buy = order_fees(TIERED, "buy", 10.0, 100)
    assert abs(buy["commission"] - 0.35) < 1e-12  # 0.0035 x 100


def test_ibkr_lite_qualifying_order_pays_regulatory_fees_only():
    qty = 122
    ask, bid = 8.15, 5.67
    buy = order_fees(LITE, "buy", ask, qty)
    sell = order_fees(LITE, "sell", bid, qty)
    # qualifying US exchange-listed stock/ETF retail RTH orders: $0 commission
    assert buy["commission"] == 0.0 and sell["commission"] == 0.0
    # the SUM of all applicable order fees equals the independent regulator
    # math: buys pay CAT only, sells pay SEC + CAT, and the 2026Q4 TAF
    # holiday leaves the sell-side TAF at zero. No per-share clearing,
    # exchange-route or pass-through charge exists on this plan.
    expected_buy = 0.000003 * qty
    expected_sell = 0.0000206 * (bid * qty) + 0.000003 * qty
    assert abs(sum(v for v in buy.values() if v is not None) - expected_buy) < 1e-12
    assert abs(sum(v for v in sell.values() if v is not None) - expected_sell) < 1e-12
    # the profile total is therefore KNOWN for the qualifying scenario
    fr = trade_fee_record(LITE, ask, bid, qty)
    assert fr["fee_total_usd"] is not None
    assert abs(fr["fee_total_usd"] - (expected_buy + expected_sell)) < 1e-12
    # and the $0 commission is a CONDITIONAL qualifying-plan price
    assert LITE.conditional is True and LITE.conditional_assumption
    commission = next(c for c in LITE.components if c.name == "commission")
    assert commission.rate == 0.0 and commission.per_order_min_usd == 0.0


def test_profile_total_is_unknown_when_route_fee_is_unmeasured():
    assert not TIERED.total_complete
    assert TIERED.unknown_components() == ["exchange_route"]
    fr = trade_fee_record(TIERED, 8.15, 5.67, 122)
    assert fr["fee_total_usd"] is None
    assert fr["buy_leg_fees"]["exchange_route"] is None
    assert fr["fee_rt_bps"] is None
    # the measured components still report exactly; the gap is never zeroed
    assert fr["buy_leg_fees"]["commission"] == 0.0035 * 122
    assert fr["buy_leg_fees"]["clearing"] == 0.0002 * 122
    assert LITE.total_complete and FIXED.total_complete and ALPACA.total_complete


def test_fee_funded_quantity_is_the_immutable_float_floor_rule():
    # 1000 // 1.6 is 624.999... in binary floats, so the floor is 624, not 625;
    # the ledger must not adopt a polars-style division+floor variant.
    assert fee_funded_quantity(1000.0, 1.6, 0.0) == 624
    assert fee_funded_quantity(1000.0, 1.6, 25.0) == fee_funded_quantity(1000.0, 1.6 * 1.00125, 0.0)
    assert fee_funded_quantity(250.0, 8.15, 0.0) == 30


def test_overnight_exit_posts_its_sell_fees_on_the_exit_day():
    row = fill_row(day="2025-03-03", exit_day="2025-03-04")
    ledger = build_day_ledger([row], ALPACA)
    assert set(ledger) == {"2025-03-03", "2025-03-04"}
    assert "sec" not in ledger["2025-03-03"]["fee_raw_by_type_usd"]
    assert "sec" in ledger["2025-03-04"]["fee_raw_by_type_usd"]


def test_posted_up_cent_allocation_reconciles_cash_nets():
    """Two known fills on ONE day whose RAW fees sum to less than a cent.

    The provider account is charged the posted EOD amounts (each fee type per
    day, up-cent), so the consumer-visible P&L must use the posted allocation,
    never the raw sub-cent sum. The pro-rata allocation must make trade, day
    and gross level reconcile exactly.
    """
    profile = ALPACA
    trades = [
        {"day": "2025-03-03", "exit_day": "2025-03-03", "qty": 5, "ask": 10.00, "bid": 9.00},
        {"day": "2025-03-03", "exit_day": "2025-03-03", "qty": 5, "ask": 20.00, "bid": 18.00},
    ]
    rows = []
    for t in trades:
        qty, ask, bid = t["qty"], t["ask"], t["bid"]
        fr = trade_fee_record(profile, ask, bid, qty)
        row = {
            "day": t["day"],
            "exit_day": t["exit_day"],
            "known_fill": True,
            "buy_notional_usd": ask * qty,
            "sell_notional_usd": bid * qty,
            "net_usd_slippage_only": fr["sell_notional_usd"] - fr["buy_notional_usd"],
            "fee_total_usd": fr["fee_total_usd"],
            "fee_posted_allocated_usd": None,
            "net_after_posted_fees_usd": None,
            "fee_rt_bps_of_entry_notional": None,
            "fee_turnover_bps_two_sided_diagnostic": None,
        }
        for comp in FEE_COMPONENT_ORDER:
            row[f"fee_{comp}_buy"] = fr["buy_leg_fees"].get(comp)
            row[f"fee_{comp}_sell"] = fr["sell_leg_fees"].get(comp)
        rows.append(row)
    raw_total = sum(r["fee_total_usd"] for r in rows)
    assert raw_total < 0.01  # sub-cent raw accrual on one day

    allocate_posted_fees(rows, profile)
    ledger = build_day_ledger(rows, profile)
    day = ledger["2025-03-03"]
    # per fee TYPE per day, up-cent: each type posts a full cent, not the raw dust
    assert day["fee_posted_by_type_usd_eod"]["cat"] == 0.01
    assert day["fee_posted_by_type_usd_eod"]["sec"] == 0.01
    posted_total = day["fee_posted_total_usd_eod"]
    assert posted_total == 0.02
    assert day["fee_raw_total_usd"] < 0.01

    allocations = [r["fee_posted_allocated_usd"] for r in rows]
    assert all(a > 0 for a in allocations)
    assert abs(sum(allocations) - posted_total) < 1e-12
    # pro-rata: the raw shares differ (SEC on a $90 vs $180 sell), so the
    # allocations differ while still summing to the posted total
    assert abs(allocations[0] - allocations[1]) > 1e-12
    gross = sum(r["net_usd_slippage_only"] for r in rows)
    nets = [r["net_after_posted_fees_usd"] for r in rows]
    assert abs(sum(nets) - (gross - posted_total)) < 1e-12
    for r, a in zip(rows, allocations, strict=True):
        assert abs(r["net_after_posted_fees_usd"] - (r["net_usd_slippage_only"] - a)) < 1e-12
        assert r["fee_rt_bps_of_entry_notional"] is not None

    # an unmeasured route fee keeps the cash consequence UNKNOWN: no fee is
    # invented, no net is computed against a partial fee
    tiered_rows = [dict(r) for r in rows]
    for r in tiered_rows:
        r["fee_exchange_route_buy"] = None
        r["fee_exchange_route_sell"] = None
    allocate_posted_fees(tiered_rows, TIERED)
    assert all(r["fee_posted_allocated_usd"] is None for r in tiered_rows)
    assert all(r["net_after_posted_fees_usd"] is None for r in tiered_rows)


def test_build_day_ledger_never_posts_rounding_for_ibkr_raw_only():
    row = fill_row()
    ledger = build_day_ledger([row], TIERED)
    day = ledger["2025-03-03"]
    assert day["fee_posted_total_usd_eod"] is None
    assert day["rounding_policy"].startswith("provider posting/rounding policy unverified")
    assert day["fee_raw_total_usd"] > 0
