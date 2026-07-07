"""
Runner capture module — promotes OPEN positions to RUNNER and computes
ATR Chandelier trailing stops.

SPEC §11.4 — Phase 3 runner capture.

Runner transition criteria (all must be true):
  - Position state is OPEN
  - Unrealized profit >= +1.5R (R = original entry risk_per_share)
  - >= 2 distinct higher lows in recent bar window
  - Latest push bar volume >= 1.5x average of prior 10 bars
  - Price >= VWAP (for longs)
  - Move state is ACTIVE (not EXTENDED, BACKSIDE, or HALT_RISK)

Trailing stop: ATR Chandelier (2.5x ATR(5) on 5-min bars), ratcheted
from highest_price_seen. Minimum distance = max(2.5x ATR, 1.0x original_risk)
to prevent too-tight trails on small-caps.
"""

from __future__ import annotations

from typing import Optional

from src.entries import Bar, find_entry
from src.models.schemas import (
    Candidate,
    EntrySignal,
    MoveState,
    PositionState,
    PositionStateModel,
)


# ──────────────────────────────────────────────────────────────────
#  ATR computation (Wilder's smoothing)
# ──────────────────────────────────────────────────────────────────


def compute_atr(bars: list[Bar], period: int = 5) -> Optional[float]:
    """Compute ATR using Wilder's smoothing method.

    TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    First ATR = SMA of first ``period`` TRs.
    Subsequent ATR = (prev_ATR * (n-1) + TR) / n.

    Returns None if insufficient bars (need at least period+1).
    """
    if len(bars) < period + 1:
        return None

    trs: list[float] = []
    for i in range(1, len(bars)):
        high = bars[i].high
        low = bars[i].low
        prev_close = bars[i - 1].close
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)

    if len(trs) < period:
        return None

    # First ATR = SMA of first `period` TRs
    atr = sum(trs[:period]) / period

    # Wilder's smoothing for the rest
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period

    return round(atr, 6)


# ──────────────────────────────────────────────────────────────────
#  Runner promotion
# ──────────────────────────────────────────────────────────────────


def should_promote_to_runner(
    pos: PositionStateModel,
    *,
    bars: Optional[list[Bar]] = None,
    current_price: Optional[float] = None,
    vwap: Optional[float] = None,
    move_state: Optional[MoveState] = None,
    activation_r_multiple: float = 1.5,
    higher_lows_required: int = 2,
    volume_confirm_multiplier: float = 1.5,
) -> bool:
    """Check if an OPEN position should be promoted to RUNNER.

    All criteria must be true:
      1. State is OPEN
      2. Unrealized profit >= activation_r_multiple * R
      3. >= higher_lows_required distinct higher lows in recent bars
      4. Latest push bar volume >= volume_confirm_multiplier * avg(prior 10)
      5. Price >= VWAP (if VWAP available)
      6. Move state is ACTIVE
    """
    if pos.state != PositionState.OPEN:
        return False
    if current_price is None or current_price <= 0:
        return False
    if pos.entry_price is None or pos.stop_price is None:
        return False
    if move_state is not None and move_state != MoveState.ACTIVE:
        return False
    if move_state is None:
        return False

    # R = original entry risk per share
    rps = pos.entry_price - pos.stop_price
    if rps <= 0:
        return False

    # Criterion 2: profit >= activation_r_multiple * R
    pnl_per_share = current_price - pos.entry_price
    pnl_r = pnl_per_share / rps
    if pnl_r < activation_r_multiple:
        return False

    # Criterion 3: higher lows
    if bars is None or len(bars) < 4:
        return False
    higher_lows = _count_higher_lows(bars)
    if higher_lows < higher_lows_required:
        return False

    # Criterion 4: volume confirmation on latest push
    if not _volume_confirms(bars, volume_confirm_multiplier):
        return False

    # Criterion 5: price >= VWAP
    if vwap is not None and current_price < vwap:
        return False

    return True


