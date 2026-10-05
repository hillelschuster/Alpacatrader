"""CASH-ADMISSION regressions for the owned-claim core engine.

In cash admission the same original roster starts as pure cash: no initial order, no
reservation, no entry fee. Every claim owns its own ``1/n`` slot, a FRESH buy must clear
the round-trip hurdle on the forecast's GROSS magnitude (entry is not sunk yet), an OWNED
position is released on the SIGN of the forecast alone (cost hysteresis), a claim may
spend only its own settled slot, and a cycle counts an ACTUAL executed buy.

Each test drives ``simulate(..., admission="cash")`` on a tiny hand-built minute corpus
and asserts the money that came out of it: order count, fee dollars, deployed notional,
share counts, per-claim slot balances, UNKNOWN flags and the entry/re-entry counters.
Nothing here asserts source text, wiring or call forwarding; the only parity case is the
consumer-visible default output of the untouched upfront engine.
"""

import sys
from pathlib import Path

# `scripts` package for the producer modules, plus their own sibling directory so their
# top-level `import lifecycle_study` resolves the same way it does in production runs.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from scripts.owned_claim_replay import simulate  # noqa: E402

SIDE = 0.005
HURDLE = 2 * SIDE / (1 - SIDE)  # the engine's own derived round-trip fee hurdle
DEPLOY_HEADROOM = 0.90  # fixed execution headroom, not a tuned sizing rule
CASH_POLICY = "state:cash"
UPFRONT_POLICY = "state:reentry"
VOL = 1e6
MONEY = {"abs": 1e-12}


def _roster(names, *, status="filled", fill_px=10.0):
    return [
        {
            "day": "d1",
            "clock": 600,
            "rank": rank,
            "ticker": name,
            "session_end": 700,
            "decision_px": 10.0,
            "status": status,
            "fill_et": 600 if status == "filled" else None,
            "fill_px": fill_px if status == "filled" else None,
            "fill_volume": VOL if status == "filled" else None,
        }
        for rank, name in enumerate(names, start=1)
    ]


def _minute(t, ticker, px, sell_et=None, sell_px=None):
    return {
        "t": t,
        "ticker": ticker,
        "px": px,
        "sell_et": sell_et,
        "sell_px": sell_px,
        "sell_volume": VOL,
    }


def _pred(events, kind="repair"):
    """(ticker, minute) -> forecast on the model's own action column."""
    return {k: {"pred_state": v, "event_kind": kind} for k, v in events.items()}


def _cash(rows, roster, predictions, n, cycles="once"):
    return simulate(
        rows, roster, predictions, SIDE, n, CASH_POLICY, admission="cash", cycles=cycles
    )


def _member(members, ticker):
    return next(m for m in members if m["ticker"] == ticker)


def _buys(fills):
    return [f for f in fills if f["side"] == "buy"]


def _sells(fills):
    return [f for f in fills if f["side"] == "sell"]


def _deployed(fill):
    """Cash actually sent: the execution print plus its buy/sell fee."""
    return fill["gross_fraction"] + fill["fee_fraction"]


def _net_receipt(sell):
    """The actual net sale receipt that matures into the selling claim's own slot."""
    return sell["gross_fraction"] - sell["fee_fraction"]


# ------------------------------------------------------------------ no order, no attempt
def test_missing_market_data_is_known_flat_cash_with_no_order_and_no_fee():
    """A roster row with no observable data is a KNOWN cash fact: nothing was attempted."""
    rows = [
        _minute(600, "A", float("nan")),
        _minute(650, "A", float("nan")),
        _minute(700, "A", float("nan")),
    ]
    roster = _roster(["A"], status="missing")

    daily, members, fills = _cash(rows, roster, _pred({}), 1)

    assert daily["orders"] == 0
    assert daily["fees"] == pytest.approx(0.0, **MONEY)
    assert daily["ret"] == pytest.approx(0.0, **MONEY)
    assert daily["unknown"] is False
    assert daily["unknown_tickers"] == []
    assert daily["fresh_entries"] == 0
    assert daily["claims_with_entry"] == 0
    # The whole account is still the untouched slot: no fee, no mark, no attempt.
    assert daily["slot_total"] == pytest.approx(1.0, **MONEY)
    assert daily["slots_idle_end"] == pytest.approx(1.0, **MONEY)
    m = _member(members, "A")
    assert m["net_pnl"] == pytest.approx(0.0, **MONEY)
    assert m["fees"] == pytest.approx(0.0, **MONEY)
    assert m["fresh_entries"] == 0


