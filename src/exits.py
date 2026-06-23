"""
Phase 8 — Exit engine per SPEC section 12.

Prioritised exit checks run every monitor cycle.  Emergency checks first.
Every open position is evaluated; the first triggered exit wins.

No broker calls.  No network.  Pure detection logic.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional

from loguru import logger

from src.entries import Bar, avg_bar_range
from src.models.schemas import (
    EntrySetupType,
    ExitDecision,
    MoveState,
    PositionState,
    PositionStateModel,
)


# ──────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────


def calculate_pnl(position: PositionStateModel, current_price: float) -> float:
    """Unrealised P&L for a position at ``current_price``."""
    if position.average_entry is None or position.current_shares <= 0:
        return 0.0
    return round((current_price - position.average_entry) * position.current_shares, 2)


def calculate_pnl_r(
    position: PositionStateModel, current_price: float, risk_per_share: float
) -> float:
    """P&L expressed in R multiples."""
    if position.current_shares <= 0 or risk_per_share <= 0:
        return 0.0
    pnl = calculate_pnl(position, current_price)
    total_risk = risk_per_share * position.current_shares
    return round(pnl / total_risk, 2) if total_risk > 0 else 0.0


def _exit_decision(
    symbol: str,
    exit_pct: int,
    reason: str,
    exit_price: Optional[float] = None,
    pnl: Optional[float] = None,
    pnl_r: Optional[float] = None,
    remaining_shares: int = 0,
) -> ExitDecision:
    return ExitDecision(
        symbol=symbol,
        should_exit=True,
        exit_pct=exit_pct,
        reason=reason,
        exit_price=exit_price,
        pnl=pnl,
        pnl_r=pnl_r,
        remaining_shares=remaining_shares,
    )


# ══════════════════════════════════════════════════════════════════
#  Exit detectors (priority order per SPEC §12.1)
# ══════════════════════════════════════════════════════════════════


def check_emergency_exit(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    spread_pct: Optional[float] = None,
    entry_spread_pct: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    position_unprotected: bool = False,
    halt_count_today: int = 0,
    broker_unreachable_seconds: Optional[float] = None,
    risk_per_share: Optional[float] = None,
) -> Optional[ExitDecision]:
    """P1 — Emergency exit conditions (SPEC §12.5).

    Returns an exit decision when any emergency condition is met.
    """
    sym = position.symbol
    price = current_price
    rps = risk_per_share or 0.01

    # Spread explosion
    if spread_pct is not None:
        if spread_pct > 5.0 or (entry_spread_pct is not None and spread_pct > entry_spread_pct * 3):
            if price is None:
                return _exit_decision(sym, 100, f"spread_explosion:{spread_pct}%")
            pnl = calculate_pnl(position, price)
            return _exit_decision(sym, 100, f"spread_explosion:{spread_pct}%", exit_price=price, pnl=pnl)

    # Quote unreliable — any stale quote >60s flattens (SPEC §12.5, §15.3)
    if quote_age_seconds is not None and quote_age_seconds > 60:
        if price is None:
            return _exit_decision(sym, 100, f"quote_unreliable_age:{quote_age_seconds}s")
        pnl = calculate_pnl(position, price)
        return _exit_decision(sym, 100, f"quote_unreliable_age:{quote_age_seconds}s", exit_price=price, pnl=pnl)

    # Position unprotected + losing
    if position_unprotected and price is not None:
        pnl = calculate_pnl(position, price)
        if pnl < 0:
            return _exit_decision(sym, 100, "unprotected_and_losing", exit_price=price, pnl=pnl)

    # Multiple halts
    if halt_count_today >= 3:
        return _exit_decision(sym, 100, f"multiple_halts:{halt_count_today}", exit_price=price)

    # Broker unreachable
    if broker_unreachable_seconds is not None and broker_unreachable_seconds > 120:
        return None  # do not assume flat — caller must mark UNPROTECTED

    return None


def check_loss_caps(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    risk_per_share: Optional[float] = None,
    daily_loss_breached: bool = False,
    per_symbol_loss_capped: bool = False,
) -> Optional[ExitDecision]:
    """P2 — Daily loss / per-symbol loss cap (SPEC §12.2 loss-caps)."""
    if daily_loss_breached:
        if current_price is None:
            return _exit_decision(position.symbol, 100, "daily_loss_cap_breached")
        pnl = calculate_pnl(position, current_price)
        return _exit_decision(position.symbol, 100, "daily_loss_cap_breached", exit_price=current_price, pnl=pnl)

    if per_symbol_loss_capped:
        if current_price is None:
            return _exit_decision(position.symbol, 100, "per_symbol_loss_cap_breached")
        pnl = calculate_pnl(position, current_price)
        return _exit_decision(position.symbol, 100, "per_symbol_loss_cap_breached", exit_price=current_price, pnl=pnl)

    return None


def check_hard_stop(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    quote_age_seconds: Optional[float] = None,
    risk_per_share: Optional[float] = None,
) -> Optional[ExitDecision]:
    """P3 — Hard stop triggers when price trades at or below stop (SPEC §12.2)."""
    if (
        current_price is None
        or position.stop_price is None
        or quote_age_seconds is None
        or quote_age_seconds > 15
    ):
        return None

    if current_price <= position.stop_price:
        pnl = calculate_pnl(position, current_price)
        pnl_r = calculate_pnl_r(position, current_price, risk_per_share or 0.01)
        return _exit_decision(
            position.symbol, 100, f"hard_stop:price={current_price}<=stop={position.stop_price}",
            exit_price=current_price, pnl=pnl, pnl_r=pnl_r,
        )
    return None


def check_invalidation(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    quote_age_seconds: Optional[float] = None,
    entry_setup: Optional[str] = None,
    bars: Optional[list[Bar]] = None,
    vwap: Optional[float] = None,
    prior_hod: Optional[float] = None,
    consolidation_low: Optional[float] = None,
    risk_per_share: Optional[float] = None,
) -> Optional[ExitDecision]:
    """P3b — Setup invalidation (SPEC §12.2)."""
    if current_price is None or quote_age_seconds is None or quote_age_seconds > 15:
        return None

    if not bars or len(bars) < 1:
        return None

    sym = position.symbol
    price = current_price
    rps = risk_per_share or 0.01
    last = bars[-1]

    if entry_setup == "first_pullback":
        # price trades below pullback low — but we don't have pullback_low stored
        # Simplified: if last candle is red and we're below VWAP → invalidated
        if vwap is not None and last.is_red and last.close < vwap:
            return _exit_decision(sym, 100, "first_pullback_invalidated")
        return None

    if entry_setup == "hod_reclaim":
        if prior_hod is not None and last.close < prior_hod and last.is_red:
            return _exit_decision(sym, 100, f"hod_reclaim_failed:close={last.close}<hod={prior_hod}")
        return None

    if entry_setup == "consolidation_breakout":
        if consolidation_low is not None and last.close < consolidation_low:
            return _exit_decision(sym, 100, "consolidation_breakout_failed")
        return None

    if entry_setup == "vwap_reclaim":
        if vwap is not None and last.close < vwap and last.is_red:
            return _exit_decision(sym, 100, f"vwap_reclaim_failed:close={last.close}<vwap={vwap}")
        return None

    if entry_setup == "scalp_reclaim":
        if last.is_red:
            return _exit_decision(sym, 100, "scalp_first_red_candle")
        return None

    return None


def check_missing_protection(
    position: PositionStateModel,
    *,
    position_unprotected: bool = False,
) -> Optional[ExitDecision]:
    """P4 — Missing protection (SPEC §12.1 priority 4).

    Fires for UNPROTECTED state or when the caller signals that
    verified protection is absent for an OPEN position.
    """
    if position_unprotected or position.state == PositionState.UNPROTECTED:
        return _exit_decision(position.symbol, 100, "missing_protection")
    return None


def check_scale_out(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    risk_per_share: Optional[float] = None,
    move_state: Optional[MoveState] = None,
    entry_setup: Optional[str] = None,
    spread_pct: Optional[float] = None,
    bars: Optional[list[Bar]] = None,
) -> Optional[ExitDecision]:
    """P5 — Scale-out triggers (SPEC §12.3)."""
    if current_price is None:
        return None

    if position.average_entry is None or position.current_shares <= 0:
        return None

    sym = position.symbol
    price = current_price
    rps = risk_per_share or 0.01
    pnl = calculate_pnl(position, price)
    pnl_r = calculate_pnl_r(position, price, rps)
    remaining = position.current_shares

    # Scalp mode
    if entry_setup == "scalp_reclaim":
        if pnl_r >= 0.5:
            return _exit_decision(sym, 100, f"scalp_target:0.5R", exit_price=price, pnl=pnl, pnl_r=pnl_r, remaining_shares=0)
        if bars and len(bars) >= 1 and bars[-1].is_red:
            return _exit_decision(sym, 100, "scalp_stall_red", exit_price=price, pnl=pnl, pnl_r=pnl_r, remaining_shares=0)
        return None

    # Extended/parabolic
    if move_state == MoveState.EXTENDED:
        if pnl_r >= 0.5:
            sell_pct = 50
            new_remaining = int(remaining * (1 - sell_pct / 100))
            return _exit_decision(sym, sell_pct, f"extended_scale_0.5R", exit_price=price, pnl=pnl, pnl_r=pnl_r, remaining_shares=new_remaining)
        return None

    # Normal starter trade
    if pnl_r >= 1.0:
        return _exit_decision(sym, 33, f"scale_out_1R", exit_price=price, pnl=pnl, pnl_r=pnl_r, remaining_shares=int(remaining * 0.67))

    # Extension bar
    if bars and len(bars) >= 2:
        last_range = bars[-1].range
        ar = avg_bar_range(bars, 10)
        if ar > 0 and last_range > 1.5 * ar and pnl_r > 0:
            return _exit_decision(sym, 25, "extension_bar", exit_price=price, pnl=pnl, pnl_r=pnl_r, remaining_shares=int(remaining * 0.75))

    # Spread enters caution while profitable
    if spread_pct is not None and spread_pct > 3.0 and pnl_r > 0:
        return _exit_decision(sym, 25, f"spread_caution_scale:{spread_pct}%", exit_price=price, pnl=pnl, pnl_r=pnl_r, remaining_shares=int(remaining * 0.75))

    return None


def check_failed_reclaim(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    risk_per_share: Optional[float] = None,
    entry_setup: Optional[str] = None,
    bars: Optional[list[Bar]] = None,
    vwap: Optional[float] = None,
) -> Optional[ExitDecision]:
    """P6 — Failed reclaim / failed pullback (SPEC §12.1)."""
    if current_price is None:
        return None

    if not bars or len(bars) < 2:
        return None
    sym = position.symbol
    price = current_price
    rps = risk_per_share or 0.01
    last = bars[-1]
    prev = bars[-2]
    pnl = calculate_pnl(position, price)
    pnl_r = calculate_pnl_r(position, price, rps)

    # Consecutive red bars after being profitable
    if last.is_red and prev.is_red and pnl_r > 0.5:
        return _exit_decision(sym, 100, "failed_reclaim_2_red_bars", exit_price=price, pnl=pnl, pnl_r=pnl_r)

    return None


def check_vwap_loss(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    vwap: Optional[float] = None,
    risk_per_share: Optional[float] = None,
) -> Optional[ExitDecision]:
    """P7 — VWAP loss without reclaim (SPEC §12.1)."""
    if current_price is None:
        return None

    if vwap is not None and current_price < vwap:
        pnl = calculate_pnl(position, current_price)
        pnl_r = calculate_pnl_r(position, current_price, risk_per_share or 0.01)
        if pnl_r < 0:
            return _exit_decision(
                position.symbol, 100, f"vwap_loss:price={current_price}<vwap={vwap}",
                exit_price=current_price, pnl=pnl, pnl_r=pnl_r,
            )
    return None


def check_spread_expansion(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    spread_pct: Optional[float] = None,
    entry_spread_pct: Optional[float] = None,
    risk_per_share: Optional[float] = None,
) -> Optional[ExitDecision]:
    """P8 — Spread expansion exit (SPEC §12.1)."""
    if current_price is None:
        return None

    if spread_pct is not None and entry_spread_pct is not None:
        if spread_pct > entry_spread_pct * 2.0 and spread_pct > 2.0:
            pnl = calculate_pnl(position, current_price)
            return _exit_decision(
                position.symbol, 100, f"spread_expansion:{spread_pct}%>2x_entry:{entry_spread_pct}%",
                exit_price=current_price, pnl=pnl,
            )
    return None


def check_volume_disappearance(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    bars: Optional[list[Bar]] = None,
    risk_per_share: Optional[float] = None,
) -> Optional[ExitDecision]:
    """P9 — Volume/liquidity disappearance (SPEC §12.1)."""
    if current_price is None:
        return None

    if not bars or len(bars) < 5:
        return None
    recent = bars[-3:]
    if all(b.volume == 0 or b.volume < 100 for b in recent):
        pnl = calculate_pnl(position, current_price)
        return _exit_decision(position.symbol, 100, "volume_disappeared", exit_price=current_price, pnl=pnl)
    return None


def check_time_exit(
    position: PositionStateModel,
    *,
    et_time: Optional[time] = None,
    flatten_time: time = time(15, 55),
) -> Optional[ExitDecision]:
    """P10 — Time-based exit (SPEC §12.1)."""
    if et_time is not None and et_time >= flatten_time:
        return _exit_decision(position.symbol, 100, f"flatten_time:{et_time}")
    return None


def check_runner_trail(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    risk_per_share: Optional[float] = None,
    highest_price_seen: Optional[float] = None,
    bars: Optional[list[Bar]] = None,
    trail_hit: bool = False,
) -> Optional[ExitDecision]:
    """P11 — Trailing runner exit (SPEC §12.4)."""
    if current_price is None or position.state != PositionState.RUNNER:
        return None
    sym = position.symbol
    price = current_price
    rps = risk_per_share or 0.01
    pnl = calculate_pnl(position, price)
    pnl_r = calculate_pnl_r(position, price, rps)

    if trail_hit:
        return _exit_decision(sym, 100, "trail_hit", exit_price=price, pnl=pnl, pnl_r=pnl_r)

    # 2 consecutive red 5-minute bars → exit
    if bars and len(bars) >= 2 and bars[-1].is_red and bars[-2].is_red:
        return _exit_decision(sym, 100, "runner_2_red_bars", exit_price=price, pnl=pnl, pnl_r=pnl_r)

    return None


# ══════════════════════════════════════════════════════════════════
#  Orchestrator
# ══════════════════════════════════════════════════════════════════


def check_exits(
    position: PositionStateModel,
    *,
    current_price: Optional[float],
    risk_per_share: Optional[float] = None,
    # Emergency
    spread_pct: Optional[float] = None,
    entry_spread_pct: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    position_unprotected: bool = False,
    halt_count_today: int = 0,
    broker_unreachable_seconds: Optional[float] = None,
    # Loss caps
    daily_loss_breached: bool = False,
    per_symbol_loss_capped: bool = False,
    # Setup context
    entry_setup: Optional[str] = None,
    bars: Optional[list[Bar]] = None,
    vwap: Optional[float] = None,
    prior_hod: Optional[float] = None,
    consolidation_low: Optional[float] = None,
    # Scale-out context
    move_state: Optional[MoveState] = None,
    # Time
    et_time: Optional[time] = None,
    flatten_time: time = time(15, 55),
    # Runner
    trail_hit: bool = False,
) -> Optional[ExitDecision]:
    """Run all exit checks in priority order.  Returns the first triggered exit.

    Priority (SPEC §12.1):
      P1 emergency → P2 loss caps → P3 hard stop → P3b invalidation
      → P4 missing protection → P5 scale-out → P6 failed reclaim
      → P7 VWAP loss → P8 spread → P9 volume → P10 time → P11 runner trail
    """
    checks = [
        ("P1_emergency", lambda: check_emergency_exit(
            position, current_price=current_price, spread_pct=spread_pct,
            entry_spread_pct=entry_spread_pct, quote_age_seconds=quote_age_seconds,
            position_unprotected=position_unprotected, halt_count_today=halt_count_today,
            broker_unreachable_seconds=broker_unreachable_seconds, risk_per_share=risk_per_share,
        )),
        ("P2_loss_caps", lambda: check_loss_caps(
            position, current_price=current_price, risk_per_share=risk_per_share,
            daily_loss_breached=daily_loss_breached, per_symbol_loss_capped=per_symbol_loss_capped,
        )),
        ("P3_hard_stop", lambda: check_hard_stop(
            position, current_price=current_price, quote_age_seconds=quote_age_seconds,
            risk_per_share=risk_per_share,
        )),
        ("P3b_invalidation", lambda: check_invalidation(
            position, current_price=current_price, entry_setup=entry_setup,
            bars=bars, vwap=vwap, prior_hod=prior_hod,
            consolidation_low=consolidation_low, quote_age_seconds=quote_age_seconds,
            risk_per_share=risk_per_share,
        )),
        ("P4_missing_protection", lambda: check_missing_protection(
            position, position_unprotected=position_unprotected,
        )),
        ("P5_scale_out", lambda: check_scale_out(
            position, current_price=current_price, risk_per_share=risk_per_share,
            move_state=move_state, entry_setup=entry_setup, spread_pct=spread_pct, bars=bars,
        )),
        ("P6_failed_reclaim", lambda: check_failed_reclaim(
            position, current_price=current_price, risk_per_share=risk_per_share,
            entry_setup=entry_setup, bars=bars, vwap=vwap,
        )),
        ("P7_vwap_loss", lambda: check_vwap_loss(
            position, current_price=current_price, vwap=vwap, risk_per_share=risk_per_share,
        )),
        ("P8_spread", lambda: check_spread_expansion(
            position, current_price=current_price, spread_pct=spread_pct,
            entry_spread_pct=entry_spread_pct, risk_per_share=risk_per_share,
        )),
        ("P9_volume", lambda: check_volume_disappearance(
            position, current_price=current_price, bars=bars, risk_per_share=risk_per_share,
        )),
        ("P10_time", lambda: check_time_exit(
            position, et_time=et_time, flatten_time=flatten_time,
        )),
        ("P11_runner_trail", lambda: check_runner_trail(
            position, current_price=current_price, risk_per_share=risk_per_share,
            bars=bars, trail_hit=trail_hit,
        )),
    ]

    for label, check_fn in checks:
        try:
            result = check_fn()
            if result is not None and result.should_exit:
                return result
        except Exception:
            logger.exception("Exit check '%s' failed for %s", label, position.symbol)
            continue

    return None
