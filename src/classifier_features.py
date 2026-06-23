"""Runtime-derived classifier features from real bars (SPEC §9).

Pure helper layer: turns a list of ``Bar`` objects + live context
(``price``, ``vwap``, ``day_high``) into a ``ClassifierFeatures``
record that ``classify_move_state()`` consumes.

No external libraries — stdlib ``dataclass`` only.  All features are
deterministic and defensive: missing context (``None`` vwap/day_high)
or short bar windows degrade gracefully to defaults rather than raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.entries import Bar


@dataclass
class ClassifierFeatures:
    """Bar-derived feature bundle consumed by ``classify_move_state()``."""

    avg_range: Optional[float] = None
    lower_highs_count: int = 0
    consecutive_below_vwap: int = 0
    higher_low_structure: bool = False
    strong_volume: bool = False
    volume_fading: bool = False
    bounces_failing: bool = False
    pullbacks_bought: bool = False
    vertical_move: bool = False
    vertical_without_pullback: bool = False
    price_moved_pct_5m: Optional[float] = None
    pullback_low: Optional[float] = None
    nearest_stop_distance_pct: Optional[float] = None
    failed_hod_reclaim: bool = False
    failed_vwap_reclaim: bool = False
    hod_behavior_repeated: bool = False
    has_pullback_formed: bool = False


def derive_classifier_features(
    bars: list[Bar],
    *,
    price: Optional[float] = None,
    vwap: Optional[float] = None,
    day_high: Optional[float] = None,
) -> ClassifierFeatures:
    """Derive runtime classifier features from a bar window.

    Parameters
    ----------
    bars:
        Recent OHLCV bars (typically last 20-30 1-minute bars).  Order
        is oldest → newest (``bars[-1]`` is the most recent).
    price:
        Current/live price.  Falls back to ``bars[-1].close`` if None.
    vwap:
        Session VWAP.  When None, VWAP-dependent features degrade to
        defaults (safeguard: never manufacture BACKSIDE on missing VWAP).
    day_high:
        Intraday high.  When None, HOD-reclaim features stay False.

    Returns
    -------
    ClassifierFeatures
        All fields populated with deterministic values; missing-context
        fields remain at their defaults.
    """
    features = ClassifierFeatures()

    if not bars:
        return features

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    n = len(bars)
    last_close = closes[-1]
    live_price = price if price is not None and price > 0 else last_close

    # ── 1. Average bar range ───────────────────────────────────────
    features.avg_range = sum(b.high - b.low for b in bars) / n

    # ── 2. Lower-highs count (consecutive from most recent) ────────
    lower_highs = 0
    for i in range(n - 1, 0, -1):
        if highs[i] < highs[i - 1]:
            lower_highs += 1
        else:
            break
    features.lower_highs_count = lower_highs

    # ── 3. Consecutive closes below VWAP (from most recent) ────────
    if vwap is not None:
        below = 0
        for c in reversed(closes):
            if c < vwap:
                below += 1
            else:
                break
        features.consecutive_below_vwap = below

    # ── 4. 5-minute price move (%) ─────────────────────────────────
    if n >= 2:
        anchor_idx = max(0, n - 5)
        anchor = closes[anchor_idx]
        if anchor > 0:
            features.price_moved_pct_5m = round(
                (last_close - anchor) / anchor * 100, 4
            )

    # ── 5. Pullback low + has_pullback_formed ──────────────────────
    window = lows[-5:] if n >= 5 else lows
    features.pullback_low = min(window)
    features.has_pullback_formed = (
        n >= 3 and min(lows[-3:]) < last_close
    )

    # ── 6. Higher-low structure ────────────────────────────────────
    features.higher_low_structure = (
        n >= 3 and lows[-1] > lows[-2] > lows[-3]
    )

    # ── 7. Volume features ─────────────────────────────────────────
    features.strong_volume = (
        n >= 2 and volumes[-1] > max(volumes[:-1])
    )
    features.volume_fading = (
        n >= 3 and volumes[-1] < volumes[-2] < volumes[-3]
    )

    # ── 8. Pullbacks bought (close up + higher low) ────────────────
    features.pullbacks_bought = (
        n >= 2 and closes[-1] > closes[-2] and lows[-1] >= lows[-2]
    )

    # ── 9. Vertical move ───────────────────────────────────────────
    # ≥10% in 5 bars OR last bar range > 2× avg_range and close in
    # upper third of the bar (strong directional thrust).
    moved_pct = features.price_moved_pct_5m or 0.0
    last_bar = bars[-1]
    last_range = last_bar.high - last_bar.low
    close_in_upper_third = (
        last_range > 0
        and last_close >= last_bar.low + (2 / 3) * last_range
    )
    features.vertical_move = (
        moved_pct >= 10.0
        or (
            features.avg_range > 0
            and last_range > 2 * features.avg_range
            and close_in_upper_third
        )
    )
    features.vertical_without_pullback = (
        features.vertical_move and not features.has_pullback_formed
    )

    # ── 10. Nearest stop distance (%) ──────────────────────────────
    # Distance from live price to pullback low (nearest valid stop).
    if (
        features.pullback_low is not None
        and features.pullback_low > 0
        and live_price > 0
    ):
        features.nearest_stop_distance_pct = round(
            (live_price - features.pullback_low) / live_price * 100, 4
        )

    # ── 11. Failed HOD reclaim ─────────────────────────────────────
    # Price approached day_high (within 0.5% over last 5 bars) then
    # last close fell back below that approach high.
    if day_high is not None and day_high > 0 and n >= 2:
        recent_highs = highs[-5:] if n >= 5 else highs
        approach_threshold = day_high * 0.995  # within 0.5% of HOD
        approached = any(h >= approach_threshold for h in recent_highs)
        features.failed_hod_reclaim = (
            approached and last_close < max(recent_highs) * 0.99
        )

    # ── 12. Failed VWAP reclaim ────────────────────────────────────
    # Price approached vwap from below then closed back below it.
    if vwap is not None and n >= 2:
        recent_closes = closes[-5:] if n >= 5 else closes
        approached_vwap = any(
            c >= vwap * 0.999 for c in recent_closes
        )
        features.failed_vwap_reclaim = (
            approached_vwap and last_close < vwap
        )

    # ── 13. HOD behavior repeated ──────────────────────────────────
    # Multiple failed HOD approaches (≥2 lower highs while near HOD).
    features.hod_behavior_repeated = (
        day_high is not None
        and day_high > 0
        and features.lower_highs_count >= 2
        and any(h >= day_high * 0.995 for h in highs[-5:])
    )

    # ── 14. Bounces failing ────────────────────────────────────────
    # Price bounced (higher low) but closed lower → bounce not
    # sustained.  Combined with volume_fading this is a BACKSIDE
    # signal in the classifier.
    features.bounces_failing = (
        n >= 3
        and features.higher_low_structure
        and closes[-1] < closes[-2]
    )

    return features
