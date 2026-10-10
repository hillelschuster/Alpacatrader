"""Consumer-visible guards for the learned micro-scalp payoff machinery.

Past-only features, no identity/label leakage, the frequent-execution policy
(cooldown, per-ticker cap, slots/cash, no overlap, UNKNOWN full-loss charging),
equal-day weights, fit-only clipping, and freeze-before-late.
"""

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alpha_micro_payoff as mp
from alpha_micro_core import states
from alpha_quote_audit import clock_us

DAY = "2021-06-01"


def _stream():
    start = clock_us(DAY, 575, 0)
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


def _states(stream):
    return states(DAY, "XYZ", stream, 959).filter(mp.common_gate())


# --- feature causality ------------------------------------------------------
def test_features_are_strictly_past_only():
    original, changed = _stream(), _stream()
    target = clock_us(DAY, 576, 0)
    for field in ("px", "size"):
        a = changed["trades"]
        a[field][a["ts"] >= target] *= 10
    for field in ("bid", "ask", "bs", "az"):
        a = changed["quotes"]
        a[field][a["ts"] >= target] *= 10
    st = _states(original)
    assert st.height, "gate must qualify the synthetic stream"

    def exprs(s):
        return [
            pl.col("signal_us"),
            *mp.feat_exprs(clock_us(DAY, 0, 0), clock_us(DAY, s["admit_t"], 0)),
        ]

    b = st.select(exprs(original)).filter(pl.col("signal_us") == target)
    a = _states(changed).select(exprs(changed)).filter(pl.col("signal_us") == target)
    assert b.equals(a), "future prints/quotes must not move current-state features"


def test_feature_order_has_no_identity_or_label_leakage():
    banned = (
        "symbol",
        "ticker",
        "day",
        "date",
        "ts",
        "utc",
        "signal_us",
        "admit",
        "gross",
        "exit",
        "known",
        "quote_supported",
        "pred",
        "coverage",
        "label",
    )
    for name in mp.FEATURES:
        for token in banned:
            assert token not in name.lower(), f"{name} leaks {token}"
    exprs = mp.feat_exprs(0, 0)
    st = _states(_stream()).select(exprs)
    assert st.columns == list(mp.FEATURES)
    assert len(mp.FEATURES) == len(set(mp.FEATURES))


def test_state_frame_columns_separate_features_from_labels():
    stream = _stream()
    st = _states(stream)
    frame = mp.state_frame(DAY, "XYZ", stream, st, 959)
    for f in mp.FEATURES:
        assert frame.schema[f] == pl.Float32
    for h in mp.HORIZONS:
        assert f"gross_{h}" in frame.columns
        assert f"known_{h}" in frame.columns
    assert "signal_us" in frame.columns


def test_warmup_nan_is_preserved_not_imputed():
    """Rolling warm-up rows are UNDEFINED (NaN), never faked as zero drawdown."""
    base = {
        "px": [10.0, 10.0],
        "ask": [10.01, 10.01],
        "bid": [10.0, 10.0],
        "bid_shares": [100, 100],
        "ask_shares": [100, 100],
        "signal_us": [1, 2],
    }
    cols = {
        **base,
        "dd120s": [float("nan"), -0.1],
        "rebound30s": [0.05, float("nan")],
        **{
            c: [0.0, 0.0]
            for c in (
                "ret5s",
                "ret30s",
                "ret60s",
                "imbalance5s",
                "imbalance30s",
                "imbalance60s",
                "classified_dv5s",
                "dv5s",
                "classified_dv30s",
                "dv30s",
                "classified_dv60s",
                "dv60s",
                "depth_imbalance",
            )
        },
        "n5s": [1, 1],
        "n30s": [1, 1],
        "n60s": [1, 1],
    }
    out = pl.DataFrame(cols).select(mp.feat_exprs(0, 0))
    assert np.isnan(out["dd120s"][0]) and out["dd120s"][1] == pytest.approx(-0.1)
    assert np.isnan(out["rebound30s"][1]) and out["rebound30s"][0] == pytest.approx(0.05)
    assert mp.CONTRACT["feature_nan_rule"]
    assert "NaN" in mp.FEATURE_SOURCES["dd120s"]