def test_upfront_default_call_is_byte_identical_to_explicit_upfront():
    """The historical default path is untouched: same money, same keys, no cash ledger."""
    rows = [
        _minute(600, "A", 10.0, 600, 10.0),
        _minute(610, "A", 9.0, 610, 9.0),
        _minute(615, "A", 9.0, 620, 9.0),
        _minute(700, "A", 9.0, 700, 9.0),
    ]
    roster = _roster(["A"])
    predictions = _pred({("A", 610): -1.0, ("A", 615): 1.0})

    default = simulate(rows, roster, predictions, SIDE, 1, UPFRONT_POLICY)
    explicit = simulate(rows, roster, predictions, SIDE, 1, UPFRONT_POLICY, admission="upfront")
    assert default == explicit  # no default-path drift at all, inputs identical

    daily, members, fills = default
    # The consumer-visible historical outcome: the virtual entry, its release at a loss,
    # the repaired re-entry funded by the settled receipt, and the terminal liquidation.
    assert [f["reason"] for f in fills] == [
        "initial",
        "state_release",
        "repair_reentry",
        "terminal",
    ]
    assert daily["orders"] == 4
    assert daily["fees"] > 0.0
    assert daily["unknown"] is False
    assert _member(members, "A")["fees"] == pytest.approx(daily["fees"], **MONEY)
    assert _member(members, "A")["reentries"] == 1
    # The cash-mode ledger columns are not smuggled into the upfront frame.
    assert "hurdle" not in daily and "fresh_entries" not in daily
    assert "settled_receipt" not in _member(members, "A")


# ------------------------------------------------------------------ cost hysteresis
def test_sub_hurdle_positive_does_not_buy_but_holds_an_owned_position():
    """Same forecast, opposite action: a fresh claim needs the hurdle, an owned one does not."""
    rows = []
    for tk in ("A", "B"):
        rows += [
            _minute(600, tk, 10.0, 600, 10.0),
            _minute(605, tk, 10.0, 610, 10.0),  # A buys here, B is below the hurdle
            _minute(610, tk, 10.0, 610, 10.0),
            _minute(615, tk, 10.0, 620, 10.0),
            _minute(620, tk, 10.0, 625, 10.0),
            _minute(700, tk, 10.0, 700, 10.0),
        ]
    roster = _roster(["A", "B"])
    predictions = _pred(
        {
            ("A", 605): 0.5,
            ("B", 605): 0.005,
            ("A", 615): 0.005,
            ("B", 615): 0.005,
            ("A", 620): -0.002,
            ("B", 620): -0.002,
        }
    )

    daily, members, fills = _cash(rows, roster, predictions, 2)

    # A's entry clears the hurdle; B's positive forecast of the same sign does not buy.
    assert [f["ticker"] for f in _buys(fills)] == ["A"]
    assert [f["exec_et"] for f in _buys(fills)] == [610]
    assert _buys(fills)[0]["reason"] == "fresh_entry"
    # A's position is held on the sub-hurdle POSITIVE and released on a small NEGATIVE:
    # exit is the sign rule, entry is the hurdle rule.
    assert [f["exec_et"] for f in _sells(fills)] == [625]
    assert _sells(fills)[0]["reason"] == "state_release"
    assert not any(f["ticker"] == "B" for f in fills)
    assert daily["fresh_entries"] == 1
    assert daily["claims_with_entry"] == 1
    assert daily["unknown"] is False

    a = _member(members, "A")
    b = _member(members, "B")
    assert a["gross_pnl"] == pytest.approx(0.0, **MONEY)  # flat price, friction only
    assert a["net_pnl"] == pytest.approx(-daily["fees"], **MONEY)
    assert b["net_pnl"] == pytest.approx(0.0, **MONEY)
    assert daily["ret"] == pytest.approx(a["net_pnl"] + b["net_pnl"], **MONEY)
    # B's own slot is untouched and was never pooled with A's.
    assert b["cash_slot_end"] == pytest.approx(0.5, **MONEY)
    assert a["settled_receipt"] == pytest.approx(_net_receipt(_sells(fills)[0]), **MONEY)
    assert a["cash_slot_end"] == pytest.approx(
        0.5 - _deployed(_buys(fills)[0]) + a["settled_receipt"], **MONEY
    )
    assert daily["slots_idle_end"] == pytest.approx(
        a["cash_slot_end"] + b["cash_slot_end"], **MONEY
    )


