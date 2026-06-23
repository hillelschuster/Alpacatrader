"""
Phase 7 — Soft annotation mapping per SPEC §8.

Moved from ``src/scanner/attention.py`` (T7.2) to keep attention scoring
and soft annotations separate concerns.

Provides:
  - ``map_soft_warnings()`` — label a candidate with soft warnings
  - ``soft_warning_multiplier()`` — compute sizing multiplier from warnings
"""

from __future__ import annotations

from typing import Optional


# ──────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────

# Sector/industry substrings that trigger speculative/biotech flags.
_BIOTECH_KEYWORDS = ("biotech", "biotechnology", "pharmaceutical", "pharma")
_SPECULATIVE_INDUSTRIES = ("cannabis", "crypto", "blockchain", "nft")


# ──────────────────────────────────────────────────────────────────
#  Warning mapping
# ──────────────────────────────────────────────────────────────────


def map_soft_warnings(
    candidate: "Candidate",
    *,
    price_range_min: Optional[float] = None,
    price_range_max: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    parabolic: bool = False,
    below_vwap: bool = False,
    below_ema: bool = False,
    is_lunch: bool = False,
    halt_history_today: bool = False,
    float_rotation: Optional[float] = None,
    data_confidence: Optional[float] = None,
    has_news: Optional[bool] = None,
    has_catalyst: Optional[bool] = None,
) -> list[str]:
    """Map a candidate to soft-annotation labels per SPEC §8.

    These are *warnings*, not hard rejects.  They inform sizing, mode,
    and entry requirements but do not block the candidate by themselves.

    Parameters
    ----------
    candidate : Candidate
    price_range_min, price_range_max : float or None
        Focus price range boundaries.
    quote_age_seconds : float or None
        Age of the current quote; >5 s → stale warning.
    spread_pct : float or None
        Current spread percentage.
    parabolic : bool
        Whether the price action looks parabolic.
    below_vwap : bool
        Whether price is below VWAP.
    below_ema : bool
        Whether price is below 9-EMA.
    is_lunch : bool
        Whether the session is in the lunch window.
    halt_history_today : bool
        Whether the symbol was halted earlier today.
    float_rotation : float or None
        Float rotation ratio.
    data_confidence : float or None
        Current data_confidence value.

    Returns
    -------
    list[str]
        Warning labels in priority order (strongest first).
    """
    warnings: list[str] = []

    # ── Demographics (scanner-available) ──────────────────
    if candidate.country and candidate.country.upper() in ("CHINA", "CN"):
        warnings.append("chinese_adr")

    # ── Sector / industry ─────────────────────────────────
    sector_lower = (candidate.sector or "").lower()
    industry_lower = (candidate.industry or "").lower()
    combined = f"{sector_lower} {industry_lower}"

    for kw in _BIOTECH_KEYWORDS:
        if kw in combined:
            warnings.append("biotech")
            break

    for kw in _SPECULATIVE_INDUSTRIES:
        if kw in combined:
            warnings.append("speculative")
            break

    # ── Price-based warnings ──────────────────────────────
    if candidate.price is not None:
        if candidate.price < 2.0:
            warnings.append("price_below_2")
        if price_range_min is not None and candidate.price < price_range_min:
            warnings.append("outside_focus_price_range_low")
        if price_range_max is not None and candidate.price > price_range_max:
            warnings.append("outside_focus_price_range_high")

    # ── Float-based ───────────────────────────────────────
    if candidate.float_shares is None:
        warnings.append("float_unknown")
    elif candidate.float_shares < 1_000_000:
        warnings.append("very_low_float")
    elif candidate.float_shares < 5_000_000:
        warnings.append("low_float")

    # ── Float rotation ────────────────────────────────────
    if float_rotation is not None and float_rotation > 2.0:
        warnings.append("float_rotation_over_200pct")

    # ── Data / quote quality warnings ─────────────────────
    if quote_age_seconds is not None and quote_age_seconds > 5:
        warnings.append("stale_quote")
    if spread_pct is not None:
        if spread_pct > 3.0:
            warnings.append("wide_spread_caution")
        elif spread_pct > 1.0:
            warnings.append("spread_caution")

    # ── Price-action warnings ─────────────────────────────
    if parabolic:
        warnings.append("parabolic")
    if below_vwap:
        warnings.append("below_vwap")
    if below_ema:
        warnings.append("below_ema")

    # ── Session / time warnings ───────────────────────────
    if is_lunch:
        warnings.append("lunch_window")
    if halt_history_today:
        warnings.append("halt_history_today")

    # ── Confidence-based ──────────────────────────────────
    if data_confidence is not None and data_confidence < 0.7:
        warnings.append("low_data_confidence")

    # ── News / catalyst status (SPEC §8, §22.12) ───────────
    if has_news is False:
        warnings.append("no_news")
    elif has_news is None:
        warnings.append("news_unknown")
    # has_news is True → nothing to warn about

    if has_catalyst is False:
        warnings.append("no_catalyst")
    elif has_catalyst is None:
        warnings.append("catalyst_unknown")
    # has_catalyst is True → nothing to warn about

    return warnings


# ──────────────────────────────────────────────────────────────────
#  Soft warning multiplier
# ──────────────────────────────────────────────────────────────────


def soft_warning_multiplier(
    warnings: list[str],
    *,
    attention_score: Optional[float] = None,
) -> float:
    """Compute the soft multiplier for a set of warnings per SPEC §8.

    Multipliers are multiplied together.  Floor is 0.25x per SPEC.

    ``no_news`` / ``no_catalyst`` are attention-dependent:
    - no penalty when attention >= 70,
    - 0.75x when attention < 70.

    Parameters
    ----------
    warnings : list[str]
        Warning labels from ``map_soft_warnings``.
    attention_score : float or None
        Attention score (0-100).  Used for attention-dependent penalties.

    Returns
    -------
    float
        Multiplier in [0.25, 1.0].
    """
    # Default multipliers per warning label
    _MULTIPLIERS: dict[str, float] = {
        "chinese_adr": 1.0,  # theme annotation, never a penalty
        "biotech": 1.0,  # volatility warning, may be positive
        "speculative": 1.0,  # annotation only
        "price_below_2": 0.5,
        "outside_focus_price_range_low": 0.5,
        "outside_focus_price_range_high": 0.5,
        "float_unknown": 0.75,
        "very_low_float": 0.5,
        "low_float": 1.0,  # squeeze potential
        "float_rotation_over_200pct": 0.5,
        "stale_quote": 0.75,
        "wide_spread_caution": 1.0,
        "spread_caution": 1.0,
        "parabolic": 0.5,
        "below_vwap": 1.0,  # context only
        "below_ema": 1.0,  # trend warning
        "lunch_window": 0.5,
        "halt_history_today": 1.0,
        "low_data_confidence": 1.0,
        "no_news": 1.0,  # handled by attention-dependent logic below
        "no_catalyst": 1.0,  # handled by attention-dependent logic below
        "news_unknown": 1.0,  # annotation only
        "catalyst_unknown": 1.0,  # annotation only
    }

    multiplier = 1.0
    for w in warnings:
        multiplier *= _MULTIPLIERS.get(w, 1.0)

    # Attention-dependent no-news / no-catalyst penalty (SPEC §8)
    has_no_news_or_catalyst = "no_news" in warnings or "no_catalyst" in warnings
    if has_no_news_or_catalyst and attention_score is not None and attention_score < 70:
        multiplier *= 0.75

    return max(0.25, round(multiplier, 4))