# --- frequent-execution policy ----------------------------------------------
def _scores(rows):
    schema = {
        "day": pl.String,
        "ticker": pl.String,
        "signal_us": pl.Int64,
        "admit_t": pl.Int64,
        "session_end_us": pl.Int64,
        "entry_open": pl.Float32,
        "entry_et": pl.Int64,
        "gross_5": pl.Float32,
        "known_5": pl.Boolean,
        "exit_et_5": pl.Int64,
        "exit_rode_5": pl.Boolean,
        "quote_supported_5": pl.Boolean,
        "gross_15": pl.Float32,
        "known_15": pl.Boolean,
        "exit_et_15": pl.Int64,
        "exit_rode_15": pl.Boolean,
        "quote_supported_15": pl.Boolean,
        "pred_gross_5": pl.Float64,
        "pred_gross_15": pl.Float64,
    }
    schema.update(dict.fromkeys(mp.FEATURES, pl.Float32))
    return pl.DataFrame(rows, schema=schema)


def _row(day, ticker, t, gross, pred=1.0, session_end_us=None, horizon=5):
    t = int(t)
    return {
        "day": day,
        "ticker": ticker,
        "signal_us": t,
        "admit_t": 575,
        "session_end_us": int(session_end_us or t + 3600 * 1_000_000),
        "entry_open": 10.0,
        "entry_et": t + 250_000,
        "gross_5": gross if horizon == 5 else gross,
        "known_5": gross is not None,
        "exit_et_5": (t + horizon * 1_000_000) if gross is not None else None,
        "exit_rode_5": False,
        "quote_supported_5": gross is not None,
        "gross_15": gross,
        "known_15": gross is not None,
        "exit_et_15": (t + 15_000_000) if gross is not None else None,
        "exit_rode_15": False,
        "quote_supported_15": gross is not None,
        "pred_gross_5": pred,
        "pred_gross_15": pred,
        **dict.fromkeys(mp.FEATURES, 0.0),
    }


def test_cooldown_and_ticker_day_cap():
    t0 = clock_us(DAY, 600, 0)
    rows = [_row(DAY, "AAA", t0 + i * 60_000_000, 0.01) for i in range(41)]
    metrics, trades = mp.replay_cell(_scores(rows), [DAY], 5, 0.0, 0.0)
    assert len(trades) == mp.MAX_ATTEMPTS_PER_TICKER
    ts = [tr["t"] for tr in trades]
    assert ts == sorted(ts)
    assert all(b - a >= mp.COOLDOWN_S * 1_000_000 for a, b in zip(ts, ts[1:], strict=False))
    assert metrics["skips"]["cooldown"] > 0 and metrics["skips"]["ticker_day_cap"] > 0


def test_slots_cash_and_no_overlap():
    t0 = clock_us(DAY, 600, 0)
    rows = [_row(DAY, f"B{i}", t0, 0.01) for i in range(4)]
    metrics, trades = mp.replay_cell(_scores(rows), [DAY], 5, 0.0, 0.0)
    assert len(trades) == mp.MAX_SLOTS
    assert len({tr["ticker"] for tr in trades}) == mp.MAX_SLOTS
    assert metrics["skips"]["cash"] == 1
    # every slot exits 5s later; cash is reused, never negative
    later = [_row(DAY, f"B{i}", t0 + 30_000_000, 0.01) for i in range(4)]
    metrics2, trades2 = mp.replay_cell(_scores(rows + later), [DAY], 5, 0.0, 0.0)
    assert len(trades2) <= 2 * mp.MAX_SLOTS
    assert all(tr["status"] in ("known_touch", "unknown_pending") for tr in trades2)


def test_no_leverage_cash_floor():
    t0 = clock_us(DAY, 600, 0)
    rows = [_row(DAY, f"C{i}", t0, -0.99) for i in range(4)]
    metrics, trades = mp.replay_cell(_scores(rows), [DAY], 5, 0.0, 0.0)
    assert len(trades) == mp.MAX_SLOTS
    assert metrics["skips"]["cash"] == 1
    day = metrics["daily"][0]
    assert day["lower_bound_pnl"] < 0
    # a later signal on the same day cannot be funded either (proceeds are tiny)
    later = _row(DAY, "C9", t0 + 60_000_000, 0.01)
    m2, _ = mp.replay_cell(_scores(rows + [later]), [DAY], 5, 0.0, 0.0)
    assert m2["skips"]["cash"] >= 1