def test_a_forecast_exactly_at_the_derived_hurdle_does_not_buy():
    """The hurdle is the derived round-trip fee 2s/(1-s), applied strictly and on the buy side."""
    rows = [
        _minute(600, "A", 10.0, 600, 10.0),
        _minute(605, "A", 10.0, 610, 10.0),
        _minute(700, "A", 10.0, 700, 10.0),
    ]
    roster = _roster(["A"])

    at_hurdle = _cash(rows, roster, _pred({("A", 605): HURDLE}), 1)
    assert at_hurdle[0]["hurdle"] == pytest.approx(HURDLE, **MONEY)
    assert _buys(at_hurdle[2]) == []
    assert at_hurdle[0]["ret"] == pytest.approx(0.0, **MONEY)
    assert at_hurdle[0]["fresh_entries"] == 0
    assert at_hurdle[0]["slots_idle_end"] == pytest.approx(1.0, **MONEY)

    just_over = _cash(rows, roster, _pred({("A", 605): HURDLE * (1 + 1e-9)}), 1)
    assert len(_buys(just_over[2])) == 1
    assert just_over[0]["fresh_entries"] == 1

    # A large NEGATIVE forecast is a release signal, not an entry: it cannot buy.
    sell_side = _cash(rows, roster, _pred({("A", 605): -HURDLE * 2}), 1)
    assert _buys(sell_side[2]) == []
    assert sell_side[0]["ret"] == pytest.approx(0.0, **MONEY)
    assert sell_side[0]["unknown"] is False


# ------------------------------------------------------------------ executed size / fees
def test_executed_buy_uses_the_decision_mark_size_and_pays_both_fee_legs():
    """Quantity is frozen at 0.90 of the slot over the CAUSAL mark, not the execution price."""
    rows = [
        _minute(600, "A", 10.0, 600, 10.0),
        _minute(605, "A", 10.0, 610, 9.6),  # decision mark 10.0, execution print 9.6
        _minute(610, "A", 9.6, 610, 9.6),
        _minute(615, "A", 9.6, 620, 9.6),
        _minute(620, "A", 9.6, 620, 9.6),
        _minute(700, "A", 9.6, 700, 9.6),
    ]
    roster = _roster(["A"])
    predictions = _pred({("A", 605): 0.5, ("A", 615): -0.5})

    daily, members, fills = _cash(rows, roster, predictions, 1)

    buy = _buys(fills)[0]
    expected_qty = DEPLOY_HEADROOM / (10.0 * (1 + SIDE))
    assert buy["decision_et"] == 605
    assert buy["exec_et"] == 610
    assert buy["px"] == pytest.approx(9.6, **MONEY)
    assert buy["shares_per_capital"] == pytest.approx(expected_qty, **MONEY)
    # The execution price is NOT the sizing basis: a mark-sized order would differ.
    assert buy["shares_per_capital"] != pytest.approx(
        DEPLOY_HEADROOM / (9.6 * (1 + SIDE)), abs=1e-6
    )
    # Cash out = quantity x execution print, grossed up by the buy fee.
    assert _deployed(buy) == pytest.approx(DEPLOY_HEADROOM * 9.6 / 10.0, **MONEY)
    assert buy["fee_fraction"] == pytest.approx(buy["gross_fraction"] * SIDE, **MONEY)

    # The round trip happens at the SAME price, so the whole loss is the two fee legs.
    sell = _sells(fills)[0]
    assert sell["exec_et"] == 620
    m = _member(members, "A")
    entry_fee = buy["gross_fraction"] * SIDE
    exit_fee = sell["gross_fraction"] * SIDE
    assert entry_fee > 0.0 and exit_fee > 0.0
    assert m["gross_pnl"] == pytest.approx(0.0, **MONEY)
    assert m["fees"] == pytest.approx(entry_fee + exit_fee, **MONEY)
    assert m["net_pnl"] == pytest.approx(-(entry_fee + exit_fee), **MONEY)
    assert daily["fees"] == pytest.approx(m["fees"], **MONEY)
    assert daily["ret"] == pytest.approx(m["net_pnl"], **MONEY)
    assert daily["unknown"] is False


