"""
Phase 6 — Risk, sizing, and add logic per SPEC section 11.

Pure-math module.  No broker calls, no network, no side-effects.

Every entry size is derived from defined risk, never from conviction.
Adding is only allowed when the starter is already right.
"""

from __future__ import annotations

from typing import Optional


# ──────────────────────────────────────────────────────────────────
#  Sizing
# ──────────────────────────────────────────────────────────────────


def starter_risk_amount(equity: float, starter_risk_pct: float) -> float:
    """Dollar amount at risk for a starter entry.

    >>> starter_risk_amount(100_000, 0.0025)
    250.0
    """
    return round(equity * starter_risk_pct, 2)


def attention_multiplier(attention_score: Optional[float]) -> float:
    """Convert attention score to risk multiplier per SPEC §11.2.

    | Score   | Multiplier |
    |---------|-----------|
    | 85–100  | 1.00      |
    | 70–84   | 0.75      |
    | 50–69   | 0.50      |
    | <50     | 0.25      |
    | None    | 0.25      |
    """
    if attention_score is None:
        return 0.25
    if attention_score >= 85:
        return 1.0
    if attention_score >= 70:
        return 0.75
    if attention_score >= 50:
        return 0.50
    return 0.25


def adjusted_starter_risk(
    starter_risk: float,
    *,
    attention_mult: float = 1.0,
    soft_mult: float = 1.0,
    data_confidence: float = 1.0,
    floor_soft: float = 0.25,
) -> float:
    """Adjusted starter risk after attention/soft/confidence multipliers.

    >>> adjusted_starter_risk(250, attention_mult=0.75, soft_mult=0.50, data_confidence=0.80)
    75.0
    """
    soft_mult = max(soft_mult, floor_soft)
    return round(starter_risk * attention_mult * soft_mult * data_confidence, 2)


def calculate_shares(risk_amount: float, risk_per_share: float) -> int:
    """Number of shares from risk amount.

    Returns 0 if ``risk_per_share <= 0`` or the result rounds to < 1 share.
    """
    if risk_per_share <= 0:
        return 0
    shares = int(risk_amount / risk_per_share)
    return max(0, shares)


def entry_sizing(
    equity: float,
    risk_per_share: float,
    *,
    starter_risk_pct: float,
    max_trade_risk_pct: float = 0.01,
    attention_score: Optional[float] = None,
    soft_multiplier: float = 1.0,
    data_confidence: float = 1.0,
) -> tuple[int, float, float, float]:
    """Full entry-sizing calculation.

    Returns
    -------
    (shares, starter_risk, adjusted_risk, risk_amount)
        risk_amount = adjusted_risk (the dollar risk used for share calc)
    """
    starter = starter_risk_amount(equity, starter_risk_pct)
    att_mult = attention_multiplier(attention_score)
    adjusted = adjusted_starter_risk(
        starter,
        attention_mult=att_mult,
        soft_mult=soft_multiplier,
        data_confidence=data_confidence,
    )
    # Cap per-trade risk at max_trade_risk_pct * equity (T4.4)
    max_risk = equity * max_trade_risk_pct
    adjusted = min(adjusted, max_risk)
    shares = calculate_shares(adjusted, risk_per_share)
    return shares, starter, adjusted, adjusted