def _count_higher_lows(bars: list[Bar]) -> int:
    """Count distinct higher lows in the bar window."""
    if len(bars) < 3:
        return 0
    count = 0
    prev_low = bars[0].low
    for i in range(1, len(bars)):
        if bars[i].low > prev_low:
            count += 1
        prev_low = bars[i].low
    return count


def _volume_confirms(bars: list[Bar], multiplier: float) -> bool:
    """Check if latest bar volume >= multiplier * avg(prior 10 bars)."""
    if len(bars) < 2:
        return False
    lookback = min(10, len(bars) - 1)
    prior_vols = [b.volume for b in bars[-(lookback + 1):-1]]
    if not prior_vols or sum(prior_vols) == 0:
        return False
    avg_vol = sum(prior_vols) / len(prior_vols)
    if avg_vol <= 0:
        return False
    return bars[-1].volume >= multiplier * avg_vol


# ──────────────────────────────────────────────────────────────────
#  Trailing stop computation (ATR Chandelier with ratchet)
# ──────────────────────────────────────────────────────────────────


def compute_runner_stop(
    highest_price_seen: Optional[float],
    atr: Optional[float],
    *,
    multiplier: float = 2.5,
    current_stop: Optional[float] = None,
    original_risk: Optional[float] = None,
) -> Optional[float]:
    """Compute ATR Chandelier trailing stop with ratchet.

    - Trail distance = max(multiplier * ATR, original_risk)
      (minimum distance prevents too-tight trails on small-caps)
    - new_stop = highest_price_seen - trail_distance
    - Ratchet: stop never moves down (returns max(new_stop, current_stop))

    Returns None if required inputs are missing.
    """
    if highest_price_seen is None or highest_price_seen <= 0:
        return current_stop
    if atr is None or atr <= 0:
        return current_stop

    trail_distance = multiplier * atr
    if original_risk is not None and original_risk > 0:
        trail_distance = max(trail_distance, original_risk)

    new_stop = highest_price_seen - trail_distance

    # Ratchet: stop never moves down
    if current_stop is not None and current_stop > 0:
        return round(max(new_stop, current_stop), 4)
    return round(new_stop, 4)


# ──────────────────────────────────────────────────────────────────
#  Scaling-in add detection (Phase 4)
# ──────────────────────────────────────────────────────────────────


def should_add_to_runner(
    pos: PositionStateModel,
    bars: list[Bar],
    current_price: float,
    vwap: Optional[float],
    move_state: Optional[MoveState],
    activation_r_multiple: float = 2.0,
    max_adds: int = 2,
    risk_per_share: Optional[float] = None,
) -> Optional[EntrySignal]:
    """Check if a RUNNER position qualifies for a scaling-in add.

    Criteria:
      1. State is RUNNER
      2. ``add_count < max_adds``
      3. ``current_price >= entry + activation_r_multiple * original_risk``
      4. Move state is not explicitly non‑ACTIVE (None / ACTIVE passes)

    Returns an ``EntrySignal`` from ``first_pullback`` or ``vwap_reclaim``
    setup detection, or ``None``.
    """
    if pos.state != PositionState.RUNNER:
        return None
    if pos.add_count >= max_adds:
        return None
    if pos.entry_price is None or pos.stop_price is None:
        return None
    if move_state != MoveState.ACTIVE:
        return None

    original_risk = risk_per_share if risk_per_share is not None else pos.entry_price - pos.stop_price
    if original_risk <= 0:
        return None

    # Criterion 3: profit >= activation_r_multiple * R
    pnl_per_share = current_price - pos.entry_price
    pnl_r = pnl_per_share / original_risk
    if pnl_r < activation_r_multiple:
        return None

    # Use find_entry with only pullback/reclaim setups
    candidate = Candidate(symbol=pos.symbol, price=current_price)
    signal = find_entry(
        candidate,
        bars,
        state=move_state,
        vwap=vwap,
        allowed_setups={"first_pullback", "vwap_reclaim"},
    )
    return signal
