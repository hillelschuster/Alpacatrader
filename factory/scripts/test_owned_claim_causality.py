"""Causality / economics regressions for the owned-claim stop / re-entry pipeline.

Each test freezes one consumer-visible financial invariant on a tiny hand-built corpus:

  * as-of peer/scanner context cannot be moved by a peer's FUTURE fill or final status;
  * a release that reprints an already-seen opening carries zero incremental price, not
    the strict next-liquidation walk's later print;
  * a known early release (11 vs current 10) stays known when an unused terminal is
    broken, and an unavailable chosen exit is excluded rather than turned into 0;
  * a claim's settled sale receipt (not its net P&L) funds exactly one re-entry whose
    share count is frozen at the causal decision mark, with an unaffordable execution
    gap reported UNKNOWN instead of resized;
  * sale proceeds are spendable only after the execution minute, never on the same open;
  * ``policy_return`` enters without the strict-control ``classify``/``fold_weights`` gate.

No source-text / wiring / defaults / mock-echo assertions: every assertion compares
realised labels, share counts or cash sentinels on a corpus with a unique financial path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
import pytest  # noqa: E402
from scripts.owned_claim_observable_peers import (  # noqa: E402
    PEER_COLS,
    SCAN_COLS,
    add_observable_context,
)
from scripts.owned_claim_policy_targets import (  # noqa: E402
    chronological_successor,
    policy_wait_targets,
    target_support,
)
from scripts.owned_claim_replay import simulate  # noqa: E402


# ---------------------------------------------------------------- observable peers
def _obs_row(t, ticker, px, rank, dvol5):
    return {
        "day": "d1",
        "clock": 540,
        "t": t,
        "ticker": ticker,
        "px": px,
        "decision_px": 10.0,
        "px_age": 0.0,
        "dvol5": dvol5,
        "rank": rank,
    }


def _obs_panel(c_future_px=10.35, c_status="filled", c_filled=True):
    """Three-name roster at t=600/605; only the C name's future state is parameterized."""
    rows = []
    for t in (600, 605):
        rows.append(_obs_row(t, "A", 10.1 if t == 600 else 10.15, 1, 1.0))
        rows.append(_obs_row(t, "B", 10.2 if t == 600 else 10.25, 2, 2.0))
        rows.append(
            {
                **_obs_row(t, "C", 10.3 if t == 600 else c_future_px, 3, 3.0),
                "status": c_status,
                "filled": c_filled,
            }
        )
    return pl.DataFrame(rows)


def test_peer_scanner_context_ignores_future_fill_and_status():
    base = add_observable_context(_obs_panel())
    cols = [c for c in (*PEER_COLS, *SCAN_COLS) if c in base.columns]
    a600 = base.filter((pl.col("t") == 600) & (pl.col("ticker") == "A")).row(0, named=True)
    # anchor the corpus: real self-excluded peer mean and full-support scanner mean
    assert abs(a600["sib_ret_mean"] - 0.025) < 1e-12
    assert abs(a600["scan3_mean_ret"] - 0.02) < 1e-12
    assert a600["scan3_known"] == 3 and a600["scan3_members"] == 3

    # C is later missing / unfilled and its future price is gone: the t=600 roster
    # numbers for every name must be byte-identical to the filled variant.
    alt = add_observable_context(
        _obs_panel(c_future_px=float("nan"), c_status="missing", c_filled=False)
    )
    assert (
        base.filter(pl.col("t") == 600).select(cols).to_dicts()
        == alt.filter(pl.col("t") == 600).select(cols).to_dicts()
    )

    # truncating every future row leaves the same prefix: no lookahead into later t
    trunc = add_observable_context(_obs_panel().filter(pl.col("t") == 600))
    assert trunc.select(cols).to_dicts() == base.filter(pl.col("t") == 600).select(cols).to_dicts()


