"""
Phase 2 — Data-confidence calculator per SPEC section 4.4.

Computes a 0.0–1.0 confidence value on every enriched candidate,
penalising missing, stale, or incomplete data.  Confidence does **not**
hard-reject — it is a downstream multiplier and logging signal.

Algorithm (SPEC §4.4)::

    start at 1.0
    -0.10  if scanner timestamp is unknown
    -0.20  if scanner data is older than 20 minutes
    -0.20  if recent bars are stale or missing
    -0.05  for each missing optional metadata field
           (float_shares, market_cap, sector, industry, country)
    -0.05  for each missing optional premarket field
           (premarket_high, premarket_low, premarket_gap_pct)
    floor at 0.3 if execution-critical quote/price fields are present
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.models.schemas import Candidate

# Fields whose absence reduces confidence by 0.05 each.
_OPTIONAL_META_FIELDS = ("float_shares", "market_cap", "sector", "industry", "country")
_OPTIONAL_PREMARKET_FIELDS = ("premarket_high", "premarket_low", "premarket_gap_pct")

# Confidence floor when execution-critical fields (price) are present.
_CONFIDENCE_FLOOR_WITH_CRITICAL = 0.3


def calculate_data_confidence(
    candidate: Candidate,
    *,
    now: Optional[datetime] = None,
    bars_available: bool = False,
    bars_timestamp: Optional[datetime] = None,
    max_bar_age_seconds: int = 300,
) -> float:
    """Compute data-confidence for a single candidate.

    Parameters
    ----------
    candidate : Candidate
        The (possibly enriched) candidate to evaluate.
    now : datetime or None
        Reference time for staleness checks.  Defaults to ``utcnow()``.
    bars_available : bool
        Whether recent bars were successfully fetched.
    bars_timestamp : datetime or None
        Timestamp of the most recent bar received.
    max_bar_age_seconds : int
        Bars older than this are considered stale (default 300 s / 5 min).

    Returns
    -------
    float
        Confidence in [0.0, 1.0], rounded to two decimal places.
    """
    confidence: float = 1.0

    if now is None:
        now = datetime.now(timezone.utc)

    # ── scanner staleness ──────────────────────────────────────────
    if candidate.source_timestamp is None:
        confidence -= 0.10
    else:
        age_seconds = (now - candidate.source_timestamp).total_seconds()
        if age_seconds > 1200:  # 20 minutes
            confidence -= 0.20

    # ── bar staleness ──────────────────────────────────────────────
    if not bars_available or bars_timestamp is None:
        confidence -= 0.20
    else:
        bar_age = (now - bars_timestamp).total_seconds()
        if bar_age > max_bar_age_seconds:
            confidence -= 0.20

    # ── missing optional metadata ──────────────────────────────────
    for field_name in _OPTIONAL_META_FIELDS:
        if getattr(candidate, field_name, None) is None:
            confidence -= 0.05

    for field_name in _OPTIONAL_PREMARKET_FIELDS:
        if getattr(candidate, field_name, None) is None:
            confidence -= 0.05

    # ── floor ──────────────────────────────────────────────────────
    has_critical = candidate.price is not None
    if has_critical and confidence < _CONFIDENCE_FLOOR_WITH_CRITICAL:
        confidence = _CONFIDENCE_FLOOR_WITH_CRITICAL

    # Clamp to valid range.
    confidence = max(0.0, min(1.0, confidence))
    return round(confidence, 2)


def compute_scanner_age_seconds(candidate: Candidate, now: Optional[datetime] = None) -> Optional[float]:
    """Return the age of the scanner snapshot in seconds, or *None* if unknown."""
    if candidate.source_timestamp is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    return round((now - candidate.source_timestamp).total_seconds(), 1)
