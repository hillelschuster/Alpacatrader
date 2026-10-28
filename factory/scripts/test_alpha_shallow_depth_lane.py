"""Regressions for the pre-registered shallow-depth refinement lane.

Consumer-visible behaviors of the ONE new gate and of the registered protocol, all on
synthetic states (no panel, no model, no network): the <500-share displayed-depth filter's
boundary and its position in the gate order (the thick intent is skipped BEFORE the arm's
quantity/spread rules and produces no trade row, while a thin intent fills exactly as the
parent lane prices it), the identical fee convention on a kept fill, the registered
kill-rule verdicts, the selection tie-break, the fill diagnostics' share/drop arithmetic and
the fail-closed pre-registration check. Every assertion is about behavior a future reader
would rely on.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from alpha_quote_aware_frequency import SymbolQuotes, minute_us  # noqa: E402

import alpha_shallow_depth_lane as sd  # noqa: E402

DAY = "2023-06-15"
SESSION_END = 895  # 14:55 ET


def synthetic_stream(ticker: str, ask: float, bid: float, ask_shares: float, bid_shares: float):
    """One ticker's RAW prints for the whole session clock range the replay can touch.

    Prints at the intent clock (601.000s), the entry arrival (601.250s), the exit due
    (660.000s) and the exit arrival (660.250s). The entry and exit prices differ so a fill
    is observable and priced; the displayed ask depth at the INTENT clock is the filter's
    object and is passed separately from the intent-side sizes where a test needs them.
    """

    def stamp(minute: int, frac: float) -> int:
        return minute_us(DAY, minute) + int(frac * 1_000_000)

    stamps = np.array(
        [stamp(600, 0.0), stamp(600, 0.25), stamp(660, 0.0), stamp(660, 0.25)], dtype=np.int64
    )
    n = stamps.shape[0]
    return SymbolQuotes(
        ticker=ticker,
        stamps=stamps,
        bid=np.full(n, bid, dtype=float),
        ask=np.full(n, ask, dtype=float),
        bid_shares=np.full(n, bid_shares, dtype=float),
        ask_shares=np.full(n, ask_shares, dtype=float),
        regular=np.ones(n, dtype=bool),
        valid=np.ones(n, dtype=bool),
    )


def intent_state(
    ticker: str,
    t: int,
    *,
    pred: float = 0.035,
    ask_shares: float,
    bid_shares: float,
    ask: float = 10.00,
    bid: float = 9.99,
):
    """A scored panel state whose intent observation is quoted, firm, fresh and funded."""
    return {
        "day": DAY,
        "ticker": ticker,
        "t": int(t),
        "admit_t": int(t) - 1,
        "entry_et": int(t),
        "entry_open": 9.98,
        "entry_status": "filled_proxy",
        "session_end": SESSION_END,
        "proxy_gross_60": 0.02,
        "pred_60": float(pred),
        "entry_minute": int(t) + 1,
        "intent_us": minute_us(DAY, int(t) + 1),
        "intent_status": "quoted",
        "intent_reason": "eligible",
        "intent_ask": float(ask),
        "intent_bid": float(bid),
        "intent_age_s": 0.1,
        "intent_spread_bps": (ask / bid - 1.0) * 10_000.0,
        "intent_ask_shares": (float(ask_shares) if ask_shares is not None else None),
        "intent_bid_shares": (float(bid_shares) if bid_shares is not None else None),
        "limit_price": float(np.ceil(ask * 1.01 * 100.0) / 100.0),
        "qty_capital": 98,  # floor(1000 / (10.10 * 1.0075))
    }


def frame(rows):
    return pl.DataFrame(rows, schema={c: sd.STATE_TYPES[c] for c in sd.STATE_COLUMNS})


def trades_frame(trade_rows):
    return pl.DataFrame(trade_rows, schema=sd.TRADE_TYPES) if trade_rows else pl.DataFrame(
        schema=sd.TRADE_TYPES
    )


def replay(rows, streams, arm_key="thr300bps|p50"):
    arm = next(a for a in sd.ARMS if a.key == arm_key)
    daily, trades = sd.replay_day(DAY, frame(rows), streams, arm)
    return daily, trades_frame(trades)


def test_depth_thin_keep_boundaries():
    base = intent_state("AAAA", 599, ask_shares=100.0, bid_shares=1000.0)
    assert sd.depth_thin_keep(base) is True
    # the thinner side is exactly the 500-share edge: NOT kept (strictly below 500)
    assert sd.depth_thin_keep(intent_state("AAAA", 599, ask_shares=500.0, bid_shares=900.0)) is False
    assert sd.depth_thin_keep(intent_state("AAAA", 599, ask_shares=900.0, bid_shares=500.0)) is False
    # 499.999 on the thin side stays kept
    assert sd.depth_thin_keep(intent_state("AAAA", 599, ask_shares=499.999, bid_shares=50.0)) is True
    # a missing observation is never kept (an unobserved side is not a thin side)
    missing = dict(base)
    missing["intent_bid_shares"] = None
    assert sd.depth_thin_keep(missing) is False
    assert sd.DEPTH_FILTER_MAX_SHARES == 500.0


def test_replay_skips_thick_depth_and_keeps_thin():
    rows = [
        intent_state("AAAA", 599, ask_shares=100.0, bid_shares=1000.0),
        intent_state("BBBB", 599, ask_shares=1000.0, bid_shares=1000.0),
    ]
    streams = {
        "AAAA": synthetic_stream("AAAA", 10.00, 9.99, 100.0, 1000.0),
        "BBBB": synthetic_stream("BBBB", 10.00, 9.99, 1000.0, 1000.0),
    }
    daily, trades = replay(rows, streams)
    # the thick intent is skipped under the REGISTERED reason, before the arm's rules
    assert daily[sd.DEPTH_FILTER_SKIP] == 1
    assert daily["intents_funded"] == 1
    assert daily["attempts"] == 1
    # it produces NO trade row at all, so it can never become cash, a fill or an UNKNOWN
    assert trades.height == 1
    assert trades["ticker"].to_list() == ["AAAA"]


def test_kept_fill_prices_exactly_as_the_parent_lane():
    rows = [intent_state("AAAA", 599, ask_shares=100.0, bid_shares=1000.0)]
    streams = {"AAAA": synthetic_stream("AAAA", 10.00, 9.99, 100.0, 1000.0)}
    for arm_key in ("thr300bps|p50", "thr300bps|p25", "thr300bps|p10", "thr300bps|fixedsize"):
        daily, trades = replay(rows, streams, arm_key=arm_key)
        assert daily["known_fills"] == 1, arm_key
        row = trades.to_dicts()[0]
        # the fee/residual convention is the parent lane's: both legs charged the rung
        for rung in sd.RUNG_COSTS:
            side = rung / 20_000.0
            expect = float(row["qty"]) * (
                float(row["exit_bid"]) * (1.0 - side) - float(row["entry_ask"]) * (1.0 + side)
            )
            assert abs(row[f"net_usd_{int(rung)}"] - expect) < 1e-9, (arm_key, rung)


def test_filter_runs_after_the_intent_gate_and_before_the_spread_rule():
    # a thick intent whose spread is ABOVE the prediction is still counted once, under the
    # depth reason (the filter sits before the arm's spread rule, so the thick side never
    # reaches it): the reason bucket the candidate lands in is the registered one.
    rows = [
        intent_state("BBBB", 599, ask_shares=1000.0, bid_shares=1000.0, ask=10.00, bid=9.90)
    ]
    streams = {"BBBB": synthetic_stream("BBBB", 10.00, 9.90, 1000.0, 1000.0)}
    daily, trades = replay(rows, streams)
    assert daily[sd.DEPTH_FILTER_SKIP] == 1
    assert daily["spread_above_prediction"] == 0
    assert trades.height == 0
    # an intent that never acquired its quote is still counted under the intent gate, never
    # under the depth filter: the filter is downstream of the arm-independent gate.
    not_acquired = intent_state("AAAA", 599, ask_shares=None, bid_shares=None)
    not_acquired["intent_status"] = "quote_not_acquired"
    not_acquired["intent_reason"] = "quote_not_acquired"
    not_acquired["intent_ask_shares"] = None
    daily2, _ = replay([not_acquired], {})
    assert daily2["quote_not_acquired"] == 1
    assert daily2[sd.DEPTH_FILTER_SKIP] == 0


def test_kill_rule_verdicts():
    def cell(rate_25, rate_100):
        return {
            "known_usd_per_panel_session": {"25": rate_25, "100": rate_100},
        }

    passed = sd.kill_rule_verdict(cell(1.0, 0.5), cell(2.0, 0.4))
    assert passed["verdict"] == "PASSED_BOTH_KILL_CONDITIONS" and passed["died"] is False
    died_val = sd.kill_rule_verdict(cell(0.0, 0.5), None)
    assert died_val["verdict"] == "DIED_VALIDATION_NEGATIVE" and died_val["died"] is True
    died_neg_val = sd.kill_rule_verdict(cell(-0.5, -0.5), None)
    assert died_neg_val["verdict"] == "DIED_VALIDATION_NEGATIVE"
    died_late = sd.kill_rule_verdict(cell(1.0, 0.5), cell(3.0, -0.2))
    assert died_late["verdict"] == "DIED_LATE_NEGATIVE_100BPS" and died_late["died"] is True
    # late exactly zero at 100bps is non-positive: the registered condition is <= 0
    assert sd.kill_rule_verdict(cell(1.0, 0.5), cell(3.0, 0.0))["verdict"] == (
        "DIED_LATE_NEGATIVE_100BPS"
    )
    # late positive at 25bps but negative at 100bps still dies on the registered rung
    assert sd.kill_rule_verdict(cell(1.0, 0.5), cell(-1.0, -0.2))[
        "condition_2_late_negative_at_100bps"
    ]["known_usd_per_panel_session"] == -0.2


def test_choose_view_tie_break_is_objective_then_fills_then_attempts_then_key():
    def surface(entries):
        out = {}
        for key, (usd, fills, attempts) in entries.items():
            out[key] = {
                "known_usd_per_calendar_day": {"25": usd},
                "known_fills": fills,
                "attempts": attempts,
                "unknown_fills": 0,
                "traded_days": 1,
            }
        return out

    base = {
        v.key: (0.0, 0, 0) for v in sd.VIEWS
    }
    # a sum, then identical sums: fewer attempts wins, then the arm key
    grid = dict(base)
    grid["thr150bps|p25"] = (5.0, 3, 7)
    grid["thr300bps|p10"] = (5.0, 3, 7)  # identical triple: the lexicographically smaller key
    grid["thr200bps|p50"] = (5.0, 4, 9)  # more fills at the same sum: wins
    grid["thr150bps|p50"] = (9.9, 1, 1)  # strictly larger sum: wins above all of them
    chosen, ranking = sd.choose_view(surface(grid))
    assert chosen.key == "thr150bps|p50"
    grid["thr150bps|p50"] = (0.0, 0, 0)
    chosen, _ = sd.choose_view(surface(grid))
    assert chosen.key == "thr200bps|p50"
    grid["thr200bps|p50"] = (5.0, 3, 4)  # same sum and fills, FEWER attempts
    grid["thr150bps|p25"] = (5.0, 3, 7)
    grid["thr300bps|p10"] = (5.0, 3, 7)
    chosen, _ = sd.choose_view(surface(grid))
    assert chosen.key == "thr200bps|p50"


def test_fill_diagnostics_shares_and_drop_sensitivities():
    cell = {
        "known_fills": 3,
        "unknown_fills": 1,
        "no_match_fills": 0,
        "attempts": 4,
        "known_usd": {"25": 100.0},
        "full_loss_lower_bound_usd_per_panel_session": {"25": -4.0},
        "known_usd_25_per_deployed_dollar": 0.05,
        "deployed_capital_usd_per_panel_session": 40.0,
        "deployed_capital_usd_per_calendar_day_span": 30.0,
        "known_usd_per_panel_session": {"25": 10.0},
        "bootstrap_daily_known_usd": {"25bps": {"p_gt_zero": 0.9}},
    }
    rows = [
        {
            "arm_key": "thr300bps|p50",
            "day": DAY,
            "ticker": "A",
            "intent_us": 1,
            "fill_status": "conditional_fill",
            "net_usd_25": 250.0,
            "entry_minute": 601,
            "intent_ask_shares": 100.0,
            "intent_bid_shares": 100.0,
        },
        {
            "arm_key": "thr300bps|p50",
            "day": DAY,
            "ticker": "B",
            "intent_us": 2,
            "fill_status": "conditional_fill",
            "net_usd_25": 50.0,
            "entry_minute": 602,
            "intent_ask_shares": 200.0,
            "intent_bid_shares": 200.0,
        },
        {
            "arm_key": "thr300bps|p50",
            "day": DAY,
            "ticker": "C",
            "intent_us": 3,
            "fill_status": "conditional_fill",
            "net_usd_25": -200.0,
            "entry_minute": 603,
            "intent_ask_shares": 300.0,
            "intent_bid_shares": 300.0,
        },
        {
            "arm_key": "thr300bps|p50",
            "day": DAY,
            "ticker": "D",
            "intent_us": 4,
            "fill_status": "unknown_exit_execution",
            "net_usd_25": None,
            "entry_minute": 604,
            "intent_ask_shares": 400.0,
            "intent_bid_shares": 400.0,
        },
    ]
    arm = next(a for a in sd.ARMS if a.key == "thr300bps|p50")
    out = sd.fill_diagnostics(cell, pl.DataFrame(rows), arm, n_sessions=10)
    assert out["conditional_fills"] == 3
    assert abs(out["top_fill_shares"]["top1"] - (250.0 / 100.0)) < 1e-12
    assert abs(out["top_fill_shares"]["top5"] - 1.0) < 1e-12
    assert out["winners_usd"] == 300.0 and out["losers_usd"] == -200.0
    # drop-best-1 keeps the remaining 50 - 200 = -150 over 100 dollars on 10 sessions
    assert out["drop_sensitivities"]["1"]["known_usd_25_ex_drop"] == -150.0
    assert out["drop_sensitivities"]["1"]["known_usd_per_panel_session_25_ex_drop"] == -15.0
    # dropping the top 5 of only three fills removes all of them: 100 - (250+50-200) = 0
    assert out["drop_sensitivities"]["5"]["known_usd_25_ex_drop"] == 0.0
    assert out["drop_sensitivities"]["5"]["known_usd_per_panel_session_25_ex_drop"] == 0.0
    assert out["drop_sensitivities"]["5"]["known_fills_ex_drop"] == 0
    assert out["top_fill_keys"][0]["ticker"] == "A"


def test_preregistration_verification_fails_closed(tmp_path=Path("/tmp")):
    ok = sd.verify_preregistration()
    assert ok["verified_before_run"] is True
    assert json.loads(sd.PREREG_PATH.read_text()) == ok["artifact"]
    # a missing registration aborts before anything runs
    real = sd.PREREG_PATH
    try:
        sd.PREREG_PATH = tmp_path / "absent_preregistration.json"
        try:
            sd.verify_preregistration()
            raise AssertionError("a missing pre-registration must abort the run")
        except SystemExit:
            pass
    finally:
        sd.PREREG_PATH = real
    # an edited registration aborts too: the content is embedded and frozen
    tampered = tmp_path / "tampered_prereg.json"
    payload = json.loads(real.read_text())
    payload["preregistration"]["refinement"]["exact_filter"] = "keep everything instead"
    tampered.write_text(json.dumps(payload))
    try:
        sd.PREREG_PATH = tampered
        try:
            sd.verify_preregistration()
            raise AssertionError("a tampered pre-registration must abort the run")
        except SystemExit:
            pass
    finally:
        sd.PREREG_PATH = real
