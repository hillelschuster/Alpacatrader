"""Observed execution pairs, ET clocks and cash books retain their ledger identity."""

from pathlib import Path

import owned_claim_cash_execution as execution
import polars as pl
import pytest

KEY = {
    "day": "2021-09-03",
    "clock": 540,
    "n": 3,
    "side_cost": 0.005,
    "policy": "h3:cash",
    "cycles": "repeat",
}


def legs():
    rows = []
    for side, minute, price, quantity in (
        ("buy", 600, 10.0, 0.03),
        ("sell", 605, 11.0, 0.03),
        ("buy", 610, 12.0, 0.025),
        ("sell", 615, 10.0, 0.025),
    ):
        rows.append(
            {
                **KEY,
                "ticker": "A",
                "rank": 1,
                "side": side,
                "exec_et": minute,
                "decision_et": minute - 1,
                "px": price,
                "gross_fraction": price * quantity,
                "fee_fraction": price * quantity * 0.005,
                "shares_per_capital": quantity,
                "participation": quantity,
                "reason": "fresh" if side == "buy" else "exit",
                "admission": "cash",
            }
        )
    return pl.DataFrame(rows)


def test_round_trips_match_exits_to_their_own_entry_cycle():
    pairs = execution.build_pairs(legs()).sort("cycle_index")
    assert pairs["entry_et"].to_list() == [600, 610]
    assert pairs["exit_et"].to_list() == [605, 615]
    assert pairs["cycle_kind"].to_list() == ["entry", "reentry"]
    assert pairs["hold_minutes"].to_list() == [5, 5]
    assert pairs["net_account"].to_list() == pytest.approx([0.02685, -0.05275])
    assert pairs["qty_mismatch"].to_list() == [0.0, 0.0]


def test_open_entry_retains_unknown_exit_and_unknown_pair_profit():
    pair = execution.build_pairs(legs().filter(pl.col("exec_et") == 600))
    assert pair["exit_et"].to_list() == [None]
    assert pair["net_account"].to_list() == [None]
    assert pair["cycle_kind"].to_list() == ["open_at_close"]


@pytest.mark.parametrize(
    "minute, clock", [(0, "00:00"), (540, "09:00"), (571, "09:31"), (780, "13:00"), (959, "15:59")]
)
def test_execution_clock_is_absolute_et_minutes(minute, clock):
    assert execution.hhmm(minute) == clock


def test_known_cash_book_accepts_empty_and_absent_zero_order_journal(tmp_path: Path):
    daily = pl.DataFrame([{**KEY, "orders": 0, "admission": "cash"}])
    empty = legs().head(0)
    result = execution.check_journal(daily, empty, tmp_path)
    assert result["daily_orders_sum"] == 0
    assert result["books_zero_orders"] == 1
    assert result["fills_rows"] == 0


def test_nonzero_order_book_rejects_empty_journal(tmp_path: Path):
    daily = pl.DataFrame([{**KEY, "orders": 1, "admission": "cash"}])
    with pytest.raises(SystemExit, match="orders"):
        execution.check_journal(daily, legs().head(0), tmp_path)


def test_member_profit_drift_fails_reconciliation_even_when_fees_and_gross_match():
    fills = legs().filter(pl.col("exec_et") <= 605)
    daily = pl.DataFrame(
        [
            {
                **KEY,
                "ret": 0.02685,
                "fees": 0.00315,
                "gross_pnl": 0.03,
                "orders": 2,
                "unknown": False,
                "roster_claims": 1,
            }
        ]
    )
    members = pl.DataFrame(
        [{**KEY, "ticker": "A", "net_pnl": 0.03685, "fees": 0.00315, "gross_pnl": 0.03}]
    )
    reconciliation = execution.recon(daily, fills, members, execution.build_pairs(fills))
    assert not reconciliation["all_checks_pass"]
    assert reconciliation["max_abs_residual"] == pytest.approx(0.01)


def test_panel_gap_reproduces_participation_and_refreshes_changed_order_quantity(tmp_path):
    fills = legs()
    day = KEY["day"]
    panel_dir = tmp_path / "panel"
    panel_dir.mkdir()
    panel = fills.select("day", "clock", "rank", "ticker").with_columns(
        fills["decision_et"].alias("t"),
        (fills["px"] - 0.5).alias("px"),
        fills["exec_et"].alias("sell_et"),
        fills["px"].alias("sell_px"),
        pl.lit(10000.0).alias("sell_volume"),
    )
    panel_path = panel_dir / f"{day}.parquet"
    panel.write_parquet(panel_path)
    pins = {day: {"panel": execution.sha256_file(panel_path)}}
    stage = tmp_path / "stage"
    observed, reason, _ = execution.read_marks(fills, tmp_path, stage, pins, [day])
    assert reason is None
    first_buy = observed.filter(pl.col("exec_et") == 600)
    assert first_buy["mark_to_next_open_gap"].item() == pytest.approx(10.0 / 9.5 - 1.0)
    assert first_buy["participation_reproduced"].item() == pytest.approx(0.03)

    changed = fills.with_columns(
        pl.when(pl.col("exec_et") == 600)
        .then(0.02)
        .otherwise(pl.col("shares_per_capital"))
        .alias("shares_per_capital"),
        pl.when(pl.col("exec_et") == 600)
        .then(0.02)
        .otherwise(pl.col("participation"))
        .alias("participation"),
    )
    refreshed, reason, _ = execution.read_marks(changed, tmp_path, stage, pins, [day])
    assert reason is None
    assert refreshed.filter(pl.col("exec_et") == 600)[
        "participation_reproduced"
    ].item() == pytest.approx(0.02)


@pytest.mark.parametrize("member_net", [None, float("nan")])
def test_known_book_with_unknown_member_fails_instead_of_skipping_net_comparison(member_net):
    fills = legs().filter(pl.col("exec_et") <= 605)
    daily = pl.DataFrame(
        [
            {
                **KEY,
                "ret": 0.02685,
                "fees": 0.00315,
                "gross_pnl": 0.03,
                "orders": 2,
                "unknown": False,
                "roster_claims": 1,
            }
        ]
    )
    members = pl.DataFrame(
        [{**KEY, "ticker": "A", "net_pnl": member_net, "fees": 0.00315, "gross_pnl": 0.03}]
    ).with_columns(pl.col("net_pnl").cast(pl.Float64))
    result = execution.recon(daily, fills, members, execution.build_pairs(fills))
    assert not result["all_checks_pass"]
    assert result["net_comparisons"]["known_books_not_comparable"] == 1


def test_known_cash_book_rejects_missing_roster_members():
    fills = legs().head(0)
    daily = pl.DataFrame(
        [
            {
                **KEY,
                "ret": 0.0,
                "fees": 0.0,
                "gross_pnl": 0.0,
                "orders": 0,
                "unknown": False,
                "roster_claims": 1,
            }
        ]
    )
    members = pl.DataFrame(
        [{**KEY, "ticker": "A", "net_pnl": 0.0, "fees": 0.0, "gross_pnl": 0.0}]
    ).head(0)
    result = execution.recon(daily, fills, members, execution.build_pairs(fills))
    assert not result["all_checks_pass"]
    assert result["net_comparisons"]["known_books_not_comparable"] == 1
