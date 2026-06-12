"""
Phase 3 — Hard filters per SPEC section 7.

Catastrophic mechanical checks only.  Every hard skip is logged with a
machine-readable reason.  No qualitative judgments — Chinese ADR, no-news,
parabolic, low-float, biotech, and similar signals are **never** hard blocks.

Exports
-------
- ``run_hard_filters()`` — the main entry point
- ``check_quote_age()``, ``check_spread()``, ``check_time_gate()`` — per-category helpers
- ``quote_age_tier()``, ``spread_tier()`` — classification utilities
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional

from src.models.schemas import AccountRiskState, Candidate, HardFilterResult

# ──────────────────────────────────────────────────────────────────
#  Classification utilities (no side-effects)
# ──────────────────────────────────────────────────────────────────


def quote_age_tier(age_seconds: Optional[float], fresh_s: float = 5.0, max_s: float = 15.0) -> str:
    """Classify quote age per SPEC §7.2.

    Returns one of: ``"normal"``, ``"stale_warning"``, ``"hard_reject"``.
    """
    if age_seconds is None:
        return "hard_reject"  # no quote at all
    if age_seconds <= fresh_s:
        return "normal"
    if age_seconds <= max_s:
        return "stale_warning"
    return "hard_reject"


def spread_tier(spread_pct: Optional[float]) -> str:
    """Classify spread per SPEC §7.3.

    Returns one of: ``"normal"``, ``"caution"``, ``"tiny_scalp"``, ``"hard_reject"``.
    """
    if spread_pct is None:
        return "hard_reject"  # cannot calculate
    if spread_pct <= 1.0:
        return "normal"
    if spread_pct <= 3.0:
        return "caution"
    if spread_pct <= 5.0:
        return "tiny_scalp"
    return "hard_reject"


# ──────────────────────────────────────────────────────────────────
#  Per-category check functions
#  Each returns a list of mechanical block reasons (empty = pass).
# ──────────────────────────────────────────────────────────────────


def check_market_structure(
    *,
    is_tradable: bool = True,
    is_halted: bool = False,
    is_otc: bool = False,
    market_allows_entries: bool = True,
    past_entry_cutoff: bool = False,
    in_watch_only_window: bool = False,
) -> list[str]:
    """Broker / market-structure hard blocks (SPEC §7.1)."""
    blocks: list[str] = []

    if not is_tradable:
        blocks.append("broker_not_tradable")
    if is_halted:
        blocks.append("symbol_halted")
    if is_otc:
        blocks.append("otc_unsupported")
    if not market_allows_entries:
        blocks.append("market_closed_for_entries")
    if past_entry_cutoff:
        blocks.append("past_entry_cutoff")
    if in_watch_only_window:
        blocks.append("watch_only_window")

    return blocks


def check_execution_data(
    *,
    current_price: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    max_quote_age_seconds: float = 15.0,
) -> list[str]:
    """Execution-data hard blocks (SPEC §7.2)."""
    blocks: list[str] = []

    if current_price is None or current_price <= 0:
        blocks.append("no_current_price")

    if bid is None or ask is None:
        blocks.append("missing_bid_ask")
    elif bid <= 0:
        blocks.append("bid_zero_or_negative")
    elif ask <= 0:
        blocks.append("ask_zero_or_negative")
    elif bid >= ask:
        blocks.append("crossed_market")

    qt = quote_age_tier(quote_age_seconds, max_s=max_quote_age_seconds)
    if qt == "hard_reject":
        blocks.append("quote_too_stale" if quote_age_seconds else "no_quote_timestamp")

    return blocks


def check_liquidity_spread(
    *,
    spread_pct: Optional[float] = None,
    volume_zero: bool = False,
    dollar_volume_5m: Optional[float] = None,
    min_dollar_volume: float = 100_000.0,
    is_scanner_only: bool = False,
) -> list[str]:
    """Liquidity / spread hard blocks (SPEC §7.3)."""
    blocks: list[str] = []

    st = spread_tier(spread_pct)
    if st == "hard_reject":
        blocks.append(f"spread_hard_reject:{spread_pct}")

    if volume_zero:
        blocks.append("zero_volume")

    if dollar_volume_5m is not None and dollar_volume_5m < min_dollar_volume:
        if not is_scanner_only:
            blocks.append(f"dollar_volume_below_min:{dollar_volume_5m}")

    return blocks


def check_risk_definition(
    *,
    has_logical_stop: bool = True,
    risk_per_share: Optional[float] = None,
    risk_amount: Optional[float] = None,
    spread_pct: Optional[float] = None,
    estimated_slippage: float = 0.01,
    stop_width_pct: Optional[float] = None,
    max_stop_width_pct: Optional[float] = None,
    entry_price: Optional[float] = None,
) -> list[str]:
    """Risk-definition hard blocks (SPEC §7.4)."""
    blocks: list[str] = []

    if not has_logical_stop:
        blocks.append("no_logical_stop")

    if risk_per_share is not None and risk_per_share <= 0:
        blocks.append("risk_per_share_zero_or_negative")

    if risk_per_share is not None and risk_amount is not None and risk_per_share > 0:
        if int(risk_amount / risk_per_share) < 1:
            blocks.append("risk_amount_too_small_for_one_share")

    # Stop too tight to matter
    if risk_per_share is not None and spread_pct is not None and entry_price is not None:
        slippage_dollars = estimated_slippage
        spread_dollars = entry_price * (spread_pct / 100.0)
        min_meaningful = 1.5 * (spread_dollars + slippage_dollars)
        if risk_per_share <= min_meaningful:
            blocks.append("stop_too_tight_for_spread_and_slippage")

    if stop_width_pct is not None and max_stop_width_pct is not None:
        if stop_width_pct > max_stop_width_pct:
            blocks.append(f"stop_exceeds_max_width:{stop_width_pct}>{max_stop_width_pct}")

    return blocks


def check_account_risk(
    account: AccountRiskState,
    *,
    symbol_locked: bool = False,
    max_positions: int = 3,
    theme_exceeds_limit: bool = False,
) -> list[str]:
    """Account / symbol risk hard blocks (SPEC §7.5)."""
    blocks: list[str] = []

    if account.is_kill_switch_on:
        reason = account.kill_switch_reason or "kill_switch_active"
        blocks.append(reason)

    if account.daily_loss_breached:
        blocks.append("daily_loss_cap_breached")

    if account.open_position_count >= max_positions:
        blocks.append("max_positions_reached")

    if account.total_open_risk > 0:
        # max_open_risk is checked by the caller with a threshold
        pass

    if symbol_locked:
        blocks.append("symbol_locked")

    if theme_exceeds_limit:
        blocks.append("theme_concentration_limit")

    return blocks


# ──────────────────────────────────────────────────────────────────
#  Main entry point
# ──────────────────────────────────────────────────────────────────


def run_hard_filters(
    candidate: Candidate,
    *,
    # ── Market structure ──────────────────────────────────
    is_tradable: bool = True,
    is_halted: bool = False,
    is_otc: bool = False,
    market_allows_entries: bool = True,
    past_entry_cutoff: bool = False,
    in_watch_only_window: bool = False,
    # ── Execution data ────────────────────────────────────
    current_price: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    max_quote_age_seconds: float = 15.0,
    # ── Liquidity / spread ────────────────────────────────
    spread_pct: Optional[float] = None,
    volume_zero: bool = False,
    dollar_volume_5m: Optional[float] = None,
    min_dollar_volume: float = 100_000.0,
    is_scanner_only: bool = False,
    # ── Risk definition ───────────────────────────────────
    has_logical_stop: bool = True,
    risk_per_share: Optional[float] = None,
    risk_amount: Optional[float] = None,
    estimated_slippage: float = 0.01,
    stop_width_pct: Optional[float] = None,
    max_stop_width_pct: Optional[float] = None,
    entry_price: Optional[float] = None,
    # ── Account / symbol risk ─────────────────────────────
    account: Optional[AccountRiskState] = None,
    symbol_locked: bool = False,
    max_positions: int = 3,
    theme_exceeds_limit: bool = False,
) -> HardFilterResult:
    """Run every hard filter and return a ``HardFilterResult``.

    This is the single gate every candidate must pass before an entry
    can be submitted.  No qualitative filters — only mechanical,
    execution-gating checks.

    Parameters
    ----------
    candidate : Candidate
        The enriched candidate (used for symbol identity only; all data
        comes from explicit keyword arguments so the function is testable
        without mocking external services).
    All ``*`` keyword arguments map directly to SPEC §7 subsections.

    Returns
    -------
    HardFilterResult
        ``passed=True`` when zero hard blocks were found.
    """
    all_blocks: list[str] = []

    all_blocks.extend(
        check_market_structure(
            is_tradable=is_tradable,
            is_halted=is_halted,
            is_otc=is_otc,
            market_allows_entries=market_allows_entries,
            past_entry_cutoff=past_entry_cutoff,
            in_watch_only_window=in_watch_only_window,
        )
    )

    all_blocks.extend(
        check_execution_data(
            current_price=current_price,
            bid=bid,
            ask=ask,
            quote_age_seconds=quote_age_seconds,
            max_quote_age_seconds=max_quote_age_seconds,
        )
    )

    all_blocks.extend(
        check_liquidity_spread(
            spread_pct=spread_pct,
            volume_zero=volume_zero,
            dollar_volume_5m=dollar_volume_5m,
            min_dollar_volume=min_dollar_volume,
            is_scanner_only=is_scanner_only,
        )
    )

    all_blocks.extend(
        check_risk_definition(
            has_logical_stop=has_logical_stop,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount,
            spread_pct=spread_pct,
            estimated_slippage=estimated_slippage,
            stop_width_pct=stop_width_pct,
            max_stop_width_pct=max_stop_width_pct,
            entry_price=entry_price,
        )
    )

    if account is not None:
        all_blocks.extend(
            check_account_risk(
                account,
                symbol_locked=symbol_locked,
                max_positions=max_positions,
                theme_exceeds_limit=theme_exceeds_limit,
            )
        )
    else:
        # Account not provided — still check symbol lock
        if symbol_locked:
            all_blocks.append("symbol_locked")

    passed = len(all_blocks) == 0
    return HardFilterResult(passed=passed, blocks=all_blocks)


# ──────────────────────────────────────────────────────────────────
#  Convenience: time-gate helpers (SPEC §7.1 default times)
# ──────────────────────────────────────────────────────────────────

_WATCH_ONLY_START = time(9, 30)   # 9:30 AM ET
_WATCH_ONLY_END = time(9, 35)     # 9:35 AM ET
_LUNCH_START = time(11, 30)       # 11:30 AM ET
_LUNCH_END = time(14, 0)          # 2:00 PM ET
_NO_NEW_ENTRIES_AFTER = time(15, 30)  # 3:30 PM ET
_FLATTEN_TIME = time(15, 55)          # 3:55 PM ET


def is_watch_only_window(et_time: time) -> bool:
    """Return True during the 9:30–9:35 ET watch-only window."""
    return _WATCH_ONLY_START <= et_time < _WATCH_ONLY_END


def is_lunch_window(et_time: time) -> bool:
    """Return True during the 11:30–14:00 ET lunch window."""
    return _LUNCH_START <= et_time < _LUNCH_END


def is_past_entry_cutoff(et_time: time) -> bool:
    """Return True if new entries are disallowed after 15:30 ET."""
    return et_time >= _NO_NEW_ENTRIES_AFTER


def is_flatten_time(et_time: time) -> bool:
    """Return True at/after 15:55 ET — flatten intraday positions."""
    return et_time >= _FLATTEN_TIME


def check_time_gate(et_time: time) -> dict[str, bool]:
    """Return all time-gate flags for a given Eastern time.

    Returns a dict with keys: ``watch_only``, ``lunch``, ``past_cutoff``, ``flatten``.
    """
    return {
        "watch_only": is_watch_only_window(et_time),
        "lunch": is_lunch_window(et_time),
        "past_cutoff": is_past_entry_cutoff(et_time),
        "flatten": is_flatten_time(et_time),
    }
