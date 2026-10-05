"""Money-arithmetic regressions for the owned-claim clock-window / money-gap producers.

Three consumer-visible dollar invariants, each frozen on a tiny hand-built frame:

  * ``_derive_channels`` prices a FRESH cash level against the fresh buyer's own share
    count: a flat price still charges the round trip (both fees) on the new cash, the
    deployed cash is the fresh budget and not the episode's own bank, and an unknown
    entry basis (or an unsupported leg frame) stays UNKNOWN rather than turning into 0;
  * ``add_break_even`` splits the deficit into DISJOINT price and cost levers. Because
    net = gross_move - fees, the price lever is the no-excursion claims' GROSS loss and
    the cost lever is the whole fee bill; adding the no-excursion NET loss to the fee
    bill would charge those fees twice and overstate the improvement. These are
    accounting levers on a measured cell, never a claim that any of them is tradable;
  * ``_day_sensitivity`` removes worst/best k BOOK-DATES with the claim counts taken from
    the same sorted rows, and its primary mean is dollars per retained book-date, with
    the per-claim-slot mean reported as an explicitly different denominator.

Every assertion compares a produced number against an independently computed dollar
quantity. No source-text, wiring, forwarding or default-copy assertions.
"""

import math
import sys
from pathlib import Path

# `scripts` package for the producer modules, plus their own sibling directory so their
# top-level `import lifecycle_study` resolves the same way it does in production runs.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl  # noqa: E402
import pytest  # noqa: E402
from scripts.owned_claim_clock_windows import _derive_channels  # noqa: E402
from scripts.owned_claim_money_gap import _day_sensitivity, add_break_even  # noqa: E402

# Declared friction ladder, restated here so the expectations do not read the
# producer's own constants back. The fresh buyer spends exactly this much new cash.
BPS = (100, 150)
FRESH_CASH = 100.0
MONEY = {"abs": 1e-9}


def _fee_factor(bps: int) -> float:
    return (1 - bps / 2e4) / (1 + bps / 2e4)


def _inherited_ladder(bps: int) -> float:
    """Exit-side re-pricing factor for the ORIGINAL budget's fixed share count."""
    return (1 - bps / 2e4) / (1 - 0.005)


def _fresh_cash_from_shares(p0: float, p: float, bps: int) -> float:
    """A fresh buyer spends FRESH_CASH at the entry open, pays the buy fee, then sells.

    shares = cash / (p0 * (1 + s)); net cash = shares * p * (1 - s) - cash. Both the buy
    and the sell fee are charged on the fresh cash; nothing about the episode's own bank.
    """
    s = bps / 2e4
    shares = FRESH_CASH / (p0 * (1 + s))
    return shares * p * (1 - s) - FRESH_CASH


# ------------------------------------------------------------------ fresh cash channels
# (label, bank_value dollars of the episode, staged increment, entry open, exit open).
# The staged increment satisfies inc / bank_value == (p - p0) / p0 exactly, and every
# bank_value is deliberately NOT the fresh budget, so a bank/cash basis confusion cannot
# pass unnoticed.
_CHANNEL_ROWS = [
    ("flat", 400.0, 0.0, 100.0, 100.0),
    ("up", 400.0, 10.0, 100.0, 102.5),
    ("down", 800.0, -20.0, 100.0, 97.5),
    ("up_wide", 250.0, 12.5, 50.0, 52.5),
    ("unknown_bank", 0.0, 5.0, None, None),
    ("unknown_inc", 400.0, None, None, None),
    ("nan_bank", float("nan"), 4.0, None, None),
]
_PRICED = {"flat", "up", "down", "up_wide"}


def _channel_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "case": [r[0] for r in _CHANNEL_ROWS],
            "inc_stage": [r[2] for r in _CHANNEL_ROWS],
            "bank_value": [r[1] for r in _CHANNEL_ROWS],
        }
    )


def _channels_by_case(frame: pl.DataFrame) -> dict:
    return {r["case"]: r for r in frame.to_dicts()}


