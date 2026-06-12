"""
Phase 5 — Entry engine per SPEC section 10.

Six price-action setup detectors, each returning a fully-populated
``EntrySignal`` when mechanical criteria are met.  Every signal includes
entry price, stop price, risk per share, target, proposed size, and
invalidation.  No signal is emitted when risk cannot be defined.

Setup priority (SPEC §9.4):
  1. first_pullback
  2. hod_reclaim
  3. consolidation_breakout
  4. micro_pullback
  5. vwap_reclaim
  6. scalp_reclaim

Orchestrator: ``find_entry()`` evaluates setups in priority order and
returns the first valid signal whose setup is permitted in the current
move state (permission matrix from Phase 4).

No network calls.  No broker orders.  Pure detection logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.models.schemas import (
    Candidate,
    EntrySetupType,
    EntrySignal,
    MoveState,
)

# ──────────────────────────────────────────────────────────────────
#  Bar data
# ──────────────────────────────────────────────────────────────────


@dataclass
class Bar:
    """One OHLCV bar (typically 1-minute or 5-minute)."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: Optional[datetime] = None

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    @property
    def is_red(self) -> bool:
        return self.close < self.open


# ──────────────────────────────────────────────────────────────────
#  Shared constants (SPEC defaults)
# ──────────────────────────────────────────────────────────────────

_TICK = 0.01
_STRONG_MOVE_MIN_BARS = 3
_STRONG_MOVE_MAX_BARS = 20
_STRONG_MOVE_MIN_PCT = 5.0
_STRONG_MOVE_VOL_MULT = 1.5
_CONTROLLED_SELLING_MAX_VOL = 0.70

# Max stop widths per setup (SPEC §10.3)
_MAX_STOP_WIDTH: dict[EntrySetupType, float] = {
    EntrySetupType.FIRST_PULLBACK: 5.0,
    EntrySetupType.MICRO_PULLBACK: 3.0,
    EntrySetupType.HOD_RECLAIM: 4.0,
    EntrySetupType.CONSOLIDATION_BREAKOUT: 3.5,
    EntrySetupType.VWAP_RECLAIM: 4.0,
    EntrySetupType.SCALP_RECLAIM: 2.0,
}

# Setup priority (SPEC §9.4)
_SETUP_PRIORITY: list[EntrySetupType] = [
    EntrySetupType.FIRST_PULLBACK,
    EntrySetupType.HOD_RECLAIM,
    EntrySetupType.CONSOLIDATION_BREAKOUT,
    EntrySetupType.MICRO_PULLBACK,
    EntrySetupType.VWAP_RECLAIM,
    EntrySetupType.SCALP_RECLAIM,
]


# ──────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────


def avg_bar_range(bars: list[Bar], n: int = 10) -> float:
    """Average (high - low) of the last ``n`` bars."""
    window = bars[-n:] if len(bars) >= n else bars
    if not window:
        return 0.0
    return sum(b.range for b in window) / len(window)


def avg_volume(bars: list[Bar]) -> float:
    """Average volume across all given bars."""
    if not bars:
        return 0.0
    return sum(b.volume for b in bars) / len(bars)


def _is_strong_move(
    bars: list[Bar],
    avg_range: float,
    *,
    min_bars: int = _STRONG_MOVE_MIN_BARS,
    max_bars: int = _STRONG_MOVE_MAX_BARS,
    min_pct: float = _STRONG_MOVE_MIN_PCT,
    vol_mult: float = _STRONG_MOVE_VOL_MULT,
) -> tuple[bool, int, int]:
    """Detect a strong upward move in the recent bars.

    Returns
    -------
    (found, surge_start_idx, surge_end_idx)
    """
    # Look at up to last max_bars bars
    window = bars[-max_bars:] if len(bars) > max_bars else bars
    if len(window) < min_bars:
        return False, -1, -1

    # Find the most recent sustained upward move
    best_start = len(window) - 1
    best_end = len(window) - 1
    best_advance = 0.0
    best_bar_count = 0

    for start in range(len(window) - 1, -1, -1):
        for end in range(start + min_bars - 1, len(window)):
            bar_count = end - start + 1
            if bar_count > max_bars:
                break
            base_price = window[start].close
            peak_price = max(b.high for b in window[start : end + 1])
            advance_pct = (peak_price - base_price) / base_price * 100 if base_price > 0 else 0
            advance_range = (peak_price - base_price)

            if advance_range >= 3 * avg_range or advance_pct >= min_pct:
                # Check volume
                prior_bars = bars[: -(len(window) - start)] if start > 0 else []
                if prior_bars:
                    prior_avg = avg_volume(prior_bars[max(0, len(prior_bars) - bar_count):])
                else:
                    prior_avg = avg_volume(bars[:start]) if start > 0 else 0

                surge_avg = avg_volume(list(window[start : end + 1]))
                if surge_avg >= prior_avg * vol_mult if prior_avg > 0 else True:
                    if advance_range > best_advance:
                        best_advance = advance_range
                        best_start = start
                        best_end = end
                        best_bar_count = bar_count

    if best_bar_count >= min_bars:
        return True, best_start, best_end

    return False, -1, -1


