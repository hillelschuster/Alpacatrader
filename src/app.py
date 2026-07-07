"""
Phase 10 — Main application loop per SPEC sections 14.1-14.2.

Connects scanner, enrichment, pipeline, execution gateway, and exit
monitoring into a running paper-trading bot.  Uses a synchronous
flag-based loop with separate monitor (10 s) and scan (30 s) cadences.

Graceful shutdown via SIGINT/SIGTERM — handler sets a flag, loop exits
at the next iteration boundary, letting the current cycle finish cleanly.
"""

from __future__ import annotations

import os.path
import signal
import time
from datetime import datetime, time as dt_time, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from src.decision_pipeline import (
    MarketSnapshot,
    PipelineResult,
    _new_hod_recent_from_bars,
    _roc_pct_from_bars,
    evaluate_candidate,
    evaluate_exits,
    execute_entry,
    run_pipeline,
)
from src.paper_execution import (
    MarketSession,
    PaperExecutionGateway,
    reconcile_open_orders,
    reconcile_positions,
)
from src.pnl_ledger import PnLLedger
from src.runner import (
    compute_atr,
    compute_runner_stop,
    should_add_to_runner,
    should_promote_to_runner,
)
from src.sizing import add_sizing
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import (
    AccountRiskState,
    Candidate,
    DecisionRecord,
    ExitDecision,
    PendingOrder,
    PositionState,
    PositionStateModel,
)
from src.scanner.attention import (
    FormerRunnerStore,
    detect_themes,
    is_symbol_in_theme,
    score_attention,
)
from src.state_machine import PositionStore, transition_position


# ──────────────────────────────────────────────────────────────────
#  Graceful shutdown
# ──────────────────────────────────────────────────────────────────

_shutdown_requested: bool = False


def _current_week_id(now: Optional[datetime] = None) -> str:
    when = now or datetime.now(timezone.utc)
    iso = when.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _signal_handler(signum: int, frame: object) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def install_shutdown_handlers() -> None:
    """Register SIGINT/SIGTERM handlers for graceful shutdown."""
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


# ──────────────────────────────────────────────────────────────────
#  Main loop
# ──────────────────────────────────────────────────────────────────