def test_unknown_exit_charges_full_budget_and_locks_slot():
    t0 = clock_us(DAY, 600, 0)
    unknown = _row(DAY, "DDD", t0, None)
    follow = _row(DAY, "DDD", t0 + 60_000_000, 0.05)
    metrics, trades = mp.replay_cell(_scores([unknown, follow]), [DAY], 5, 0.0, 0.0)
    assert len(trades) == 1 and trades[0]["status"] == "unknown_pending"
    assert trades[0]["net"] is None and trades[0]["gross"] is None
    day = metrics["daily"][0]
    assert day["unknown"] == 1
    assert day["lower_bound_pnl"] == pytest.approx(-mp.ORDER_BUDGET)
    assert metrics["skips"]["overlap"] == 1  # pending exit locked the symbol


def test_known_and_unknown_pnl_mix():
    t0 = clock_us(DAY, 600, 0)
    rows = [_row(DAY, "EEE", t0, 0.02), _row(DAY, "EEE", t0 + 400_000_000, None)]
    metrics, _ = mp.replay_cell(_scores(rows), [DAY], 5, 0.0, 0.0)
    day = metrics["daily"][0]
    assert day["known_pnl"] == pytest.approx(mp.ORDER_BUDGET * 0.02)
    assert day["lower_bound_pnl"] == pytest.approx(mp.ORDER_BUDGET * 0.02 - mp.ORDER_BUDGET)


def test_mean_net_known_fill_is_a_fraction_not_a_percent():
    """The per-order mean is a FRACTION of the order budget, never scaled by 100."""
    t0 = clock_us(DAY, 600, 0)
    metrics, trades = mp.replay_cell(_scores([_row(DAY, "KKK", t0, 0.02)]), [DAY], 5, 0.0, 0.0)
    view = mp.cell_view(
        metrics, trades, [DAY], "validation", 5, 0.001, 10.0, {DAY: {"complete": True}}, 1
    )
    assert view["mean_net_known_fill_fraction"] == pytest.approx(0.02)
    assert view["mean_net_known_fill_usd"] == pytest.approx(mp.ORDER_BUDGET * 0.02)
    assert "mean_net_known_fill_pct" not in view


def test_cost_is_charged_round_trip_on_real_touch():
    t0 = clock_us(DAY, 600, 0)
    gross = 0.01
    _, trades = mp.replay_cell(_scores([_row(DAY, "FFF", t0, gross)]), [DAY], 5, 0.0, 100.0)
    side = 100.0 / 20_000
    g32 = float(np.float32(gross))  # the corpus stores labels as float32
    expected = (1 + g32) * (1 - side) / (1 + side) - 1
    assert trades[0]["net"] == pytest.approx(expected, rel=1e-12)


def test_signal_ordering_is_deterministic_by_score_then_ticker():
    t0 = clock_us(DAY, 600, 0)
    rows = [_row(DAY, "GGG", t0, 0.01, pred=0.5), _row(DAY, "HHH", t0, 0.02, pred=0.9)]
    _, first = mp.replay_cell(_scores(rows), [DAY], 5, 0.0, 0.0)
    _, second = mp.replay_cell(_scores(list(reversed(rows))), [DAY], 5, 0.0, 0.0)
    assert [tr["ticker"] for tr in first] == [tr["ticker"] for tr in second] == ["HHH", "GGG"]
    assert first[0]["score"] > first[1]["score"]


# --- fit/bookkeeping --------------------------------------------------------
def test_equal_day_weights():
    days = ["d1"] * 3 + ["d2"]
    w = mp.equal_day_weights(days)
    assert w[:3].sum() == pytest.approx(0.5)
    assert w[3] == pytest.approx(0.5)
    assert w[0] == w[1] == w[2]