# ------------------------------------------------------------------ slot segregation
def test_unused_global_cash_is_never_borrowed_across_claims():
    """Roster of 2 claims on a 3-view book: slots sum to 2/3 and the rest stays global."""
    rows = []
    for tk in ("A", "B"):
        rows += [
            _minute(600, tk, 10.0, 600, 10.0),
            _minute(605, tk, 10.0, 610, 10.0),
            _minute(610, tk, 10.0, 610, 10.0),
            _minute(700, tk, 10.0, 700, 10.0),
        ]
    roster = _roster(["A", "B"])  # two claims, but the view is n = 3
    predictions = _pred({("A", 605): 0.5, ("B", 605): 0.005})

    daily, members, fills = _cash(rows, roster, predictions, 3)

    assert daily["roster_claims"] == 2
    assert daily["slot_total"] == pytest.approx(2 / 3, **MONEY)
    assert daily["unassigned_at_start"] == pytest.approx(1 / 3, **MONEY)

    # A may spend its own slot and not one dollar more, even though a third of the
    # account sits unassigned and the whole of B's slot is idle.
    spends = _buys(fills)
    assert len(spends) == 1 and spends[0]["ticker"] == "A"
    spent = _deployed(spends[0])
    assert spent == pytest.approx(DEPLOY_HEADROOM * (1 / 3), **MONEY)
    assert spent < 1 / 3 < 2 / 3

    a = _member(members, "A")
    b = _member(members, "B")
    # Per-claim conservation: own slot + own matured receipts - own deployments.
    assert a["cash_slot_end"] == pytest.approx(1 / 3 - spent + a["settled_receipt"], **MONEY)
    assert b["cash_slot_end"] == pytest.approx(1 / 3, **MONEY)
    assert b["net_pnl"] == pytest.approx(0.0, **MONEY)
    assert daily["slots_idle_end"] == pytest.approx(2 / 3 - spent, **MONEY)
    # Cash conservation: the unassigned third was never spent, and the book is known.
    assert daily["ret"] == pytest.approx(-daily["fees"], **MONEY)
    assert daily["unknown"] is False


# ------------------------------------------------------------------ unresolved outcomes
def test_execution_gap_over_budget_is_unknown_and_never_resized():
    """The order was sized at the decision mark; a future print cannot stretch it."""

    def book(exec_px):
        rows = [
            _minute(600, "A", 10.0, 600, 10.0),
            _minute(605, "A", 10.0, 610, exec_px),
            _minute(610, "A", exec_px, 610, exec_px),
            _minute(700, "A", exec_px, 700, exec_px),
        ]
        return _cash(rows, _roster(["A"]), _pred({("A", 605): 0.5}), 1)

    over, over_members, over_fills = book(20.0)
    assert _buys(over_fills) == []  # no hindsight resize, no partial fill
    assert over["affordability_unknown"] == 1
    assert over["unknown"] is True
    assert over["ret"] is None
    assert over["orders"] == 0
    m = _member(over_members, "A")
    assert m["fresh_entries"] == 0
    assert m["net_pnl"] is None

    # The same decision at an affordable print commits exactly the mark-sized order.
    ok, _ok_members, ok_fills = book(10.0)
    assert len(_buys(ok_fills)) == 1
    assert _buys(ok_fills)[0]["shares_per_capital"] == pytest.approx(
        DEPLOY_HEADROOM / (10.0 * (1 + SIDE)), **MONEY
    )
    assert ok["affordability_unknown"] == 0
    assert ok["unknown"] is False
    assert ok["ret"] == pytest.approx(-ok["fees"], **MONEY)