def _is_controlled_selling(pullback_bars: list[Bar], surge_avg_volume: float) -> bool:
    """Check if pullback volume is controlled (≤70% of surge avg)."""
    if not pullback_bars:
        return True
    pb_avg_vol = avg_volume(pullback_bars)
    if surge_avg_volume <= 0:
        return True
    return pb_avg_vol <= surge_avg_volume * _CONTROLLED_SELLING_MAX_VOL


def _near_level(price: float, level: float, tolerance: float = 0.01) -> bool:
    """Check if price is near a logical level within 1% tolerance."""
    if level <= 0:
        return False
    return abs(price - level) / level <= tolerance


def _build_signal(
    symbol: str,
    setup: EntrySetupType,
    entry_price: float,
    stop_price: float,
    target_price: float,
    invalidation: str,
    state: Optional[MoveState] = None,
    state_evidence: Optional[list[str]] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    data_confidence: float = 1.0,
) -> Optional[EntrySignal]:
    """Build an EntrySignal. Returns None if risk cannot be defined."""
    risk_per_share = round(abs(entry_price - stop_price), 2)
    if risk_per_share <= 0:
        return None

    max_width = _MAX_STOP_WIDTH.get(setup, 5.0)
    stop_width_pct = (risk_per_share / entry_price * 100) if entry_price > 0 else 999
    if stop_width_pct > max_width:
        return None

    try:
        return EntrySignal(
            symbol=symbol,
            entry_setup=setup,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_per_share=risk_per_share,
            target_price=target_price,
            proposed_shares=1,  # Phase 6 replaces with real sizing
            risk_amount=risk_per_share,  # Phase 6 replaces
            invalidation=invalidation,
            state=state,
            state_evidence=state_evidence or [],
            quote_age_seconds=quote_age_seconds,
            spread_pct=spread_pct,
            data_confidence=data_confidence,
        )
    except (ValueError, TypeError):
        return None


# ══════════════════════════════════════════════════════════════════
#  Setup detectors
# ══════════════════════════════════════════════════════════════════


