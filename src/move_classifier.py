"""
Phase 4 — Move-state classifier per SPEC section 9.

Classifies a candidate into one of five states (priority order):
  1. halt_risk
  2. backside
  3. extended
  4. active
  5. early

Each classification returns the state, a recommended mode, and a list of
human-readable evidence strings.  No entry logic — that is Phase 5.

No network calls.  No bar-data parsing — the caller is responsible for
extracting features from raw bars before calling the classifier.
"""

from __future__ import annotations

from typing import Optional

from src.models.schemas import ModeType, MoveState


# ──────────────────────────────────────────────────────────────────
#  Main classifier
# ──────────────────────────────────────────────────────────────────


def classify_move_state(
    *,
    # ── Basic identity ──────────────────────────────────────
    symbol: str = "",
    price: Optional[float] = None,
    day_high: Optional[float] = None,
    day_low: Optional[float] = None,
    # ── Halt / spread ───────────────────────────────────────
    halt_count_today: int = 0,
    spread_pct: Optional[float] = None,
    quote_instability: bool = False,
    # ── Volume ──────────────────────────────────────────────
    rvol: Optional[float] = None,
    volume_fading: bool = False,
    # ── Bar-derived features ────────────────────────────────
    avg_range: Optional[float] = None,
    vwap: Optional[float] = None,
    ema9: Optional[float] = None,
    # ── Pattern detection ───────────────────────────────────
    lower_highs_count: int = 0,
    failed_hod_reclaim: bool = False,
    consecutive_below_vwap: int = 0,
    failed_vwap_reclaim: bool = False,
    bounces_failing: bool = False,
    vertical_move: bool = False,
    vertical_without_pullback: bool = False,
    candle_range_gt_2x_avg: bool = False,
    has_pullback_formed: bool = False,
    pullback_low: Optional[float] = None,
    nearest_stop_distance_pct: Optional[float] = None,
    hod_behavior_repeated: bool = False,
    higher_low_structure: bool = False,
    pullbacks_bought: bool = False,
    strong_volume: bool = False,
    price_moved_pct_5m: Optional[float] = None,
    # ── Context ─────────────────────────────────────────────
    from_early_pullback: bool = False,
    appeared_recently: bool = False,
    attention_building: bool = False,
    volume_improving: bool = False,
    prior_state: Optional[str] = None,
    multi_day_runner: bool = False,
    # ── Thresholds (SPEC defaults, tunable) ──────────────────
    max_stop_width_pct: float = 5.0,
    parabolic_extended_pct: float = 15.0,
    _state_debug: bool = False,
) -> tuple[MoveState, ModeType, list[str]]:
    """Classify a candidate's move state per SPEC §9.

    Returns
    -------
    (MoveState, ModeType, list[str])
        The detected state, recommended mode, and evidence list.
    """
    evidence: list[str] = []

    # ── 1. Halt-risk ───────────────────────────────────────────
    is_hr, hr_evidence = _is_halt_risk(
        halt_count_today=halt_count_today,
        spread_pct=spread_pct,
        vertical_move=vertical_move,
        price_moved_pct_5m=price_moved_pct_5m,
        quote_instability=quote_instability,
        vertical_without_pullback=vertical_without_pullback,
        avg_range=avg_range,
    )
    if is_hr:
        evidence.extend(hr_evidence)
        return MoveState.HALT_RISK, ModeType.AVOID_NEW_LONGS, evidence

    # ── 2. Backside / fading ───────────────────────────────────
    is_bs, bs_evidence = _is_backside(
        lower_highs_count=lower_highs_count,
        failed_hod_reclaim=failed_hod_reclaim,
        consecutive_below_vwap=consecutive_below_vwap,
        failed_vwap_reclaim=failed_vwap_reclaim,
        volume_fading=volume_fading,
        bounces_failing=bounces_failing,
        spread_pct=spread_pct,
        price=price,
        vwap=vwap,
    )
    if is_bs:
        evidence.extend(bs_evidence)
        return MoveState.BACKSIDE, ModeType.AVOID_NEW_LONGS, evidence

    # ── 3. Extended / parabolic ────────────────────────────────
    is_ex, ex_evidence = _is_extended(
        nearest_stop_distance_pct=nearest_stop_distance_pct,
        max_stop_width_pct=max_stop_width_pct,
        price=price,
        pullback_low=pullback_low,
        parabolic_extended_pct=parabolic_extended_pct,
        candle_range_gt_2x_avg=candle_range_gt_2x_avg,
        vertical_without_pullback=vertical_without_pullback,
        has_pullback_formed=has_pullback_formed,
    )
    if is_ex:
        evidence.extend(ex_evidence)
        return MoveState.EXTENDED, ModeType.SCALP_ONLY, evidence

    # ── 4. Active ──────────────────────────────────────────────
    is_ac, ac_evidence = _is_active(
        hod_behavior_repeated=hod_behavior_repeated,
        higher_low_structure=higher_low_structure,
        pullbacks_bought=pullbacks_bought,
        strong_volume=strong_volume,
        rvol=rvol,
        spread_pct=spread_pct,
        nearest_stop_distance_pct=nearest_stop_distance_pct,
        max_stop_width_pct=max_stop_width_pct,
    )
    if is_ac:
        evidence.extend(ac_evidence)
        return MoveState.ACTIVE, ModeType.STARTER_ENTRY, evidence

    # ── 5. Early ───────────────────────────────────────────────
    evidence.append("default_early")
    if appeared_recently:
        evidence.append("appeared_recently")
    if attention_building:
        evidence.append("attention_building")
    if volume_improving:
        evidence.append("volume_improving")
    if from_early_pullback:
        evidence.append("from_early_pullback")

    return MoveState.EARLY, ModeType.WATCH, evidence