class TradingApp:
    """Paper-trading main loop.

    Orchestrates scanner, enrichment, pipeline, execution, and exit
    monitoring.  All external data providers (scanner, enrichment) are
    injectable callbacks so the app is fully testable without network.

    Usage::

        app = TradingApp(
            scanner_fn=scan_finviz_candidates,
            logger=DecisionLogger("data/decisions.jsonl"),
        )
        app.run()
    """

    def __init__(
        self,
        *,
        scanner_fn: Optional[Callable[[], list[Candidate]]] = None,
        enrichment_fn: Optional[Callable[[Candidate], Candidate]] = None,
        market_data_fn: Optional[Callable[[Candidate], Optional[MarketSnapshot]]] = None,
        market_data_batch_fn: Optional[
            Callable[[list[Candidate]], dict[str, Optional[MarketSnapshot]]]
        ] = None,
        logger: Optional[DecisionLogger] = None,
        execution_gw: Optional[PaperExecutionGateway] = None,
        position_store: Optional[PositionStore] = None,
        former_runner_store: Optional[FormerRunnerStore] = None,
        broker_snapshot_fn: Optional[Callable[[], Optional[dict[str, tuple[int, float]]]]] = None,
        broker_orders_snapshot_fn: Optional[Callable[[], Optional[list[PendingOrder]]]] = None,
        # Market session (SPEC §11.7)
        market_session_fn: Optional[Callable[[], MarketSession]] = None,
        # Cadence configuration
        monitor_interval_seconds: float = 10.0,
        scan_interval_seconds: float = 30.0,
        # Session gating
        entry_cutoff_time: Optional[str] = "15:30",
        flatten_time: Optional[str] = "15:55",
        # Risk
        equity: float = 100_000.0,
        starter_risk_pct: float = 0.0025,
        max_trade_risk_pct: float = 0.01,
        max_positions: int = 3,
        max_open_risk_pct: float = 0.03,
        max_daily_loss_pct: float = 0.03,
        max_consecutive_losses: int = 3,
        weekly_drawdown_pct: float = 0.06,
        focus_price_min: float = 1.0,
        focus_price_max: float = 50.0,
        dollar_volume_min: float = 50_000.0,
        # Runner config
        runner_activation_r: float = 1.5,
        runner_atr_period: int = 5,
        runner_trail_multiplier: float = 2.5,
        # Scaling config (Phase 4)
        add_risk_pct: float = 0.0025,
        add_size_multiplier: float = 0.5,
        add_activation_r_multiple: float = 2.0,
        max_adds: int = 2,
        # Paper mode
        paper_mode: bool = True,
        # Persistence
        persist_path: Optional[str] = None,
        pnl_persist_path: Optional[str] = None,
        # Phase 6 live-readiness
        exiting_timeout_seconds: int = 120,
        order_reconcile_interval_seconds: float = 30.0,
        max_monitor_failures: int = 3,
        max_broker_order_snapshot_failures: int = 3,
    ) -> None:
        self._scanner_fn = scanner_fn or (lambda: [])
        self._enrichment_fn = enrichment_fn or (lambda c: c)
        self._market_data_fn = market_data_fn
        self._market_data_batch_fn = market_data_batch_fn
        self._logger = logger
        self._execution = execution_gw or PaperExecutionGateway()
        self._positions = position_store or self._execution.positions
        self._former_runners = (
            former_runner_store if former_runner_store is not None else FormerRunnerStore()
        )
        self._broker_snapshot_fn = broker_snapshot_fn  # None = no broker configured
        self._broker_orders_snapshot_fn = broker_orders_snapshot_fn
        self._market_session_fn = market_session_fn

        self._monitor_interval = monitor_interval_seconds
        self._scan_interval = scan_interval_seconds
        self._entry_cutoff = entry_cutoff_time
        self._flatten_time = flatten_time
        self._equity = equity
        self._starter_risk_pct = starter_risk_pct
        self._max_trade_risk_pct = max_trade_risk_pct
        self._max_positions = max_positions
        self._max_open_risk_pct = max_open_risk_pct
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_consecutive_losses = max_consecutive_losses
        self._weekly_drawdown_pct = weekly_drawdown_pct
        self._focus_price_min = focus_price_min
        self._focus_price_max = focus_price_max
        self._dollar_volume_min = dollar_volume_min
        self._runner_activation_r = runner_activation_r
        self._runner_atr_period = runner_atr_period
        self._runner_trail_multiplier = runner_trail_multiplier
        self._add_risk_pct = add_risk_pct
        self._add_size_multiplier = add_size_multiplier
        self._add_activation_r_multiple = add_activation_r_multiple
        self._max_adds = max_adds
        self._paper_mode = paper_mode
        self._persist_path = persist_path
        if pnl_persist_path is not None:
            self._pnl_persist_path = pnl_persist_path
        elif persist_path is not None:
            # Default to sibling pnl_ledger.json beside persist_path
            parent = os.path.dirname(persist_path)
            self._pnl_persist_path = os.path.join(parent, "pnl_ledger.json")
        else:
            self._pnl_persist_path = None
        self._last_pnl_checkpoint: float = 0.0
        self._exiting_timeout_seconds = exiting_timeout_seconds
        self._order_reconcile_interval = order_reconcile_interval_seconds
        self._max_monitor_failures = max_monitor_failures
        self._max_broker_order_snapshot_failures = max_broker_order_snapshot_failures
        self._outage_tracker: dict[str, float] = {}
        self._monitor_failures: dict[str, int] = {}
        self._broker_order_snapshot_failures: int = 0
        self._scanner_seen_counts: dict[str, int] = {}

        # ponytail: periodic maintenance timers
        self._last_equity_refresh: float = 0.0
        self._last_position_reconcile: float = 0.0
        self._last_position_save: float = 0.0
        self._pending_exit_orders: dict[str, str] = {}  # symbol → exit order_id (Task #21)

        self._cycle_count: int = 0
        self._started_at: Optional[datetime] = None
        self._risk_state: AccountRiskState = AccountRiskState()
        self._session_realized_pnl: float = 0.0
        self._session_per_symbol_pnl: dict[str, float] = {}
        self._weekly_realized_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._pnl_week_id: str = _current_week_id()
        self._et_time: Optional[dt_time] = None
        self._active_flatten_time: Optional[dt_time] = None
        self._last_market_session: Optional[MarketSession] = None
        self._last_market_audit_key: Optional[str] = None

    def _configured_flatten_time(self) -> dt_time:
        if self._flatten_time is None:
            return dt_time(15, 55)
        hour, minute = self._flatten_time.split(":")
        return dt_time(int(hour), int(minute))

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def is_running(self) -> bool:
        global _shutdown_requested
        return not _shutdown_requested

    def _has_emergency(self) -> bool:
        """Return True if any open position is UNPROTECTED, EXITING, or ADDING."""
        for pos in self._positions.all_open():
            if pos.state in (PositionState.UNPROTECTED, PositionState.EXITING, PositionState.ADDING):
                return True
        return False

    def _cleanup_error_positions(self) -> None:
        """Close zero-share ERROR positions so rejected-entry symbols unlock.

        ERROR positions with shares may still represent broker exposure and
        remain locked for manual/broker reconciliation.
        """
        for pos in self._positions.all_positions():
            if pos.state != PositionState.ERROR:
                continue
            if pos.current_shares != 0:
                continue
            if self._execution.pending.get_for_symbol(pos.symbol):
                continue
            transition_position(pos, PositionState.CLOSED)
            pos.current_shares = 0
            self._positions.upsert(pos)
            logger.warning(
                "ERROR cleanup: {} had zero shares and no pending orders — marked CLOSED",
                pos.symbol,
            )

    def _now_et(self) -> datetime:
        """Return current time in US/Eastern. Overrideable in tests via monkeypatch."""
        return datetime.now(ZoneInfo("US/Eastern"))

    def _is_market_open(self) -> bool:
        """Return True during US market hours.

        Uses the injectable ``market_session_fn`` (SPEC §11.7) when available,
        falling back to hardcoded Mon-Fri 9:30-16:00 ET rules.

        Updates ``_active_flatten_time`` from the market session so
        the monitor path can use it for time-based flattening.
        """
        if self._market_session_fn is not None:
            try:
                session = self._market_session_fn()
                self._last_market_session = session
                self._active_flatten_time = session.flatten_time
                return session.is_open
            except Exception:
                logger.exception("market_session_fn failed — falling back to hardcoded hours")

        # Legacy hardcoded fallback
        self._active_flatten_time = self._configured_flatten_time()
        now = self._now_et()
        if now.weekday() >= 5:  # Saturday/Sunday
            self._last_market_session = MarketSession(
                is_open=False,
                source="fallback_weekend",
                flatten_time=self._active_flatten_time,
            )
            return False
        market_open = dt_time(9, 30)
        market_close = dt_time(16, 0)
        is_open = market_open <= now.time() < market_close
        self._last_market_session = MarketSession(
            is_open=is_open,
            source="fallback_hardcoded",
            open_time=market_open,
            close_time=market_close,
            flatten_time=self._active_flatten_time,
        )
        return is_open

    def _record_market_safety_audit(self) -> None:
        """Write one decision-log audit entry for each closed market session."""
        session = self._last_market_session
        if session is None or session.is_open or self._logger is None:
            return
        key = f"{self._now_et().date()}:{session.source}:closed"
        if key == self._last_market_audit_key:
            return
        self._last_market_audit_key = key
        self._logger.write(DecisionRecord(
            symbol="__MARKET__",
            decision="audit",
            reason=f"market_closed:{session.source}",
            source=session.source,
        ))

    def request_shutdown(self) -> None:
        """Programmatic shutdown — sets the flag so the loop exits cleanly."""
        global _shutdown_requested
        _shutdown_requested = True

    # ── Startup reconciliation (SPEC §15.4) ──────────────────────

    def _reconcile_on_startup(self) -> None:
        """Reconcile broker positions vs local state at startup.

        Calls ``reconcile_positions()`` with the injectable broker
        snapshot.  Handles ALL reconciliation actions (T6.1) and
        broker-unreachable startup policy (T6.2).

        Broker-unreachable policy (T6.2):
          - If ``broker_snapshot_fn`` is None: no broker configured → skip.
          - If it raises or returns None: broker unreachable →
            mark all locally OPEN positions as UNPROTECTED for manual review.
          - Empty dict ``{}``: broker reachable with zero positions.
        """
        if self._broker_snapshot_fn is None:
            logger.info("No broker snapshot function configured — skipping reconciliation")
            return

        # Attempt broker snapshot — broker-unreachable policy (T6.2)
        try:
            broker = self._broker_snapshot_fn()
        except Exception:
            logger.exception("Broker snapshot unreachable — escalating local positions to UNPROTECTED")
            broker = None

        if broker is None:
            # Broker unreachable: mark all local OPEN positions as UNPROTECTED
            unprotected_count = 0
            for pos in self._positions.all_open():
                if pos.state == PositionState.OPEN:
                    try:
                        self._execution.mark_unprotected(pos.symbol)
                        unprotected_count += 1
                    except ValueError:
                        pass  # already terminal
            if unprotected_count > 0:
                logger.warning(
                    "Broker unreachable at startup — {} OPEN position(s) marked UNPROTECTED",
                    unprotected_count,
                )
            return

        # Broker reachable — run full reconciliation
        actions = reconcile_positions(
            broker_positions=broker,
            local_store=self._positions,
            pending_store=self._execution.pending,
        )

        for action in actions:
            action_type = action.get("action", "")
            symbol = action["symbol"]

            if action_type == "insert_protect":
                pos = self._positions.get(symbol)
                if pos is not None and pos.stop_price is not None:
                    self._execution.protect_position(
                        symbol, pos.stop_price, pos.current_shares,
                    )
                elif pos is not None:
                    logger.warning(
                        "insert_protect for %s: position has no stop_price — left UNPROTECTED", symbol
                    )

            elif action_type == "verify_stop":
                pos = self._positions.get(symbol)
                if pos is not None and pos.stop_price is not None:
                    self._execution.protect_position(
                        symbol, pos.stop_price, pos.current_shares,
                    )

            elif action_type == "update_qty_reprotect":
                qty = action.get("qty", 0)
                pos = self._positions.get(symbol)
                if pos is not None and pos.stop_price is not None and qty > 0:
                    self._execution.protect_position(symbol, pos.stop_price, qty)

            elif action_type == "update_qty_reprotect_warning":
                qty = action.get("qty", 0)
                pos = self._positions.get(symbol)
                logger.warning(
                    "Reconciliation: broker qty > local for %s — reprotecting %d shares. Reason: %s",
                    symbol, qty, action.get("reason", "unknown"),
                )
                if pos is not None and pos.stop_price is not None and qty > 0:
                    self._execution.protect_position(symbol, pos.stop_price, qty)

            elif action_type == "close_local":
                logger.info(
                    "Reconciliation: closed local position for %s — broker has no position",
                    symbol,
                )

            elif action_type == "cancel_stale_order":
                logger.info(
                    "Reconciliation: cancelled stale order %s for %s",
                    action.get("order_id", "?"), symbol,
                )

            elif action_type == "update_qty_reprotect_add_missed":
                qty = action.get("qty", 0)
                avg_entry = action.get("avg_entry")
                pos = self._positions.get(symbol)
                if pos is not None:
                    transition_position(pos, PositionState.RUNNER, force=True)
                    # Increment add_count since this was a missed ADD fill
                    pos.add_count += 1
                    pos.current_shares = qty
                    if avg_entry and avg_entry > 0:
                        pos.average_entry = avg_entry
                    self._positions.upsert(pos)
                    logger.warning(
                        "Reconciliation: missed ADD fill for %s → updated to %d shares, "
                        "add_count=%d. Reason: %s",
                        symbol, qty, pos.add_count, action.get("reason", "unknown"),
                    )
                    # Re-protect with trailing_stop_price for runner, or stop_price
                    stop = pos.trailing_stop_price or pos.stop_price
                    if stop is not None and qty > 0:
                        self._execution.protect_position(symbol, stop, qty)

            elif action_type == "irreconcilable":
                if self._paper_mode:
                    logger.warning(
                        "Reconciliation: IRRECONCILABLE mismatch for %s — paper mode "
                        "auto-resolving to CLOSED (broker qty=0, local active). "
                        "Reason: %s",
                        symbol, action.get("reason", "unknown"),
                    )
                    pos = self._positions.get(symbol)
                    if pos is not None:
                        pos.state = PositionState.CLOSED
                        pos.current_shares = 0
                        pos.updated_at = datetime.now(timezone.utc)
                        self._positions.upsert(pos)
                else:
                    logger.error(
                        "Reconciliation: IRRECONCILABLE mismatch for %s — LIVE MODE. "
                        "Reason: %s. HALTING STARTUP.",
                        symbol, action.get("reason", "unknown"),
                    )
                    raise RuntimeError(
                        f"IRRECONCILABLE: {symbol} — broker qty = 0 but local is active. "
                        "Cannot safely start live trading."
                    )

        self._reconcile_open_orders_against_broker(immediate=True)

        # ── Post-reconciliation: RUNNER-specific hardening (Task 1) ──
        for pos in self._positions.all_open():
            if pos.state == PositionState.RUNNER:
                if pos.trailing_stop_price is None:
                    # RUNNER without trailing_stop_price → escalate to UNPROTECTED
                    logger.warning(
                        "Reconciliation: RUNNER %s has no trailing_stop_price → "
                        "escalating to UNPROTECTED",
                        pos.symbol,
                    )
                    try:
                        self._execution.mark_unprotected(pos.symbol)
                    except ValueError:
                        pass
                else:
                    # Populate missing runner fields
                    changed = False
                    if pos.highest_price_seen is None:
                        pos.highest_price_seen = pos.entry_price or pos.average_entry
                        logger.warning(
                            "Reconciliation: populated missing highest_price_seen "
                            "for RUNNER %s = %s",
                            pos.symbol, pos.highest_price_seen,
                        )
                        changed = True
                    if pos.runner_since is None:
                        pos.runner_since = datetime.now(timezone.utc)
                        logger.warning(
                            "Reconciliation: populated missing runner_since for RUNNER %s",
                            pos.symbol,
                        )
                        changed = True
                    if changed:
                        self._positions.upsert(pos)
                    # Re-protect with trailing_stop_price if stop is missing
                    if (
                        pos.current_shares > 0
                        and not self._execution._has_pending_stop(pos.symbol)
                    ):
                        self._execution.protect_position(
                            pos.symbol,
                            pos.trailing_stop_price,
                            pos.current_shares,
                        )
                        logger.info(
                            "Reconciliation: re-protected RUNNER %s at trailing_stop=%.2f",
                            pos.symbol, pos.trailing_stop_price,
                        )

    def _reconcile_open_orders_against_broker(self, *, immediate: bool = False) -> None:
        """Reconcile broker open orders against local pending-order state."""
        if self._broker_orders_snapshot_fn is None:
            return

        try:
            broker_orders = self._broker_orders_snapshot_fn()
        except Exception:
            logger.exception("Broker open-order snapshot unreachable")
            broker_orders = None
        if broker_orders is None:
            self._broker_order_snapshot_failures += 1
            logger.warning(
                "Broker open-order snapshot unavailable — cannot verify pending stops"
            )
            if (
                not immediate
                and self._broker_order_snapshot_failures
                < self._max_broker_order_snapshot_failures
            ):
                return
            for pos in self._positions.all_open():
                if pos.state in (PositionState.OPEN, PositionState.RUNNER):
                    try:
                        self._execution.mark_unprotected(pos.symbol)
                    except ValueError:
                        pass
            return

        self._broker_order_snapshot_failures = 0

        order_actions = reconcile_open_orders(
            broker_orders=broker_orders,
            local_store=self._positions,
            pending_store=self._execution.pending,
        )
        for action in order_actions:
            action_type = action.get("action", "")
            symbol = action["symbol"]
            if action_type == "cancel_orphan_broker_order":
                cancelled = self._execution.cancel_order(action.get("order_id", ""))
                logger.warning(
                    "Reconciliation: orphan broker order %s for %s cancel_requested=%s",
                    action.get("order_id", "?"), symbol, cancelled,
                )
            elif action_type == "missing_broker_stop":
                logger.warning(
                    "Reconciliation: broker stop missing for %s — marked UNPROTECTED",
                    symbol,
                )
            elif action_type == "import_broker_order":
                logger.info(
                    "Reconciliation: imported broker open order %s for %s",
                    action.get("order_id", "?"), symbol,
                )
            elif action_type == "drop_local_missing_broker_order":
                logger.warning(
                    "Reconciliation: dropped local pending order %s for %s — missing at broker",
                    action.get("order_id", "?"), symbol,
                )

    def _record_monitor_failure(self, symbol: str) -> bool:
        """Track monitor exceptions; escalate after configured consecutive failures."""
        count = self._monitor_failures.get(symbol, 0) + 1
        self._monitor_failures[symbol] = count
        if count < self._max_monitor_failures:
            return False

        try:
            pos = self._execution.mark_unprotected(symbol)
        except ValueError:
            return False

        logger.warning(
            "Monitor failed {} consecutive times for {} — marked UNPROTECTED",
            count, symbol,
        )
        if self._logger is not None:
            self._logger.write(DecisionRecord(
                symbol=symbol,
                decision="audit",
                reason="monitor_failure_escalated_unprotected",
                state=pos.state.value,
            ))
        return True

    def _clear_monitor_failure(self, symbol: str) -> None:
        self._monitor_failures.pop(symbol, None)

    # ── Account risk state (SPEC §13.5) ──────────────────────────

    def _build_risk_state(self) -> AccountRiskState:
        """Derive ``AccountRiskState`` from position store + session ledger.

        Realized P&L from closed positions is accumulated in
        ``_session_realized_pnl`` across the session.  Open positions
        are marked to market when fresh prices are available.
        """
        open_positions = self._positions.all_open()
        total_risk = 0.0
        unrealized = 0.0
        per_symbol: dict[str, float] = dict(self._session_per_symbol_pnl)

        for pos in open_positions:
            if (
                pos.average_entry is not None
                and pos.stop_price is not None
                and pos.current_shares > 0
            ):
                total_risk += max(
                    0.0, (pos.average_entry - pos.stop_price) * pos.current_shares
                )
            up = pos.unrealized_pnl or 0.0
            unrealized += up
            per_symbol[pos.symbol] = round(
                per_symbol.get(pos.symbol, 0.0) + min(0.0, up), 2
            )

        realized = self._session_realized_pnl + sum(
            pos.realized_pnl or 0.0 for pos in open_positions
        )
        daily_pnl_val = realized + unrealized
        daily_loss_breached = (
            daily_pnl_val < -self._equity * self._max_daily_loss_pct
        )
        kill_switch_active = False
        kill_switch_reason = ""
        if (
            self._max_consecutive_losses > 0
            and self._consecutive_losses >= self._max_consecutive_losses
        ):
            kill_switch_active = True
            kill_switch_reason = "consecutive_loss_throttle"
        elif (
            self._weekly_drawdown_pct > 0
            and self._weekly_realized_pnl <= -self._equity * self._weekly_drawdown_pct
        ):
            kill_switch_active = True
            kill_switch_reason = "weekly_drawdown_throttle"
        # Per-symbol loss cap: check each symbol's accumulated loss
        per_symbol_cap = self._max_daily_loss_pct * self._equity  # reuse daily cap per symbol
        per_symbol_loss_capped: dict[str, bool] = {
            sym: abs(loss) >= per_symbol_cap
            for sym, loss in per_symbol.items()
            if loss < 0
        }

        return AccountRiskState(
            daily_realized_pnl=round(realized, 2),
            daily_unrealized_pnl=round(unrealized, 2),
            total_open_risk=round(total_risk, 2),
            open_position_count=len(open_positions),
            per_symbol_daily_loss=per_symbol,
            theme_exposure={},
            kill_switch_active=kill_switch_active,
            daily_loss_breached=daily_loss_breached,
            kill_switch_reason=kill_switch_reason,
        )

    def _record_realized_trade_pnl(self, symbol: str, realized: float) -> None:
        """Update session and roadmap #11 throttle state after a realized exit."""
        self._session_realized_pnl += realized
        self._weekly_realized_pnl += realized
        self._session_per_symbol_pnl[symbol] = round(
            self._session_per_symbol_pnl.get(symbol, 0.0) + realized, 2
        )
        if realized < 0:
            self._consecutive_losses += 1
        elif realized > 0:
            self._consecutive_losses = 0

    def run(self) -> None:
        """Start the main trading loop.  Blocks until shutdown requested."""
        install_shutdown_handlers()
        self._started_at = datetime.now(timezone.utc)

        # ══ Restore persisted position state (T6.3) ════════════════
        if self._persist_path:
            try:
                restored = PositionStore.load_from_disk(self._persist_path)
                if len(restored) > 0:
                    # Merge restored positions into current store
                    for pos in restored.all_positions():
                        if pos.state not in (PositionState.NONE, PositionState.CLOSED):
                            self._positions.upsert(pos)
                    logger.info(
                        "Restored {} position(s) from {}",
                        len(restored), self._persist_path,
                    )
            except Exception:
                logger.exception("Failed to restore position state from %s", self._persist_path)

        # ══ Restore P&L ledger (roadmap #2) ═════════════════════
        self._restore_pnl_ledger()

        # ══ Startup reconciliation ═══════════════════════════════
        self._reconcile_on_startup()

        last_position_check = time.monotonic()
        last_scan = time.monotonic()
        last_order_reconcile = time.monotonic()

        while self.is_running:
            now = time.monotonic()
            now_et = self._now_et()
            self._et_time = now_et.time()
            market_open = self._is_market_open()
            if not market_open:
                self._record_market_safety_audit()

            # ── Off-hours backoff (SPEC §15.3) ─────────────────────
            # When market is closed AND no open positions to monitor,
            # sleep on a bounded backoff instead of spinning every 1s.
            # Positions still get monitored when they exist (fall through).
            if not market_open and not self._positions.all_open():
                time.sleep(60.0)
                continue

            # ── Position monitoring (every cycle) ──────────────
            if now - last_position_check >= self._monitor_interval:
                self._monitor_positions()
                last_position_check = now

            # ── Broker open-order reconciliation (periodic) ────────
            if now - last_order_reconcile >= self._order_reconcile_interval:
                self._reconcile_open_orders_against_broker()
                last_order_reconcile = now

            # ── Scan (on cadence, suppressed during emergencies / off-hours) ─
            if now - last_scan >= self._scan_interval:
                if self._has_emergency():
                    logger.warning("Scan suppressed — unresolved emergency positions exist")
                elif not market_open:
                    logger.debug("Scan suppressed — market closed")
                else:
                    self._scan_and_process()
                last_scan = now

            # ── Periodic P&L checkpoint (~45s, best-effort) ──
            if self._pnl_persist_path and now - self._last_pnl_checkpoint >= 45.0:
                self._checkpoint_pnl_ledger()
                self._last_pnl_checkpoint = now

            # ── Periodic equity refresh (~60s) — Task #38d ──
            if now - self._last_equity_refresh >= 60.0:
                try:
                    fresh = self._execution.get_account_equity()
                    if fresh and fresh > 0:
                        self._equity = fresh
                except Exception:
                    logger.debug("Equity refresh failed — using stale value")
                self._last_equity_refresh = now

            # ── Periodic position state save (~30s) — Task #34 ──
            if self._persist_path and now - self._last_position_save >= 30.0:
                try:
                    self._positions.save_to_disk(self._persist_path)
                except Exception:
                    logger.warning("Periodic position save failed")
                self._last_position_save = now

            # ── Periodic position reconciliation (~60s) — Task #25 ──
            if now - self._last_position_reconcile >= 60.0:
                self._reconcile_positions_periodic()
                self._last_position_reconcile = now

            self._cycle_count += 1
            time.sleep(1.0)  # 1-second tick granularity

        self._shutdown()

    def _monitor_positions(self) -> None:
        """Check exits for all open/locked positions using fresh market data.

        SPEC §6.4 data-unavailable policy:
          - Protected + verified stop + transient outage = hold/retry/log
          - Unprotected or missing stop = escalate to UNPROTECTED
          - Never fabricate price=0.0 or fake P&L
        """
        self._cleanup_error_positions()
        self._risk_state = self._build_risk_state()

        for pos in self._positions.all_open():
            # Try to get fresh market data for this position
            snapshot = None
            if self._market_data_fn:
                try:
                    temp = Candidate(symbol=pos.symbol, price=pos.average_entry or 0)
                    snapshot = self._market_data_fn(temp)
                except Exception:
                    logger.exception("Market-data fetch failed for %s during monitor", pos.symbol)
                    if self._record_monitor_failure(pos.symbol):
                        continue
                    snapshot = None

            current_price: Optional[float] = None

            # ── Data-unavailable policy (SPEC §6.4, §10.3) ──────────
            if snapshot is None:
                if self._execution._has_pending_stop(pos.symbol):
                    if pos.state == PositionState.RUNNER:
                        # Track outage duration for protected RUNNER positions (Task 3)
                        if pos.symbol not in self._outage_tracker:
                            self._outage_tracker[pos.symbol] = time.monotonic()
                        outage_elapsed = time.monotonic() - self._outage_tracker[pos.symbol]
                        if outage_elapsed > self._exiting_timeout_seconds:
                            logger.warning(
                                "Data outage for protected RUNNER {} exceeded {:.0f}s "
                                "— escalating to UNPROTECTED",
                                pos.symbol, self._exiting_timeout_seconds,
                            )
                            try:
                                self._execution.mark_unprotected(pos.symbol)
                                self._outage_tracker.pop(pos.symbol, None)
                            except ValueError:
                                pass
                            # Fall through to pipeline so exit engine can handle
                        else:
                            logger.warning(
                                "Data unavailable for {} (protected RUNNER) — "
                                "holding position, retry next cycle ({:.0f}s of {:.0f}s)",
                                pos.symbol, outage_elapsed,
                                self._exiting_timeout_seconds,
                            )
                            continue
                    else:
                        logger.warning(
                            "Data unavailable for {} (protected) — holding position, "
                            "retry next cycle",
                            pos.symbol,
                        )
                        continue  # skip this position, retry on next monitor cycle
                else:
                    logger.warning(
                        "Data unavailable for {} (unprotected) — escalating to UNPROTECTED",
                        pos.symbol,
                    )
                    try:
                        self._execution.mark_unprotected(pos.symbol)
                    except ValueError:
                        pass  # already in a terminal state
                    # Fall through to pipeline so the exit engine can handle P4
            else:
                # Clear outage tracker when data is available again
                self._outage_tracker.pop(pos.symbol, None)
                self._clear_monitor_failure(pos.symbol)
                if snapshot.candidate.price is not None and snapshot.candidate.price > 0:
                    current_price = snapshot.candidate.price

            candidate = Candidate(symbol=pos.symbol, price=current_price)

            try:
                # Steps 1-6: pure analysis (gets move_state for exit engine)
                result = evaluate_candidate(
                    candidate,
                    bars=snapshot.bars if snapshot else None,
                    vwap=snapshot.vwap if snapshot else None,
                    ema9=snapshot.ema9 if snapshot else None,
                    day_high=snapshot.day_high if snapshot else None,
                    prior_hod=snapshot.prior_hod if snapshot else None,
                    quote_age_seconds=snapshot.quote_age_seconds if snapshot else None,
                    spread_pct=snapshot.spread_pct if snapshot else None,
                    rvol=snapshot.rvol if snapshot else None,
                    halt_count_today=snapshot.halt_count_today if snapshot else 0,
                    execution_gw=self._execution,
                    equity=self._equity,
                    max_positions=self._max_positions,
                    max_open_risk_pct=self._max_open_risk_pct,
                    focus_price_min=self._focus_price_min,
                    focus_price_max=self._focus_price_max,
                    dollar_volume_min=self._dollar_volume_min,
                    account=self._risk_state,
                    per_symbol_loss_capped=self._risk_state.per_symbol_daily_loss.get(pos.symbol, 0) < 0 and abs(self._risk_state.per_symbol_daily_loss.get(pos.symbol, 0)) >= self._max_daily_loss_pct * self._equity,
                    et_time=self._et_time,
                )

                # Step 9: exit check (monitor path — no entry submission)
                exit_dec: Optional[ExitDecision] = None
                pos_current = self._positions.get(pos.symbol)
                bars = snapshot.bars if snapshot else None
                runner_bars = (
                    snapshot.five_min_bars
                    if snapshot and snapshot.five_min_bars
                    else bars
                )
                runner_atr = compute_atr(runner_bars, self._runner_atr_period) if runner_bars else None
                if pos_current is not None and pos_current.state in (
                    PositionState.OPEN, PositionState.RUNNER,
                    PositionState.UNPROTECTED, PositionState.EXITING,
                ):
                    # EXITING timeout recovery: escalate stale EXITING → UNPROTECTED
                    if pos_current.state == PositionState.EXITING:
                        elapsed = (datetime.now(timezone.utc) - pos_current.updated_at).total_seconds()
                        if elapsed > self._exiting_timeout_seconds:
                            try:
                                self._execution.mark_unprotected(pos_current.symbol)
                                pos_current = self._positions.get(pos.symbol)
                            except ValueError:
                                pass
                            logger.warning(
                                "EXITING timeout for {} ({:.0f}s) → escalated to UNPROTECTED",
                                pos.symbol, elapsed,
                            )
                        else:
                            pos_current = None  # within timeout — skip exit checks
                    if pos_current is not None and pos_current.state != PositionState.EXITING:
                        risk_per_share = pos_current.original_risk_per_share or (
                            abs(pos_current.entry_price - pos_current.stop_price)
                            if pos_current.entry_price and pos_current.stop_price else None
                        )
                        if pos_current.state == PositionState.RUNNER:
                            self._update_runner_trail(
                                pos_current,
                                current_price=current_price,
                                atr=runner_atr,
                                risk_per_share=risk_per_share,
                            )
                        position_unprotected = (
                            pos_current.state == PositionState.UNPROTECTED
                            or not self._execution._has_pending_stop(pos_current.symbol)
                        )
                        exit_dec = evaluate_exits(
                            pos_current,
                            current_price=current_price,
                            risk_per_share=risk_per_share,
                            position_unprotected=position_unprotected,
                            spread_pct=snapshot.spread_pct if snapshot else None,
                            quote_age_seconds=snapshot.quote_age_seconds if snapshot else None,
                            bars=bars,
                            vwap=snapshot.vwap if snapshot else None,
                            move_state=result.move_state,
                            entry_setup=None,
                            prior_hod=snapshot.prior_hod if snapshot else None,
                            daily_loss_breached=self._risk_state.daily_loss_breached,
                            per_symbol_loss_capped=self._risk_state.per_symbol_daily_loss.get(pos.symbol, 0) < 0 and abs(self._risk_state.per_symbol_daily_loss.get(pos.symbol, 0)) >= self._max_daily_loss_pct * self._equity,
                            halt_count_today=snapshot.halt_count_today if snapshot else 0,
                            et_time=self._et_time,
                            flatten_time=self._active_flatten_time,
                            highest_price_seen=pos_current.highest_price_seen,
                            atr=runner_atr,
                            trail_multiplier=self._runner_trail_multiplier,
                        )

                if (
                    exit_dec is None
                    and pos_current is not None
                    and pos_current.state == PositionState.OPEN
                    and should_promote_to_runner(
                        pos_current,
                        bars=runner_bars,
                        current_price=current_price,
                        vwap=snapshot.vwap if snapshot else None,
                        move_state=result.move_state,
                        activation_r_multiple=self._runner_activation_r,
                    )
                ):
                    self._promote_to_runner(
                        pos_current,
                        current_price=current_price,
                        atr=runner_atr,
                    )
                    result.decision = "watch"
                    result.decision_reason = "promoted_to_runner"

                # ── Phase 4: scaling-in add check (RUNNER positions) ──
                _add_attempted = False
                if (
                    exit_dec is None
                    and pos_current is not None
                    and current_price is not None
                    and pos_current.state == PositionState.RUNNER
                    and bars is not None
                ):
                    add_signal = should_add_to_runner(
                        pos_current,
                        bars=bars,
                        current_price=current_price,
                        vwap=snapshot.vwap if snapshot else None,
                        move_state=result.move_state,
                        activation_r_multiple=self._add_activation_r_multiple,
                        max_adds=self._max_adds,
                        risk_per_share=risk_per_share,
                    )
                    if add_signal is not None:
                        add_shares, _, _ = add_sizing(
                            equity=self._equity,
                            add_risk_pct=self._add_risk_pct,
                            add_count=pos_current.add_count,
                            risk_per_share_at_add=add_signal.risk_per_share,
                            max_open_risk_pct=self._max_open_risk_pct,
                            total_open_risk=self._risk_state.total_open_risk,
                            add_size_multiplier=self._add_size_multiplier,
                        )
                        if add_shares > 0:
                            _add_attempted = True
                            add_stop = max(
                                add_signal.entry_price,
                                pos_current.entry_price or 0,
                            )

                            try:
                                add_order, _ = self._execution.submit_add(
                                    pos_current.symbol,
                                    qty=add_shares,
                                    entry_price=add_signal.entry_price,
                                    stop_price=add_stop,
                                )
                                # Log add_submitted after successful submission
                                result.decision = "watch"
                                result.decision_reason = "add_submitted"
                                if self._logger is not None:
                                    self._logger.write(result.to_decision_record())

                                filled_state = self._execution.confirm_fill(add_order.order_id)
                                # Log add_filled or add_pending based on resulting state
                                if filled_state.state == PositionState.RUNNER:
                                    result.decision_reason = "add_filled"
                                else:
                                    result.decision_reason = "add_pending"
                                result.decision = "watch"
                                if self._logger is not None:
                                    self._logger.write(result.to_decision_record())
                            except Exception:
                                logger.exception(
                                    "Add failed for %s — returning to RUNNER",
                                    pos_current.symbol,
                                )
                                failed_pos = self._positions.get(pos_current.symbol)
                                if failed_pos is not None:
                                    transition_position(
                                        failed_pos, PositionState.RUNNER, force=True,
                                    )
                                    self._positions.upsert(failed_pos)
                                    # Ensure position has a verified stop after failure
                                    if (
                                        failed_pos.stop_price is not None
                                        and failed_pos.current_shares > 0
                                        and not self._execution._has_pending_stop(failed_pos.symbol)
                                    ):
                                        try:
                                            self._execution.protect_position(
                                                failed_pos.symbol,
                                                failed_pos.stop_price,
                                                failed_pos.current_shares,
                                            )
                                        except Exception:
                                            logger.exception(
                                                "Failed to restore stop for %s after add failure",
                                                failed_pos.symbol,
                                            )
                                            try:
                                                self._execution.mark_unprotected(failed_pos.symbol)
                                            except ValueError:
                                                pass
                                result.decision = "watch"
                                result.decision_reason = "add_failed"
                                if self._logger is not None:
                                    self._logger.write(result.to_decision_record())

                if _add_attempted:
                    # Skip generic watch log and exit path for this position
                    # since we already logged add_submitted / add_filled / add_failed.
                    continue

                # Log decision record
                if exit_dec is not None and exit_dec.should_exit:
                    result.exit_decision = exit_dec
                    # Task 2: atr_trail_hit gets dedicated trail_exit decision
                    if "atr_trail_hit" in (exit_dec.reason or ""):
                        result.decision = "trail_exit"
                    else:
                        result.decision = "exit"
                    result.decision_reason = exit_dec.reason
                if self._logger is not None:
                    self._logger.write(result.to_decision_record())

                # Execute exit if pipeline detected one
                if result.exit_decision is not None and result.exit_decision.should_exit:
                    try:
                        shares_before_exit = pos.current_shares
                        order, pos_after = self._execution.submit_exit(
                            pos.symbol,
                            reason=result.exit_decision.reason,
                            exit_pct=result.exit_decision.exit_pct,
                            exit_price=current_price,
                        )
                        pos_after = self._execution.confirm_exit_fill(order.order_id)
                        filled_qty = max(shares_before_exit - pos_after.current_shares, 0)
                        if result.decision == "trail_exit" and filled_qty > 0:
                            self._former_runners.mark(pos.symbol)
                        # Accumulate realized P&L from confirmed execution fill, not quote snapshot.
                        if filled_qty > 0 and (pos_after.realized_pnl or current_price is not None):
                            realized = pos_after.realized_pnl or 0.0
                            self._record_realized_trade_pnl(pos.symbol, realized)
                    except Exception:
                        logger.exception(
                            "Exit execution failed for %s — position may remain OPEN",
                            pos.symbol,
                        )
                else:
                    # Mark-to-market: update unrealized P&L for open positions (T4.5)
                    if (
                        pos.average_entry
                        and pos.current_shares > 0
                        and current_price is not None
                        and current_price > 0
                    ):
                        pos.unrealized_pnl = (current_price - pos.average_entry) * pos.current_shares
            except Exception:
                logger.exception("Pipeline error in position monitor for %s", pos.symbol)
                self._record_monitor_failure(pos.symbol)
                continue

    def _update_runner_trail(
        self,
        pos: PositionStateModel,
        *,
        current_price: Optional[float],
        atr: Optional[float],
        risk_per_share: Optional[float],
    ) -> None:
        """Ratchet a RUNNER position's high-water mark and ATR stop.

        Only syncs to broker when the computed stop actually changes,
        avoiding unnecessary cancel/re-place churn.
        """
        if current_price is None or current_price <= 0:
            return
        previous_high = pos.highest_price_seen or current_price
        pos.highest_price_seen = max(previous_high, current_price)
        previous_stop = pos.trailing_stop_price
        pos.trailing_stop_price = compute_runner_stop(
            pos.highest_price_seen,
            atr,
            multiplier=self._runner_trail_multiplier,
            current_stop=pos.trailing_stop_price or pos.stop_price,
            original_risk=risk_per_share,
        )
        self._positions.upsert(pos)
        if pos.trailing_stop_price != previous_stop:
            self._sync_runner_stop_to_broker(pos)
        logger.debug(
            "trail_updated symbol={} high={} atr={} stop={}",
            pos.symbol, pos.highest_price_seen, atr, pos.trailing_stop_price,
        )

    def _promote_to_runner(
        self,
        pos: PositionStateModel,
        *,
        current_price: Optional[float],
        atr: Optional[float],
    ) -> None:
        """Transition OPEN → RUNNER and initialize runner trail fields."""
        if current_price is None or current_price <= 0:
            return
        risk_per_share = pos.original_risk_per_share or (
            abs(pos.entry_price - pos.stop_price)
            if pos.entry_price and pos.stop_price else None
        )
        pos.highest_price_seen = max(pos.highest_price_seen or current_price, current_price)
        pos.trailing_stop_price = compute_runner_stop(
            pos.highest_price_seen,
            atr,
            multiplier=self._runner_trail_multiplier,
            current_stop=pos.trailing_stop_price or pos.stop_price,
            original_risk=risk_per_share,
        )
        transition_position(pos, PositionState.RUNNER)
        pos.runner_since = datetime.now(timezone.utc)
        self._positions.upsert(pos)
        logger.info(
            "promoted_to_runner symbol={} high={} atr={} stop={}",
            pos.symbol, pos.highest_price_seen, atr, pos.trailing_stop_price,
        )
        self._sync_runner_stop_to_broker(pos)

    # ── Broker stop sync (SPEC §11.17.13 roadmap #1) ──────────────

    def _sync_runner_stop_to_broker(self, pos: PositionStateModel) -> None:
        """Cancel stale orders for symbol, then place new stop at trailing price.

        On failure, conservatively mark the position UNPROTECTED so emergency
        handling or manual review can catch it.  Never raises.
        Called after every trail update and on promotion-to-runner.
        """
        symbol = pos.symbol
        stop_price = pos.trailing_stop_price
        qty = pos.current_shares
        if stop_price is None or qty <= 0:
            return
        try:
            self._execution.cancel_stale_orders(symbol)
            self._execution.protect_position(symbol, stop_price, qty)
            logger.debug(
                "runner_stop_synced symbol={} stop={} qty={}",
                symbol, stop_price, qty,
            )
        except Exception:
            logger.warning(
                "Runner stop sync failed for {} — marking UNPROTECTED", symbol,
            )
            try:
                transition_position(pos, PositionState.UNPROTECTED, force=True)
                self._positions.upsert(pos)
            except Exception:
                logger.exception(
                    "Failed to mark {} UNPROTECTED after stop sync failure", symbol,
                )

    def _scan_and_process(self) -> None:
        """Run scanner, enrich candidates, run pipeline for each."""
        try:
            raw = self._scanner_fn()
        except Exception:
            logger.exception("Scanner failure — retrying next cycle")
            return

        candidates = [self._enrichment_fn(c) for c in raw]

        seen_symbols = {c.symbol for c in candidates}
        for symbol in seen_symbols:
            self._scanner_seen_counts[symbol] = self._scanner_seen_counts.get(symbol, 0) + 1
        for symbol in list(self._scanner_seen_counts):
            if symbol not in seen_symbols:
                del self._scanner_seen_counts[symbol]

        # Score and rank candidates by attention before processing (T5.3)
        from src.scanner.attention import score_candidates
        ranked = score_candidates(
            candidates,
            former_runner_store=self._former_runners,
            scanner_seen_counts=self._scanner_seen_counts,
        )
        candidates = [c for c, _ in ranked]

        # Build risk state from current positions before processing
        self._risk_state = self._build_risk_state()

        batch_snapshots: dict[str, Optional[MarketSnapshot]] = {}
        if self._market_data_batch_fn:
            batch_candidates = [
                c for c in candidates
                if not self._execution.is_symbol_locked(c.symbol)
            ]
            try:
                batch_snapshots = self._market_data_batch_fn(batch_candidates) or {}
            except Exception:
                logger.exception("Batch market-data failure — explicit empty snapshots this cycle")
                batch_snapshots = {c.symbol: None for c in batch_candidates}

            themes = detect_themes(candidates)

            def _snapshot_attention_score(c: Candidate) -> float:
                snapshot = batch_snapshots.get(c.symbol) or MarketSnapshot(candidate=c)
                scored_candidate = snapshot.candidate
                hod_price = (
                    snapshot.day_high
                    if snapshot.day_high is not None
                    else scored_candidate.day_high
                )
                score = score_attention(
                    scored_candidate,
                    rvol=snapshot.rvol,
                    dollar_volume_5m=snapshot.dollar_volume_5m,
                    hod_price=hod_price,
                    roc_1m_pct=_roc_pct_from_bars(snapshot.bars, 1),
                    roc_3m_pct=_roc_pct_from_bars(snapshot.bars, 3),
                    roc_5m_pct=_roc_pct_from_bars(snapshot.bars, 5),
                    new_hod_recent=_new_hod_recent_from_bars(snapshot.bars, hod_price),
                    theme_active=is_symbol_in_theme(scored_candidate, themes),
                    former_runner=self._former_runners.is_runner(scored_candidate.symbol),
                    scanner_seen_count=self._scanner_seen_counts.get(scored_candidate.symbol),
                )
                return score.score

            candidates.sort(key=_snapshot_attention_score, reverse=True)

        cycle_enters = 0
        cycle_watches = 0
        cycle_skips = 0

        for c in candidates:
            if not self.is_running:
                break

            # Skip locked symbols
            if self._execution.is_symbol_locked(c.symbol):
                continue

            # Per-symbol daily loss cap: block re-entry on a symbol that
            # already hit its per-symbol loss cap for the session.
            symbol_loss = self._risk_state.per_symbol_daily_loss.get(c.symbol, 0.0)
            per_symbol_loss_capped = (
                symbol_loss < 0
                and abs(symbol_loss) >= self._max_daily_loss_pct * self._equity
            )

            # Build market data snapshot (bars, quote, etc.)
            if self._market_data_batch_fn:
                snapshot = batch_snapshots.get(c.symbol)
                if snapshot is None:
                    snapshot = MarketSnapshot(candidate=c)
            elif self._market_data_fn:
                snapshot = self._market_data_fn(c)
                if snapshot is None:
                    snapshot = MarketSnapshot(candidate=c)
            else:
                snapshot = MarketSnapshot(candidate=c)

            # Validate snapshot before running the pipeline (SPEC §6).
            # Missing price/quote/spread → surface as hard blocks so the
            # decision record carries the exact missing field.
            snap_valid, snap_missing = snapshot.validate_for_entry()

            try:
                # Steps 1-6: pure analysis
                result = evaluate_candidate(
                    snapshot.candidate,
                    bars=snapshot.bars,
                    vwap=snapshot.vwap,
                    ema9=snapshot.ema9,
                    day_high=snapshot.day_high,
                    prior_hod=snapshot.prior_hod,
                    quote_age_seconds=snapshot.quote_age_seconds,
                    spread_pct=snapshot.spread_pct,
                    rvol=snapshot.rvol,
                    dollar_volume_5m=snapshot.dollar_volume_5m,
                    daily_volume=snapshot.daily_volume,
                    scanner_seen_count=self._scanner_seen_counts.get(c.symbol),
                    halt_count_today=snapshot.halt_count_today,
                    former_runner_store=self._former_runners,
                    execution_gw=self._execution,
                    equity=self._equity,
                    max_positions=self._max_positions,
                    max_open_risk_pct=self._max_open_risk_pct,
                    focus_price_min=self._focus_price_min,
                    focus_price_max=self._focus_price_max,
                    dollar_volume_min=self._dollar_volume_min,
                    account=self._risk_state,
                    per_symbol_loss_capped=per_symbol_loss_capped,
                    et_time=self._et_time,
                    snapshot_missing=snap_missing if not snap_valid else None,
                )

                # Steps 7-8: sizing + order submission
                if result.entry_signal is not None:
                    result = execute_entry(
                        result,
                        execution_gw=self._execution,
                        equity=self._equity,
                        starter_risk_pct=self._starter_risk_pct,
                        max_trade_risk_pct=self._max_trade_risk_pct,
                        pre_submit_quote_fn=self._market_data_fn,
                    )

                # Step 10: Log
                if self._logger is not None:
                    self._logger.write(result.to_decision_record())

                if result.decision == "enter":
                    cycle_enters += 1
                    # ponytail: rebuild risk state so next candidate sees updated position count
                    self._risk_state = self._build_risk_state()
                    logger.info(">>> ENTRY: {} {} {}sh @ ${:.2f} stop=${:.2f}",
                        result.symbol, result.entry_signal.entry_setup.value if result.entry_signal else "?",
                        result.entry_shares,
                        result.entry_signal.entry_price if result.entry_signal else 0,
                        result.entry_signal.stop_price if result.entry_signal else 0,
                    )
                elif result.decision == "watch":
                    cycle_watches += 1
                elif result.decision == "skip":
                    cycle_skips += 1

            except Exception:
                logger.exception("Pipeline error in scan for %s", c.symbol)
                continue

        # Cycle summary
        if cycle_enters or cycle_watches or cycle_skips:
            logger.info("Cycle #{}: enter={} watch={} skip={} | positions={}",
                self._cycle_count, cycle_enters, cycle_watches, cycle_skips,
                len(self._positions.all_open()),
            )

    # ── P&L ledger persistence (SPEC §11.17.13 roadmap #2) ────────

    def _restore_pnl_ledger(self) -> None:
        """Restore P&L ledger from disk on startup."""
        if not self._pnl_persist_path:
            return
        try:
            ledger = PnLLedger.load_from_disk(self._pnl_persist_path)
            self._session_realized_pnl = ledger.session_realized_pnl
            self._session_per_symbol_pnl = ledger.per_symbol_pnl
            current_week = _current_week_id()
            self._pnl_week_id = current_week
            if ledger.week_id in ("", current_week):
                self._weekly_realized_pnl = ledger.weekly_realized_pnl
                self._consecutive_losses = ledger.consecutive_losses
            else:
                self._weekly_realized_pnl = 0.0
                self._consecutive_losses = 0
            logger.info(
                "Restored P&L ledger from {}: realized={}",
                self._pnl_persist_path,
                ledger.session_realized_pnl,
            )
        except Exception:
            logger.exception(
                "Failed to restore P&L ledger from %s", self._pnl_persist_path,
            )

    def _checkpoint_pnl_ledger(self) -> None:
        """Save P&L ledger to disk.  Best-effort — never crashes the loop."""
        if not self._pnl_persist_path:
            return
        try:
            PnLLedger(
                session_realized_pnl=self._session_realized_pnl,
                per_symbol_pnl=self._session_per_symbol_pnl,
                weekly_realized_pnl=self._weekly_realized_pnl,
                consecutive_losses=self._consecutive_losses,
                week_id=self._pnl_week_id,
            ).save_to_disk(self._pnl_persist_path)
        except Exception:
            logger.warning("P&L checkpoint failed — continuing")

    def _shutdown(self) -> None:
        """Clean shutdown — persist state, reconcile, close resources."""
        # Persist P&L ledger (roadmap #2)
        self._checkpoint_pnl_ledger()
        # Persist position state (T6.3)
        if self._persist_path:
            try:
                self._positions.save_to_disk(self._persist_path)
                logger.info(
                    "Persisted {} position(s) to {}",
                    len(self._positions), self._persist_path,
                )
            except Exception:
                logger.exception("Failed to persist position state to %s", self._persist_path)

        # Future: reconcile broker state, log shutdown event via self._logger.