def test_attempted_fresh_buy_unresolved_at_terminal_is_unknown_not_zero():
    """Nothing settled after the attempt: the book is UNKNOWN, not a known flat 0."""

    def book(exec_et, exec_px):
        rows = [
            _minute(600, "A", 10.0, 600, 10.0),
            _minute(690, "A", 10.0, exec_et, exec_px),
            _minute(700, "A", 10.0, 700, 10.0),
        ]
        return _cash(rows, _roster(["A"]), _pred({("A", 690): 0.5}), 1)

    unresolved, unresolved_members, unresolved_fills = book(None, None)
    assert _buys(unresolved_fills) == []
    # An execution opening that never printed leaves nothing to resolve later, so the
    # attempt is UNKNOWN through its own channel rather than a pending order.
    assert unresolved["unknown"] is True
    assert unresolved["ret"] is None
    assert unresolved["unknown_tickers"] == ["A"]
    assert _member(unresolved_members, "A")["fresh_entries"] == 0
    assert _member(unresolved_members, "A")["net_pnl"] is None

    # The same attempt that does settle before the boundary is a known book, not UNKNOWN.
    resolved, resolved_members, resolved_fills = book(695, 10.0)
    assert len(_buys(resolved_fills)) == 1
    assert resolved["unresolved_fresh"] is False
    assert resolved["unknown"] is False
    assert resolved["ret"] == pytest.approx(-resolved["fees"], **MONEY)
    assert _member(resolved_members, "A")["fresh_entries"] == 1


def test_finite_but_out_of_session_buy_is_unknown_and_names_the_claim():
    """A real price at an execution minute past the close is UNKNOWN, and only for A."""

    def book(a_exec_et):
        rows = []
        for tk in ("A", "B"):
            rows += [
                _minute(600, tk, 10.0, 600, 10.0),
                _minute(690, tk, 10.0, a_exec_et if tk == "A" else 700, 10.0),
                _minute(700, tk, 10.0, 700, 10.0),
            ]
        # A's forecast clears the hurdle; B's never does, so B never trades at all.
        return _cash(rows, _roster(["A", "B"]), _pred({("A", 690): 0.5, ("B", 690): 0.005}), 2)

    late, late_members, late_fills = book(780)  # finite price, finite minute, past session_end
    assert late_fills == []
    assert late["orders"] == 0
    assert late["unresolved_fresh"] is True
    assert late["unresolved_fresh_tickers"] == ["A"]  # exactly the late fill, not the peer
    assert late["unknown"] is True
    assert late["ret"] is None
    # The claim whose funding attempt never resolved is NAMED, not reported as a known 0.
    assert late["unknown_tickers"] == ["A"]
    a = _member(late_members, "A")
    assert a["net_pnl"] is None
    assert a["fresh_entries"] == 0
    # B never attempted an order, so B's own book is a KNOWN flat 0 with a whole slot.
    b = _member(late_members, "B")
    assert b["net_pnl"] == pytest.approx(0.0, **MONEY)
    assert b["fees"] == pytest.approx(0.0, **MONEY)
    assert b["fresh_entries"] == 0
    assert b["cash_slot_end"] == pytest.approx(0.5, **MONEY)
    assert late["slots_idle_end"] == pytest.approx(1.0, **MONEY)

    # Only the CLOCK of the opening is different in the control: the same decision minute
    # with an execution minute inside the session is a known book.
    ok, ok_members, ok_fills = book(695)
    assert len(_buys(ok_fills)) == 1
    assert ok["unresolved_fresh"] is False
    assert ok["unresolved_fresh_tickers"] == []
    assert ok["unknown"] is False
    assert ok["unknown_tickers"] == []
    assert ok["ret"] == pytest.approx(-ok["fees"], **MONEY)
    assert _member(ok_members, "A")["fresh_entries"] == 1
    assert _member(ok_members, "B")["net_pnl"] == pytest.approx(0.0, **MONEY)


# ------------------------------------------------------------------ cycles
def _cycle_book():
    """A enters at 605, sells at a LOSS at 620, and is offered a big positive again."""
    rows = [
        _minute(600, "A", 10.0, 600, 10.0),
        _minute(605, "A", 10.0, 610, 10.0),  # entry decided here, executed at 610
        _minute(610, "A", 10.0, 610, 10.0),
        _minute(615, "A", 9.0, 620, 9.0),  # release decided here, executed at 620 at a loss
        _minute(620, "A", 9.0, 620, 9.0),
        _minute(621, "A", 9.0, 625, 9.0),  # re-entry decided the minute the receipt lands
        _minute(625, "A", 9.0, 625, 9.0),
        _minute(630, "A", 9.0, 630, 9.0),
        _minute(700, "A", 9.0, 700, 9.0),
    ]
    predictions = _pred(
        {
            ("A", 605): 0.5,
            ("A", 615): -0.5,
            ("A", 620): 0.5,
            ("A", 621): 0.5,
            ("A", 630): -0.5,
        }
    )
    return rows, _roster(["A"]), predictions