# ══════════════════════════════════════════════════════════════════
#  State detectors (each returns (detected, evidence))
# ══════════════════════════════════════════════════════════════════


def _is_halt_risk(
    *,
    halt_count_today: int = 0,
    spread_pct: Optional[float] = None,
    vertical_move: bool = False,
    price_moved_pct_5m: Optional[float] = None,
    quote_instability: bool = False,
    vertical_without_pullback: bool = False,
    avg_range: Optional[float] = None,
) -> tuple[bool, list[str]]:
    """Detect halt-risk state (SPEC §9.3)."""
    evidence: list[str] = []
    signals: int = 0

    if halt_count_today > 0:
        evidence.append(f"halt_count={halt_count_today}")
        signals += 1

    if spread_pct is not None and spread_pct > 3.0 and vertical_move:
        evidence.append(f"spread_gt_3pct_and_vertical:spread={spread_pct}")
        signals += 1

    if price_moved_pct_5m is not None and price_moved_pct_5m >= 10.0:
        evidence.append(f"moved_gt_10pct_in_5m:{price_moved_pct_5m}%")
        signals += 1

    if quote_instability:
        evidence.append("quote_instability")
        signals += 1

    if vertical_without_pullback:
        evidence.append("vertical_without_pullback")
        signals += 1

    return signals > 0, evidence


def _is_backside(
    *,
    lower_highs_count: int = 0,
    failed_hod_reclaim: bool = False,
    consecutive_below_vwap: int = 0,
    failed_vwap_reclaim: bool = False,
    volume_fading: bool = False,
    bounces_failing: bool = False,
    spread_pct: Optional[float] = None,
    price: Optional[float] = None,
    vwap: Optional[float] = None,
) -> tuple[bool, list[str]]:
    """Detect backside/fading state (SPEC §9.3)."""
    evidence: list[str] = []
    signals: int = 0

    # At least 2 lower highs over last 20 bars and failed HOD reclaim
    if lower_highs_count >= 2 and failed_hod_reclaim:
        evidence.append(f"lower_highs={lower_highs_count}_and_failed_hod_reclaim")
        signals += 1

    # Below VWAP for 5 consecutive bars and at least one failed VWAP reclaim
    if consecutive_below_vwap >= 5 and failed_vwap_reclaim:
        evidence.append(f"below_vwap={consecutive_below_vwap}_bars_with_failed_reclaim")
        signals += 1

    # Volume fading while bounces fail
    if volume_fading and bounces_failing:
        evidence.append("volume_fading_and_bounces_failing")
        signals += 1

    # Spread widening while price cannot reclaim
    if spread_pct is not None and spread_pct > 1.0 and not _can_reclaim(price, vwap):
        evidence.append(f"spread_widening_no_reclaim:spread={spread_pct}")
        signals += 1

    return signals > 0, evidence


