#!/usr/bin/env python3
"""Contract tests for alpha_passive_spread_capture (fill gates, book, UNKNOWN budget).

These exercise the lane's own rules on synthetic session records: the conservative
fill rule (a print at/through the limit AND a displayed touch size that defends the
quantity), the STRICT 3x queue-pessimism variant, the 3-slot $3,000 book with a 15-minute
post-exit cooldown, and the UNKNOWN full-budget lower bound. No tape files are read.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "alpha_passive_spread_capture.py"
spec = importlib.util.spec_from_file_location("alpha_passive_spread_capture", SCRIPT)
psc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(psc)


def _cand(
    ticker: str,
    send_us: int,
    limit: float,
    qty: int,
    bid_shares: float,
    spread_bps: float,
    bucket: int,
    fills: dict,
    exits: dict,
    session_end_us: int,
) -> dict:
    return {
        "day": "2023-05-15",
        "ticker": ticker,
        "t": 570,
        "send_us": int(send_us),
        "limit": float(limit),
        "qty": int(qty),
        "reserved": qty * limit * (1.0 + psc.SIZE_RUNG_BPS / 20_000.0),
        "spread_bps": float(spread_bps),
        "bid_shares": float(bid_shares),
        "quote_us": int(send_us),
        "rank": 1.0,
        "admit_t": 570,
        "admit_gain": 0.05,
        "bucket": bucket,
        "sq": None,
        "session_end_us": int(session_end_us),
        "fills": fills,
        "exits": exits,
    }


def _fill_exits(fill_us: int | None, status: str = "priced", bid_shares: float = 1e9):
    """Per-TTL print resolution and the hold-independent exit records."""
    state = "hit" if fill_us is not None else "miss"
    fills = {ttl: (state, fill_us) for ttl in psc.TTLS_SEC}
    exits = {}
    if fill_us is not None:
        for ttl in psc.TTLS_SEC:
            for hold, _ in psc.HOLDS:
                exits[(ttl, hold)] = {
                    "status": status,
                    "touch_px": 1.0,
                    "mid_px": 1.002,
                    "bid_shares": bid_shares,
                    "quote_us": fill_us,
                    "priced_at": fill_us + 60_000_000,
                }
    return fills, exits


def _replay(cands, session_end_us=959 * 60_000_000 + 23 * 3_600_000_000):
    return psc.replay_day_configs(cands, session_end_us)


SESSION_END_US = 700 * 60_000_000  # any value beyond the candidate clocks
T0 = 1_000_000_000


def test_quote_touch_alone_never_fills():
    """No print in the TTL window -> expired unfilled, never a priced fill."""
    fills, exits = _fill_exits(None)
    c = _cand("AAAA", T0, 2.0, 100, 5000.0, 200.0, 2, fills, exits, SESSION_END_US)
    row = _replay([c])["base|ttl15s|hold30s|touch"]
    assert row["placements"] == 1
    assert row["fills"] == 0
    assert row["unknowns"] == 0
    assert row["unfilled_no_print"] == 1
    assert row["known_usd"][25] == 0.0


def test_base_fill_requires_displayed_size_ge_qty():
    """A print through the limit fills only when the placement touch size defends q."""
    fills, exits = _fill_exits(T0 + 1_000_000)
    thin = _cand("AAAA", T0, 2.0, 100, 99.0, 200.0, 2, fills, exits, SESSION_END_US)
    row = _replay([thin])["base|ttl15s|hold30s|touch"]
    assert row["placements"] == 1
    assert row["fills"] == 0
    assert row["unfilled_size_gate"] == 1  # print hit, size gate failed: NO recorded fill
    ok = _cand("BBBB", T0, 2.0, 100, 100.0, 200.0, 2, fills, exits, SESSION_END_US)
    row2 = _replay([ok])["base|ttl15s|hold30s|touch"]
    assert row2["fills"] == 1
    assert row2["unfilled_size_gate"] == 0


def test_strict_rule_requires_3x_displayed_size():
    """STRICT queue pessimism: the same fills need 3x the displayed size."""
    fills, exits = _fill_exits(T0 + 1_000_000)
    c = _cand("AAAA", T0, 2.0, 100, 250.0, 200.0, 2, fills, exits, SESSION_END_US)
    base = _replay([c])["base|ttl15s|hold30s|touch"]
    strict = _replay([c])["strict|ttl15s|hold30s|touch"]
    assert base["fills"] == 1  # 250 >= 100
    assert strict["fills"] == 0  # 250 < 300
    assert strict["unfilled_size_gate"] == 1
    big = _cand("BBBB", T0, 2.0, 100, 300.0, 200.0, 2, fills, exits, SESSION_END_US)
    assert _replay([big])["strict|ttl15s|hold30s|touch"]["fills"] == 1


def test_exit_capacity_gate_and_full_budget_lower_bound():
    """An exit whose displayed bid cannot carry q is an UNKNOWN charged the full budget."""
    fills, exits = _fill_exits(T0 + 1_000_000, bid_shares=10.0)
    c = _cand("AAAA", T0, 2.0, 100, 5000.0, 200.0, 2, fills, exits, SESSION_END_US)
    row = _replay([c])["base|ttl15s|hold30s|touch"]
    assert row["fills"] == 0
    assert row["unknowns"] == 1
    assert row["unknown_partial_depth"] == 1
    assert row["reserved_unknown"] == pytest.approx(c["reserved"])
    # the mid upper-bound leg carries no depth gate, so the same fill prices there
    mid = _replay([c])["base|ttl15s|hold30s|mid"]
    assert mid["fills"] == 1
    assert mid["unknowns"] == 0


def test_three_slots_and_one_position_per_ticker():
    """Four simultaneous candidates -> three placements; a ticker never overlaps itself."""
    fills, exits = _fill_exits(T0 + 1_000_000)
    cands = [
        _cand(t, T0, 2.0, 100, 5000.0, 300.0 - 10 * i, 2, fills, exits, SESSION_END_US)
        for i, t in enumerate(("AAAA", "BBBB", "CCCC", "DDDD"))
    ]
    row = _replay(cands)["base|ttl15s|hold30s|touch"]
    assert row["placements"] == psc.MAX_SLOTS  # the 4th same-clock candidate is not funded
    assert "DDDD" not in {r["ticker"] for r in row["fill_rows"]}
    # a second order on the same ticker inside its TTL is never sent
    again = _cand("AAAA", T0 + 60_000_000, 2.0, 100, 5000.0, 300.0, 2, fills, exits, SESSION_END_US)
    row2 = _replay([cands[0], again])["base|ttl15s|hold30s|touch"]
    assert row2["placements"] == 1


def test_cooldown_after_actual_exit():
    """A filled position blocks its ticker for a flat 15 minutes after the exit clock."""
    fills, exits = _fill_exits(T0 + 1_000_000)
    first = _cand("AAAA", T0, 2.0, 100, 5000.0, 200.0, 2, fills, exits, SESSION_END_US)
    exit_at = first["exits"][(15, "30s")]["priced_at"]
    during = _cand("AAAA", exit_at + 60_000_000, 2.0, 100, 5000.0, 200.0, 2, fills, exits, SESSION_END_US)
    after = _cand("AAAA", exit_at + psc.COOLDOWN_MIN * 60_000_000, 2.0, 100, 5000.0, 200.0, 2, fills, exits, SESSION_END_US)
    row = _replay([first, during])["base|ttl15s|hold30s|touch"]
    assert row["placements"] == 1  # still cooling down
    row2 = _replay([first, after])["base|ttl15s|hold30s|touch"]
    assert row2["placements"] == 2  # cooldown elapsed


def test_nets_usd_charges_the_rung_on_both_legs():
    nets = psc.nets_usd(100, 10.0, 10.0)
    assert nets[0] == pytest.approx(0.0)
    assert nets[1] == pytest.approx(-100 * 10.0 * 2 * (25.0 / 20_000.0))
    assert nets[2] == pytest.approx(-100 * 10.0 * 2 * (100.0 / 20_000.0))


def test_size_gate_helpers_and_buckets():
    cand = {"qty": 100, "bid_shares": 350.0}
    assert psc.size_gate_ok(cand, "base") is True
    assert psc.size_gate_ok(cand, "strict") is True
    assert psc.size_gate_ok({"qty": 100, "bid_shares": 299.0}, "strict") is False
    assert psc.bucket_of(50.0) == 0
    assert psc.bucket_of(74.999) == 0
    assert psc.bucket_of(75.0) == 1
    assert psc.bucket_of(5000.0) == 4
    assert psc.bucket_of(49.0) == -1


def test_cell_key_and_grid_ordering():
    assert psc.cell_key("base", 15, "30s", "touch") == "base|ttl15s|hold30s|touch"
    keys = ["strict|ttl60s|hold30m|mid", "base|ttl60s|hold30s|touch", "base|ttl15s|hold300m|touch"]
    ordered = sorted(keys, key=psc.grid_sort_key)
    assert ordered[0] == "base|ttl15s|hold300m|touch"
    assert ordered[-1] == "strict|ttl60s|hold30m|mid"


def test_unresolvable_print_is_unknown_full_budget():
    """A placement with no resolvable print is an UNKNOWN charged the full budget."""
    fills = {ttl: ("unresolved", None) for ttl in psc.TTLS_SEC}
    c = _cand("AAAA", T0, 2.0, 100, 5000.0, 200.0, 2, fills, {}, SESSION_END_US)
    row = _replay([c])["base|ttl15s|hold30s|touch"]
    assert row["placements"] == 1
    assert row["fills"] == 0
    assert row["unfilled_no_print"] == 0  # never silently an unfilled expiry
    assert row["unknowns"] == 1
    assert row["unknown_unresolved_print"] == 1
    assert row["reserved_unknown"] == pytest.approx(c["reserved"])


def test_prereg_spec_is_frozen_and_outcome_free():
    """The frozen spec carries the rule, not any measured number."""
    spec_text = psc.digest_bytes(psc.PREREG_SPEC)
    assert spec_text == psc.PREREG_SHA
    for banned in ("usd_per_panel_session", "known_fills", "validation_ranking"):
        assert banned not in psc.PREREG_SPEC
    assert psc.PREREG_SPEC["selection_rule"]["objective"].startswith("2023 validation")