def test_fresh_channel_equals_a_new_buyer_share_count_at_both_fee_levels():
    out = _channels_by_case(_derive_channels(_channel_frame(), is_leg=False))
    for bps in BPS:
        for label, bank, inc, p0, p in _CHANNEL_ROWS:
            if label not in _PRICED:
                continue
            fresh = out[label][f"fresh_{bps}"]
            assert fresh == pytest.approx(_fresh_cash_from_shares(p0, p, bps), **MONEY), (
                f"{label}@{bps} is not the fresh buyer's own share count"
            )
            # Priced on FRESH_CASH, never on the episode's bank basis: a bank-funded
            # version of the same row is a different number at every row here.
            wrong_basis = bank * (p / p0 - 1.0) * _fee_factor(bps) - bank * (1 - _fee_factor(bps))
            assert fresh != pytest.approx(wrong_basis, abs=1e-6)
            # The inherited channels answer a different question on the same row.
            ladder = _inherited_ladder(bps)
            assert out[label][f"inc_{bps}"] == pytest.approx(inc * ladder, **MONEY)
            assert out[label][f"value_{bps}"] == pytest.approx((bank + inc) * ladder, **MONEY)


def test_flat_price_still_charges_both_fees_on_fresh_cash():
    out = _channels_by_case(_derive_channels(_channel_frame(), is_leg=False))
    charges = []
    for bps in BPS:
        fresh = out["flat"][f"fresh_{bps}"]
        assert fresh == pytest.approx(-FRESH_CASH * (1 - _fee_factor(bps)), **MONEY)
        assert fresh < 0.0, "flat price: the owned increment is 0 but the fresh buyer still pays"
        # Cancelling the entry fee against the exit fee (a DIFFERENCE, not a LEVEL) would
        # print exactly 0.0 on this row.
        assert fresh != pytest.approx(0.0, abs=1e-12)
        charges.append(fresh)
    assert charges[1] < charges[0] < 0.0, "a 150bps round trip costs more fresh cash than 100bps"
    # The inherited channels of the same row do stay at the flat level: only the fresh
    # cash pays the round trip.
    assert out["flat"]["inc_100"] == pytest.approx(0.0, abs=1e-12)
    assert out["flat"]["value_100"] == pytest.approx(400.0, **MONEY)


def test_unknown_entry_basis_stays_unknown_not_zero():
    out = _channels_by_case(_derive_channels(_channel_frame(), is_leg=False))
    for label in ("unknown_bank", "unknown_inc"):
        for bps in BPS:
            assert out[label][f"fresh_{bps}"] is None
            assert out[label][f"fresh_{bps}"] != pytest.approx(0.0, abs=1e-12)
    for bps in BPS:
        # A NaN entry basis is the UNKNOWN sentinel of the staged frames: whatever shape
        # it arrives in, it may not be laundered into a confident zero fresh P&L.
        nan_fresh = out["nan_bank"][f"fresh_{bps}"]
        assert nan_fresh is None or math.isnan(nan_fresh)
        assert nan_fresh != pytest.approx(0.0, abs=1e-12)


def test_leg_frames_never_publish_a_fresh_or_level_channel():
    legs = _channels_by_case(
        _derive_channels(
            pl.DataFrame({"case": ["leg"], "inc_stage": [3.0], "bank_value": [400.0]}),
            is_leg=True,
        )
    )
    for bps in BPS:
        assert legs["leg"][f"fresh_{bps}"] is None
        assert legs["leg"][f"value_{bps}"] is None
    assert legs["leg"]["inc_100"] == pytest.approx(3.0, **MONEY)
    assert legs["leg"]["inc_150"] == pytest.approx(3.0 * _inherited_ladder(150), **MONEY)


# ------------------------------------------------------------------ break-even levers
def _cell(
    cash_net,
    fees,
    dud_gross,
    dud_net,
    dud_fees,
    tail_net,
    tail_claims,
    paired_days,
    total_net_loss,
    gap,
):
    return {
        "cash_net": cash_net,
        "paired_days": paired_days,
        "fees": fees,
        "total_net_loss_dollars": total_net_loss,
        "dud_no_excursion": {
            "gross_loss_dollars": dud_gross,
            "net_loss_dollars": dud_net,
            "fees": dud_fees,
        },
        "tail": {"preserved_net": tail_net, "preserved_claims": tail_claims},
        "surrendered_upside": {"surrender_gap_dollars_raw": gap},
    }