def detect_first_pullback(
    symbol: str,
    bars: list[Bar],
    *,
    avg_range: Optional[float] = None,
    vwap: Optional[float] = None,
    ema9: Optional[float] = None,
    day_high: Optional[float] = None,
    state: Optional[MoveState] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    data_confidence: float = 1.0,
) -> Optional[EntrySignal]:
    """Detect first-pullback setup (SPEC §10.4).

    1. Strong move exists.
    2. First observed pullback since new session high.
    3. Pullback retraces ≥20% of up-leg or ≥1·avg_range.
    4. Pullback spans 2–8 bars.
    5. Controlled selling.
    6. Pullback low holds a logical level.
    7. Reclaim candle closes green above prior candle high, vol >1.2x pb avg.
    """
    if len(bars) < 5:
        return None

    ar = avg_range if avg_range is not None else avg_bar_range(bars)

    # 1. Strong move
    found, surge_start, surge_end = _is_strong_move(bars, ar)
    if not found:
        return None

    surge_bars = bars[surge_start : surge_end + 1]
    surge_avg_vol = avg_volume(surge_bars)
    up_leg = max(b.high for b in surge_bars) - min(b.low for b in surge_bars[:3])

    # 2-4. Find pullback after surge
    pb_bars = bars[surge_end + 1:]
    if len(pb_bars) < 2 or len(pb_bars) > 8:
        return None

    pb_low = min(b.low for b in pb_bars)
    pb_high = max(b.high for b in surge_bars)
    retrace = (pb_high - pb_low)
    min_retrace = max(up_leg * 0.20, ar)

    if retrace < min_retrace:
        return None

    # 5. Controlled selling
    if not _is_controlled_selling(pb_bars, surge_avg_vol):
        return None

    # 6. Pullback low holds a logical level
    logical_levels = [lvl for lvl in (vwap, ema9, day_high) if lvl is not None]
    if logical_levels and not any(_near_level(pb_low, lvl) for lvl in logical_levels):
        return None

    # 7. Reclaim — need at least 1 bar after pullback
    # In practice, this would be the CURRENT bar; here we simulate with the last bar
    # (len(pb_bars) >= 2 is already enforced above, which guarantees bars[-2] exists)
    reclaim = bars[-1]
    prev = bars[-2]
    pb_avg_vol = avg_volume(pb_bars)

    if not reclaim.is_green:
        return None
    if reclaim.close <= prev.high:
        return None
    if pb_avg_vol > 0 and reclaim.volume <= pb_avg_vol * 1.2:
        return None

    entry_price = round(reclaim.close + _TICK, 2)
    stop_price = round(pb_low - _TICK, 2)
    target = round(entry_price + 2 * (entry_price - stop_price), 2)

    evidence = [
        f"surge={surge_end - surge_start + 1}bars",
        f"pullback={len(pb_bars)}bars",
        f"retrace={round(retrace, 2)}",
        f"ar={round(ar, 2)}",
    ]

    return _build_signal(
        symbol, EntrySetupType.FIRST_PULLBACK,
        entry_price, stop_price, target,
        "price trades below pullback low before fill",
        state=state, state_evidence=evidence,
        quote_age_seconds=quote_age_seconds, spread_pct=spread_pct,
        data_confidence=data_confidence,
    )


def detect_micro_pullback(
    symbol: str,
    bars: list[Bar],
    *,
    avg_range: Optional[float] = None,
    vwap: Optional[float] = None,
    state: Optional[MoveState] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    data_confidence: float = 1.0,
) -> Optional[EntrySignal]:
    """Detect micro-pullback setup (SPEC §10.5).

    1. State must be active.
    2. Price advanced ≥1.5·avg_range over last 3–5 bars.
    3. 1–3 red/doji candles pause after surge peak.
    4. Dip candles have lower volume than surge avg.
    5. Dip doesn't break VWAP or nearest logical support.
    6. Green reclaim candle closes above surge peak, vol ≥1.5x dip avg.
    """
    if state != MoveState.ACTIVE:
        return None
    if len(bars) < 6:
        return None

    ar = avg_range if avg_range is not None else avg_bar_range(bars)

    # 2. Price advanced ≥1.5·ar over last 3-5 bars
    surge_window = bars[-6:-1]  # bars before last
    if len(surge_window) < 3:
        return None
    surge_start_price = surge_window[0].close
    surge_peak = max(b.high for b in surge_window)
    advance = surge_peak - surge_start_price
    if advance < 1.5 * ar:
        return None

    surge_avg_vol = avg_volume(surge_window)

    # 3. 1-3 red/doji candles after peak (in surge_window tail)
    dip_bars = [b for b in surge_window[-3:] if b.is_red or abs(b.close - b.open) < ar * 0.3]
    if not dip_bars or len(dip_bars) > 3:
        return None

    # 4. Dip volume < surge avg
    dip_avg_vol = avg_volume(dip_bars)
    if dip_avg_vol >= surge_avg_vol:
        return None

    # 5. Dip doesn't break VWAP
    dip_low = min(b.low for b in dip_bars)
    if vwap is not None and dip_low < vwap:
        return None

    # 6. Reclaim candle
    reclaim = bars[-1]
    if not reclaim.is_green:
        return None
    if reclaim.close <= surge_peak:
        return None
    if dip_avg_vol > 0 and reclaim.volume < dip_avg_vol * 1.5:
        return None

    entry_price = round(reclaim.close + _TICK, 2)
    stop_price = round(dip_low - _TICK, 2)
    target = round(entry_price + 2 * (entry_price - stop_price), 2)

    return _build_signal(
        symbol, EntrySetupType.MICRO_PULLBACK,
        entry_price, stop_price, target,
        "dip low breaks or spread enters hard zone",
        state=state, state_evidence=[f"ar={round(ar,2)}", f"advance={round(advance,2)}"],
        quote_age_seconds=quote_age_seconds, spread_pct=spread_pct,
        data_confidence=data_confidence,
    )