# ---------------------------------------------------------------- chronological targets
def test_chronological_release_at_reprint_is_zero_not_strict_walk():
    # claim events: A(t600,px10) -> B(t605,open620) -> C(t614,reprints620,px10)
    #               -> E(t625,px10) -> terminal(t690,px12); previous round releases at C.
    seq = np.array([0, 1, 2, 3, 4], dtype=np.int64)
    grp = np.zeros(5, dtype=np.int64)
    succ = chronological_successor(seq, grp)
    assert succ.tolist() == [1, 2, 3, 4, -1]

    price = np.array([10.0, 10.0, 10.0, 10.0, 12.0])
    entry = np.array([10.0] * 5)
    executable = np.ones(5, dtype=bool)
    pred = np.array([0.1, 0.1, -0.5, 0.1, 0.1])
    dest, known, _ = policy_wait_targets(price, entry, executable, succ, pred, 1)

    assert known[0] and dest[0] == 2  # A's wait exits at C, where it got out of the policy
    assert dest[0] != 4  # not the strict E->terminal later print
    y = (price[dest[0]] - price[0]) / entry[0]
    assert abs(y) < 1e-12  # reprint of the same opening == zero incremental, not +0.20


def test_early_release_stays_known_when_terminal_missing():
    # A(t600,px10) -> B(t610,px11, release) -> terminal(t690, unusable/missing)
    succ = np.array([1, 2, -1], dtype=np.int64)
    price = np.array([10.0, 11.0, np.nan])
    entry = np.array([10.0, 10.0, 10.0])
    executable = np.array([True, True, False])
    pred = np.array([0.1, -0.5, 0.1])
    dest, known, _ = policy_wait_targets(price, entry, executable, succ, pred, 1)

    assert known[0] and dest[0] == 1  # released at 11; the broken suffix is irrelevant
    y = (price[dest[0]] - price[0]) / entry[0]
    assert abs(y - 0.1) < 1e-12
    support = target_support(succ, executable, entry, dest, known)
    assert support["chosen_exit_unknown"] == 1  # missing terminal reported, not used as a label
    assert support["known_fit"] == 1


def test_unknown_chosen_exit_is_excluded_not_zeroed():
    # same shape, but the release event itself cannot be sold
    succ = np.array([1, 2, -1], dtype=np.int64)
    price = np.array([10.0, 12.0, np.nan])
    entry = np.array([10.0, 10.0, 10.0])
    executable = np.array([True, False, False])
    pred = np.array([0.1, -0.5, 0.1])
    dest, known, _ = policy_wait_targets(price, entry, executable, succ, pred, 1)

    assert not known.any()
    assert dest[0] == -1  # unavailable exit -> UNKNOWN sentinel, never a zeroed 0.0 label
    support = target_support(succ, executable, entry, dest, known)
    assert support["chosen_exit_unknown"] == 1
    assert support["known_fit"] == 0


# ---------------------------------------------------------------- replay / re-entry
def _reentry_book(exec_px):
    """One claim that sells at a loss (9 < 10 entry) then tries to re-enter at t=615."""
    roster = [
        {
            "day": "d1",
            "clock": 600,
            "rank": 1,
            "ticker": "A",
            "session_end": 700,
            "decision_px": 10.0,
            "status": "filled",
            "fill_et": 600,
            "fill_px": 10.0,
            "fill_volume": 1e6,
        }
    ]
    rows = [
        {"t": 600, "ticker": "A", "px": 10.0, "sell_et": 600, "sell_px": 10.0, "sell_volume": 1e6},
        {"t": 610, "ticker": "A", "px": 9.0, "sell_et": 610, "sell_px": 9.0, "sell_volume": 1e6},
        {
            "t": 615,
            "ticker": "A",
            "px": 9.0,
            "sell_et": 620,
            "sell_px": exec_px,
            "sell_volume": 1e6,
        },
        {"t": 700, "ticker": "A", "px": 9.0, "sell_et": 700, "sell_px": 9.0, "sell_volume": 1e6},
    ]
    predictions = {
        ("A", 610): {"pred_state": -1.0, "event_kind": "stop"},
        ("A", 615): {"pred_state": 1.0, "event_kind": "repair"},
    }
    return rows, roster, predictions