def test_once_counts_one_entry_and_never_re_enters_after_it():
    rows, roster, predictions = _cycle_book()
    daily, members, fills = _cash(rows, roster, predictions, 1, cycles="once")

    buys = _buys(fills)
    assert [f["decision_et"] for f in buys] == [605]
    assert daily["fresh_entries"] == 1
    assert daily["claims_with_entry"] == 1
    m = _member(members, "A")
    assert m["fresh_entries"] == 1
    assert m["reentries"] == 0  # the first fresh buy is an ENTRY, not a re-entry
    # The exit printed 9.0 against a 10.0 entry, so the book is a real loss, not just fees.
    assert m["gross_pnl"] < 0.0
    assert m["net_pnl"] == pytest.approx(m["gross_pnl"] - m["fees"], **MONEY)
    assert daily["ret"] < 0.0  # a losing exit is still a book, not a reason to re-enter


def test_repeat_re_entry_is_funded_only_by_the_settled_owner_receipt():
    rows, roster, predictions = _cycle_book()
    daily, members, fills = _cash(rows, roster, predictions, 1, cycles="repeat")

    buys = _buys(fills)
    assert len(buys) == 2
    assert [f["decision_et"] for f in buys] == [605, 621]
    # The sale executed at 620 and its receipt settles at 621; the offer that arrived on
    # the sale's own minute did NOT fund a re-entry.
    assert not any(f["decision_et"] == 620 for f in buys)

    first_sale = _sells(fills)[0]
    assert first_sale["exec_et"] == 620
    receipt = _net_receipt(first_sale)
    assert first_sale["fee_fraction"] > 0.0
    assert receipt < first_sale["gross_fraction"]
    # The losing exit leaves the claim with LESS than its original slot, and the re-entry
    # may only spend what actually settled back into its own slot.
    leftover = 1.0 - _deployed(buys[0])
    budget = leftover + receipt
    assert leftover == pytest.approx(0.1, **MONEY)
    assert receipt < 1.0
    assert _deployed(buys[1]) == pytest.approx(DEPLOY_HEADROOM * budget, **MONEY)
    assert _deployed(buys[1]) < DEPLOY_HEADROOM * 1.0  # not re-sized to the original slot
    assert buys[1]["shares_per_capital"] == pytest.approx(
        DEPLOY_HEADROOM * budget / (9.0 * (1 + SIDE)), **MONEY
    )

    m = _member(members, "A")
    assert m["fresh_entries"] == 2
    assert m["reentries"] == 1  # exactly one cycle after the first entry
    assert m["settled_receipt"] > receipt  # both sales' receipts matured into one slot
    assert m["cash_slot_end"] == pytest.approx(
        1.0 - sum(_deployed(f) for f in buys) + m["settled_receipt"], **MONEY
    )
    assert daily["fresh_entries"] == 2
    assert daily["claims_with_entry"] == 1
    assert daily["unknown"] is False


# ------------------------------------------------------------------ calendar-exit action
# The published CALENDAR_EXIT action code. The converter's contract is EQUALITY against the
# sentinel it is handed, and the engine only ever reads the sign, so the flow below is the
# same for whatever value the score set publishes; nothing here needs the value backend.
CALENDAR_EXIT_CODE = -1.0e6


def _calendar_exit_book():
    """A holds from 610; at 615 the published action is a calendar exit; 700 prints 8.8."""
    rows = [
        _minute(600, "A", 10.0, 600, 10.0),
        _minute(605, "A", 10.0, 610, 10.0),  # fresh entry decided here
        _minute(610, "A", 10.0, 610, 10.0),
        _minute(615, "A", 9.5, 620, 9.5),  # the calendar-exit decision minute
        _minute(620, "A", 9.5, 620, 9.5),
        _minute(700, "A", 8.8, 700, 8.8),  # only reached if the claim is still held
    ]
    return rows, _roster(["A"])