def detect_hod_reclaim(
    symbol: str,
    bars: list[Bar],
    *,
    prior_hod: Optional[float] = None,
    state: Optional[MoveState] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    data_confidence: float = 1.0,
) -> Optional[EntrySignal]:
    """Detect HOD reclaim setup (SPEC §10.6).

    1. Prior HOD is established.
    2. Price pulls back and holds above logical stop level.
    3. Price closes back above prior HOD.
    4. Reclaim candle volume ≥ avg of prior 10 bars or >1.2x pullback avg.
    """
    if prior_hod is None or prior_hod <= 0:
        return None
    if len(bars) < 5:
        return None

    # Find pullback: bars where price was below prior_hod after being above
    reclaim = bars[-1]
    if reclaim.close <= prior_hod:
        return None

    # Find the pullback region (bars below HOD before reclaim)
    pb_bars = []
    for b in reversed(bars[:-1]):
        if b.close < prior_hod:
            pb_bars.append(b)
        else:
            break
    pb_bars.reverse()

    if not pb_bars:
        return None

    # 2. Price holds above logical stop level — assume pb low is the stop area
    pb_low = min(b.low for b in pb_bars)

    # 4. Volume check
    prior_10 = bars[-11:-1] if len(bars) >= 11 else bars[:-1]
    prior_10_avg = avg_volume(prior_10)
    pb_avg_vol = avg_volume(pb_bars)

    vol_ok = reclaim.volume >= prior_10_avg
    if pb_avg_vol > 0 and reclaim.volume >= pb_avg_vol * 1.2:
        vol_ok = True
    if not vol_ok:
        return None

    entry_price = round(reclaim.close + _TICK, 2)
    stop_price = round(min(pb_low, reclaim.low) - _TICK, 2)
    target = round(entry_price + 2 * (entry_price - stop_price), 2)

    return _build_signal(
        symbol, EntrySetupType.HOD_RECLAIM,
        entry_price, stop_price, target,
        "next bar closes below reclaimed HOD",
        state=state, state_evidence=[f"prior_hod={prior_hod}", f"pb_bars={len(pb_bars)}"],
        quote_age_seconds=quote_age_seconds, spread_pct=spread_pct,
        data_confidence=data_confidence,
    )


def detect_consolidation_breakout(
    symbol: str,
    bars: list[Bar],
    *,
    day_high: Optional[float] = None,
    state: Optional[MoveState] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    data_confidence: float = 1.0,
) -> Optional[EntrySignal]:
    """Detect consolidation-breakout setup (SPEC §10.7).

    1. Range lasts 5–20 bars.
    2. Range high-low ≤2% of price.
    3. Range within 3% of HOD.
    4. Volume not dead: recent vol ≥50% of prior 10-bar avg.
    5. Breakout candle closes above range high.
    """
    if len(bars) < 6:
        return None

    # 1. Look for a tight range in bars[-6:-1] (5+ bars)
    range_bars = bars[-6:-1]  # exclude current bar
    if len(range_bars) < 5 or len(range_bars) > 20:
        return None

    range_high = max(b.high for b in range_bars)
    range_low = min(b.low for b in range_bars)
    mid_price = (range_high + range_low) / 2
    if mid_price <= 0:
        return None

    # 2. Range ≤2% of price
    range_pct = (range_high - range_low) / mid_price * 100
    if range_pct > 2.0:
        return None

    # 3. Range within 3% of HOD
    if day_high is not None and day_high > 0:
        dist_from_hod = (day_high - range_high) / day_high * 100
        if dist_from_hod > 3.0:
            return None

    # 4. Volume not dead
    range_avg_vol = avg_volume(range_bars)
    if len(bars) >= 17:
        prior_10 = bars[-17:-7]
    else:
        prior_start = max(0, len(bars) - len(range_bars) - 10)
        prior_10 = bars[prior_start : -len(range_bars) - 1]
    prior_10_avg = avg_volume(prior_10) if prior_10 else range_avg_vol
    if prior_10_avg > 0 and range_avg_vol < prior_10_avg * 0.5:
        return None

    # 5. Breakout
    breakout = bars[-1]
    if breakout.close <= range_high:
        return None

    entry_price = round(range_high + _TICK, 2)
    stop_price = round(range_low - _TICK, 2)
    target = round(entry_price + 2 * (entry_price - stop_price), 2)

    return _build_signal(
        symbol, EntrySetupType.CONSOLIDATION_BREAKOUT,
        entry_price, stop_price, target,
        "breakout closes back inside range immediately",
        state=state, state_evidence=[f"range_pct={round(range_pct,2)}", f"range_bars={len(range_bars)}"],
        quote_age_seconds=quote_age_seconds, spread_pct=spread_pct,
        data_confidence=data_confidence,
    )