def test_losing_receipt_funds_one_reentry_at_decision_mark():
    shares_by_price = {}
    for exec_px in (9.5, 9.9):
        rows, roster, predictions = _reentry_book(exec_px)
        daily, members, fills = simulate(rows, roster, predictions, 0.005, 1, "state:reentry")
        sale = next(f for f in fills if f["side"] == "sell" and f["exec_et"] == 610)
        assert sale["gross_fraction"] < 1.0  # sold below the 1.0 entry notional (a loss)
        budget = sale["gross_fraction"] - sale["fee_fraction"]  # settled receipt, not net P&L
        reentries = [f for f in fills if f["reason"] == "repair_reentry"]
        assert len(reentries) == 1
        f = reentries[0]
        assert f["side"] == "buy" and f["decision_et"] == 615 and f["exec_et"] == 620
        # share count frozen from the causal decision mark (9), independent of exec price
        assert abs(f["shares_per_capital"] - 0.9 * budget / (9.0 * 1.005)) < 1e-12
        shares_by_price[exec_px] = f["shares_per_capital"]
        assert members[0]["ticker"] == "A" and members[0]["reentries"] == 1
        assert not daily["unknown"]

    # two affordable future prices buy the identical size: no hindsight resizing
    assert shares_by_price[9.5] > 0
    assert abs(shares_by_price[9.5] - shares_by_price[9.9]) < 1e-15


def test_unaffordable_reentry_gap_is_unknown_not_resized():
    rows, roster, predictions = _reentry_book(20.0)
    daily, members, fills = simulate(rows, roster, predictions, 0.005, 1, "state:reentry")

    assert not any(f["reason"] == "repair_reentry" for f in fills)  # never resized to fit
    assert daily["affordability_unknown"] == 1
    assert daily["unknown"]
    m = next(x for x in members if x["ticker"] == "A")
    assert m["reentries"] == 0 and m["net_pnl"] is None


def test_sale_proceeds_are_not_spendable_on_the_sale_open():
    """A's sell is decided at t=605 (executes at 610); B first allocates at 610 and 611."""
    roster = [
        {
            "day": "d1",
            "clock": 600,
            "rank": 1,
            "ticker": "A",
            "session_end": 700,
            "decision_px": 10.0,
            "status": "filled",
            "fill_et": 600,
            "fill_px": 10.0,
            "fill_volume": 1e6,
        },
        {
            "day": "d1",
            "clock": 600,
            "rank": 2,
            "ticker": "B",
            "session_end": 700,
            "decision_px": 10.0,
            "status": "filled",
            "fill_et": 600,
            "fill_px": 10.0,
            "fill_volume": 1e6,
        },
    ]
    rows = [
        {"t": 600, "ticker": "A", "px": 10.0, "sell_et": 600, "sell_px": 10.0, "sell_volume": 1e6},
        {"t": 605, "ticker": "A", "px": 9.0, "sell_et": 610, "sell_px": 9.0, "sell_volume": 1e6},
        {"t": 610, "ticker": "A", "px": 9.0, "sell_et": 610, "sell_px": 9.0, "sell_volume": 1e6},
        {"t": 600, "ticker": "B", "px": 10.0, "sell_et": 600, "sell_px": 10.0, "sell_volume": 1e6},
        {"t": 610, "ticker": "B", "px": 10.0, "sell_et": 615, "sell_px": 10.0, "sell_volume": 1e6},
        {"t": 611, "ticker": "B", "px": 10.0, "sell_et": 615, "sell_px": 10.0, "sell_volume": 1e6},
        {"t": 700, "ticker": "B", "px": 9.0, "sell_et": 700, "sell_px": 9.0, "sell_volume": 1e6},
    ]
    predictions = {
        ("A", 605): {"pred_state": -1.0, "event_kind": "stop"},
        ("B", 610): {"pred_state": 1.0, "event_kind": "repair"},
        ("B", 611): {"pred_state": 1.0, "event_kind": "repair"},
    }
    daily, _members, fills = simulate(rows, roster, predictions, 0.005, 2, "state:allocate")

    # no allocation at the 610 open where A's sale executes -> proceeds not spendable yet
    assert not any(
        f["side"] == "buy" and f["ticker"] == "B" and f["decision_et"] == 610 for f in fills
    )
    # the receipt settles at 611 and only then funds one allocation
    funded = [
        f["shares_per_capital"]
        for f in fills
        if f["side"] == "buy" and f["ticker"] == "B" and f["decision_et"] == 611
    ]
    assert len(funded) == 1 and funded[0] > 0
    assert not daily["unknown"]


