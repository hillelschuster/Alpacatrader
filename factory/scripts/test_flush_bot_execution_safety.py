#!/usr/bin/env python3
"""Execution-safety broker-state regressions for flush_bot.py.

Deterministic, offline, no live API. Each case drives a real flush_bot entry
point (``poll`` / ``manage_symbol`` / ``sync_fills`` / ``startup_reconcile``)
against an in-memory broker that models ASYNCHRONOUS broker transitions:

  * ``cancel()`` only REQUESTS a cancel: the order stays on the book with
    status ``pending_cancel`` until the test resolves it to a terminal status
    and removes it. No test may treat a cancel request as an applied cancel.
  * ``order(id)`` reports broker truth, which may be ``None`` (lookup
    unavailable) or ``pending_cancel`` separately from the open snapshot.

Assertions read broker state (owned open buys/sells with qty and limit,
cancels, closes, held quantity, protection coverage) and the lifecycle meta
keys; they never assert journal wording, call counts, source text, or
incidental defaults.

Cases (one per validated execution-apparatus finding):

  resize_waits_cancel      a resized OCO is not replaced until every cancelled
                           protective order is broker-terminal; intervening
                           held-qty growth is covered exactly once
  resize_partial_fill      a protective OCO that fills during a resize cancel
                           resolves it (filled is terminal); the residual held
                           qty is protected exactly once
  stale_held_after_filled  a filled protective OCO never lets a stale held
                           snapshot re-protect or market-close (pre & post tl30)
  missing_position_cached  a working cached OCO is retained while the position
                           snapshot is missing, and not duplicated when it returns
  overdue_cached_working   an overdue working cached OCO is cancelled, waited to
                           terminal, then flattened exactly once
  cached_oco_pending       a cached OCO absent from the snapshot but None/
                           nonterminal at the broker is never re-submitted
  due_exit_pending_cancel  an overdue exit does not market-close while a
                           cancelled protective order is unconfirmed (incl. an
                           intervening partial sell fill); a filled protective
                           OCO means the book already exited
  poll_single_oco          a fresh fill inside one poll produces exactly one
                           OCO (no duplicate from the stale same-poll snapshot)
  entry_replace_terminal   a deferred bid replacement needs a terminal no-
                           position outcome: absent-from-snapshot plus a
                           None/pending_cancel lookup keeps the old order id
  overdue_partial          a rejected protection submit cannot abort an
                           overdue partial entry exit, and other symbols are
                           still managed in the same poll
  stale_cumulative_fill    a cumulative entry fill that already exited is not
                           replayed; the new residual OCO is the exact unsold qty
  monotonic_lifetime_sales cumulative sold shares never let a fully-exited
                           lifetime re-protect a ghost quantity
  partial_fill_then_cancel a partially-filled then canceled protective OCO
                           yields the exact residual and its fill is not replayed
  market_close_fill        an actually-filled market close never consumes a
                           later entry residual (protection or a second close)
  accepted_close_rejected  an accepted close that is later rejected is retried
                           exactly once, with no duplicate while unresolved
  oco_family               Alpaca OCO parent/child legs: a stop child fill is
                           accounted once (nested+flat dedupe) and the family
                           stays working until every leg is terminal; foreign
                           sells are never adopted
  failed_close             a failed market close is not latched and is retried;
                           an accepted close is never duplicated
  dead_cached_oco          a cached-but-dead OCO is reconciled from broker
                           truth and re-submitted; a covering OCO is not
  scan_isolation           a scanner exception disables new entries but
                           existing fills/protection/tl30/EOD still run
  restart_owned_orders     after a failed startup fetch an owned non-candidate
                           buy is tracked and cancelled at cutoff
  emptybars_expiry         empty bars still honour the stored 120-min bid expiry
  restart_existing_oco     a restart adopts a journal-attributed position whose
                           OWN OCO family is already live: the family and its
                           entry inventory are cached, so a stop fill never
                           re-protects or closes a stale held snapshot
  restart_hidden_family    a retained working family omitted from the open
                           snapshot is not duplicated; a foreign sell is never
                           adopted or cancelled
  restart_partial_family   an adopted family that already sold 40 against a
                           fresh NET 60 reprotects exactly the remaining 60
                           after its cancel (never 20, never a stale 100)
  hidden_multifamily_resize two restart-retained OWN families where one drops
                           out of the open snapshot: the resize cancels
                           BOTH, and no replacement (nor, when overdue, a
                           flatten) is submitted while the hidden root still
                           works or its lookup is unavailable; once it
                           resolves, one exact residual OCO covers the
                           unsold remainder
  filled_sibling_family    a FILLED family must not evict a sibling that is
                           merely WORKING from the tracked set (working
                           outranks filled): otherwise the ordinary protection
                           path clears the cache and submits a duplicate over
                           the live bracket, and the sibling's shares are
                           re-protected
  partial_fill_coverage    protection coverage is the family's REMAINING size,
                           not the root's original qty: an OCO whose target leg
                           filled 40 of 100 (broker shrinks the stop leg to 60)
                           still leaves 90 held under-protected after a late
                           entry fill, and must be replaced by exactly one 90 OCO
                           — including when the root is missing from the open
                           snapshot and only the lookup reports it working
                           — and the same holds when the position snapshot has
                           vanished: known inventory (entered - sold) is the
                           measure, and an unresolvable root means keep
                           waiting, never zero
                           — including that the held figure is recomputed after
                           a cancel discovers MORE sales: an exhausted ledger
                           protects nothing, and a lagging one protects the
                           reduced remainder, never the pre-accounting figure
  terminal_root_coverage   a CANCELED OCO parent's nominal shares are not
                           protection — only its unresolved legs' remaining
                           size counts, so a parent canceled with the stop leg
                           partially filled covers 60, not 100

Run: ``python factory/scripts/test_flush_bot_execution_safety.py``
"""

import contextlib
import functools
import json
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flush_bot as fb  # noqa: E402

ET = ZoneInfo("America/New_York")
OWN = fb.OWN_PREFIX

ET_NOW = datetime(2026, 9, 11, 11, 0, tzinfo=ET)
_DAY = pathlib.Path("/tmp/flushbot-exec-safety/2026-09-11")
TODAY = "2026-09-11"


def silent(fn):
    """Hermetic per-test isolation: swallow the journal sink (no files, no
    stdout) and pin day_dir to a path with no journal, restoring the previous
    bindings afterwards so a sibling test module's globals are untouched."""

    @functools.wraps(fn)
    def wrapper(*a, **k):
        prev_jlog, prev_day = fb.jlog, fb.day_dir
        fb.jlog = lambda event, **kw: None
        fb.day_dir = lambda: _DAY
        try:
            return fn(*a, **k)
        finally:
            fb.jlog, fb.day_dir = prev_jlog, prev_day

    return wrapper