def detect_vwap_reclaim(
    symbol: str,
    bars: list[Bar],
    *,
    vwap: Optional[float] = None,
    state: Optional[MoveState] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    data_confidence: float = 1.0,
) -> Optional[EntrySignal]:
    """Detect VWAP reclaim/bounce setup (SPEC §10.8).

    - reclaim/bounce candle closes above VWAP
    - stop can be placed below VWAP or reclaim low
    - volume not dead
    - spread acceptable
    """
    if vwap is None or vwap <= 0:
        return None
    if len(bars) < 3:
        return None

    reclaim = bars[-1]
    if reclaim.close <= vwap:
        return None

    # Check previous bars were below/near VWAP
    prior_bars = bars[-4:-1] if len(bars) >= 4 else bars[:-1]
    below_or_near = any(b.close <= vwap or _near_level(b.low, vwap) for b in prior_bars)
    if not below_or_near:
        return None

    # Volume not dead
    vol_ok = reclaim.volume > 0
    if len(bars) >= 6:
        recent_avg = avg_volume(bars[-6:-1])
        vol_ok = recent_avg <= 0 or reclaim.volume >= recent_avg * 0.5
    if not vol_ok:
        return None

    # Spread acceptable (relaxed — hard filter handles wide spread)
    if spread_pct is not None and spread_pct > 5.0:
        return None

    reclaim_low = min(b.low for b in bars[-3:])
    entry_price = round(reclaim.close + _TICK, 2)
    stop_price = round(min(vwap, reclaim_low) - _TICK, 2)
    target = round(entry_price + 2 * (entry_price - stop_price), 2)

    return _build_signal(
        symbol, EntrySetupType.VWAP_RECLAIM,
        entry_price, stop_price, target,
        "next bar closes below VWAP and cannot reclaim",
        state=state, state_evidence=[f"vwap={vwap}", f"reclaim_close={reclaim.close}"],
        quote_age_seconds=quote_age_seconds, spread_pct=spread_pct,
        data_confidence=data_confidence,
    )


def detect_scalp_reclaim(
    symbol: str,
    bars: list[Bar],
    *,
    avg_range: Optional[float] = None,
    state: Optional[MoveState] = None,
    spread_pct: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    data_confidence: float = 1.0,
) -> Optional[EntrySignal]:
    """Detect scalp-reclaim setup (SPEC §10.9).

    Only allowed when state is extended or halt-risk.
    - spread ≤3%
    - quote age ≤5s
    - 1-2 candle micro dip forms
    - immediate reclaim
    - stop width ≤1% of price or ≤1·avg_range (whichever smaller)
    """
    if state not in (MoveState.EXTENDED, MoveState.HALT_RISK):
        return None
    if spread_pct is not None and spread_pct > 3.0:
        return None
    if quote_age_seconds is not None and quote_age_seconds > 5.0:
        return None
    if len(bars) < 4:
        return None

    ar = avg_range if avg_range is not None else avg_bar_range(bars)

    # Find 1-2 candle micro dip
    dip_bars = []
    for b in reversed(bars[-4:-1]):
        if b.is_red:
            dip_bars.insert(0, b)
        else:
            break
    if len(dip_bars) < 1 or len(dip_bars) > 2:
        return None

    dip_low = min(b.low for b in dip_bars)
    dip_high = max(b.high for b in dip_bars)

    # Immediate reclaim
    reclaim = bars[-1]
    if not reclaim.is_green:
        return None
    if reclaim.close <= dip_high:
        return None

    # Stop width ≤1% of price or ≤1·ar
    entry = round(reclaim.close + _TICK, 2)
    stop = round(dip_low - _TICK, 2)
    risk = entry - stop
    max_risk = min(entry * 0.01, ar)
    if risk > max_risk:
        return None

    target = round(entry + risk * 0.5, 2)  # 0.5R scalp

    return _build_signal(
        symbol, EntrySetupType.SCALP_RECLAIM,
        entry, stop, target,
        "first stall/red candle after entry if target not hit",
        state=state, state_evidence=[f"dip_bars={len(dip_bars)}", f"ar={round(ar,2)}"],
        quote_age_seconds=quote_age_seconds, spread_pct=spread_pct,
        data_confidence=data_confidence,
    )


