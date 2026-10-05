"""Causality / action-coding regressions for the FULL-MINUTE owned-claim value producer.

Each test freezes one consumer-visible invariant of ``scripts.owned_claim_minute_value``
on a tiny hand-built synthetic corpus:

  * a claim minute's fitted feature plane is a function of the PAST only: rewriting every
    future liquidation print (``sell_px`` / ``sell_et`` / ``sell_volume``) leaves every
    fitted feature bit-identical while the forward labels it is scored against do move —
    so a reintroduced future-availability feature (the removed ``feature_sellable``) fails
    here instead of silently training on the future;
  * a fixed-horizon view publishes its raw forecast only where the target is calendar
    reachable, and ``CALENDAR_EXIT`` — an ACTION code, never a 0 HOLD — where it is not;
  * ``pred_max`` maximises over calendar-FEASIBLE horizons only: an unreachable horizon's
    large positive forecast is neither selected nor published, an all-negative feasible set
    stays negative (so the unchanged engine's release rule still fires), and a minute with
    NO reachable horizon publishes the declared exit action with ``sel_h = -1``, kept
    distinct from the last minute that can still exit;
  * feasible-but-all-non-finite forecasts raise instead of publishing a value the engine
    would read as a silent HOLD, while a genuinely zero forecast stays a legal HOLD;
  * one horizon column carries BOTH known and UNKNOWN cells split exactly by calendar
    reachability, and a missing future print is UNKNOWN rather than zero-filled;
  * the unchanged stop engine, handed the declared exit action, actually releases the owned
    shares at the next observed open and books the real proceeds — the sentinel is not a
    forecast of, or a claim on, 1e6 dollars;
  * the label of a known case equals the canonical per-original-claim-cash dollar with the
    common 0.995/1.005 fee scale applied once, a reprint of the SAME opening is UNKNOWN
    (not a known zero), and a sale that cannot land inside its own horizon is UNKNOWN even
    when the very next minute prints a strictly later, in-session open.

No source-text / wiring / defaults / mock-echo assertions: every assertion compares real
feature matrices, real labels, real action columns or real settled cash on a corpus with a
unique financial path.  Nothing here reads market data.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
import pytest  # noqa: E402
from scripts.owned_claim_replay import simulate  # noqa: E402


def _minute_module():
    """Import the minute-value producer, skipping on a genuinely absent backend.

    The rules under test (feature provenance, calendar feasibility, action encoding) are
    pure column logic, but the module imports lightgbm at load time; a genuinely missing
    backend skips explicitly rather than fabricating a fake module that would let the
    import "succeed" and prove nothing.
    """
    pytest.importorskip("lightgbm")
    return importlib.import_module("scripts.owned_claim_minute_value")


# ------------------------------------------------------------------ synthetic claim panel
# The panel's own causal columns, in the exact order the producer expects: `feats` must
# decompose into the claim's `feature_*` columns (this order) followed by the whitelisted
# derived columns, and every one of these names is a past-observable column of
# owned_claim_events.load_day.
PANEL_FEATS = (
    "feature_t",
    "feature_clock",
    "feature_rank",
    "feature_px",
    "feature_fill_px",
    "feature_remaining_session",
    "feature_ret_fill",
    "feature_dd_from_high",
    "feature_ret5",
    "feature_peak_gain",
    "feature_minutes_since_high",
    "feature_dd_velocity5",
    "feature_drank5",
    "feature_reclaim_fill_now",
)

T0, SESSION_END, FILL_ET, FILL_PX = 600, 700, 600, 10.0
FULL_GRID = tuple(range(T0, SESSION_END + 1))


def _claim_frame(mv, sell_px, sell_et, session_end=SESSION_END, sell_volume=1e6):
    """One filled claim's contiguous minute grid and its declared feature list.

    ``sell_px`` / ``sell_et`` / ``sell_volume`` are the NEXT observed open at each minute —
    the label plane only.  Everything else is the past-only panel read verbatim.
    """
    rows = []
    peak, high_t = FILL_PX, T0
    for i in range(len(sell_px)):
        t = T0 + i
        px = FILL_PX + 0.05 * i
        if px > peak:
            peak, high_t = px, t
        ret_fill = px / FILL_PX - 1.0
        dd = px / peak - 1.0
        ret5 = 0.02 if i % 2 else 0.04
        drank = 0.0
        vel = -0.01 if dd < 0 else 0.02
        peak_gain = peak / FILL_PX - 1.0
        msh = float(t - high_t)
        row = {
            "day": "d1",
            "clock": 600,
            "rank": 1,
            "ticker": "A",
            "t": t,
            "session_end": session_end,
            "status": "filled",
            "fill_et": FILL_ET,
            "fill_px": FILL_PX,
            "fill_volume": 1e6,
            # ---- canonical flag columns (owned_claim_events.FLAG_KEYS)
            "ret_fill": ret_fill,
            "dd_from_high": dd,
            "ret5": ret5,
            "peak_gain": peak_gain,
            "minutes_since_high": msh,
            "dd_velocity5": vel,
            "drank5": drank,
            "reclaim_fill_now": False,
            # ---- label plane: the next observed open at this minute
            "sell_px": sell_px[i],
            "sell_et": sell_et[i],
            "sell_volume": sell_volume,
        }
        row.update(
            {
                "feature_t": float(t),
                "feature_clock": 600.0,
                "feature_rank": 1.0,
                "feature_px": px,
                "feature_fill_px": FILL_PX,
                "feature_remaining_session": float(session_end - t),
                "feature_ret_fill": ret_fill,
                "feature_dd_from_high": dd,
                "feature_ret5": ret5,
                "feature_peak_gain": peak_gain,
                "feature_minutes_since_high": msh,
                "feature_dd_velocity5": vel,
                "feature_drank5": drank,
                "feature_reclaim_fill_now": 0.0,
            }
        )
        rows.append(row)
    claim = pl.DataFrame(rows)
    feats = list(PANEL_FEATS) + list(mv.DERIVED_FEATURES)
    mv.check_features(feats, list(PANEL_FEATS))
    return claim, feats


def _as_float64(frame, cols):
    return frame.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()


def _ladder(overrides_px=None, overrides_et=None):
    """A rising ladder of opens over the FULL session grid.

    ``overrides_px`` / ``overrides_et`` are keyed by MINUTE (``t``), not by list index, so
    the label-plane edits in each test read as the market-time statements they are.
    """
    sell_px = {t: FILL_PX + 0.05 * (t - T0) for t in FULL_GRID}
    sell_et = {t: t + 1 for t in FULL_GRID}
    sell_px.update(overrides_px or {})
    sell_et.update(overrides_et or {})
    return [sell_px[t] for t in FULL_GRID], [sell_et[t] for t in FULL_GRID]


def _expected_label(sell_px, mv, t, h):
    """The declared cash-unit label at minute ``t`` for horizon ``h``, computed by hand."""
    times = list(FULL_GRID)
    i = times.index(t)
    j = min(next(k for k, tt in enumerate(times) if tt >= t + h), len(times) - 1)
    value = (sell_px[i] / FILL_PX) * (sell_px[j] / sell_px[i] - 1.0)
    value *= (1.0 - mv.FEE_SIDE) / (1.0 + mv.FEE_SIDE)
    return value


# ---------------------------------------------------------------- 1. past-only features
def test_future_liquidation_prints_cannot_move_any_fitted_feature():
    mv = _minute_module()
    sell_px, sell_et = _ladder()
    base = mv.claim_minute_frame(*_claim_frame(mv, sell_px, sell_et), "d1")

    # Only the label plane moves: from minute 605 on, EVERY future liquidation print is
    # rewritten — price, opening time and volume.  Nothing at or before 604 changes.
    moved_px, moved_et = _ladder(
        overrides_px={t: 40.0 - 0.2 * (t - T0) for t in FULL_GRID if t >= 605},
        overrides_et={t: t + 2 for t in FULL_GRID if t >= 605},
    )
    moved = mv.claim_minute_frame(*_claim_frame(mv, moved_px, moved_et, sell_volume=1e3), "d1")

    # both grids cover the same FULL post-fill minute grid, thinned by nothing
    assert base.height == moved.height == len(FULL_GRID) - 1
    assert base["t"].to_list() == moved["t"].to_list() == list(FULL_GRID[1:])

    # the fitted predictor plane is byte-identical, NaN-aware
    cols = list(PANEL_FEATS) + list(mv.DERIVED_FEATURES)
    assert np.array_equal(_as_float64(base, cols), _as_float64(moved, cols), equal_nan=True)
    # ...while the forward labels really did move, so this corpus is label-sensitive
    assert any(
        not np.array_equal(
            base[f"label_h{h}"].to_numpy(), moved[f"label_h{h}"].to_numpy(), equal_nan=True
        )
        for h in mv.HORIZONS
    )
    # the compared plane is not a block of constants, so the equality above is a real
    # invariance claim rather than a comparison of two degenerate matrices
    assert np.unique(_as_float64(base, ["feature_minutes_since_fill"])).size > 1


# ---------------------------------------------------------------- 2. calendar actions
def _forecasts(mv, **by_horizon):
    preds = {h: np.full(3, -0.10, dtype=np.float64) for h in mv.HORIZONS}
    for name, values in by_horizon.items():
        preds[int(name[1:])] = np.asarray(values, dtype=np.float64)
    return preds


def test_pred_max_selects_only_calendar_feasible_horizons():
    mv = _minute_module()

    # three minutes: everything reachable | only h<=5 reachable | nothing reachable
    t = np.array([600, 691, SESSION_END], dtype=np.int64)
    session_end = np.array([1000, SESSION_END, SESSION_END], dtype=np.int64)
    preds = _forecasts(
        mv,
        h1=[-0.01, -0.01, -0.01],
        h3=[-0.02, -0.02, -0.02],
        h5=[-0.50, -0.50, -0.50],
        h10=[-0.30, 9.90, 9.90],
        h120=[-7.50, 7.50, 7.50],
    )

    p_max, sel, feas = mv.horizon_views(preds, t, session_end)
    assert feas.tolist() == [
        [True] * 8,
        [True, True, True, False, False, False, False, False],
        [False] * 8,
    ]
    # every reachable forecast is negative, so the maximum is negative too: the unchanged
    # engine's `pred < 0` release rule still sees a release
    assert p_max[0] == pytest.approx(-0.01) and sel[0] == 1
    # near the boundary the unreachable h10 forecast (+9.90) is neither selected nor read
    assert p_max[1] == pytest.approx(-0.01) and sel[1] == 1
    assert not np.isclose(p_max[1], 9.90)
    # nothing reachable -> the declared ACTION code, never a 0 that would read as HOLD
    assert p_max[2] == mv.CALENDAR_EXIT and sel[2] == -1
    assert p_max[2] != 0.0 and p_max[2] < 0
    # the last minute that can still exit and the terminal minute stay distinct
    assert sel[1] != sel[2] and p_max[1] != p_max[2]


def test_fixed_horizon_view_encodes_reachability_not_a_padded_forecast():
    mv = _minute_module()
    forecast = np.array([0.02, 0.02, -0.03, 0.02])
    reachable = np.array([True, False, True, False])
    view = mv.encode_horizon_view(forecast, reachable)

    assert view[0] == pytest.approx(0.02)  # reachable: the raw forecast, unscaled
    assert view[2] == pytest.approx(-0.03)  # reachable negative: a real release, not the code
    assert view[1] == mv.CALENDAR_EXIT  # unreachable: the declared exit action
    assert view[3] == mv.CALENDAR_EXIT  # unreachable whatever the forecast's sign
    assert view[1] != forecast[1] and view[3] != forecast[3]
    assert np.isfinite(view).all()


def test_nonfinite_forecast_on_feasible_horizons_refuses_rather_than_holding():
    mv = _minute_module()
    t = np.array([600, 690], dtype=np.int64)
    session_end = np.array([SESSION_END, SESSION_END], dtype=np.int64)
    preds = {h: np.array([np.nan, np.nan]) for h in mv.HORIZONS}
    preds[120] = np.array([5.0, 5.0])  # unreachable on both rows: irrelevant to the refusal

    with pytest.raises(SystemExit) as exc:
        mv.horizon_views(preds, t, session_end)
    assert "refusing to publish" in str(exc.value)

    # the fixed-horizon ACTION column has the same guard: a non-finite forecast on a
    # reachable target is refused instead of being published as a silent HOLD
    with pytest.raises(SystemExit):
        mv.encode_horizon_view(np.array([np.nan]), np.array([True]))
    # an unreachable cell never publishes the raw forecast, so its NaN cannot reach the
    # engine either — the declared exit action replaces it
    assert mv.encode_horizon_view(np.array([np.nan]), np.array([False]))[0] == mv.CALENDAR_EXIT

    # a finite zero is a legitimate HOLD forecast and must not trip either refusal
    zeros = {h: np.zeros(2, dtype=np.float64) for h in mv.HORIZONS}
    p_max, sel, _ = mv.horizon_views(zeros, t, session_end)
    assert p_max.tolist() == [0.0, 0.0] and sel.tolist() == [1, 1]
    assert mv.encode_horizon_view(np.array([0.0]), np.array([True]))[0] == 0.0


# ---------------------------------------------------------------- 3. labels: UNKNOWN
def test_one_horizon_column_carries_known_and_unknown_split_by_reachability():
    mv = _minute_module()
    sell_px, sell_et = _ladder()
    grid = mv.claim_minute_frame(*_claim_frame(mv, sell_px, sell_et), "d1")
    assert grid is not None

    times = grid["t"].to_numpy().astype(np.int64)
    label = grid["label_h60"].to_numpy()
    known = np.isfinite(label)
    assert known.any() and (~known).any()
    assert (label == 0.0).sum() == 0
    # the h60 target must still have an observed open INSIDE the session; the ladder's last
    # in-session open is at 700, so minute 639 is the last reachable one and 640 is not
    assert times[known].max() == 639 and known[times > 639].sum() == 0
    assert known[times < 639].all()
    # ...and a known cell equals the declared cash-unit value, not a zero
    known_row = int(np.flatnonzero(known)[0])
    assert label[known_row] == pytest.approx(
        _expected_label(sell_px, mv, int(times[known_row]), 60), rel=1e-6
    )
    assert not np.isclose(label[known_row], 0.0)


def test_missing_future_print_is_unknown_not_zero():
    mv = _minute_module()
    sell_px, sell_et = _ladder(overrides_px={612: float("nan")})
    grid = mv.claim_minute_frame(*_claim_frame(mv, sell_px, sell_et), "d1")
    assert grid is not None

    times = grid["t"].to_numpy().astype(np.int64)
    label = grid["label_h1"].to_numpy()
    known = np.isfinite(label)
    # this minute has no executable open at all, and the minute before has no future print
    assert not known[times == 612].any() and not known[times == 611].any()
    # ...while the minutes either side still do, so UNKNOWN is not blanket
    assert known[times == 610].all() and known[times == 613].all()
    assert (label[times == 612] == 0.0).sum() == 0


def test_flat_price_between_two_distinct_opens_is_a_known_zero_not_unknown():
    """A reachable target that prints the SAME price is a KNOWN zero continuation.

    Two observed opening TIMES at one price (630 -> 631) and one opening TIME reprinted
    (640 -> 641, 642 -> 643) share the identical positive price.  Only the first has a
    strictly later in-session open to sell into, so only the first is a known zero; a
    zero-censoring bug (an exact zero turned into UNKNOWN) or an availability bug (a
    flat print read as "no print") cannot satisfy both cells at once.
    """
    mv = _minute_module()
    flat_630 = FILL_PX + 0.05 * (630 - T0)
    sell_px, sell_et = _ladder(
        overrides_px={
            631: flat_630,  # same positive price, one minute later
            641: FILL_PX + 0.05 * (640 - T0),  # REPRINT of minute 640's price and time
            643: FILL_PX + 0.05 * (642 - T0),
        },
        overrides_et={641: 641, 643: 643},
    )
    grid = mv.claim_minute_frame(*_claim_frame(mv, sell_px, sell_et), "d1")
    assert grid is not None

    times = grid["t"].to_numpy().astype(np.int64)
    ix = {int(t): i for i, t in enumerate(times)}
    label = grid["label_h1"].to_numpy()

    # two distinct opening times at one positive price -> the continuation is KNOWN and
    # exactly zero: holding to that open neither gains nor loses relative to selling now
    assert flat_630 > 0
    assert sell_px[FULL_GRID.index(631)] == sell_px[FULL_GRID.index(630)] == flat_630
    assert sell_et[FULL_GRID.index(631)] == sell_et[FULL_GRID.index(630)] + 1
    assert np.isfinite(label[ix[630]]) and label[ix[630]] == 0.0
    assert _expected_label(sell_px, mv, 630, 1) == 0.0

    # the same flat price with only ONE opening time is UNKNOWN, not a zero
    for flat_t, reprint_t in ((640, 641), (642, 643)):
        assert sell_px[FULL_GRID.index(reprint_t)] == sell_px[FULL_GRID.index(flat_t)]
        assert sell_et[FULL_GRID.index(reprint_t)] == sell_et[FULL_GRID.index(flat_t)]
        assert np.isnan(label[ix[flat_t]])

    # a zero is a value, not a hole: the surrounding ladder is known and non-zero, so the
    # exact zero above cannot come from the whole column being censored or filled
    assert np.isfinite(label[ix[631]]) and label[ix[631]] > 0
    assert np.isfinite(label[ix[629]]) and label[ix[629]] > 0
    assert label[ix[631]] == pytest.approx(_expected_label(sell_px, mv, 631, 1), rel=1e-6)


# ---------------------------------------------------------------- 4. stop engine release
def _stop_book():
    """One filled claim; the only exit decision point is t=690, whose next open is 695."""
    roster = [
        {
            "day": "d1",
            "clock": 600,
            "rank": 1,
            "ticker": "A",
            "session_end": SESSION_END,
            "decision_px": 10.0,
            "status": "filled",
            "fill_et": 600,
            "fill_px": 10.0,
            "fill_volume": 1e6,
        }
    ]
    rows = [
        {"t": 600, "ticker": "A", "px": 10.0, "sell_et": 600, "sell_px": 10.0, "sell_volume": 1e6},
        {"t": 690, "ticker": "A", "px": 9.9, "sell_et": 695, "sell_px": 9.9, "sell_volume": 1e6},
        {"t": 695, "ticker": "A", "px": 9.9, "sell_et": 695, "sell_px": 9.9, "sell_volume": 1e6},
        {"t": 700, "ticker": "A", "px": 9.8, "sell_et": 700, "sell_px": 9.8, "sell_volume": 1e6},
    ]
    return rows, roster


def test_calendar_exit_action_releases_owned_shares_at_the_next_open():
    mv = _minute_module()
    rows, roster = _stop_book()
    side, n = 0.005, 1

    # The h120 view at t=690: 690 + 120 is past the session end, so the target is
    # unreachable and the published action is CALENDAR_EXIT whatever the forecast was.
    feasible = mv.feasibility(np.array([690]), np.array([SESSION_END]))[:, mv.HORIZONS.index(120)]
    assert not bool(feasible[0])
    action = mv.encode_horizon_view(np.array([9.90]), feasible)
    assert action[0] == mv.CALENDAR_EXIT

    released_daily, released_members, released_fills = simulate(
        rows,
        roster,
        {("A", 690): {"pred_h120": float(action[0]), "event_kind": "exit"}},
        side,
        n,
        "h120:stop",
    )
    held_daily, held_members, held_fills = simulate(
        rows,
        roster,
        {("A", 690): {"pred_h120": 0.25, "event_kind": "hold"}},
        side,
        n,
        "h120:stop",
    )

    entry = next(f for f in released_fills if f["side"] == "buy")
    state = [f for f in released_fills if f["reason"] == "state_release"]
    assert len(state) == 1
    sell = state[0]
    assert sell["side"] == "sell" and sell["decision_et"] == 690 and sell["exec_et"] == 695
    # the whole owned position left at the next observed open
    assert sell["shares_per_capital"] == pytest.approx(entry["shares_per_capital"])
    assert released_members[0]["remaining_shares"] == pytest.approx(0.0)
    assert not released_daily["unknown"] and released_members[0]["net_pnl"] is not None
    assert released_fills[-1]["exec_et"] == 695  # nothing left for the terminal sweep

    # real proceeds only: the sentinel is an ACTION code, not a 1e6-dollar forecast the
    # book may bank as wealth
    proceeds = sell["gross_fraction"] - sell["fee_fraction"]
    assert proceeds == pytest.approx(sell["shares_per_capital"] * 9.9 * (1 - side))
    assert proceeds < entry["gross_fraction"]  # 9.9 against the 10.0 entry: a real loss
    assert released_daily["ret"] == pytest.approx(proceeds - 1.0, abs=1e-12)
    assert abs(released_daily["ret"]) < 0.05 and abs(released_daily["gross_pnl"]) < 0.05

    # the same book HELD by a positive forecast never releases early
    assert not any(f["reason"] == "state_release" for f in held_fills)
    assert [f["exec_et"] for f in held_fills if f["reason"] == "terminal"] == [700]
    assert held_members[0]["reentries"] == 0
    # holding to the 9.8 terminal open books a strictly worse result than exiting at 695
    assert held_daily["ret"] < released_daily["ret"]
    assert not held_daily["unknown"] and held_members[0]["net_pnl"] is not None


# ---------------------------------------------------------------- 5. known label value
def test_known_label_is_cash_scaled_and_needs_a_strictly_later_open():
    mv = _minute_module()
    # a REPRINT of the same observed open: minute 605 carries minute 604's price AND time,
    # so its h1 window contains no strictly later opening
    sell_px, sell_et = _ladder(
        overrides_px={605: FILL_PX + 0.05 * (604 - T0)},
        overrides_et={
            605: 605,  # a REPRINT of minute 604's opening time AND price
            660: 668,  # this minute's own sale lands long after its short horizons ...
            661: 669,  # ... while its NEXT opening is strictly later and in-session
        },
    )
    # the next observed open at minute 612 does not exist
    sell_px[FULL_GRID.index(612)] = float("nan")

    grid = mv.claim_minute_frame(*_claim_frame(mv, sell_px, sell_et), "d1")
    assert grid is not None
    times = grid["t"].to_numpy().astype(np.int64)
    ix = {int(t): i for i, t in enumerate(times)}
    label = {h: grid[f"label_h{h}"].to_numpy() for h in mv.HORIZONS}

    # known case: minute 601, h=1 -> the open at 602, in per-original-claim-CASH dollars
    # with the common 0.995/1.005 fee scale applied exactly once
    expected = _expected_label(sell_px, mv, 601, 1)
    assert expected > 0 and mv.FEE_RATIO == 0.995 / 1.005
    assert label[1][ix[601]] == pytest.approx(expected, rel=1e-6)
    # the unfee'd gross-entry-dollar version is NOT the published unit
    gross = sell_px[FULL_GRID.index(602)] / FILL_PX - 1.0
    assert not np.isclose(label[1][ix[601]], gross, rtol=1e-4)

    # a reprint of the SAME opening is UNKNOWN, not a known zero: the horizon contains no
    # strictly later observed open to sell into
    assert sell_px[FULL_GRID.index(605)] == sell_px[FULL_GRID.index(604)]
    assert np.isnan(label[1][ix[604]])
    # ...while a longer horizon that does contain a strictly later, in-session open is a
    # real, non-zero label
    longer = label[15][ix[604]]
    assert np.isfinite(longer) and not np.isclose(longer, 0.0)
    assert longer == pytest.approx(_expected_label(sell_px, mv, 604, 15), rel=1e-6)

    # not enough horizon: minute 660's own sale arrives at 668, past t+1, yet the minute
    # right after it prints at 669 — strictly later and inside the session.  Only the
    # "current sale must land inside the horizon" guard makes that cell UNKNOWN, and the
    # value it would otherwise publish is a real non-zero number.
    assert np.isnan(label[1][ix[660]])
    assert sell_et[FULL_GRID.index(661)] > sell_et[FULL_GRID.index(660)]
    assert sell_et[FULL_GRID.index(661)] <= SESSION_END
    guarded = (
        (sell_px[FULL_GRID.index(660)] / FILL_PX)
        * (sell_px[FULL_GRID.index(661)] / sell_px[FULL_GRID.index(660)] - 1.0)
        * mv.FEE_RATIO
    )
    assert guarded > 0
    # a wider horizon does contain this minute's own opening, so the same minute is known
    assert np.isfinite(label[10][ix[660]])

    # a missing future print is UNKNOWN, never zero-filled
    assert np.isnan(label[1][ix[611]]) and np.isnan(label[1][ix[612]])
    assert np.isfinite(label[1][ix[609]])