@contextlib.contextmanager
def restart_journal(events):
    """Restart sandbox: a throwaway day directory holding ``events`` as its
    ``journal.jsonl``, the journal sink silenced and ``day_dir`` pinned to it,
    so ``startup_reconcile`` reads a controlled history. The operational bot
    journal is never written and both bindings are restored on exit."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="flushbot-restart-"))
    day = root / TODAY
    day.mkdir()
    (day / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    prev_jlog, prev_day = fb.jlog, fb.day_dir
    fb.jlog = lambda event, **kw: None
    fb.day_dir = lambda: day
    try:
        yield day
    finally:
        fb.jlog, fb.day_dir = prev_jlog, prev_day
        shutil.rmtree(root, ignore_errors=True)


def fill_event(sym, ts, oid, bid=1.8, c0=2.0):
    """One journalled entry fill, as ``startup_reconcile`` reads it back."""
    return {"ts": ts.isoformat(), "event": "fill", "symbol": sym, "B": bid, "c0": c0, "oid": oid}


def et_dt(t):
    """Normalize a journalled (ISO string) or broker timestamp for comparison."""
    t = datetime.fromisoformat(t) if isinstance(t, str) else t
    return t if t.tzinfo else t.replace(tzinfo=ET)


def live_protective_qty(br, sym="AAA"):
    """Quantity of every OWN protective sell on the open book, sorted."""
    return sorted(
        int(float(o.qty or 0))
        for o in br._orders
        if o.symbol == sym and o.side.value == "sell" and str(o.client_order_id).startswith(OWN)
    )


# --------------------------------------------------------------------------- #
# restart_existing_oco / restart_hidden_family / restart_partial_family
# --------------------------------------------------------------------------- #
@silent
def test_restart_adopts_existing_oco_and_never_reprotects_stop_fill():
    et = ET_NOW
    entry_ts = et - timedelta(minutes=10)
    with restart_journal([fill_event("AAA", entry_ts, "e1")]):
        br = PollBroker(positions={"AAA": MPos("AAA", 100, 1.8)}, bars=BARS)
        e1 = br.remember("e1", symbol="AAA", side="buy", px=1.8, status="filled", fq=100, qty=100)
        e1.filled_at = entry_ts
        parent, _child = br.oco("AAA", "p1", qty=100, status="new", child_id="c1")
        br.open_oco(parent)
        meta = {}

        fb.startup_reconcile(br, meta)
        assert br.canceled == [], ("restart cancelled a live protective family", br.canceled)
        assert m_sells_live(br) == ["p1"], m_sells_live(br)
        m = meta["AAA"]
        assert m["entry_ts"] == entry_ts, ("restart reset the journalled anchor", m)
        assert m["entry_B"] == 1.8, ("restart reset the journalled entry price", m)

        # a healthy adopted family is neither cancelled nor duplicated
        poll_at(br, meta, et)
        assert br.sells == [], ("restart duplicated protection already live", br.sells)
        assert br.closed == [], br.closed
        assert m_sells_live(br) == ["p1"], m_sells_live(br)

        # the stop child sells the whole family while the held snapshot is
        # STALE (still 100): already-sold shares must not be re-protected
        br.partial("c1", 100, status="filled")
        br.resolve_cancel("p1", status="canceled")
        poll_at(br, meta, et + timedelta(minutes=1))
        assert br.sells == [], ("re-protected shares the adopted family sold", br.sells)
        poll_at(br, meta, et + timedelta(minutes=2))
        assert br.sells == [], ("re-protected a stale held qty after a stop fill", br.sells)
        assert br.closed == [], ("market-closed a stale holding after a stop fill", br.closed)
        assert live_protective_qty(br) == [], live_protective_qty(br)

        # the broker snapshot finally prints flat -> the completed exit books
        br.set_position("AAA", 0)
        poll_at(br, meta, et + timedelta(minutes=3))
        assert m.get("entry_ts") is None, ("sold-flat position not booked", m)
        assert br.sells == [] and br.closed == [], (br.sells, br.closed)


@silent
def test_restart_existing_family_hidden_from_snapshot_not_duplicated():
    et = ET_NOW
    entry_ts = et - timedelta(minutes=8)
    with restart_journal([fill_event("AAA", entry_ts, "e2")]):
        br = PollBroker(positions={"AAA": MPos("AAA", 100, 1.8)}, bars=BARS)
        e2 = br.remember("e2", symbol="AAA", side="buy", px=1.8, status="filled", fq=100, qty=100)
        e2.filled_at = entry_ts
        parent, _child = br.oco("AAA", "p2", qty=100, status="new", child_id="c2")
        br.open_oco(parent)
        br.add_order(MOrder("AAA", "sell", "f9", 2.0, qty=100, cid="other-strategy-9"))
        meta = {}

        fb.startup_reconcile(br, meta)
        poll_at(br, meta, et)
        assert br.sells == [], ("restart duplicated protection already live", br.sells)
        assert "p2" not in br.canceled, br.canceled
        assert "f9" not in br.canceled, ("foreign sell cancelled", br.canceled)

        # the open-orders snapshot omits the working root while the lookup
        # still resolves it: absence is not cancellation, so no duplicate
        br.hide("p2")
        poll_at(br, meta, et + timedelta(minutes=1))
        assert br.sells == [], (
            "duplicate protection for a working family missing from the snapshot",
            br.sells,
        )
        assert br.closed == [], br.closed
        poll_at(br, meta, et + timedelta(minutes=2))
        assert br.sells == [], ("duplicate protection on a later stale snapshot", br.sells)

        # our family then dies at the broker (both legs, as a real cancel
        # does): the remaining shares are reprotected exactly once. A foreign
        # sell adopted as our bracket would leave them unprotected here.
        br.resolve_cancel("p2", status="canceled")
        br.restatus("c2", "canceled")
        poll_at(br, meta, et + timedelta(minutes=3))
        assert len(br.sells) == 1 and int(br.sells[0].qty) == 100, (
            "remaining shares not reprotected exactly once after the family resolved",
            [(str(s.id), s.qty) for s in br.sells],
        )
        assert "f9" not in br.canceled, ("foreign sell cancelled", br.canceled)
        assert br.closed == [], br.closed

        # stable afterwards: one covering family, still no duplicate
        poll_at(br, meta, et + timedelta(minutes=4))
        assert len(br.sells) == 1, ("duplicate protection after the reprotect", br.sells)
        assert live_protective_qty(br) == [100], live_protective_qty(br)


@silent
def test_restart_partial_family_reprotects_exact_remaining():
    et = ET_NOW
    entry_ts = et - timedelta(minutes=22)
    with restart_journal([fill_event("AAA", entry_ts, "e3")]):
        br = PollBroker(positions={"AAA": MPos("AAA", 60, 1.85)}, bars=BARS)
        # adopted family: 100 entered, 40 already sold by the target leg, and
        # the broker shrank the stop leg to the remaining 60
        parent, _child = br.oco(
            "AAA",
            "p3",
            qty=100,
            status="partially_filled",
            fq=40,
            child_id="c3",
            child_qty=60,
        )
        br.open_oco(parent)
        meta = {}

        fb.startup_reconcile(br, meta)
        m = meta["AAA"]
        assert et_dt(m["entry_ts"]) == entry_ts, ("restart reset the journalled anchor", m)
        assert m["entry_B"] == 1.8, ("adoption reset the journalled entry price", m)

        # the partially-sold family still covers the remaining 60 -> no resize,
        # no cancel, no second bracket
        poll_at(br, meta, et)
        assert br.sells == [], ("re-protected an already-partially-sold family", br.sells)
        assert "p3" not in br.canceled, br.canceled
        assert br.closed == [], br.closed

        # the family's remainder is canceled at the broker (both legs) while the
        # held snapshot goes STALE (prints 100): the replacement must cover the
        # exact UNSOLD remainder, 100 entered - 40 sold = 60 -- not 20 from a
        # net-only baseline and not the stale 100
        br.resolve_cancel("p3", status="canceled")
        br.restatus("c3", "canceled")
        br.set_position("AAA", 100)
        poll_at(br, meta, et + timedelta(minutes=1))
        assert len(br.sells) == 1, ("remaining shares not reprotected after the cancel", br.sells)
        assert int(br.sells[0].qty) == 60, (
            "replacement after a partially-sold family's cancel is not the exact remaining 60",
            [(str(s.id), s.qty) for s in br.sells],
        )
        assert br.closed == [], br.closed
        replacement = br.sells[0]

        # a second stale snapshot must not resize the new bracket upward
        poll_at(br, meta, et + timedelta(minutes=2))
        assert len(br.sells) == 1, ("duplicate protection while the snapshot is stale", br.sells)
        assert str(replacement.id) not in br.canceled, (
            "protection resized to a stale held snapshot",
            br.canceled,
        )
        assert live_protective_qty(br) == [60], live_protective_qty(br)

        # the snapshot catches up to NET 60: still exactly one covering family
        br.set_position("AAA", 60)
        poll_at(br, meta, et + timedelta(minutes=3))
        assert len(br.sells) == 1 and live_protective_qty(br) == [60], (
            br.sells,
            live_protective_qty(br),
        )


def restart_two_families():
    """Restart state for a journal-attributed 300-share position whose OWN
    protection is TWO separate OCO families on the broker book: family A
    (parent ``pA``, qty 100, stop leg ``cA``) and family B (parent ``pB``,
    qty 200, stop leg ``cB``). Both are live and jointly cover the position,
    so nothing is oversized until one of them leaves the open snapshot.
    Returns (broker, meta, family-B stop leg)."""
    br = PollBroker(positions={"AAA": MPos("AAA", 300, 1.8)}, bars=BARS)
    a_parent, _a_leg = br.oco("AAA", "pA", qty=100, status="new", child_id="cA")
    b_parent, b_leg = br.oco("AAA", "pB", qty=200, status="new", child_id="cB")
    br.open_oco(a_parent)
    br.open_oco(b_parent)
    meta = {}
    fb.startup_reconcile(br, meta)
    return br, meta, b_leg


# --------------------------------------------------------------------------- #
# hidden_multifamily_resize / hidden_multifamily_exit
# --------------------------------------------------------------------------- #
@silent
def test_hidden_second_family_never_forgotten_by_resize():
    et = ET_NOW
    with restart_journal([fill_event("AAA", et - timedelta(minutes=6), "e6")]):
        br, meta, b_leg = restart_two_families()
        assert br.canceled == [], ("restart cancelled inherited protection", br.canceled)
        assert m_sells_live(br) == ["pA", "pB"], m_sells_live(br)

        # family B drops out of the open snapshot: A alone (100) no longer
        # covers the 300 held, so a resize starts -- and B must not be lost
        br.hide("pB")
        poll_at(br, meta, et)
        assert "pA" in br.canceled, ("undersized family not cancel-requested", br.canceled)
        assert br.sells == [], ("replacement submitted before any family resolved", br.sells)
        assert br.closed == [], br.closed

        # A's cancel is still only a request -> nothing may overlap it
        poll_at(br, meta, et + timedelta(minutes=1))
        assert br.sells == [] and br.closed == [], (br.sells, br.closed)
        assert "pB" in br.canceled, (
            "snapshot-lagged family B forgotten by the resize",
            br.canceled,
        )

        # only A resolves; B is hidden but still WORKING at the broker ->
        # neither a replacement nor a close may go in
        br.resolve_cancel("pA", status="canceled")
        br.restatus("cA", "canceled")  # the broker's cancel takes every leg
        poll_at(br, meta, et + timedelta(minutes=2))
        assert br.sells == [], (
            "replacement overlapped the snapshot-lagged family B still working",
            br.sells,
        )
        assert br.closed == [], ("market-closed under the still-working family B", br.closed)

        # B's lookup goes unavailable: absence is not resolution
        br.forget("pB")
        poll_at(br, meta, et + timedelta(minutes=3))
        assert br.sells == [], ("replacement while family B is unresolvable", br.sells)
        assert br.closed == [], br.closed

        # B finally resolves terminal having sold 40 -> exactly one OCO for the
        # unsold remainder (300 entered - 40 sold), never a second bracket
        br.restatus("cB", "canceled")
        br.remember(
            "pB",
            symbol="AAA",
            side="sell",
            px=2.0,
            status="canceled",
            qty=200,
            fq=40,
            legs=[b_leg],
        )
        poll_at(br, meta, et + timedelta(minutes=4))
        assert len(br.sells) == 1 and int(br.sells[0].qty) == 260, (
            "multi-family resize did not reprotect the exact unsold remainder",
            [(str(s.id), s.qty) for s in br.sells],
        )
        assert br.closed == [], br.closed

        poll_at(br, meta, et + timedelta(minutes=5))
        assert len(br.sells) == 1, ("duplicate protection after the multi-family resize", br.sells)
        assert live_protective_qty(br) == [260], live_protective_qty(br)


@silent
def test_hidden_second_family_blocks_overdue_flatten():
    et = ET_NOW
    with restart_journal([fill_event("AAA", et - timedelta(minutes=31), "e7")]):
        br, meta, b_leg = restart_two_families()
        br.hide("pB")

        # overdue at restart: the visible family is cancelled, never flattened
        poll_at(br, meta, et)
        assert "pA" in br.canceled, br.canceled
        assert br.closed == [], ("flattened while inherited protection still works", br.closed)
        assert br.sells == [], ("overdue path re-armed protection", br.sells)

        # only A resolves; B is hidden but still working -> still no flatten
        br.resolve_cancel("pA", status="canceled")
        br.restatus("cA", "canceled")  # the broker's cancel takes every leg
        poll_at(br, meta, et + timedelta(minutes=1))
        assert br.closed == [], ("flattened while snapshot-lagged family B works", br.closed)
        assert br.sells == [], ("re-armed protection instead of waiting on family B", br.sells)
        assert "pB" in br.canceled, ("snapshot-lagged family B forgotten by the exit", br.canceled)

        # B unresolvable -> still no flatten
        br.forget("pB")
        poll_at(br, meta, et + timedelta(minutes=2))
        assert br.closed == [], ("flattened while family B is unresolvable", br.closed)

        # B's stop leg sells 100 while the snapshot lagged; the broker catches
        # up -> flatten exactly the unsold remainder (300 - 100), once
        br.partial("cB", 100, status="filled")
        br.remember(
            "pB",
            symbol="AAA",
            side="sell",
            px=2.0,
            status="canceled",
            qty=200,
            fq=0,
            legs=[b_leg],
        )
        br.set_position("AAA", 200)
        poll_at(br, meta, et + timedelta(minutes=3))
        assert br.closed == ["AAA"], (
            "overdue exit never completed after the hidden family resolved",
            br.closed,
        )
        assert int(br.order("close1").qty) == 200, (
            "flattened shares the hidden family had already sold",
            br.order("close1").qty,
        )

        poll_at(br, meta, et + timedelta(minutes=4))
        assert br.closed == ["AAA"], ("duplicate flatten for one overdue exit", br.closed)


# --------------------------------------------------------------------------- #
# filled_sibling_family
# --------------------------------------------------------------------------- #
@silent
def test_filled_sibling_family_keeps_working_family_tracked():
    """Ordinary (not-due, no-resize) multi-family book. The visible families'
    largest qty must already cover the held qty here, otherwise the
    consolidation resize owns the symbol — that path is covered separately."""
    et = ET_NOW
    with restart_journal([fill_event("AAA", et - timedelta(minutes=5), "e8")]):
        br = PollBroker(positions={"AAA": MPos("AAA", 400, 1.8)}, bars=BARS)
        a_parent, _a_leg = br.oco("AAA", "pA", qty=400, status="new", child_id="cA")
        b_parent, _b_leg = br.oco("AAA", "pB", qty=50, status="new", child_id="cB")
        br.open_oco(a_parent)
        br.open_oco(b_parent)
        meta = {}

        fb.startup_reconcile(br, meta)
        # the 400 bracket already covers the 400 held -> nothing oversized
        poll_at(br, meta, et)
        assert br.sells == [], ("consolidated healthy multi-family protection", br.sells)
        assert br.canceled == [], ("cancelled healthy multi-family protection", br.canceled)
        assert m_sells_live(br) == ["pA", "pB"], m_sells_live(br)

        # the small sibling FILLS its 50 (parent canceled, stop leg filled) and
        # the covering bracket drops out of the lagging snapshot while it still
        # works: a filled family must not evict it from the tracked set
        br.partial("cB", 50, status="filled")
        br.restatus("pB", "canceled")
        br._orders = [o for o in br._orders if str(o.id) != "pB"]
        br.hide("pA")
        poll_at(br, meta, et + timedelta(minutes=1))
        assert br.sells == [], (
            "a filled sibling family dropped the still-working bracket and "
            "replacement protection was submitted over it",
            br.sells,
        )
        assert br.closed == [], br.closed
        assert "pA" not in br.canceled, ("cancelled the still-working bracket", br.canceled)

        # the working bracket then resolves with nothing sold: the exact
        # unsold remainder (400 entered - 50 sold by the sibling) is covered
        # once, and only once
        br.resolve_cancel("pA", status="canceled")
        br.restatus("cA", "canceled")
        poll_at(br, meta, et + timedelta(minutes=2))
        assert len(br.sells) == 1 and int(br.sells[0].qty) == 350, (
            "sibling family's shares were not subtracted from the replacement",
            [(str(s.id), s.qty) for s in br.sells],
        )

        poll_at(br, meta, et + timedelta(minutes=3))
        assert len(br.sells) == 1, ("duplicate protection after the family resolved", br.sells)
        assert live_protective_qty(br) == [350], live_protective_qty(br)


# --------------------------------------------------------------------------- #
# partial_fill_coverage
# --------------------------------------------------------------------------- #
@silent
def test_partial_filled_oco_coverage_uses_remaining_shares():
    et = ET_NOW
    # entry buy: 100 filled first, then a late remainder taking it to 130
    e1 = owned_entry("AAA", "e1", 1.8, status="partially_filled", fq=130, filled_at=et)
    br = PollBroker(orders=[e1], positions={"AAA": MPos("AAA", 90, 1.8)}, bars=BARS)
    # OCO for the first 100: the target leg filled 40 and the broker shrank the
    # stop leg to the remaining 60, so 60 shares are actually covered
    parent, _stop = br.oco(
        "AAA",
        "p1",
        qty=100,
        status="partially_filled",
        fq=40,
        child_id="c1",
        child_qty=60,
    )
    br.open_oco(parent)
    meta = {
        "_day": TODAY,
        "AAA": {"prev_close": 1.0, "order_id": "e1", "entry_B": 1.8, "anchor_ts": et},
    }

    # 90 held, only 60 covered: the original 100 is NOT what this bracket
    # covers, so the family is canceled and reconciled before any replacement
    poll_at(br, meta, et)
    assert "p1" in br.canceled, (
        "a partially filled OCO counted as full coverage of the held qty",
        br.canceled,
    )
    assert br.sells == [], ("replacement submitted before the family resolved", br.sells)
    assert br.closed == [], br.closed

    # the cancel is a request only -> still no overlapping protection
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [] and br.closed == [], (br.sells, br.closed)

    # family terminal (its 40 already accounted) -> exactly one OCO for the
    # whole actual holding of 90
    br.resolve_cancel("p1", status="canceled")
    br.restatus("c1", "canceled")
    poll_at(br, meta, et + timedelta(minutes=2))
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 90, (
        "under-covering partially filled OCO not replaced by the exact holding",
        [(str(s.id), s.qty) for s in br.sells],
    )
    bracket = br.sells[0]

    # the new bracket fills 30 (60 left) and the snapshot catches up: exact
    # coverage, so nothing is resized or re-armed
    br.partial(str(bracket.id), 30, status="partially_filled")
    br.set_position("AAA", 60)
    poll_at(br, meta, et + timedelta(minutes=3))
    assert len(br.sells) == 1, ("resized protection that covers the holding", br.sells)

    # a further entry remainder lifts the holding to 90 while the open-orders
    # snapshot drops the cached root: the lookup still reports it WORKING with
    # 60 remaining, which is under-coverage — absence is not full protection
    br.partial("e1", 160, status="partially_filled")
    br.set_position("AAA", 90)
    br.hide(str(bracket.id))
    poll_at(br, meta, et + timedelta(minutes=4))
    assert str(bracket.id) in br.canceled, (
        "a working-but-partial cached OCO missing from the snapshot was assumed"
        " to cover the holding",
        br.canceled,
    )
    assert len(br.sells) == 1, ("replacement submitted under the unconfirmed cancel", br.sells)
    assert br.closed == [], br.closed

    # it resolves -> one replacement for the full 90, and no duplicate after
    br.resolve_cancel(str(bracket.id), status="canceled")
    poll_at(br, meta, et + timedelta(minutes=5))
    assert len(br.sells) == 2 and int(br.sells[1].qty) == 90, (
        "under-covering hidden bracket not replaced by exactly one 90 OCO",
        [(str(s.id), s.qty) for s in br.sells],
    )
    replacement = br.sells[1]

    poll_at(br, meta, et + timedelta(minutes=6))
    assert len(br.sells) == 2, ("duplicate protection after the coverage resize", br.sells)
    assert live_protective_qty(br) == [90], live_protective_qty(br)

    # The snapshot now LAGS OUT: the bracket fills 30 (60 active left) and a
    # further entry remainder lifts the entered watermark to 190, so known
    # inventory says 90 held (190 - 100 sold) while the family actively covers
    # 60. With no position printed the flat branch decides, and it must act on
    # that known under-coverage instead of waving it through as "protection
    # still working".
    br.partial(str(replacement.id), 30, status="partially_filled")
    br.partial("e1", 190, status="partially_filled")
    br.set_position("AAA", 0)
    poll_at(br, meta, et + timedelta(minutes=7))
    assert str(replacement.id) in br.canceled, (
        "known under-coverage ignored while the position snapshot was absent",
        br.canceled,
    )
    assert len(br.sells) == 2, (
        "replacement overlapped the cancel of the under-covering family",
        [(str(s.id), s.qty) for s in br.sells],
    )
    assert br.closed == [], br.closed

    # the cancel is still a request only
    poll_at(br, meta, et + timedelta(minutes=8))
    assert len(br.sells) == 2, ("replacement under an unconfirmed cancel", br.sells)
    assert br.closed == [], br.closed

    # the root's lookup goes unavailable: unknown coverage must keep waiting,
    # never be read as zero and resized against
    br.forget(str(replacement.id))
    poll_at(br, meta, et + timedelta(minutes=9))
    assert len(br.sells) == 2, (
        "unknown coverage treated as zero and the family resized",
        [(str(s.id), s.qty) for s in br.sells],
    )
    assert br.closed == [], br.closed

    # it resolves terminal (its 30 already accounted) -> exactly one OCO for the
    # known 90 held, even though the position snapshot never came back
    br.remember(
        str(replacement.id),
        symbol="AAA",
        side="sell",
        px=2.0,
        status="canceled",
        qty=90,
        fq=30,
    )
    poll_at(br, meta, et + timedelta(minutes=10))
    assert len(br.sells) == 3 and int(br.sells[2].qty) == 90, (
        "known 90 held left uncovered while the position snapshot lags",
        [(str(s.id), s.qty) for s in br.sells],
    )
    assert meta["AAA"].get("entry_ts") is not None, (
        "booked a flat exit while 90 booked shares are still held",
        meta["AAA"],
    )
    poll_at(br, meta, et + timedelta(minutes=11))
    assert len(br.sells) == 3, ("duplicate protection after the lagging resize", br.sells)
    assert live_protective_qty(br) == [90], live_protective_qty(br)
    covered = br.sells[2]

    # state ordering: the coverage gap opens again (bracket fills 30, a further
    # entry remainder lands), and the terminal-cancel poll is the one that
    # discovers the sell filled 30 MORE. Post-reconcile the ledger reads
    # 220 entered - 160 sold = 60, so 60 must be protected — not the 90 that
    # was true before that sale landed.
    #
    # Honest scope: this phase pins the RESIDUAL (60, not 90), not the ordering
    # itself. Mutation checks show that moving the pre-reconcile read earlier or
    # later still passes, because the shared handler derives its own quantity
    # from broker state and reconciliation only ever adds sales, so a stale
    # scalar cannot turn into a submit. Keep re-testing that first if the
    # handler ever starts reconciling between its decision and the submit.
    br.partial(str(covered.id), 30, status="partially_filled")
    br.partial("e1", 220, status="partially_filled")
    poll_at(br, meta, et + timedelta(minutes=12))
    assert str(covered.id) in br.canceled, (
        "under-coverage ignored once the position snapshot was absent",
        br.canceled,
    )
    assert len(br.sells) == 3, (
        "replacement overlapped the cancel of the under-covering family",
        [(str(s.id), s.qty) for s in br.sells],
    )
    assert br.closed == [], br.closed

    br.resolve_cancel(str(covered.id), status="canceled", fq=60)
    poll_at(br, meta, et + timedelta(minutes=13))
    assert len(br.sells) == 4 and int(br.sells[3].qty) == 60, (
        "protected 90 (the held figure from before the cancel's extra sale) "
        "instead of the re-accounted 60",
        [(str(s.id), s.qty) for s in br.sells],
    )
    assert meta["AAA"].get("entry_ts") is not None, (
        "booked a flat exit while 60 booked shares are still held",
        meta["AAA"],
    )

    poll_at(br, meta, et + timedelta(minutes=14))
    assert len(br.sells) == 4, ("duplicate protection after the re-accounted resize", br.sells)
    assert live_protective_qty(br) == [60], live_protective_qty(br)

    # the replacement then sells the rest: entered equals total sold, so the
    # ledger is exhausted — nothing may be protected and the flat exit books,
    # while the position snapshot is still absent
    final = br.sells[3]
    br.settle(str(final.id), status="filled", fq=60)
    poll_at(br, meta, et + timedelta(minutes=15))
    assert len(br.sells) == 4, ("protected shares the ledger shows already sold", br.sells)
    assert meta["AAA"].get("entry_ts") is None, (
        "exhausted ledger not booked as a confirmed flat exit",
        meta["AAA"],
    )

    poll_at(br, meta, et + timedelta(minutes=16))
    assert len(br.sells) == 4, ("ghost protection after the ledger was exhausted", br.sells)
    assert live_protective_qty(br) == [], live_protective_qty(br)
    assert br.closed == [], br.closed


# --------------------------------------------------------------------------- #
# terminal_root_coverage
# --------------------------------------------------------------------------- #
@silent
def test_terminal_root_nominal_qty_is_not_coverage():
    """Same under-coverage rule as partial_fill_coverage, from the other
    broker shape: the take-profit ROOT is already canceled while its stop leg
    can still fill. Modelled as its own case because the two shapes cannot
    coexist on one timeline — a root goes terminal at a single point."""
    et = ET_NOW
    # Alpaca OCO shape: the parent is canceled (its nominal 100 shares are
    # gone) while the stop child can still fill and has already sold 40 of its
    # own 100, leaving 60 actively protected. NET is 90.
    br = PollBroker(bars=BARS)
    _root, child = br.oco(
        "AAA",
        "p1",
        qty=100,
        status="canceled",
        fq=0,
        child_id="c1",
        child_status="partially_filled",
        child_fq=40,
    )
    # the canceled root left the open book; only the live leg remains on it
    br.add_order(child)
    br.set_position("AAA", 90)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et - timedelta(minutes=12),
            "entry_B": 1.8,
            "oco_id": "p1",
            "entry_filled_qty": 130,
        },
    }

    # 90 held, 60 actively covered: the dead root's nominal 100 is not
    # protection, so the family is canceled/reconciled before any replacement
    poll_at(br, meta, et)
    assert "p1" in br.canceled, (
        "a canceled root's nominal qty counted as protection coverage",
        br.canceled,
    )
    assert br.sells == [], ("replacement submitted before the family resolved", br.sells)
    assert br.closed == [], br.closed

    # the cancel is a request only, and the leg can still sell meanwhile
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [] and br.closed == [], (br.sells, br.closed)

    # the leg goes terminal (its 40 already accounted) -> exactly one OCO for
    # the whole actual holding of 90
    br.restatus("c1", "canceled")
    br._orders = [o for o in br._orders if str(o.id) != "c1"]
    poll_at(br, meta, et + timedelta(minutes=2))
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 90, (
        "canceled-root family not replaced by the exact holding",
        [(str(s.id), s.qty) for s in br.sells],
    )

    poll_at(br, meta, et + timedelta(minutes=3))
    assert len(br.sells) == 1, ("duplicate protection after the coverage resize", br.sells)
    assert live_protective_qty(br) == [90], live_protective_qty(br)


# --------------------------------------------------------------------------- #
# in-memory broker modelling asynchronous transitions
# --------------------------------------------------------------------------- #
class _Val:
    def __init__(self, v):
        self.value = v


class MOrder:
    def __init__(self, sym, side, oid, px, status="new", fq=0, cid=None, qty=0, legs=None):
        self.symbol = sym
        self.side = _Val(side)
        self.id = oid
        self.limit_price = float(px)
        self.status = _Val(status)
        self.filled_qty = fq
        self.client_order_id = (
            cid if cid is not None else f"{OWN}{'b' if side == 'buy' else 's'}-{sym}"
        )
        self.filled_avg_price = float(px)
        self.filled_at = None
        self.qty = qty
        self.legs = list(legs or [])


class MPos:
    def __init__(self, sym, qty, avg=1.0):
        self.symbol = sym
        self.qty = qty
        self.avg_entry_price = avg


def _open_status(st):
    return st not in fb.TERMINAL and st != "filled"


class PollBroker:
    """Deterministic broker with asynchronous state transitions.

    ``cancel`` marks an order ``pending_cancel`` and leaves it on the book;
    ``resolve_cancel`` is the broker completing the cancel (terminal). Fake
    fills/closes are explicit helper calls, never implicit side effects of a
    request. ``sell_raises`` is the set of symbols whose OCO submit the broker
    rejects; ``close_fail`` is the number of close submissions that fail."""

    live = True

    def __init__(
        self,
        positions=None,
        orders=None,
        bars=None,
        sell_raises=(),
        close_fail=0,
        clock_open=True,
        close_instant=True,
    ):
        self._orders = []  # working (open) orders
        self._by_id = {}  # every order the broker knows
        self._positions = {}
        self._bars = bars
        self.sell_raises = set(sell_raises or ())
        self.close_fail = close_fail
        self.clock_open = clock_open
        self.close_instant = close_instant
        self.canceled = []
        self.buys = []
        self.sells = []
        self.closed = []
        for o in orders or ():
            self.add_order(o)
        for sym, q in (positions or {}).items():
            if isinstance(q, MPos):
                self._positions[sym] = q
            else:
                self._positions[sym] = MPos(sym, q)

    # test-facing state helpers -------------------------------------------
    def add_order(self, o):
        self._by_id[str(o.id)] = o
        if _open_status(str(o.status.value)):
            self._orders.append(o)

    def set_position(self, sym, qty, avg=1.0):
        if qty == 0:
            self._positions.pop(sym, None)
        else:
            self._positions[sym] = MPos(sym, qty, avg)

    def resolve_cancel(self, oid, status="canceled", fq=None):
        """Broker completes a pending cancel: terminal + off the book. A
        terminal ``filled`` reports its cumulative filled qty as its qty
        unless an explicit partial ``fq`` is supplied."""
        o = self._by_id.get(str(oid))
        if o is not None:
            o.status = _Val(status)
            if fq is not None:
                o.filled_qty = fq
            elif status == "filled":
                o.filled_qty = int(o.qty or 0)
        self._orders = [x for x in self._orders if str(x.id) != str(oid)]

    def forget(self, oid):
        """Simulate an unavailable lookup: the id is unknown to the broker."""
        self._by_id.pop(str(oid), None)
        self._orders = [x for x in self._orders if str(x.id) != str(oid)]

    def hide(self, oid):
        """Keep broker truth for the id but drop it from the open snapshot
        (a lagging/partial open-orders fetch); the lookup still resolves."""
        self._orders = [x for x in self._orders if str(x.id) != str(oid)]

    def restatus(self, oid, status):
        self._by_id[str(oid)].status = _Val(status)

    def remember(self, oid, **kw):
        """Re-expose an order for lookup without putting it back on the book.
        A terminal ``filled`` order reports its cumulative filled qty as its
        qty unless an explicit partial ``fq`` is supplied. ``legs`` re-nests a
        returned OCO family, as a broker lookup of a bracket root reports it."""
        status = kw.get("status", "pending_cancel")
        qty = int(kw.get("qty", 0) or 0)
        fq = kw.get("fq")
        if fq is None:
            fq = qty if status == "filled" else 0
        o = MOrder(
            kw.get("symbol", "AAA"),
            kw.get("side", "buy"),
            oid,
            kw.get("px", 1.8),
            status=status,
            fq=fq,
            cid=kw.get("cid"),
            qty=qty,
            legs=kw.get("legs"),
        )
        self._by_id[str(oid)] = o
        return o

    def settle(self, oid, status="filled", fq=None):
        """Broker reports a terminal/filled state and drops it from the book.
        A ``filled`` status defaults its cumulative filled qty to the order
        qty unless an explicit partial ``fq`` is supplied."""
        o = self._by_id[str(oid)]
        o.status = _Val(status)
        if fq is not None:
            o.filled_qty = fq
        elif status == "filled":
            o.filled_qty = int(o.qty or 0)
        self._orders = [x for x in self._orders if str(x.id) != str(oid)]
        return o

    def partial(self, oid, fq, status="partially_filled"):
        """Broker reports a partial fill; the order may stay on the book."""
        o = self._by_id[str(oid)]
        o.filled_qty = fq
        o.status = _Val(status)
        return o

    def next_buy_id(self):
        return f"b{len(self.buys)}"

    def oco(
        self,
        sym,
        oid,
        qty,
        status="new",
        px=2.0,
        fq=0,
        child_id=None,
        child_status="new",
        child_fq=0,
        child_qty=None,
        cid=None,
    ):
        """Build an Alpaca OCO family: an OWN-prefixed take-profit PARENT whose
        ``legs`` hold a server-generated (non-OWN) stop CHILD. Mirrors the
        nested=true response shape. ``child_qty`` is the leg's remaining size,
        which the broker shrinks to ``qty - fq`` once the parent has filled."""
        child = MOrder(
            sym,
            "sell",
            child_id or f"{oid}-stop",
            px,
            status=child_status,
            fq=child_fq,
            cid=f"alpaca-{oid}-stop",
            qty=qty if child_qty is None else child_qty,
        )
        parent = MOrder(
            sym,
            "sell",
            oid,
            px,
            status=status,
            fq=fq,
            cid=cid or f"{OWN}s-{sym}-{oid}",
            qty=qty,
            legs=[child],
        )
        self._by_id[str(parent.id)] = parent
        self._by_id[str(child.id)] = child
        return parent, child

    def open_oco(self, parent):
        """Put an OCO parent on the open book (nested form)."""
        if _open_status(str(parent.status.value)):
            self._orders.append(parent)
        return parent

    # broker API ----------------------------------------------------------
    def clock(self):
        return self.clock_open, fb.now_et()

    def open_orders(self):
        return list(self._orders)

    def positions(self):
        return dict(self._positions)

    def order(self, oid):
        return self._by_id.get(str(oid))

    def bars(self, symbol, start):
        return pd.DataFrame() if self._bars is None else self._bars

    def trades_at_bid(self, *a, **k):
        return {}

    def cancel(self, oid):
        self.canceled.append(str(oid))
        o = self._by_id.get(str(oid))
        if o is not None and str(o.status.value) not in fb.TERMINAL:
            o.status = _Val("pending_cancel")
        # A cancel REQUEST: the order stays on the book until resolve_cancel.

    def submit_buy(self, symbol, qty, price):
        o = MOrder(
            symbol,
            "buy",
            self.next_buy_id(),
            price,
            qty=qty,
            cid=f"{OWN}b-{symbol}-{len(self.buys)}",
        )
        self.buys.append(o)
        self.add_order(o)
        return o

    def sell_oco(self, symbol, qty, stop_price, limit_price):
        if symbol in self.sell_raises:
            raise RuntimeError("oco submit rejected")
        o = MOrder(
            symbol,
            "sell",
            f"oco{len(self.sells) + 1}",
            limit_price,
            qty=qty,
            cid=f"{OWN}s-{symbol}-{len(self.sells) + 1}",
        )
        self.sells.append(o)
        self.add_order(o)
        return o

    def close_market(self, symbol):
        """A close submission can fail (returns None) or be accepted (returns
        the accepted order, registered for lookup). With ``close_instant`` the
        fill is immediate (legacy cases); otherwise the order stays 'accepted'
        with zero filled qty until ``fill_close`` models the actual fill, so
        accepted != filled."""
        self.closed.append(symbol)
        if self.close_fail > 0:
            self.close_fail -= 1
            return None
        qty = int(self._positions[symbol].qty) if symbol in self._positions else 0
        o = MOrder(symbol, "sell", f"close{len(self.closed)}", 0.0, qty=qty)
        if self.close_instant:
            o.status = _Val("filled")
            o.filled_qty = qty
            self._positions.pop(symbol, None)
        else:
            o.status = _Val("accepted")
        self._by_id[str(o.id)] = o
        return o

    def fill_close(self, oid):
        """The accepted market close actually fills: cumulative filled qty set
        and the position leaves the book."""
        o = self._by_id[str(oid)]
        o.status = _Val("filled")
        o.filled_qty = int(o.qty or 0)
        self._positions.pop(o.symbol, None)
        return o


class FlakyBroker(PollBroker):
    """First ``open_orders`` calls fail (broker fetch down at startup)."""

    def __init__(self, open_orders_fail=0, **kw):
        super().__init__(**kw)
        self._fail_left = open_orders_fail

    def open_orders(self):
        if self._fail_left > 0:
            self._fail_left -= 1
            raise RuntimeError("broker fetch failed")
        return super().open_orders()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_bars(n=20, base=2.0):
    """20 completed ET minutes on a $2 base that is strict at the last bar."""
    closes = [base] * (n - 5) + [base, base, base * 1.02, base * 1.1, base * 1.2]
    ts = pd.to_datetime([f"2026-09-11T{13 + i // 60:02d}:{i % 60:02d}:00Z" for i in range(n)])
    return pd.DataFrame({"ts": ts, "et": list(range(570, 570 + n)), "close": closes})


BARS = make_bars()
EMPTY = pd.DataFrame()
NO_CANDS = pd.DataFrame(columns=["rank", "symbol", "close", "change"])


def poll_at(br, meta, et, cands=None, scan=None):
    """Run one poll with the clock/scan pinned to this test (restored after).

    ``scan`` overrides the scanner callable (e.g. to raise); otherwise the
    scanner returns ``cands`` (or no candidates)."""
    prev_now, prev_scan = fb.now_et, fb.scan_candidates
    fb.now_et = lambda: et
    if scan is not None:
        fb.scan_candidates = scan
    else:
        fb.scan_candidates = lambda: (NO_CANDS if cands is None else cands, "test")
    try:
        fb.poll(br, meta, probe=False)
    finally:
        fb.now_et, fb.scan_candidates = prev_now, prev_scan


def owned_entry(sym, oid, px, status="new", fq=0, filled_at=None):
    o = MOrder(sym, "buy", oid, px, status=status, fq=fq, cid=f"{OWN}b-{sym}-{oid}")
    o.filled_at = filled_at
    return o


# --------------------------------------------------------------------------- #
# resize_waits_cancel
# --------------------------------------------------------------------------- #
@silent
def test_resize_waits_for_pending_oco_cancel():
    et = ET_NOW
    s1 = MOrder("AAA", "sell", "s1", 2.0, qty=100, cid=f"{OWN}s-AAA-1")
    br = PollBroker(orders=[s1], positions={"AAA": 300}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {"prev_close": 1.0, "entry_ts": et, "entry_B": 1.8, "oco_id": "s1"},
    }

    # poll 1: held 300 but the OCO only covers 100 -> request the resize cancel.
    # The pre-seeded s1 is broker state, not a submission: the bot must submit
    # NO replacement while the cancel is unconfirmed, and s1 must stay live.
    poll_at(br, meta, et)
    assert "s1" in br.canceled, ("resize did not cancel the undersized OCO", br.canceled, br.sells)
    assert br.sells == [], ("replacement OCO submitted before the old one was terminal", br.sells)
    assert m_sells_live(br) == ["s1"], ("old OCO left the book on request alone", m_sells_live(br))

    # poll 2: the entry remainder fills during the pending cancel (held grows);
    # the cancel is still unconfirmed -> still no overlapping replacement
    br.set_position("AAA", 500)
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [], ("second OCO submitted while the resize cancel was pending", br.sells)
    assert m_sells_live(br) == ["s1"], (m_sells_live(br), br.canceled)

    # poll 3: broker confirms the cancel terminal -> exactly one new OCO for the
    # broker-held quantity (500), and s1 is gone from the book
    br.resolve_cancel("s1")
    poll_at(br, meta, et + timedelta(minutes=2))
    assert len(br.sells) == 1, (
        "resize protection not placed exactly once after the cancel landed",
        br.sells,
        br.canceled,
    )
    assert m_sells_live(br) == [br.sells[0].id], (
        "stale OCO survived the confirmed cancel",
        m_sells_live(br),
        br.sells,
    )
    assert int(br.sells[0].qty) == 500, (
        "replacement OCO not sized to the broker-held quantity during resize",
        int(br.sells[0].qty),
    )


def m_sells_live(br):
    """Owned protective sells still on the open book, in order."""
    return [
        str(o.id)
        for o in br._orders
        if o.side.value == "sell" and str(o.client_order_id).startswith(OWN)
    ]


# --------------------------------------------------------------------------- #
# resize_partial_protective_fill
# --------------------------------------------------------------------------- #
@silent
def test_resize_survives_partial_protective_fill():
    et = ET_NOW
    s1 = MOrder("AAA", "sell", "s1", 2.0, qty=100, cid=f"{OWN}s-AAA-1")
    br = PollBroker(orders=[s1], positions={"AAA": 300}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "s1",
            "entry_filled_qty": 300,
            "entry_open_qty": 300,
        },
    }

    # poll 1: undersized OCO -> resize cancel, no replacement yet
    poll_at(br, meta, et)
    assert "s1" in br.canceled and br.sells == [], (br.canceled, br.sells)

    # the OCO fills its 100 shares while the cancel is in flight: those shares
    # are SOLD (terminal "filled"), but the position snapshot still prints the
    # stale 300 -> the filled order must resolve the resize and the residual
    # 200 must be protected exactly once (300 - 100 sold)
    br.hide("s1")
    br.resolve_cancel("s1", status="filled")
    poll_at(br, meta, et + timedelta(minutes=1))
    assert len(br.sells) == 1, (
        "residual left unprotected after a protective OCO filled during resize",
        br.sells,
    )
    assert int(br.sells[0].qty) == 200, (
        "residual not sized to the broker-held quantity",
        int(br.sells[0].qty),
    )
    assert br.closed == [], ("resize path market-closed a non-overdue position", br.closed)


# --------------------------------------------------------------------------- #
# stale_held_after_filled_oco
# --------------------------------------------------------------------------- #
@silent
def test_stale_held_after_filled_oco_never_reprotected():
    et = ET_NOW
    br = PollBroker(positions={"AAA": 100}, bars=BARS)
    br.remember("s1", status="filled", side="sell", qty=100)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "s1",
            "entry_filled_qty": 100,
            "entry_open_qty": 100,
        },
    }

    # pre-tl30: the protective OCO filled (shares sold); a stale held snapshot
    # must not re-protect, on this poll or the next
    poll_at(br, meta, et)
    assert br.sells == [], (
        "re-protected a stale held snapshot after a filled protective OCO",
        br.sells,
    )
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [], ("re-protected a stale held snapshot on a second poll", br.sells)

    # post-tl30: the same stale held snapshot must not be re-protected or closed
    meta["AAA"]["entry_ts"] = et - timedelta(minutes=31)
    poll_at(br, meta, et + timedelta(minutes=2))
    assert br.sells == [], ("re-protected a stale held snapshot after tl30", br.sells)
    assert br.closed == [], (
        "market-closed a stale held snapshot after a filled protective OCO",
        br.closed,
    )


# --------------------------------------------------------------------------- #
# missing_position_cached_oco
# --------------------------------------------------------------------------- #
@silent
def test_missing_position_cached_oco_retained_then_held_no_duplicate():
    et = ET_NOW
    br = PollBroker(positions={}, bars=BARS)
    br.remember("s1", status="pending_cancel", side="sell", qty=100)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "s1",
            "entry_filled_qty": 100,
            "entry_open_qty": 100,
            "protect_qty": 100,
        },
    }

    # flat snapshot + absent open snapshot, but the cached OCO is still working:
    # retain it (no exit bookkeeping, no resubmit)
    poll_at(br, meta, et)
    assert br.sells == [], ("resubmitted protection for a still-working cached OCO", br.sells)
    assert meta["AAA"]["oco_id"] == "s1", (
        "dropped a working cached OCO when the position snapshot was missing",
        meta["AAA"],
    )

    # the held snapshot returns: the retained working OCO must not be duplicated
    br.set_position("AAA", 100)
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [], ("duplicated protection once the held snapshot returned", br.sells)
    assert meta["AAA"]["oco_id"] == "s1", meta["AAA"]


# --------------------------------------------------------------------------- #
# overdue_cached_working_oco
# --------------------------------------------------------------------------- #
@silent
def test_overdue_cached_working_oco_cancels_then_closes():
    et = ET_NOW
    br = PollBroker(positions={"AAA": 100}, bars=BARS)
    br.remember("s1", status="new", side="sell", qty=100)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et - timedelta(minutes=31),
            "entry_B": 1.8,
            "oco_id": "s1",
            "entry_filled_qty": 100,
            "entry_open_qty": 100,
        },
    }

    # overdue, cached OCO working but absent from the snapshot: request its
    # cancel and wait; never flatten while it can still work
    poll_at(br, meta, et)
    assert "s1" in br.canceled, ("overdue cached working OCO cancel never requested", br.canceled)
    assert br.closed == [], ("flattened while a cached working OCO was still live", br.closed)

    # broker reports it pending_cancel -> still no close
    br.restatus("s1", "pending_cancel")
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.closed == [], ("flattened while the cached OCO cancel was pending", br.closed)

    # broker confirms terminal -> flatten once
    br.resolve_cancel("s1")
    poll_at(br, meta, et + timedelta(minutes=2))
    assert br.closed == ["AAA"], (
        "overdue exit unreachable after the cached OCO resolved",
        br.closed,
    )


# --------------------------------------------------------------------------- #
# cached_oco_pending
# --------------------------------------------------------------------------- #
@silent
def test_cached_oco_absent_nonterminal_not_resubmitted():
    et = ET_NOW
    pos = {"AAA": MPos("AAA", 100, 1.8)}
    m = {"prev_close": 1.0, "entry_ts": et, "entry_B": 1.8, "oco_id": "s1"}

    # a) cached OCO absent from the open snapshot but nonterminal at the broker
    #    -> absence is not confirmation: retain the id, do not re-submit
    br = PollBroker(positions={"AAA": 100}, bars=BARS)
    br.remember("s1", status="pending_cancel", side="sell", px=2.0, qty=100)
    fb.manage_symbol(br, "AAA", m, BARS, pos, [], 660, et)
    assert br.sells == [], (
        "resubmitted protection while the cached OCO was only pending_cancel",
        br.sells,
    )
    assert m["oco_id"] == "s1", ("cached OCO id dropped on a nonterminal lookup", m)

    # b) lookup unavailable -> also not confirmation
    br.forget("s1")
    fb.manage_symbol(br, "AAA", m, BARS, pos, [], 660, et)
    assert br.sells == [], ("resubmitted protection on an unavailable cached-OCO lookup", br.sells)
    assert m["oco_id"] == "s1", m

    # c) broker reports it terminal -> clear the cache and cover the held qty
    br.remember("s1", status="canceled", side="sell", px=2.0, qty=100)
    fb.manage_symbol(br, "AAA", m, BARS, pos, [], 660, et)
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 100, (
        "dead cached OCO never replaced after a terminal lookup",
        br.sells,
    )
    assert m["oco_id"] == str(br.sells[0].id), m


# --------------------------------------------------------------------------- #
# due_exit_pending_cancel
# --------------------------------------------------------------------------- #
@silent
def test_due_exit_waits_for_pending_protective_cancel():
    et = ET_NOW
    s1 = MOrder("AAA", "sell", "s1", 2.0, qty=100, cid=f"{OWN}s-AAA-1")
    br = PollBroker(orders=[s1], positions={"AAA": 100}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et - timedelta(minutes=31),
            "entry_B": 1.8,
            "oco_id": "s1",
        },
    }

    # poll 1: overdue -> cancel the working OCO; never flatten under it
    poll_at(br, meta, et)
    assert "s1" in br.canceled and br.closed == [], (
        "overdue exit flattened under a live protective OCO",
        br.closed,
    )

    # poll 2: an intervening partial sell (40) leaves the OCO open but it
    # drops out of the open snapshot -> the unconfirmed cancel must block
    # the market close, even though the held qty is now 60
    br.hide("s1")
    br.restatus("s1", "partially_filled")
    br.set_position("AAA", 60)
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.closed == [], (
        "market-closed while a protective cancel was unconfirmed and the "
        "order had dropped from the open snapshot",
        br.closed,
    )

    # poll 3: broker confirms the cancel terminal (the 40 filled, 60 unfilled)
    # -> flatten the actual remaining position exactly once
    br.resolve_cancel("s1", status="canceled")
    poll_at(br, meta, et + timedelta(minutes=2))
    assert br.closed == ["AAA"], (
        "overdue exit never completed after the protective order resolved",
        br.closed,
    )

    # poll 4: the accepted close is not repeated
    poll_at(br, meta, et + timedelta(minutes=3))
    assert br.closed == ["AAA"], ("duplicate flatten for one overdue exit", br.closed)


@silent
def test_due_exit_skips_close_when_protection_filled():
    et = ET_NOW
    # the protective OCO already filled (shares sold); only the cache remains
    br = PollBroker(positions={}, bars=BARS)
    br.remember("s1", status="filled", side="sell", px=2.0, qty=100)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et - timedelta(minutes=31),
            "entry_B": 1.8,
            "oco_id": "s1",
        },
    }

    poll_at(br, meta, et)
    assert br.closed == [], (
        "market-closed a book already exited by a filled protective OCO",
        br.closed,
    )
    assert br.sells == [], (
        "re-protected a book already exited by a filled protective OCO",
        br.sells,
    )
    assert meta["AAA"].get("entry_ts") is None, (
        "completed protective fill not booked",
        meta["AAA"],
    )


@silent
def test_due_exit_waits_for_pending_resize_cancel():
    et = ET_NOW
    s2 = MOrder("AAA", "sell", "s2", 2.0, qty=100, cid=f"{OWN}s-AAA-2")
    br = PollBroker(orders=[s2], positions={"AAA": 300}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {"prev_close": 1.0, "entry_ts": et, "entry_B": 1.8, "oco_id": "s2"},
    }

    # poll 1: undersized OCO starts a resize cancel (100 < 300)
    poll_at(br, meta, et)
    assert "s2" in br.canceled and br.sells == [], (br.canceled, br.sells)

    # the position ages into the tl30 window while the resize cancel is still
    # unconfirmed and the order has dropped from the open snapshot
    meta["AAA"]["entry_ts"] = et - timedelta(minutes=31)
    br.hide("s2")
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.closed == [], ("flattened while a resize cancel was unconfirmed", br.closed)

    # broker confirms terminal -> the overdue exit runs
    br.resolve_cancel("s2")
    poll_at(br, meta, et + timedelta(minutes=2))
    assert br.closed == ["AAA"], (
        "overdue exit never completed after the resize cancel resolved",
        br.closed,
    )


# --------------------------------------------------------------------------- #
# poll_single_oco
# --------------------------------------------------------------------------- #
@silent
def test_poll_fresh_fill_places_single_oco():
    et = ET_NOW
    p1 = owned_entry("AAA", "p1", 1.8, status="partially_filled", fq=100, filled_at=et)
    br = PollBroker(orders=[p1], positions={"AAA": 100}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {"prev_close": 1.0, "order_id": "p1", "entry_B": 1.8, "anchor_ts": et},
    }

    poll_at(br, meta, et)
    assert len(br.sells) == 1, ("one poll with a fresh fill produced a duplicate OCO", br.sells)
    assert int(br.sells[0].qty) == 100, ("OCO not sized to the held fill", int(br.sells[0].qty))

    # next poll: the OCO is now broker truth, the fill is unchanged -> still one
    poll_at(br, meta, et + timedelta(minutes=1))
    assert len(br.sells) == 1, ("stable fill re-protected into a second OCO", br.sells)


# --------------------------------------------------------------------------- #
# entry_replace_terminal
# --------------------------------------------------------------------------- #
@silent
def test_entry_replacement_waits_for_terminal_lookup():
    et = ET_NOW
    o1 = owned_entry("AAA", "o1", 1.8)
    br = PollBroker(orders=[o1], bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {"prev_close": 1.0, "order_id": "o1", "anchor_ts": et, "entry_B": 1.8},
    }

    # poll 1: the tick moved -> cancel the stale bid, no replacement yet
    poll_at(br, meta, et)
    assert "o1" in br.canceled and br.buys == [], (br.canceled, br.buys)

    # poll 2: order absent from the open snapshot AND the lookup is unavailable
    br.forget("o1")
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.buys == [], ("replacement bid submitted without a resolved terminal outcome", br.buys)
    assert meta["AAA"]["order_id"] == "o1", (
        "unresolved entry id dropped when the lookup was unavailable",
        meta["AAA"],
    )

    # poll 3: lookup returns a nonterminal pending_cancel -> still no bid
    br.remember("o1", status="pending_cancel", px=1.8)
    poll_at(br, meta, et + timedelta(minutes=2))
    assert br.buys == [], (
        "replacement bid submitted while the old order was only pending_cancel",
        br.buys,
    )
    assert meta["AAA"]["order_id"] == "o1", meta["AAA"]

    # poll 4: broker confirms the old bid terminal -> exactly one replacement
    br.resolve_cancel("o1")
    poll_at(br, meta, et + timedelta(minutes=3))
    assert len(br.buys) == 1, ("no replacement bid after the old one resolved", br.buys)
    b = br.buys[0]
    assert abs(b.limit_price - 2.16) < 1e-9, ("replacement at the wrong tick", b.limit_price)
    assert b.qty == fb.qty_for(2.16) and b.qty * b.limit_price <= fb.NOTIONAL + 1e-6, (
        "replacement not sized to full notional",
        b.qty,
        b.limit_price,
    )
    assert meta["AAA"]["order_id"] == str(b.id), ("replacement not tracked", meta["AAA"])


# --------------------------------------------------------------------------- #
# overdue_partial
# --------------------------------------------------------------------------- #
@silent
def test_overdue_partial_exit_survives_rejected_protection():
    et = ET_NOW
    p1 = owned_entry(
        "AAA", "p1", 1.8, status="partially_filled", fq=100, filled_at=et - timedelta(minutes=31)
    )
    br = PollBroker(orders=[p1], positions={"AAA": 100, "BBB": 50}, sell_raises={"AAA"}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "order_id": "p1",
            "entry_B": 1.8,
            "entry_ts": et - timedelta(minutes=31),
        },
        "BBB": {"prev_close": 1.0, "entry_ts": et, "entry_B": 1.8},
    }

    # A rejected OCO submit for the overdue partial must not escape the poll,
    # the 31-min exit must still fire, and BBB must still be protected.
    poll_at(br, meta, et)
    assert br.closed == ["AAA"], (
        "overdue partial exit skipped when protection was rejected",
        br.closed,
    )
    assert not [s for s in br.sells if s.symbol == "AAA"], (
        "overdue position re-armed protection",
        br.sells,
    )
    assert [s for s in br.sells if s.symbol == "BBB" and int(s.qty) == 50], (
        "other symbol left unprotected after a per-symbol submit failure",
        br.sells,
    )


# --------------------------------------------------------------------------- #
# stale_cumulative_fill
# --------------------------------------------------------------------------- #
@silent
def test_stale_cumulative_fill_after_exit_is_not_replayed():
    et = ET_NOW
    p1 = owned_entry("AAA", "p1", 1.8, status="partially_filled", fq=100, filled_at=et)
    br = PollBroker(orders=[p1], positions={"AAA": 100}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {"prev_close": 1.0, "order_id": "p1", "entry_B": 1.8, "anchor_ts": et},
    }

    # poll 1: book the 100 and protect it
    poll_at(br, meta, et)
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 100, br.sells

    # the protective sell fills: position flat, the entry remainder cancel is
    # still unresolved (p1 still prints partially filled)
    sold = br.sells[0]
    br.settle(sold.id, status="filled", fq=100)
    br.set_position("AAA", 0)
    poll_at(br, meta, et + timedelta(minutes=5))
    assert meta["AAA"].get("entry_ts") is None, (
        "position exit not booked after the protective fill",
        meta["AAA"],
    )
    assert len(br.sells) == 1, ("unchanged cumulative fill re-protected a flat book", br.sells)

    # the remainder then fills (+200) and the position prints the fresh 200:
    # the new residual is EXACTLY the unsold quantity (200), not the
    # cumulative 300 and not a replayed 100
    br.settle("p1", status="filled", fq=300)
    br.set_position("AAA", 200)
    poll_at(br, meta, et + timedelta(minutes=6))
    assert meta["AAA"].get("entry_ts") is not None, (
        "late remainder fill never booked",
        meta["AAA"],
    )
    new = [s for s in br.sells if str(s.id) != str(sold.id)]
    assert len(new) == 1, ("late residual not protected exactly once", br.sells)
    assert int(new[0].qty) == 200, (
        "late remainder OCO not the exact unsold quantity",
        int(new[0].qty),
    )


# --------------------------------------------------------------------------- #
# monotonic_lifetime_sales
# --------------------------------------------------------------------------- #
@silent
def test_monotonic_lifetime_sales_no_ghost_after_full_exit():
    et = ET_NOW
    s1 = MOrder("AAA", "sell", "s1", 2.0, qty=100, cid=f"{OWN}s-AAA-1")
    br = PollBroker(orders=[s1], positions={"AAA": 300}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "s1",
            "entry_filled_qty": 300,
            "entry_open_qty": 300,
        },
    }

    # poll 1: undersized protective OCO -> resize cancel
    poll_at(br, meta, et)
    assert "s1" in br.canceled and br.sells == [], (br.canceled, br.sells)

    # poll 2: the 100-share OCO fills while the snapshot still prints 300 ->
    # the residual OCO is exactly 200
    br.hide("s1")
    br.settle("s1", status="filled", fq=100)
    poll_at(br, meta, et + timedelta(minutes=1))
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 200, br.sells
    residual = br.sells[0]

    # poll 3: the snapshot catches up to 200 with the residual OCO working ->
    # no duplicate protection
    br.set_position("AAA", 200)
    poll_at(br, meta, et + timedelta(minutes=2))
    assert len(br.sells) == 1, ("duplicate protection after the snapshot caught up", br.sells)

    # poll 4: the residual OCO sells its 200 -> flat; the cumulative 300 sold
    # must not let a ghost 100 be re-protected
    br.settle(residual.id, status="filled", fq=200)
    br.set_position("AAA", 0)
    poll_at(br, meta, et + timedelta(minutes=3))
    assert len(br.sells) == 1, ("ghost protection after a fully-exited lifetime", br.sells)
    assert br.closed == [], (
        "market-closed a book already fully exited by protective fills",
        br.closed,
    )


# --------------------------------------------------------------------------- #
# partial_fill_then_cancel
# --------------------------------------------------------------------------- #
@silent
def test_partial_fill_then_cancel_residual_not_replayed():
    et = ET_NOW
    s1 = MOrder("AAA", "sell", "s1", 2.0, qty=100, cid=f"{OWN}s-AAA-1")
    br = PollBroker(orders=[s1], positions={"AAA": 300}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "s1",
            "entry_filled_qty": 300,
            "entry_open_qty": 300,
        },
    }

    # poll 1: undersized protective OCO -> resize cancel
    poll_at(br, meta, et)
    assert "s1" in br.canceled and br.sells == [], (br.canceled, br.sells)

    # poll 2: 40 of the old OCO filled but it is still working -> the resize
    # must wait (no overlapping protection) while the 40 is accounted
    br.hide("s1")
    br.partial("s1", 40, status="partially_filled")
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [], ("protected while the old OCO was still partially working", br.sells)

    # poll 3: the 60 remainder is canceled (terminal) with the same 40 fill ->
    # the residual OCO is exactly 260 and the 40 is accounted only once
    br.settle("s1", status="canceled", fq=40)
    poll_at(br, meta, et + timedelta(minutes=2))
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 260, br.sells

    # poll 4: a repeated poll with the same stale snapshot must not replay the
    # already-accounted 40-share partial fill (per-order watermark)
    poll_at(br, meta, et + timedelta(minutes=3))
    assert len(br.sells) == 1, ("replayed an already-accounted partial protective fill", br.sells)
    assert int(br.sells[0].qty) == 260, (
        "residual re-sized by a replayed partial fill",
        int(br.sells[0].qty),
    )


# --------------------------------------------------------------------------- #
# market_close_fill
# --------------------------------------------------------------------------- #
@silent
def test_market_close_fill_does_not_consume_late_entry_residual():
    et = ET_NOW
    p1 = owned_entry(
        "AAA", "p1", 1.8, status="partially_filled", fq=100, filled_at=et - timedelta(minutes=31)
    )
    br = PollBroker(orders=[p1], positions={"AAA": 100}, bars=BARS, close_instant=False)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "order_id": "p1",
            "entry_B": 1.8,
            "entry_ts": et - timedelta(minutes=31),
            "entry_filled_qty": 100,
            "entry_open_qty": 100,
        },
    }

    # poll 1: overdue -> accepted close (not yet filled); the position is held
    poll_at(br, meta, et)
    assert len(br.closed) == 1 and "AAA" in br.positions(), (br.closed, br.positions())

    # poll 2: the close actually fills 100 -> flat; the late entry remainder
    #         then fills 200 (cumulative 300)
    br.fill_close("close1")
    br.settle("p1", status="filled", fq=300)
    poll_at(br, meta, et + timedelta(minutes=1))

    # poll 3: a fresh position prints 200 -> the residual must be managed
    #         (a 200 OCO, or a second close), never consumed by the earlier
    #         market close
    br.set_position("AAA", 200)
    poll_at(br, meta, et + timedelta(minutes=2))
    residual_protected = [s for s in br.sells if int(s.qty) == 200]
    assert residual_protected or len(br.closed) >= 2, (
        "late 200 residual consumed by the earlier market close",
        br.sells,
        br.closed,
        meta["AAA"],
    )
    assert len(br.closed) <= 2, ("duplicate close while the second close was unresolved", br.closed)


@silent
def test_accepted_close_rejected_retries_once_without_duplicate():
    et = ET_NOW
    br = PollBroker(positions={"AAA": 100}, bars=BARS, close_instant=False)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et - timedelta(minutes=31),
            "entry_B": 1.8,
            "entry_filled_qty": 100,
            "entry_open_qty": 100,
        },
    }

    # poll 1: overdue -> accepted close latched, position still held
    poll_at(br, meta, et)
    assert len(br.closed) == 1 and "AAA" in br.positions(), (br.closed, br.positions())

    # poll 2: the position snapshot is absent while the accepted close is
    #         unresolved -> no duplicate close, and the exit is NOT booked
    br.set_position("AAA", 0)
    poll_at(br, meta, et + timedelta(minutes=1))
    assert len(br.closed) == 1, ("duplicate close while the accepted one was unresolved", br.closed)
    assert meta["AAA"].get("entry_ts") is not None, (
        "booked an exit while the accepted close was unresolved",
        meta["AAA"],
    )

    # poll 3: the accepted close is rejected and a fresh held prints -> the
    #         failed close is retried (same or next poll), never duplicated
    br.set_position("AAA", 100)
    br.restatus("close1", "rejected")
    poll_at(br, meta, et + timedelta(minutes=2))
    assert len(br.closed) <= 2, ("runaway close retries after a rejection", br.closed)

    # poll 4: exactly one retry, and no further duplicate while it is unresolved
    poll_at(br, meta, et + timedelta(minutes=3))
    assert len(br.closed) == 2, ("rejected close not retried exactly once", br.closed)
    assert meta["AAA"].get("entry_ts") is not None, (
        "booked an exit while the retry was unresolved",
        meta["AAA"],
    )


# --------------------------------------------------------------------------- #
# oco_family
# --------------------------------------------------------------------------- #
@silent
def test_oco_parent_canceled_child_filled_no_reprotect():
    et = ET_NOW
    br = PollBroker(positions={"AAA": 100}, bars=BARS)
    # OCO: OWN take-profit parent canceled (fq 0), server-cid stop CHILD filled
    br.oco(
        "AAA",
        "p1",
        qty=100,
        status="canceled",
        fq=0,
        child_id="c1",
        child_status="filled",
        child_fq=100,
    )
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "p1",
            "entry_filled_qty": 100,
            "entry_open_qty": 100,
        },
    }

    # the stop child sold the 100 shares; a stale held snapshot must not
    # re-protect them, on this poll or the next
    poll_at(br, meta, et)
    assert br.sells == [], ("re-protected after the OCO stop child filled", br.sells)
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [], ("re-protected a stale held snapshot across polls", br.sells)


@silent
def test_oco_child_pending_cancel_waits_then_residual():
    et = ET_NOW
    br = PollBroker(positions={"AAA": 300}, bars=BARS)
    parent, child = br.oco(
        "AAA", "p1", qty=100, status="new", fq=0, child_id="c1", child_status="new", child_fq=0
    )
    br.open_oco(parent)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "p1",
            "entry_filled_qty": 300,
            "entry_open_qty": 300,
        },
    }

    # poll 1: undersized OCO -> resize cancels the family
    poll_at(br, meta, et)
    assert "p1" in br.canceled and br.sells == [], (br.canceled, br.sells)

    # poll 2: the parent is canceled but the stop child is still pending_cancel
    # (the family can still sell) -> no replacement yet
    br.restatus("p1", "canceled")
    br.restatus("c1", "pending_cancel")
    br._orders = [o for o in br._orders if str(o.id) != "p1"]
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.sells == [], (
        "replaced protection while the OCO stop child was still working",
        br.sells,
    )

    # poll 3: the child is terminal -> protect the actual broker-held qty
    br.restatus("c1", "canceled")
    br._orders = [o for o in br._orders if str(o.id) != "c1"]
    poll_at(br, meta, et + timedelta(minutes=2))
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 300, br.sells


@silent
def test_oco_child_duplicate_leg_not_double_counted():
    et = ET_NOW
    br = PollBroker(positions={"AAA": 300}, bars=BARS)
    parent, child = br.oco(
        "AAA",
        "p1",
        qty=100,
        status="canceled",
        fq=0,
        child_id="c1",
        child_status="filled",
        child_fq=100,
    )
    # the same stop child also shows up flat in the order list (nested + flat
    # duplication); its 100-share fill must be accounted once
    br._orders.append(child)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "oco_id": "p1",
            "entry_filled_qty": 300,
            "entry_open_qty": 300,
        },
    }

    poll_at(br, meta, et)
    assert len(br.sells) == 1, ("residual not protected", br.sells)
    assert int(br.sells[0].qty) == 200, (
        "OCO child fill double-counted across nested and flat responses",
        int(br.sells[0].qty),
    )


@silent
def test_foreign_sell_not_adopted():
    et = ET_NOW
    foreign = MOrder("AAA", "sell", "f1", 2.0, qty=100, cid="other-strategy-1")
    br = PollBroker(orders=[foreign], positions={"AAA": 100}, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {
            "prev_close": 1.0,
            "entry_ts": et,
            "entry_B": 1.8,
            "entry_filled_qty": 100,
            "entry_open_qty": 100,
        },
    }

    # a foreign sell is not our protection: we place our own OCO and never
    # cancel or adopt the foreign order
    poll_at(br, meta, et)
    assert len(br.sells) == 1 and int(br.sells[0].qty) == 100, br.sells
    assert "f1" not in br.canceled, ("foreign sell adopted/cancelled", br.canceled)


# --------------------------------------------------------------------------- #
# failed_close
# --------------------------------------------------------------------------- #
@silent
def test_failed_close_retries_and_accepted_close_is_not_duplicated():
    et = ET_NOW
    br = PollBroker(positions={"AAA": 100}, close_fail=1, bars=BARS)
    meta = {
        "_day": TODAY,
        "AAA": {"prev_close": 1.0, "entry_ts": et - timedelta(minutes=31), "entry_B": 1.8},
    }

    # poll 1: the close submission fails -> not latched, position still held
    poll_at(br, meta, et)
    assert br.closed == ["AAA"], ("overdue exit never attempted", br.closed)
    assert "AAA" in br.positions(), ("failed close treated as a submitted exit", br.positions())

    # poll 2: retried and accepted
    poll_at(br, meta, et + timedelta(minutes=1))
    assert br.closed == ["AAA", "AAA"], (
        "failed market close not retried on the next poll",
        br.closed,
    )
    assert "AAA" not in br.positions(), br.positions()

    # poll 3: flat book -> the completed exit is booked (anchor cleared) and
    # the accepted close is not repeated
    poll_at(br, meta, et + timedelta(minutes=2))
    assert br.closed == ["AAA", "AAA"], ("accepted close duplicated on a later poll", br.closed)
    assert meta["AAA"].get("entry_ts") is None, ("completed market exit not booked", meta["AAA"])


# --------------------------------------------------------------------------- #
# dead_cached_oco
# --------------------------------------------------------------------------- #
@silent
def test_dead_cached_oco_reprotected_without_duplicates():
    et = ET_NOW
    # a) cached oco_id whose bracket was rejected (present at the broker with a
    #    rejected status, absent from open orders) -> must be re-submitted
    m = {"prev_close": 1.0, "entry_ts": et, "entry_B": 1.8, "oco_id": "dead"}
    br = PollBroker(positions={"AAA": 100}, bars=BARS)
    br.remember("dead", status="rejected", side="sell", px=2.0, fq=0)
    fb.manage_symbol(br, "AAA", m, BARS, {"AAA": MPos("AAA", 100, 1.8)}, [], 660, et)
    assert [s for s in br.sells if int(s.qty) == 100], (
        "held position left with a cached-but-rejected OCO and no retry",
        m,
        br.sells,
    )
    assert m["oco_id"] == br.sells[-1].id, ("replacement OCO not tracked", m)

    # b) a healthy covering bracket must not be duplicated
    s = MOrder("AAA", "sell", "s1", 2.0, qty=100, cid=f"{OWN}s-AAA-1")
    m2 = {"prev_close": 1.0, "entry_ts": et, "entry_B": 1.8, "oco_id": "s1"}
    br2 = PollBroker(orders=[s], positions={"AAA": 100}, bars=BARS)
    fb.manage_symbol(br2, "AAA", m2, BARS, {"AAA": MPos("AAA", 100, 1.8)}, [s], 660, et)
    assert br2.sells == [], ("duplicate protection for an already covered position", br2.sells)
    assert m2["oco_id"] == "s1", m2


# --------------------------------------------------------------------------- #
# scan_isolation
# --------------------------------------------------------------------------- #
@silent
def test_scanner_exception_does_not_block_risk_management():
    def boom():
        raise RuntimeError("scanner down")

    prev_scan = fb.scan_candidates
    fb.scan_candidates = boom
    try:
        # a) 11:00: the held position still gets protected; no new entries
        br = PollBroker(positions={"AAA": 100}, bars=BARS)
        meta = {"_day": TODAY, "AAA": {"prev_close": 1.0, "entry_ts": ET_NOW, "entry_B": 1.8}}
        poll_at(br, meta, ET_NOW, scan=boom)
        assert [s for s in br.sells if int(s.qty) == 100], (
            "existing position left unprotected when the scanner failed",
            meta,
            br.sells,
        )
        assert br.buys == [], ("new entry submitted while the scanner failed", br.buys)

        # b) 15:55: EOD flatten and cutoff still run, still no new bid
        late = datetime(2026, 9, 11, 15, 55, tzinfo=ET)
        s = MOrder("AAA", "sell", "s1", 2.0, qty=100, cid=f"{OWN}s-AAA-1")
        br2 = PollBroker(orders=[s], positions={"AAA": 100}, bars=BARS)
        meta2 = {
            "_day": TODAY,
            "AAA": {"prev_close": 1.0, "entry_ts": late, "entry_B": 1.8, "oco_id": "s1"},
        }
        poll_at(br2, meta2, late, scan=boom)
        assert br2.closed == ["AAA"], (
            "15:55 flatten skipped when the scanner failed",
            meta2,
            br2.closed,
        )
        assert br2.buys == [], ("new bid after 15:55", br2.buys)
    finally:
        fb.scan_candidates = prev_scan


# --------------------------------------------------------------------------- #
# restart_owned_orders
# --------------------------------------------------------------------------- #
@silent
def test_owned_restart_buy_tracked_after_failed_fetch():
    et = datetime(2026, 9, 11, 15, 40, tzinfo=ET)
    zz = owned_entry("ZZZ", "z1", 1.5)
    ff = MOrder("FFF", "buy", "f1", 1.5, cid="foreign-1")
    br = FlakyBroker(open_orders_fail=1, orders=[zz, ff], bars=EMPTY)
    meta = {}

    fb.startup_reconcile(br, meta)
    assert br.canceled == [], ("orders touched during a failed startup broker fetch", br.canceled)

    poll_at(br, meta, et)
    assert "z1" in br.canceled, (
        "owned non-candidate restart buy untracked at cutoff",
        meta,
        br.canceled,
    )
    assert "f1" not in br.canceled, ("foreign buy cancelled", br.canceled)
    assert br.buys == [], ("new entry submitted at cutoff", br.buys)
    assert not meta.get("ZZZ", {}).get("entry_ts"), (
        "anchor invented for an unknown restart entry",
        meta.get("ZZZ"),
    )


# --------------------------------------------------------------------------- #
# emptybars_expiry
# --------------------------------------------------------------------------- #
@silent
def test_empty_bars_honour_stored_expiry():
    et = ET_NOW
    # expired: stored anchor is 121 min old -> cancel the bid, no replacement
    o = owned_entry("AAA", "o2", 1.8)
    m = {
        "prev_close": 1.0,
        "order_id": "o2",
        "anchor_ts": et - timedelta(minutes=121),
        "entry_B": 1.8,
    }
    br = PollBroker(orders=[o], bars=EMPTY)
    fb.manage_symbol(br, "AAA", m, EMPTY, {}, [o], 660, et)
    assert "o2" in br.canceled and br.buys == [], (
        "empty bars hid the stored 120-min bid expiry",
        m,
        br.canceled,
        br.buys,
    )

    # unexpired: 119 min old, empty bars -> bid stays
    o3 = owned_entry("AAA", "o3", 1.8)
    m3 = {
        "prev_close": 1.0,
        "order_id": "o3",
        "anchor_ts": et - timedelta(minutes=119),
        "entry_B": 1.8,
    }
    br3 = PollBroker(orders=[o3], bars=EMPTY)
    fb.manage_symbol(br3, "AAA", m3, EMPTY, {}, [o3], 660, et)
    assert br3.canceled == [] and m3["order_id"] == "o3", (
        "unexpired resting bid cancelled on empty bars",
        m3,
        br3.canceled,
    )

    # valid strict bars, fresh anchor: no spurious expiry (refresh path owns it)
    o4 = owned_entry("AAA", "o4", 1.8)
    m4 = {"prev_close": 1.0, "order_id": "o4", "anchor_ts": et, "entry_B": 1.8}
    br4 = PollBroker(orders=[o4], bars=BARS)
    fb.manage_symbol(br4, "AAA", m4, BARS, {}, [o4], 660, et)
    assert m4["order_id"] is not None and m4["anchor_ts"] is not None, (
        "valid strict bars corrupted the bid lifecycle",
        m4,
    )


CASES = [
    test_resize_waits_for_pending_oco_cancel,
    test_resize_survives_partial_protective_fill,
    test_stale_held_after_filled_oco_never_reprotected,
    test_missing_position_cached_oco_retained_then_held_no_duplicate,
    test_overdue_cached_working_oco_cancels_then_closes,
    test_cached_oco_absent_nonterminal_not_resubmitted,
    test_due_exit_waits_for_pending_protective_cancel,
    test_due_exit_skips_close_when_protection_filled,
    test_due_exit_waits_for_pending_resize_cancel,
    test_poll_fresh_fill_places_single_oco,
    test_entry_replacement_waits_for_terminal_lookup,
    test_overdue_partial_exit_survives_rejected_protection,
    test_stale_cumulative_fill_after_exit_is_not_replayed,
    test_monotonic_lifetime_sales_no_ghost_after_full_exit,
    test_partial_fill_then_cancel_residual_not_replayed,
    test_market_close_fill_does_not_consume_late_entry_residual,
    test_accepted_close_rejected_retries_once_without_duplicate,
    test_oco_parent_canceled_child_filled_no_reprotect,
    test_oco_child_pending_cancel_waits_then_residual,
    test_oco_child_duplicate_leg_not_double_counted,
    test_foreign_sell_not_adopted,
    test_failed_close_retries_and_accepted_close_is_not_duplicated,
    test_dead_cached_oco_reprotected_without_duplicates,
    test_scanner_exception_does_not_block_risk_management,
    test_owned_restart_buy_tracked_after_failed_fetch,
    test_empty_bars_honour_stored_expiry,
    test_restart_adopts_existing_oco_and_never_reprotects_stop_fill,
    test_restart_existing_family_hidden_from_snapshot_not_duplicated,
    test_restart_partial_family_reprotects_exact_remaining,
    test_hidden_second_family_never_forgotten_by_resize,
    test_hidden_second_family_blocks_overdue_flatten,
    test_filled_sibling_family_keeps_working_family_tracked,
    test_partial_filled_oco_coverage_uses_remaining_shares,
    test_terminal_root_nominal_qty_is_not_coverage,
]


def main():
    for case in CASES:
        case()
        print(f"PASS {case.__name__}")
    print("ALL EXECUTION-SAFETY TESTS PASS")


if __name__ == "__main__":
    main()