def test_calendar_exit_action_sells_the_held_claim_at_the_next_open():
    """The converted action releases a held claim now, not at the terminal liquidation."""
    from scripts.owned_claim_cash_first_replay import FEE_FACTOR_PUBLISHER, to_engine_action

    sentinel = CALENDAR_EXIT_CODE
    rows, roster = _calendar_exit_book()

    entry_action = to_engine_action(0.5, sentinel, FEE_FACTOR_PUBLISHER)
    exit_action = to_engine_action(sentinel, sentinel, FEE_FACTOR_PUBLISHER)
    # A financial forecast is converted into gross units; the ACTION code is forwarded
    # exactly, because rescaling an action code would fabricate a dollar magnitude.
    assert entry_action == pytest.approx(0.5 / FEE_FACTOR_PUBLISHER, **MONEY)
    assert exit_action == sentinel

    daily, members, fills = _cash(
        rows, roster, _pred({("A", 605): entry_action, ("A", 615): exit_action}), 1
    )

    exit_fill = _sells(fills)[0]
    assert exit_fill["decision_et"] == 615
    assert exit_fill["exec_et"] == 620
    assert exit_fill["px"] == pytest.approx(9.5, **MONEY)
    assert exit_fill["reason"] == "state_release"
    assert daily["exit_times"] == [620]
    # The claim was released at 9.5, so nothing is left to liquidate at the 8.8 terminal.
    assert not any(f["exec_et"] == 700 for f in fills)
    assert _member(members, "A")["remaining_shares"] == pytest.approx(0.0, **MONEY)

    # Same book, same rows: dropping the action to a missing forecast makes the engine
    # HOLD, so the position is deferred to the terminal print and books worse.
    dropped_daily, dropped_members, dropped_fills = _cash(
        rows, roster, _pred({("A", 605): entry_action, ("A", 615): None}), 1
    )
    deferred = _sells(dropped_fills)[0]
    assert deferred["reason"] == "terminal"
    assert deferred["exec_et"] == 700
    assert deferred["px"] == pytest.approx(8.8, **MONEY)
    assert dropped_daily["exit_times"] == [700]
    assert _member(dropped_members, "A")["net_pnl"] < _member(members, "A")["net_pnl"]


def test_calendar_exit_action_can_never_buy_fresh_capital():
    """The action code is a release, not a fresh positive: an unheld claim stays all cash."""
    from scripts.owned_claim_cash_first_replay import FEE_FACTOR_PUBLISHER, to_engine_action

    rows, roster = _calendar_exit_book()
    action = to_engine_action(CALENDAR_EXIT_CODE, CALENDAR_EXIT_CODE, FEE_FACTOR_PUBLISHER)

    # A non-finite forecast has no missingness or zero fallback: the engine reads both as
    # HOLD, so the converter refuses instead of inventing an action.
    for bad in (None, float("nan"), float("inf")):
        with pytest.raises(SystemExit):
            to_engine_action(bad, CALENDAR_EXIT_CODE, FEE_FACTOR_PUBLISHER)

    daily, members, fills = _cash(rows, roster, _pred({("A", 615): action}), 1)

    assert fills == []
    assert daily["fresh_entries"] == 0
    assert daily["ret"] == pytest.approx(0.0, **MONEY)
    assert daily["fees"] == pytest.approx(0.0, **MONEY)
    assert _member(members, "A")["cash_slot_end"] == pytest.approx(1.0, **MONEY)


@pytest.mark.parametrize(
    "round_trip_bps, enters", [(25, True), (50, True), (100, False), (150, False)]
)
def test_declared_cost_hurdle_controls_entry_and_charges_both_flat_price_legs(
    round_trip_bps, enters
):
    side = round_trip_bps / 20000.0
    rows = [
        _minute(600, "A", 10.0, 600, 10.0),
        _minute(610, "A", 10.0, 610, 10.0),
        _minute(615, "A", 10.0, 615, 10.0),
    ]
    daily, members, fills = simulate(
        rows,
        _roster(["A"]),
        _pred({("A", 610): 0.007, ("A", 615): -0.001}),
        side,
        1,
        CASH_POLICY,
        admission="cash",
        cycles="once",
    )
    expected_fee = 2 * side * DEPLOY_HEADROOM / (1 + side) if enters else 0.0
    assert daily["fresh_entries"] == int(enters)
    assert daily["orders"] == (2 if enters else 0)
    assert daily["ret"] == pytest.approx(-expected_fee, **MONEY)
    assert daily["fees"] == pytest.approx(expected_fee, **MONEY)
    assert members[0]["net_pnl"] == pytest.approx(-expected_fee, **MONEY)
    assert sum(f["fee_fraction"] for f in fills) == pytest.approx(expected_fee, **MONEY)
