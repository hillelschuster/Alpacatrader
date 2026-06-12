"""
Phase 9 — Integration pipeline per SPEC section 14.

Wires all Phase 1-8 modules into a single processing function that
takes a candidate → runs the full decision chain → writes a JSONL
DecisionRecord.

No network calls.  No broker integration.  Pure composition of
previously-built modules, designed for testing with mock data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional

from src.entries import Bar, find_entry
from src.hard_filters import run_hard_filters
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import (
    AccountRiskState,
    Candidate,
    DecisionRecord,
    EntryInfo,
    EntrySetupType,
    EntrySignal,
    ExitDecision,
    ExitInfo,
    MoveState,
    PositionState,
)
from src.move_classifier import classify_move_state, get_allowed_setups
from src.paper_execution import PaperExecutionGateway
from src.scanner.attention import (
    FormerRunnerStore,
    map_soft_warnings,
    score_attention,
    soft_warning_multiplier,
)
from src.scanner.confidence import calculate_data_confidence, compute_scanner_age_seconds
from src.sizing import attention_multiplier, entry_sizing
from src.state_machine import PositionStore

import loguru


# ──────────────────────────────────────────────────────────────────
#  Market snapshot — enrichment return shape
# ──────────────────────────────────────────────────────────────────


@dataclass
class MarketSnapshot:
    """Enriched market data for a single candidate.

    Bundles the candidate with all real-time data fields the pipeline
    needs for hard filters, attention, move classification, and entry
    detection.
    """

    candidate: Candidate
    bars: Optional[list[Bar]] = None
    vwap: Optional[float] = None
    ema9: Optional[float] = None
    day_high: Optional[float] = None
    prior_hod: Optional[float] = None
    quote_age_seconds: Optional[float] = None
    spread_pct: Optional[float] = None
    rvol: Optional[float] = None
    dollar_volume_5m: Optional[float] = None
    halt_count_today: int = 0


# ──────────────────────────────────────────────────────────────────
#  Pipeline context (mutable accumulator — Context7 recommended pattern)
# ──────────────────────────────────────────────────────────────────


class PipelineResult:
    """Accumulates the result of each pipeline step for one candidate.

    Mutable by design — each step reads previous outputs and writes its own.
    """

    def __init__(self, candidate: Candidate) -> None:
        self.candidate = candidate
        self.symbol = candidate.symbol

        # Step outputs (populated as the pipeline runs)
        self.attention_score: Optional[float] = None
        self.attention_drivers: list[str] = []
        self.data_confidence: Optional[float] = None
        self.scanner_age_seconds: Optional[float] = None
        self.quote_age_seconds: Optional[float] = None
        self.soft_warnings: list[str] = []
        self.hard_blocks: list[str] = []
        self.hard_filter_passed: bool = True
        self.move_state: Optional[MoveState] = None
        self.move_mode: Optional[str] = None
        self.state_evidence: list[str] = []
        self.entry_signal: Optional[EntrySignal] = None
        self.entry_shares: int = 0
        self.entry_risk_amount: float = 0.0
        self.exit_decision: Optional[ExitDecision] = None

        # Final decision
        self.decision: str = "watch"
        self.decision_reason: str = ""

    def to_decision_record(self) -> DecisionRecord:
        """Convert pipeline result to a JSONL-logged DecisionRecord.

        Populates both flat entry/exit fields (backward compat) and nested
        ``entry``/``exit`` objects per SPEC §22.15 item 20.
        """
        sig = self.entry_signal
        ex = self.exit_decision

        # Build nested entry info (empty = all nulls)
        entry_info = EntryInfo()
        if sig:
            entry_info = EntryInfo(
                price=sig.entry_price,
                stop=sig.stop_price,
                risk_per_share=sig.risk_per_share,
                shares=self.entry_shares if self.entry_shares > 0 else None,
                risk_amount=self.entry_risk_amount if self.entry_risk_amount > 0 else None,
            )

        # Build nested exit info (empty = all nulls)
        exit_info = ExitInfo()
        if ex:
            exit_info = ExitInfo(
                reason=ex.reason if ex.should_exit else None,
                pnl=ex.pnl,
                pnl_r=ex.pnl_r,
                remaining_shares=ex.remaining_shares,
            )

        return DecisionRecord(
            symbol=self.symbol,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=self.candidate.source,
            source_timestamp=self.candidate.source_timestamp.isoformat()
                if self.candidate.source_timestamp else None,
            scanner_age_seconds=self.scanner_age_seconds,
            quote_age_seconds=self.quote_age_seconds,
            attention_score=self.attention_score,
            attention_drivers=self.attention_drivers,
            data_confidence=self.data_confidence,
            hard_blocks=self.hard_blocks,
            soft_warnings=self.soft_warnings,
            state=self.move_state.value if self.move_state else None,
            state_evidence=self.state_evidence,
            mode=self.move_mode,
            entry_setup=sig.entry_setup.value if sig else None,
            entry_price=sig.entry_price if sig else None,
            entry_stop=sig.stop_price if sig else None,
            entry_risk_per_share=sig.risk_per_share if sig else None,
            entry_shares=self.entry_shares if self.entry_shares > 0 else None,
            entry_risk_amount=self.entry_risk_amount if self.entry_risk_amount > 0 else None,
            exit_reason=ex.reason if ex and ex.should_exit else None,
            exit_pnl=ex.pnl if ex else None,
            exit_pnl_r=ex.pnl_r if ex else None,
            exit_remaining_shares=ex.remaining_shares if ex else None,
            decision=self.decision,
            reason=self.decision_reason,
            entry=entry_info,
            exit=exit_info,
        )


# ══════════════════════════════════════════════════════════════════
#  Pipeline runner
# ══════════════════════════════════════════════════════════════════


def run_pipeline(
    candidate: Candidate,
    *,
    # Enrichment
    bars: Optional[list[Bar]] = None,
    vwap: Optional[float] = None,
    ema9: Optional[float] = None,
    day_high: Optional[float] = None,
    prior_hod: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    spread_pct: Optional[float] = None,
    rvol: Optional[float] = None,
    dollar_volume_5m: Optional[float] = None,
    bars_available: Optional[bool] = None,  # derived from bars; kept for test compat
    # Attention context
    theme_active: bool = False,
    former_runner_store: Optional[FormerRunnerStore] = None,
    # Execution
    execution_gw: Optional[PaperExecutionGateway] = None,
    position_store: Optional[PositionStore] = None,
    # Risk config
    equity: float = 100_000.0,
    starter_risk_pct: float = 0.0025,
    max_positions: int = 3,
    max_open_risk_pct: float = 0.03,
    max_daily_loss_pct: float = 0.03,
    focus_price_min: float = 1.0,
    focus_price_max: float = 50.0,
    account: Optional[AccountRiskState] = None,
    # Exit context
    check_exits_for_open: bool = False,
    daily_loss_breached: bool = False,
    per_symbol_loss_capped: bool = False,
    halt_count_today: int = 0,
    et_time: Optional[time] = None,
    # Logger
    logger: Optional[DecisionLogger] = None,
    # News / catalyst status (SPEC §8, §22.12)
    has_news: Optional[bool] = None,
    has_catalyst: Optional[bool] = None,
) -> PipelineResult:
    """Run the full decision pipeline for one candidate.

    Steps (in order):
      1. Data confidence
      2. Attention scoring
      3. Soft warnings
      4. Hard filters
      5. Move classification
      6. Entry detection (if allowed)
      7. Sizing
      8. Order submission (if execution gateway provided)
      9. Exit check (if position exists)
     10. Decision record + JSONL log

    Returns
    -------
    PipelineResult
    """
    result = PipelineResult(candidate)

    # Derive bars_available from actual bars if not explicitly provided
    if bars_available is None:
        bars_available = bars is not None and len(bars) > 0

    # ── 1. Data confidence ─────────────────────────────────────
    now = datetime.now(timezone.utc)
    result.data_confidence = calculate_data_confidence(
        candidate, now=now, bars_available=bars_available,
    )
    result.scanner_age_seconds = compute_scanner_age_seconds(candidate, now=now)
    result.quote_age_seconds = quote_age_seconds

    # ── 2. Attention ────────────────────────────────────────────
    is_runner = (
        former_runner_store.is_runner(candidate.symbol)
        if former_runner_store else False
    )
    att = score_attention(
        candidate, rvol=rvol, dollar_volume_5m=dollar_volume_5m,
        theme_active=theme_active, former_runner=is_runner,
    )
    result.attention_score = att.score
    result.attention_drivers = att.drivers

    # ── 3. Soft warnings ────────────────────────────────────────
    result.soft_warnings = map_soft_warnings(
        candidate, price_range_min=focus_price_min, price_range_max=focus_price_max,
        quote_age_seconds=quote_age_seconds, spread_pct=spread_pct,
        data_confidence=result.data_confidence,
        has_news=has_news, has_catalyst=has_catalyst,
    )

    # ── 4. Hard filters ─────────────────────────────────────────
    # Estimate bid/ask from price when not provided (paper mode)
    est_bid = (candidate.price * 0.999) if candidate.price else None
    est_ask = (candidate.price * 1.001) if candidate.price else None
    symbol_locked = (
        execution_gw.is_symbol_locked(candidate.symbol)
        if execution_gw else False
    )
    hf = run_hard_filters(
        candidate,
        current_price=candidate.price,
        bid=est_bid, ask=est_ask,
        quote_age_seconds=quote_age_seconds,
        spread_pct=spread_pct,
        dollar_volume_5m=dollar_volume_5m,
        is_halted=(halt_count_today > 0),
        account=account,
        symbol_locked=symbol_locked,
        max_positions=max_positions,
    )
    result.hard_blocks = hf.blocks
    result.hard_filter_passed = hf.passed

    # ── 5. Move classification ──────────────────────────────────
    state, mode, evidence = classify_move_state(
        price=candidate.price, day_high=day_high, vwap=vwap, ema9=ema9,
        spread_pct=spread_pct, rvol=rvol,
        appeared_recently=(result.attention_score or 0) > 50,
    )
    result.move_state = state
    result.move_mode = mode.value
    result.state_evidence = evidence

    # ── 6. Entry detection ──────────────────────────────────────
    if result.hard_filter_passed and result.attention_score is not None:
        att_mult = attention_multiplier(result.attention_score)

        # Only attempt entry if attention allows (>0.25x)
        if att_mult > 0.25 and bars:
            allowed_setups = get_allowed_setups(state) if state else set()
            signal = find_entry(
                candidate, bars, state=state,
                vwap=vwap, ema9=ema9, day_high=day_high, prior_hod=prior_hod,
                spread_pct=spread_pct, quote_age_seconds=quote_age_seconds,
                data_confidence=result.data_confidence or 1.0,
                allowed_setups=allowed_setups,
            )
            if signal is not None:
                result.entry_signal = signal

                # ── 7. Sizing ───────────────────────────────
                soft_mult = soft_warning_multiplier(
                    result.soft_warnings,
                    attention_score=result.attention_score,
                )
                shares, starter, adj_risk, risk_amount = entry_sizing(
                    equity, signal.risk_per_share,
                    starter_risk_pct=starter_risk_pct,
                    attention_score=result.attention_score,
                    soft_multiplier=soft_mult,
                    data_confidence=result.data_confidence or 1.0,
                )
                result.entry_shares = shares
                result.entry_risk_amount = risk_amount

                # ── 8. Order submission ──────────────────────
                if shares > 0 and execution_gw is not None:
                    try:
                        sized_signal = signal.model_copy(update={
                            "proposed_shares": shares,
                            "risk_amount": risk_amount,
                        })
                        order, pos = execution_gw.submit_entry(sized_signal)

                        # ── 8a. Fill confirmation (paper sim) ──
                        try:
                            execution_gw.confirm_fill(order.order_id)
                        except Exception:
                            loguru.logger.exception(
                                "confirm_fill failed for %s order %s — position stays PENDING_ENTRY",
                                sized_signal.symbol, order.order_id,
                            )

                        # ── 8b. Place stop protection ──────────
                        try:
                            execution_gw.protect_position(
                                sized_signal.symbol,
                                sized_signal.stop_price,
                                sized_signal.proposed_shares,
                            )
                        except Exception:
                            # Protection placement failed — mark UNPROTECTED explicitly
                            # so the exit monitor can see and escalate per SPEC §12.5.
                            try:
                                execution_gw.mark_unprotected(sized_signal.symbol)
                            except Exception:
                                loguru.logger.exception(
                                    "mark_unprotected failed for %s after protection failure",
                                    sized_signal.symbol,
                                )

                        result.decision = "enter"
                        result.decision_reason = f"setup={signal.entry_setup.value} shares={shares}"
                    except ValueError:
                        result.decision = "watch"
                        result.decision_reason = "symbol_locked"
                elif shares > 0:
                    result.decision = "enter"
                    result.decision_reason = f"setup={signal.entry_setup.value} shares={shares} (paper sim)"
                else:
                    result.decision = "watch"
                    result.decision_reason = "zero_shares_from_sizing"
            else:
                result.decision = "watch"
                result.decision_reason = "no_entry_setup_detected"
        else:
            result.decision = "watch"
            result.decision_reason = "attention_too_low" if att_mult <= 0.25 else "no_bars_for_entry"
    elif not result.hard_filter_passed:
        result.decision = "skip"
        result.decision_reason = f"hard_blocks:{','.join(result.hard_blocks)}"
    else:
        result.decision = "watch"
        result.decision_reason = "no_attention_score"

    # ── 9. Exit check (if position exists) ──────────────────────
    if check_exits_for_open and position_store is not None:
        pos = position_store.get(candidate.symbol)
        if pos is not None and pos.state in (PositionState.OPEN, PositionState.UNPROTECTED):
            from src.exits import check_exits as run_exits
            position_unprotected = pos.state == PositionState.UNPROTECTED
            exit_dec = run_exits(
                pos,
                current_price=candidate.price or 0,
                risk_per_share=pos.entry_price - pos.stop_price if pos.entry_price and pos.stop_price else None,
                position_unprotected=position_unprotected,
                spread_pct=spread_pct, quote_age_seconds=quote_age_seconds,
                bars=bars, vwap=vwap, move_state=state,
                entry_setup=result.entry_signal.entry_setup.value if result.entry_signal else None,
                prior_hod=prior_hod,
                daily_loss_breached=daily_loss_breached,
                per_symbol_loss_capped=per_symbol_loss_capped,
                halt_count_today=halt_count_today,
                et_time=et_time,
            )
            if exit_dec is not None and exit_dec.should_exit:
                result.exit_decision = exit_dec
                result.decision = "exit"
                result.decision_reason = exit_dec.reason

    # ── 10. Log ─────────────────────────────────────────────────
    record = result.to_decision_record()
    if logger is not None:
        logger.write(record)

    return result


def run_pipeline_batch(
    candidates: list[Candidate],
    **kwargs,
) -> list[PipelineResult]:
    """Run the pipeline for every candidate. Returns results in attention order."""
    results = [run_pipeline(c, **kwargs) for c in candidates]
    results.sort(
        key=lambda r: r.attention_score or 0,
        reverse=True,
    )
    return results