# ---------------------------------------------------------------- policy_return entry
def _value_module():
    """Import owned_claim_value, skipping if the optional LightGBM backend is absent.

    ``method_outcome_prep`` under test is pure planning/target logic, but the module
    imports lightgbm at load time; a genuinely missing backend skips explicitly rather
    than fabricating a fake module that would let the import "succeed".
    """
    pytest.importorskip("lightgbm")
    import importlib

    return importlib.import_module("scripts.owned_claim_value")


def test_policy_return_enters_without_strict_control_gate():
    val = _value_module()

    # Events 0 and 1 reprint the same delayed opening (620), so the strict next-liquidation
    # pointer jumps 0 -> 2 (skipping the intervening emitted event) and no strictly-later
    # executable control label exists; the chronological walk must still see event 1.
    df = pl.DataFrame(
        {
            "day": ["d1", "d1", "d1"],
            "clock": [540, 540, 540],
            "rank": [1, 1, 1],
            "ticker": ["A", "A", "A"],
            "sequence": [0, 1, 2],
            "next_sequence": [2, -1, -1],
            "t": [605, 614, 700],
            "session_end": [700, 700, 700],
            "event_kind": ["release", "release", "terminal"],
            "sell_px": [620.0, 620.0, 620.0],
            "sell_et": [620, 620, 700],
            "next_sell_px": [-1.0, -1.0, -1.0],
            "next_sell_et": [-1, -1, -1],
            "fill_px": [620.0, 620.0, 620.0],
            "_ri": [0, 1, 2],
        }
    )

    # real CLI preparation: successor / executable / calendar-terminal from the frame
    succ, executable, calendar_terminal = val.chronological_metadata(
        df,
        val.pd_num(df, "sell_px"),
        val.pd_num(df, "session_end"),
        val.pd_num(df, "sell_et"),
    )
    assert succ.tolist() == [1, 2, -1]
    assert executable.tolist() == [True, True, True]
    assert calendar_terminal.tolist() == [False, False, True]

    prep = val.method_outcome_prep(df, succ, executable, calendar_terminal, "policy_return")
    assert prep["nx"] is None and prep["status"] is None and prep["next_sell_px"] is None
    assert prep["label_status"].tolist() == ["wait", "wait", "terminal"]
    assert prep["reasons"]["wait_rows"] == 2 and prep["reasons"]["unknown_rows"] == 0

    # the same corpus is untrainable for the strict control gate, so policy_return must
    # enter without it rather than inheriting the no-valid-rows SystemExit.
    with pytest.raises(SystemExit):
        val.method_outcome_prep(df, succ, executable, calendar_terminal, "value_iteration")

    # and the policy_return label for the delayed reprint is a KNOWN zero, not missing
    price = np.array([620.0, 620.0, 620.0])
    entry = np.array([620.0, 620.0, 620.0])
    pred = np.array([0.1, 0.1, -0.5])
    dest, known, _ = policy_wait_targets(price, entry, executable, succ, pred, 1)
    assert known[0] and dest[0] == 2
    y = (price[dest[0]] - price[0]) / entry[0]
    assert abs(y) < 1e-12