def _is_extended(
    *,
    nearest_stop_distance_pct: Optional[float] = None,
    max_stop_width_pct: float = 5.0,
    price: Optional[float] = None,
    pullback_low: Optional[float] = None,
    parabolic_extended_pct: float = 15.0,
    candle_range_gt_2x_avg: bool = False,
    vertical_without_pullback: bool = False,
    has_pullback_formed: bool = False,
) -> tuple[bool, list[str]]:
    """Detect extended/parabolic state (SPEC §9.3)."""
    evidence: list[str] = []
    signals: int = 0

    # Distance from nearest logical stop > setup max stop width
    if nearest_stop_distance_pct is not None and nearest_stop_distance_pct > max_stop_width_pct:
        evidence.append(f"stop_distance={nearest_stop_distance_pct}pct_gt_max={max_stop_width_pct}")
        signals += 1

    # Price >15% above last clean pullback low
    if price is not None and pullback_low is not None and pullback_low > 0:
        above_pct = (price - pullback_low) / pullback_low * 100
        if above_pct > parabolic_extended_pct:
            evidence.append(f"price={price}_gt_15pct_above_pb_low={pullback_low}")
            signals += 1

    # Recent candle range >2x avg range for multiple candles
    if candle_range_gt_2x_avg:
        evidence.append("candle_range_gt_2x_avg")
        signals += 1

    # Vertical move without pullback
    if vertical_without_pullback and not has_pullback_formed:
        evidence.append("vertical_without_pullback")
        signals += 1

    return signals > 0, evidence


def _is_active(
    *,
    hod_behavior_repeated: bool = False,
    higher_low_structure: bool = False,
    pullbacks_bought: bool = False,
    strong_volume: bool = False,
    rvol: Optional[float] = None,
    spread_pct: Optional[float] = None,
    nearest_stop_distance_pct: Optional[float] = None,
    max_stop_width_pct: float = 5.0,
) -> tuple[bool, list[str]]:
    """Detect active state (SPEC §9.3).

    Active requires *most* signals to be true (≥3 of 5 core signals,
    with override if volume is strong and only 2 core signals).
    """
    evidence: list[str] = []
    core_signals: int = 0

    if hod_behavior_repeated:
        evidence.append("hod_repeated")
        core_signals += 1
    if higher_low_structure:
        evidence.append("higher_lows")
        core_signals += 1
    if pullbacks_bought:
        evidence.append("pullbacks_bought")
        core_signals += 1
    if strong_volume or (rvol is not None and rvol >= 2.0):
        evidence.append("strong_volume")
        core_signals += 1

    # Spread manageable
    if spread_pct is not None:
        if spread_pct <= 3.0:
            evidence.append(f"spread_ok={spread_pct}%")
            core_signals += 1
        else:
            evidence.append(f"spread_wide={spread_pct}%")
    else:
        evidence.append("spread_unknown")

    # Not too extended from stop
    if nearest_stop_distance_pct is not None and nearest_stop_distance_pct <= max_stop_width_pct:
        evidence.append("stop_in_range")
        core_signals += 1
    elif nearest_stop_distance_pct is not None:
        evidence.append(f"stop_too_far={nearest_stop_distance_pct}pct")

    # Active requires most signals: ≥3 of 5
    detected = core_signals >= 3
    return detected, evidence


def _can_reclaim(price: Optional[float], vwap: Optional[float]) -> bool:
    """Check if price is close enough to VWAP for a potential reclaim."""
    if price is None or vwap is None or vwap <= 0:
        return False
    distance_pct = abs(price - vwap) / vwap * 100
    return distance_pct <= 2.0  # within 2% of VWAP — reclaimable


# ──────────────────────────────────────────────────────────────────
#  Entry permission matrix (SPEC §9.4)
# ──────────────────────────────────────────────────────────────────

# Which setups are allowed in which state.
_ENTRY_PERMISSION: dict[str, set[str]] = {
    "early":    {"first_pullback", "vwap_reclaim"},
    "active":   {"first_pullback", "micro_pullback", "hod_reclaim",
                  "consolidation_breakout", "vwap_reclaim"},
    "extended": {"first_pullback", "hod_reclaim", "vwap_reclaim", "scalp_reclaim"},
    "backside": {"vwap_reclaim"},
    "halt_risk": {"scalp_reclaim"},
}

# Mode mapping per state
_MODE_MAP: dict[str, ModeType] = {
    "early": ModeType.WATCH,
    "active": ModeType.STARTER_ENTRY,
    "extended": ModeType.SCALP_ONLY,
    "backside": ModeType.AVOID_NEW_LONGS,
    "halt_risk": ModeType.AVOID_NEW_LONGS,
}


def setup_allowed(state: MoveState, setup: str) -> bool:
    """Return ``True`` if ``setup`` is permitted in ``state`` per SPEC §9.4.

    Used primarily by tests; the pipeline calls ``get_allowed_setups()``.
    """
    return setup in _ENTRY_PERMISSION.get(state.value, set())


def get_allowed_setups(state: MoveState) -> set[str]:
    """Return the set of setup names allowed in the given state (SPEC §9.4)."""
    return _ENTRY_PERMISSION.get(state.value, set())


def state_mode(state: MoveState) -> ModeType:
    """Return the default mode for a given state.

    Internal convenience — ``classify_move_state()`` returns the mode directly.
    """
    return _MODE_MAP.get(state.value, ModeType.WATCH)
