"""
Phase 10 — Main application loop per SPEC sections 14.1-14.2.

Connects scanner, enrichment, pipeline, execution gateway, and exit
monitoring into a running paper-trading bot.  Uses a synchronous
flag-based loop with separate monitor (10 s) and scan (30 s) cadences.

Graceful shutdown via SIGINT/SIGTERM — handler sets a flag, loop exits
at the next iteration boundary, letting the current cycle finish cleanly.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime, time as dt_time, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from src.decision_pipeline import MarketSnapshot, PipelineResult, run_pipeline
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import AccountRiskState, Candidate, PositionState
from src.paper_execution import PaperExecutionGateway, reconcile_positions
from src.scanner.attention import FormerRunnerStore
from src.state_machine import PositionStore


# ──────────────────────────────────────────────────────────────────
#  Graceful shutdown
# ──────────────────────────────────────────────────────────────────

_shutdown_requested: bool = False


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
        logger: Optional[DecisionLogger] = None,
        execution_gw: Optional[PaperExecutionGateway] = None,
        position_store: Optional[PositionStore] = None,
        former_runner_store: Optional[FormerRunnerStore] = None,
        broker_snapshot_fn: Optional[Callable[[], Optional[dict[str, tuple[int, float]]]]] = None,
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
        focus_price_min: float = 1.0,
        focus_price_max: float = 50.0,
        # Paper mode
        paper_mode: bool = True,
        # Persistence
        persist_path: Optional[str] = None,
    ) -> None:
        self._scanner_fn = scanner_fn or (lambda: [])
        self._enrichment_fn = enrichment_fn or (lambda c: c)
        self._market_data_fn = market_data_fn
        self._logger = logger
        self._execution = execution_gw or PaperExecutionGateway()
        self._positions = position_store or self._execution.positions
        self._former_runners = former_runner_store or FormerRunnerStore()
        self._broker_snapshot_fn = broker_snapshot_fn  # None = no broker configured

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
        self._focus_price_min = focus_price_min
        self._focus_price_max = focus_price_max
        self._paper_mode = paper_mode
        self._persist_path = persist_path

        self._cycle_count: int = 0
        self._started_at: Optional[datetime] = None
        self._risk_state: AccountRiskState = AccountRiskState()
        self._session_realized_pnl: float = 0.0
        self._session_per_symbol_pnl: dict[str, float] = {}
        self._et_time: Optional[dt_time] = None

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def is_running(self) -> bool:
        global _shutdown_requested
        return not _shutdown_requested

    def _has_emergency(self) -> bool:
        """Return True if any open position is UNPROTECTED or EXITING."""
        for pos in self._positions.all_open():
            if pos.state in (PositionState.UNPROTECTED, PositionState.EXITING):
                return True
        return False

    def _now_et(self) -> datetime:
        """Return current time in US/Eastern. Overrideable in tests via monkeypatch."""
        return datetime.now(ZoneInfo("US/Eastern"))

    def _is_market_open(self) -> bool:
        """Return True during regular US market hours (Mon-Fri, 9:30-16:00 ET)."""
        now = self._now_et()
        if now.weekday() >= 5:  # Saturday/Sunday
            return False
        market_open = dt_time(9, 30)
        market_close = dt_time(16, 0)
        return market_open <= now.time() < market_close

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

            elif action_type == "irreconcilable":
                logger.error(
                    "Reconciliation: IRRECONCILABLE mismatch for %s — reason: %s. ESCALATE.",
                    symbol, action.get("reason", "unknown"),
                )

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
            kill_switch_active=False,
            daily_loss_breached=daily_loss_breached,
        )

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

        # ══ Startup reconciliation ═══════════════════════════════
        self._reconcile_on_startup()

        last_position_check = time.monotonic()
        last_scan = time.monotonic()

        while self.is_running:
            now = time.monotonic()
            now_et = self._now_et()
            self._et_time = now_et.time()

            # ── Off-hours backoff (SPEC §15.3) ─────────────────────
            # When market is closed AND no open positions to monitor,
            # sleep on a bounded backoff instead of spinning every 1s.
            # Positions still get monitored when they exist (fall through).
            if not self._is_market_open() and not self._positions.all_open():
                time.sleep(60.0)
                continue

            # ── Position monitoring (every cycle) ──────────────
            if now - last_position_check >= self._monitor_interval:
                self._monitor_positions()
                last_position_check = now

            # ── Scan (on cadence, suppressed during emergencies / off-hours) ─
            if now - last_scan >= self._scan_interval:
                if self._has_emergency():
                    logger.warning("Scan suppressed — unresolved emergency positions exist")
                elif not self._is_market_open():
                    logger.debug("Scan suppressed — market closed")
                else:
                    self._scan_and_process()
                last_scan = now

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
                    snapshot = None

            current_price: Optional[float] = None

            # ── Data-unavailable policy (SPEC §6.4, §10.3) ──────────
            if snapshot is None:
                if self._execution._has_pending_stop(pos.symbol):
                    logger.warning(
                        "Data unavailable for %s (protected) — holding position, retry next cycle",
                        pos.symbol,
                    )
                    continue  # skip this position, retry on next monitor cycle
                else:
                    logger.warning(
                        "Data unavailable for %s (unprotected) — escalating to UNPROTECTED",
                        pos.symbol,
                    )
                    try:
                        self._execution.mark_unprotected(pos.symbol)
                    except ValueError:
                        pass  # already in a terminal state
                    # Fall through to pipeline so the exit engine can handle P4
            else:
                if snapshot.candidate.price is not None and snapshot.candidate.price > 0:
                    current_price = snapshot.candidate.price

            candidate = Candidate(symbol=pos.symbol, price=current_price)

            try:
                result = run_pipeline(
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
                    position_store=self._positions,
                    logger=self._logger,
                    check_exits_for_open=True,
                    daily_loss_breached=self._risk_state.daily_loss_breached,
                    per_symbol_loss_capped=self._risk_state.per_symbol_daily_loss.get(pos.symbol, 0) < 0 and abs(self._risk_state.per_symbol_daily_loss.get(pos.symbol, 0)) >= self._max_daily_loss_pct * self._equity,
                    equity=self._equity,
                    starter_risk_pct=self._starter_risk_pct,
                    max_positions=self._max_positions,
                    focus_price_min=self._focus_price_min,
                    focus_price_max=self._focus_price_max,
                    account=self._risk_state,
                    et_time=self._et_time,
                )

                # Execute exit if pipeline detected one
                if result.exit_decision is not None and result.exit_decision.should_exit:
                    try:
                        order, pos_after = self._execution.submit_exit(
                            pos.symbol,
                            reason=result.exit_decision.reason,
                            exit_pct=result.exit_decision.exit_pct,
                        )
                        self._execution.confirm_exit_fill(order.order_id)
                        # Accumulate realized P&L for session ledger (T4.2)
                        if (
                            pos.average_entry
                            and order.qty > 0
                            and current_price is not None
                            and current_price > 0
                        ):
                            realized = (current_price - pos.average_entry) * order.qty
                            self._session_realized_pnl += realized
                            self._session_per_symbol_pnl[pos.symbol] = round(
                                self._session_per_symbol_pnl.get(pos.symbol, 0.0) + realized, 2
                            )
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
                continue

    def _scan_and_process(self) -> None:
        """Run scanner, enrich candidates, run pipeline for each."""
        try:
            raw = self._scanner_fn()
        except Exception:
            logger.exception("Scanner failure — retrying next cycle")
            return

        candidates = [self._enrichment_fn(c) for c in raw]

        # Score and rank candidates by attention before processing (T5.3)
        from src.scanner.attention import score_candidates
        ranked = score_candidates(
            candidates,
            former_runner_store=self._former_runners,
        )
        candidates = [c for c, _ in ranked]

        # Build risk state from current positions before processing
        self._risk_state = self._build_risk_state()

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
            if self._market_data_fn:
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
                result = run_pipeline(
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
                    halt_count_today=snapshot.halt_count_today,
                    execution_gw=self._execution,
                    position_store=self._positions,
                    former_runner_store=self._former_runners,
                    logger=self._logger,
                    equity=self._equity,
                    starter_risk_pct=self._starter_risk_pct,
                    max_trade_risk_pct=self._max_trade_risk_pct,
                    max_positions=self._max_positions,
                    max_open_risk_pct=self._max_open_risk_pct,
                    max_daily_loss_pct=self._max_daily_loss_pct,
                    focus_price_min=self._focus_price_min,
                    focus_price_max=self._focus_price_max,
                    account=self._risk_state,
                    per_symbol_loss_capped=per_symbol_loss_capped,
                    et_time=self._et_time,
                    snapshot_missing=snap_missing if not snap_valid else None,
                    pre_submit_quote_fn=self._market_data_fn,
                )

                if result.decision == "enter":
                    cycle_enters += 1
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

    def _shutdown(self) -> None:
        """Clean shutdown — persist state, reconcile, close resources."""
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
