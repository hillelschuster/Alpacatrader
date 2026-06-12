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
from datetime import datetime, timezone
from typing import Callable, Optional

from loguru import logger

from src.decision_pipeline import MarketSnapshot, PipelineResult, run_pipeline
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import AccountRiskState, Candidate
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
        broker_snapshot_fn: Optional[Callable[[], dict[str, tuple[int, float]]]] = None,
        # Cadence configuration
        monitor_interval_seconds: float = 10.0,
        scan_interval_seconds: float = 30.0,
        # Session gating
        entry_cutoff_time: Optional[str] = "15:30",
        flatten_time: Optional[str] = "15:55",
        # Risk
        equity: float = 100_000.0,
        starter_risk_pct: float = 0.0025,
        max_positions: int = 3,
        max_open_risk_pct: float = 0.03,
        max_daily_loss_pct: float = 0.03,
        focus_price_min: float = 1.0,
        focus_price_max: float = 50.0,
        # Paper mode
        paper_mode: bool = True,
    ) -> None:
        self._scanner_fn = scanner_fn or (lambda: [])
        self._enrichment_fn = enrichment_fn or (lambda c: c)
        self._market_data_fn = market_data_fn
        self._logger = logger
        self._execution = execution_gw or PaperExecutionGateway()
        self._positions = position_store or self._execution.positions
        self._former_runners = former_runner_store or FormerRunnerStore()
        self._broker_snapshot_fn = broker_snapshot_fn or (lambda: {})

        self._monitor_interval = monitor_interval_seconds
        self._scan_interval = scan_interval_seconds
        self._entry_cutoff = entry_cutoff_time
        self._flatten_time = flatten_time
        self._equity = equity
        self._starter_risk_pct = starter_risk_pct
        self._max_positions = max_positions
        self._max_open_risk_pct = max_open_risk_pct
        self._max_daily_loss_pct = max_daily_loss_pct
        self._focus_price_min = focus_price_min
        self._focus_price_max = focus_price_max
        self._paper_mode = paper_mode

        self._cycle_count: int = 0
        self._started_at: Optional[datetime] = None
        self._risk_state: AccountRiskState = AccountRiskState()

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def is_running(self) -> bool:
        global _shutdown_requested
        return not _shutdown_requested

    def request_shutdown(self) -> None:
        """Programmatic shutdown — sets the flag so the loop exits cleanly."""
        global _shutdown_requested
        _shutdown_requested = True

    # ── Startup reconciliation (SPEC §15.4) ──────────────────────

    def _reconcile_on_startup(self) -> None:
        """Reconcile broker positions vs local state at startup.

        Calls ``reconcile_positions()`` with the injectable broker
        snapshot.  Re-protects any positions that were inserted
        during reconciliation.
        """
        broker = self._broker_snapshot_fn()
        if broker:
            actions = reconcile_positions(
                broker_positions=broker,
                local_store=self._positions,
                pending_store=self._execution.pending,
            )
            # Re-protect positions that were inserted without a stop
            for action in actions:
                if action.get("action") == "insert_protect":
                    symbol = action["symbol"]
                    pos = self._positions.get(symbol)
                    if pos is not None and pos.stop_price is not None:
                        self._execution.protect_position(
                            symbol, pos.stop_price, pos.current_shares,
                        )

    # ── Account risk state (SPEC §13.5) ──────────────────────────

    def _build_risk_state(self) -> AccountRiskState:
        """Derive ``AccountRiskState`` from the current position store.

        Computed fresh each cycle so entry gates always see the latest
        open positions, open risk, and daily P&L.
        """
        open_positions = self._positions.all_open()
        total_risk = 0.0
        realized = 0.0
        unrealized = 0.0
        per_symbol: dict[str, float] = {}

        for pos in open_positions:
            if (
                pos.average_entry is not None
                and pos.stop_price is not None
                and pos.current_shares > 0
            ):
                total_risk += max(
                    0.0, (pos.average_entry - pos.stop_price) * pos.current_shares
                )
            realized += pos.realized_pnl or 0.0
            unrealized += pos.unrealized_pnl or 0.0
            rp = pos.realized_pnl or 0.0
            up = pos.unrealized_pnl or 0.0
            per_symbol[pos.symbol] = round(rp + min(0.0, up), 2)

        daily_pnl_val = realized + unrealized
        daily_loss_breached = (
            daily_pnl_val < -self._equity * self._max_daily_loss_pct
        )

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

        # ══ Startup reconciliation ═══════════════════════════════
        self._reconcile_on_startup()

        last_position_check = time.monotonic()
        last_scan = time.monotonic()

        while self.is_running:
            now = time.monotonic()

            # ── Position monitoring (every cycle) ──────────────
            if now - last_position_check >= self._monitor_interval:
                self._monitor_positions()
                last_position_check = now

            # ── Scan (on cadence) ──────────────────────────────
            if now - last_scan >= self._scan_interval:
                self._scan_and_process()
                last_scan = now

            self._cycle_count += 1
            time.sleep(1.0)  # 1-second tick granularity

        self._shutdown()

    def _monitor_positions(self) -> None:
        """Check exits for all open/locked positions using fresh market data.

        Uses ``market_data_fn`` (if provided) to obtain a current quote,
        spread, bar, and stale-age information for each position.  If the
        data provider is not configured or fails, the quote is treated as
        stale (999s old) per SPEC §15.3 / §12.5 emergency rules.
        """
        self._risk_state = self._build_risk_state()

        for pos in self._positions.all_open():
            # Try to get fresh market data for this position
            snapshot = None
            if self._market_data_fn:
                try:
                    temp = Candidate(symbol=pos.symbol, price=0)
                    snapshot = self._market_data_fn(temp)
                except Exception:
                    logger.exception("Market-data fetch failed for %s during monitor", pos.symbol)
                    snapshot = None

            # Determine current price — never fall back to average_entry
            if snapshot is not None and snapshot.candidate.price is not None and snapshot.candidate.price > 0:
                current_price = snapshot.candidate.price
            else:
                current_price = 0.0  # triggers emergency stale-quote handling

            candidate = Candidate(symbol=pos.symbol, price=current_price)

            try:
                result = run_pipeline(
                    candidate,
                    bars=snapshot.bars if snapshot else None,
                    vwap=snapshot.vwap if snapshot else None,
                    ema9=snapshot.ema9 if snapshot else None,
                    day_high=snapshot.day_high if snapshot else None,
                    prior_hod=snapshot.prior_hod if snapshot else None,
                    quote_age_seconds=snapshot.quote_age_seconds if snapshot else 999.0,
                    spread_pct=snapshot.spread_pct if snapshot else None,
                    rvol=snapshot.rvol if snapshot else None,
                    halt_count_today=snapshot.halt_count_today if snapshot else 0,
                    execution_gw=self._execution,
                    position_store=self._positions,
                    logger=self._logger,
                    check_exits_for_open=True,
                    daily_loss_breached=self._risk_state.daily_loss_breached,
                    equity=self._equity,
                    starter_risk_pct=self._starter_risk_pct,
                    max_positions=self._max_positions,
                    focus_price_min=self._focus_price_min,
                    focus_price_max=self._focus_price_max,
                    account=self._risk_state,
                )

                # Execute exit if pipeline detected one
                if result.exit_decision is not None and result.exit_decision.should_exit:
                    try:
                        order = self._execution.submit_exit(
                            pos.symbol,
                            reason=result.exit_decision.reason,
                        )
                        self._execution.confirm_exit_fill(order.order_id)
                    except Exception:
                        logger.exception(
                            "Exit execution failed for %s — position may remain OPEN",
                            pos.symbol,
                        )
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

        # Rebuild risk state from current positions before processing
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

            # Build market data snapshot (bars, quote, etc.)
            if self._market_data_fn:
                snapshot = self._market_data_fn(c)
                if snapshot is None:
                    snapshot = MarketSnapshot(candidate=c)
            else:
                snapshot = MarketSnapshot(candidate=c)

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
                    max_positions=self._max_positions,
                    max_open_risk_pct=self._max_open_risk_pct,
                    max_daily_loss_pct=self._max_daily_loss_pct,
                    focus_price_min=self._focus_price_min,
                    focus_price_max=self._focus_price_max,
                    account=self._risk_state,
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
        """Clean shutdown — reconcile state, close resources."""
        # Future: save position store to disk, reconcile broker state,
        # log shutdown event via self._logger.