def test_clip_is_fit_only_and_pnl_is_unclipped():
    t0 = clock_us(DAY, 600, 0)
    rows = []
    for i in range(6):
        rows.append(_row(DAY, f"III{i}", t0 + i * 600_000_000, 0.5))
    frame = _scores(rows)
    model, report = mp.fit_head(frame, 5)
    assert report["label_clip_for_fit"] == [-0.2, 0.3]
    assert report["label_unclipped"]["max"] == pytest.approx(0.5)
    assert model is not None
    # the replay still prices the unclipped outcome
    _, trades = mp.replay_cell(_scores([_row(DAY, "JJJ", t0, 0.5)]), [DAY], 5, 0.0, 0.0)
    assert trades[0]["net"] == pytest.approx(0.5)


def test_adjust_coverage_unknown_day_is_whole_book_loss():
    day_rows = [
        {
            "day": DAY,
            "attempts": 2,
            "known_pnl": 10.0,
            "unknown": 0,
            "lower_bound_pnl": 10.0,
            "cash_skips": 0,
            "cooldown_skips": 0,
            "ticker_day_cap_skips": 0,
            "overlap_skips": 0,
            "signals": 3,
        }
    ]
    cov = {DAY: {"complete": False}}
    out = mp.adjust_coverage(day_rows, [DAY], cov)
    assert out[0]["lower_bound_pnl"] == -mp.BOOK
    assert out[0]["coverage_unknown"] is True


def test_bootstrap_is_deterministic():
    a = mp.bootstrap_daily([1.0, -2.0, 3.0])
    b = mp.bootstrap_daily([1.0, -2.0, 3.0])
    assert a == b
    assert a["ci95_lo"] <= a["mean"] <= a["ci95_hi"]


def test_freeze_is_required_before_late(tmp_path):
    with pytest.raises(SystemExit):
        mp.load_frozen(tmp_path)


def test_freeze_identity_preserves_first_freeze_on_schema_replay():
    """A replay of the SAME view keeps the original freeze identity; anything else is fresh."""
    prior = {
        "frozen_at": "2026-10-09T02:10:31+0300",
        "chosen": {"horizon": 5, "threshold": 0.001},
        "fit_report": {"train_rows": 7},
    }
    same = {"horizon": 5, "threshold": 0.001}
    at, rep, replay = mp.freeze_identity(prior, None, same)
    assert replay is True and at == prior["frozen_at"] and rep == {"train_rows": 7}
    # a caller that fits (cmd_run) writes a fresh freeze, never a replay of the old identity
    at, rep, replay = mp.freeze_identity(prior, {"train_rows": 8}, same)
    assert replay is False and rep == {"train_rows": 8} and at != prior["frozen_at"]
    # a changed view is a fresh freeze
    at, rep, replay = mp.freeze_identity(prior, None, {"horizon": 15, "threshold": 0.003})
    assert replay is False and rep is None and at != prior["frozen_at"]
    # a first freeze has nothing to preserve
    at, rep, replay = mp.freeze_identity(None, None, same)
    assert replay is False and rep is None


def test_surface_native_two_part_keys_load(tmp_path):
    """The persisted surface keeps the writer's native two-part keys (h<h>|<thr>)."""
    spath = tmp_path / "surface_validation.json"
    spath.write_text(
        json.dumps({"h5|0.001": {"n_known_fills": 0}, "h15|0.010": {"n_known_fills": 8}})
    )
    assert mp.load_surface(spath) == {
        (5, 0.001): {"n_known_fills": 0},
        (15, 0.010): {"n_known_fills": 8},
    }


def test_selection_prefers_highest_mean_then_evidence():
    surface = {
        (5, 0.001): {"mean_daily_net_usd": 1.0, "n_known_fills": 10},
        (5, 0.003): {"mean_daily_net_usd": 1.0, "n_known_fills": 20},
        (15, 0.001): {"mean_daily_net_usd": 0.5, "n_known_fills": 99},
    }
    chosen = mp.choose_cell(surface)
    assert (chosen["horizon"], chosen["threshold"]) == (5, 0.003)


def test_resume_hash_pins_schema_and_day():
    a = mp.resume_hash(DAY, "sha")
    assert a == mp.resume_hash(DAY, "sha")
    assert a != mp.resume_hash("2021-06-02", "sha")
    assert a != mp.resume_hash(DAY, "other")