def test_break_even_levers_are_disjoint_price_and_cost():
    be = add_break_even(
        _cell(
            cash_net=-15.0,
            fees=3.0,
            dud_gross=10.0,
            dud_net=11.0,
            dud_fees=1.0,
            tail_net=4.0,
            tail_claims=3,
            paired_days=5,
            total_net_loss=40.0,
            gap=6.0,
        )
    )["break_even"]

    assert be["deficit_to_cash_dollars"] == pytest.approx(15.0, **MONEY)
    assert be["deficit_per_paired_day_dollars"] == pytest.approx(3.0, **MONEY)

    price, cost = be["price_lever"], be["cost_lever"]
    assert price["dollars"] == pytest.approx(10.0, **MONEY)
    assert cost["dollars"] == pytest.approx(3.0, **MONEY)
    # Each lever ALONE, scored against the same deficit.
    assert price["residual_if_applied_alone_dollars"] == pytest.approx(5.0, **MONEY)
    assert cost["residual_if_applied_alone_dollars"] == pytest.approx(12.0, **MONEY)
    assert price["covers_deficit_alone"] is False
    assert cost["covers_deficit_alone"] is False

    joint = be["joint_disjoint_scenario"]
    # Gross price loss + fee bill, no overlap: 10 + 3 = 13. The dud NET loss (11) plus the
    # same fee bill prints 14 and would charge the dud claims' 1.0 of fees twice.
    assert joint["improvement_dollars"] == pytest.approx(13.0, **MONEY)
    assert joint["residual_dollars"] == pytest.approx(2.0, **MONEY)
    assert joint["improvement_dollars"] == pytest.approx(
        price["dollars"] + cost["dollars"], **MONEY
    )

    tail = be["tail_rent_other_outcomes_fixed"]
    assert tail["additional_tail_dollars_needed"] == pytest.approx(15.0, **MONEY)
    assert tail["as_multiple_of_current_preserved_tail"] == pytest.approx(3.75, **MONEY)
    assert tail["dollars_per_preserved_tail_claim"] == pytest.approx(5.0, **MONEY)
    assert tail["residual_after_joint_disjoint_levers_dollars"] == pytest.approx(2.0, **MONEY)

    assert be["price_lever_share_of_deficit"] == pytest.approx(10.0 / 15.0, **MONEY)
    assert be["cost_lever_share_of_deficit"] == pytest.approx(3.0 / 15.0, **MONEY)
    assert be["loss_reduction_fraction_of_all_losses_needed"] == pytest.approx(0.375, **MONEY)
    assert be["surrender_gap_share_of_deficit"] == pytest.approx(0.4, **MONEY)


def test_double_counted_dud_fees_would_falsely_close_the_deficit():
    be = add_break_even(
        _cell(
            cash_net=-4.2,
            fees=2.0,
            dud_gross=2.0,
            dud_net=2.5,
            dud_fees=0.5,
            tail_net=1.0,
            tail_claims=2,
            paired_days=3,
            total_net_loss=10.0,
            gap=1.0,
        )
    )["break_even"]

    joint = be["joint_disjoint_scenario"]
    # 2.0 gross + 2.0 fees = 4.0 against a 4.2 deficit, so the disjoint levers do NOT
    # reach break-even. Laddering the dud NET loss (2.5) plus all fees (2.0) prints 4.5
    # and claims a 0.0 residual it never earned.
    assert joint["improvement_dollars"] == pytest.approx(4.0, **MONEY)
    assert joint["residual_dollars"] == pytest.approx(0.2, **MONEY)
    assert joint["residual_dollars"] > 0.0
    assert joint["improvement_dollars"] < be["deficit_to_cash_dollars"]
    assert be["price_lever"]["residual_if_applied_alone_dollars"] == pytest.approx(2.2, **MONEY)
    assert be["cost_lever"]["residual_if_applied_alone_dollars"] == pytest.approx(2.2, **MONEY)
    assert be["tail_rent_other_outcomes_fixed"]["as_multiple_of_current_preserved_tail"] == (
        pytest.approx(4.2, **MONEY)
    )