# ══════════════════════════════════════════════════════════════════
#  Orchestrator
# ══════════════════════════════════════════════════════════════════

# Extra kwargs per detector (keys accepted beyond the common base set).
# Each detector also accepts: symbol, bars, state, quote_age_seconds,
# spread_pct, data_confidence.
_DETECTOR_EXTRA_KWARGS: dict[EntrySetupType, dict[str, str]] = {
    EntrySetupType.FIRST_PULLBACK:        {"avg_range": "ar", "vwap": "vwap", "ema9": "ema9", "day_high": "day_high"},
    EntrySetupType.MICRO_PULLBACK:        {"avg_range": "ar", "vwap": "vwap"},
    EntrySetupType.HOD_RECLAIM:           {"prior_hod": "prior_hod"},
    EntrySetupType.CONSOLIDATION_BREAKOUT: {"day_high": "day_high"},
    EntrySetupType.VWAP_RECLAIM:           {"vwap": "vwap"},
    EntrySetupType.SCALP_RECLAIM:         {"avg_range": "ar"},
}


def find_entry(
    candidate: Candidate,
    bars: list[Bar],
    *,
    state: Optional[MoveState] = None,
    # Enrichment context
    vwap: Optional[float] = None,
    ema9: Optional[float] = None,
    day_high: Optional[float] = None,
    prior_hod: Optional[float] = None,
    avg_range: Optional[float] = None,
    spread_pct: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    data_confidence: float = 1.0,
    # Permission gating
    allowed_setups: Optional[set[str]] = None,
) -> Optional[EntrySignal]:
    """Find the best valid entry signal for a candidate.

    Evaluates setups in priority order.  Returns the first signal
    whose setup is permitted (via ``allowed_setups`` or by default
    per SPEC §9.4 permission matrix).

    Parameters
    ----------
    candidate : Candidate
    bars : list[Bar]
        Recent 1-minute bars (most recent last).
    state : MoveState or None
        Current move state from Phase 4 classifier.
    allowed_setups : set[str] or None
        If provided, only setups whose string value is in this set
        are considered.  ``None`` means all setups are eligible.
    All other keyword args are enrichment context.

    Returns
    -------
    EntrySignal or None
    """
    symbol = candidate.symbol
    ar = avg_range if avg_range is not None else avg_bar_range(bars)

    # Build a local variable lookup for _DETECTOR_EXTRA_KWARGS
    _locals = {
        "ar": ar,
        "vwap": vwap,
        "ema9": ema9,
        "day_high": day_high,
        "prior_hod": prior_hod,
    }

    detectors = [
        (EntrySetupType.FIRST_PULLBACK, detect_first_pullback),
        (EntrySetupType.HOD_RECLAIM, detect_hod_reclaim),
        (EntrySetupType.CONSOLIDATION_BREAKOUT, detect_consolidation_breakout),
        (EntrySetupType.MICRO_PULLBACK, detect_micro_pullback),
        (EntrySetupType.VWAP_RECLAIM, detect_vwap_reclaim),
        (EntrySetupType.SCALP_RECLAIM, detect_scalp_reclaim),
    ]

    for setup_type, detector in detectors:
        if allowed_setups is not None and setup_type.value not in allowed_setups:
            continue

        # Build kwargs: always include base params, add extras by setup type
        kwargs: dict[str, object] = {
            "symbol": symbol,
            "bars": bars,
            "state": state,
            "quote_age_seconds": quote_age_seconds,
            "spread_pct": spread_pct,
            "data_confidence": data_confidence,
        }
        for extra_key, local_name in _DETECTOR_EXTRA_KWARGS.get(setup_type, {}).items():
            kwargs[extra_key] = _locals[local_name]

        try:
            signal = detector(**kwargs)
        except Exception:
            from loguru import logger
            logger.exception(
                "Entry detector '%s' failed for %s — skipping to next detector",
                setup_type.value, symbol,
            )
            continue
        if signal is not None:
            return signal

    return None