# ------------------------------------------------------------------ day sensitivity
def test_day_sensitivity_removes_worst_dates_with_their_own_claim_counts():
    """Five bad book-dates of 3 claims each and one good book-date of a single claim."""
    rows = [{"day": f"bad{i}", "net": -0.006} for i in range(5) for _ in range(3)]
    rows.append({"day": "good", "net": 0.02})
    frame = pl.DataFrame(rows)

    total = float(frame["net"].sum())
    assert total == pytest.approx(-0.07, abs=1e-12)

    s = _day_sensitivity(frame, total, 5)
    assert s["days"] == 6
    assert s["k"] == 5
    assert s["total_slots"] == 16

    # The five worst DATES are the five bad dates, and they take 15 of the 16 slots.
    assert s["worst_k_days_sum"] == pytest.approx(-0.09, abs=1e-12)
    assert s["worst_k_days_slots"] == 15
    assert sorted(s["worst_k_dates_removed"]) == [f"bad{i}" for i in range(5)]
    # Head side off the SAME sorted rows: the good date's single slot plus 4 bad dates.
    assert s["best_k_dates_removed"][0] == "good"
    assert s["best_k_days_slots"] == 13
    assert s["best_k_days_sum"] == pytest.approx(0.02 - 4 * 0.018, abs=1e-12)

    # One book-date survives, the good one: dollars per retained book-date.
    assert s["mean_ex_worst_k_days"] == pytest.approx(
        float(frame.filter(pl.col("day") == "good")["net"].sum()), abs=1e-12
    )
    assert s["mean_ex_worst_k_days"] == pytest.approx(0.02, abs=1e-12)
    assert s["mean_ex_best_k_days"] == pytest.approx(-0.018, abs=1e-12)
    assert s["deficit_carried_by_few_bad_days"] is True

    # The per-claim-slot mean is a DIFFERENT denominator, reported separately; on the
    # best side it is a third of the book-date mean and must not be read as one.
    assert s["secondary_mean_ex_worst_k_days_per_claim_slot"] == pytest.approx(0.02, abs=1e-12)
    assert s["secondary_mean_ex_best_k_days_per_claim_slot"] == pytest.approx(-0.006, abs=1e-12)


def test_book_date_mean_is_not_the_claim_slot_mean():
    """Good date carries 3 claims and the worst date 2, so the two units must differ."""
    frame = pl.DataFrame(
        {
            "day": ["good", "good", "good", "bad1", "bad2", "bad2"],
            "net": [0.02, 0.02, 0.02, -0.02, -0.015, -0.015],
        }
    )
    total = float(frame["net"].sum())
    s = _day_sensitivity(frame, total, 1)

    assert s["total_slots"] == 6
    assert s["worst_k_days_slots"] == 2
    assert s["best_k_days_slots"] == 3
    assert s["worst_k_dates_removed"] == ["bad2"]
    assert s["best_k_dates_removed"] == ["good"]

    # Retained book-dates {good, bad1} hold 0.04 dollars over 2 dates: 0.02 per date.
    retained_days = frame.filter(pl.col("day") != "bad2")
    assert s["mean_ex_worst_k_days"] == pytest.approx(
        float(retained_days.group_by("day").agg(pl.col("net").sum())["net"].sum()) / 2.0,
        abs=1e-12,
    )
    # Retained claim-slots hold the same 0.04 dollars over 4 slots: 0.01 per slot.
    assert s["secondary_mean_ex_worst_k_days_per_claim_slot"] == pytest.approx(0.01, abs=1e-12)
    assert s["mean_ex_best_k_days"] == pytest.approx(-0.025, abs=1e-12)
    assert s["secondary_mean_ex_best_k_days_per_claim_slot"] == pytest.approx(
        -0.05 / 3.0, abs=1e-12
    )
    assert s["deficit_carried_by_few_bad_days"] is True
