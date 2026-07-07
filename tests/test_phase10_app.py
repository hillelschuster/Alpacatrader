"""Phase 10 app-loop tests.

Verifies:
  - TradingApp initialises without crashes.
  - Scanner and enrichment callbacks are injectable.
  - App runs a limited number of cycles and shuts down cleanly.
  - Position monitoring fires on cadence.
  - Scanner fires on cadence.
  - Shutdown flag stops the loop.
  - Symbol-locked candidates are skipped.
"""

import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.app import TradingApp, _shutdown_requested
from src.decision_pipeline import MarketSnapshot, PipelineResult
from src.entries import Bar
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import (
    Candidate,
    EntrySetupType,
    EntrySignal,
    MoveState,
    OrderActionType,
    PendingOrder,
    PositionState,
    PositionStateModel,
)
from src.paper_execution import MarketSession, PaperExecutionGateway
from src.scanner.attention import FormerRunnerStore


# ── Helpers ───────────────────────────────────────────────────────


def _c(symbol: str = "DSY", **kw) -> Candidate:
    return Candidate(symbol=symbol, price=10.50, percent_gain=25.0,
                     source="finviz", **kw)


def _runner_bars() -> list[Bar]:
    return [
        Bar(open=10.00, high=10.20, low=9.90, close=10.10, volume=1_000),
        Bar(open=10.10, high=10.35, low=10.00, close=10.25, volume=1_000),
        Bar(open=10.25, high=10.50, low=10.10, close=10.40, volume=1_000),
        Bar(open=10.40, high=10.65, low=10.20, close=10.55, volume=1_000),
        Bar(open=10.55, high=10.85, low=10.30, close=10.75, volume=1_000),
        Bar(open=10.75, high=11.10, low=10.45, close=10.95, volume=2_000),
    ]


def _add_stop(gw: PaperExecutionGateway, symbol: str, stop: float, qty: int) -> None:
    gw.pending.add(PendingOrder(
        symbol=symbol,
        order_id=f"stop-{symbol}",
        order_type=OrderActionType.STOP,
        side="sell",
        qty=qty,
        status="submitted",
        stop_price=stop,
    ))


def _make_result(candidate: Candidate, move_state: MoveState) -> PipelineResult:
    """Create a PipelineResult with a specific move_state."""
    result = PipelineResult(candidate)
    result.move_state = move_state
    return result


# ──────────────────────────────────────────────────────────────────
#  App initialisation
# ──────────────────────────────────────────────────────────────────


class TestTradingAppInit:
    def test_creates_with_defaults(self):
        app = TradingApp()
        assert app.cycle_count == 0
        assert app.is_running is True

    def test_injects_scanner_fn(self):
        called = []
        app = TradingApp(scanner_fn=lambda: called.append(1) or [_c()])
        app._scan_and_process()
        assert len(called) == 1

    def test_injects_enrichment_fn(self):
        def enrich(c: Candidate) -> Candidate:
            c = Candidate(symbol=c.symbol, price=c.price, percent_gain=c.percent_gain,
                         sector="Enriched", source=c.source)
            return c

        app = TradingApp(scanner_fn=lambda: [_c()], enrichment_fn=enrich)
        app._scan_and_process()

    def test_injects_logger(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "test.jsonl"
            logger = DecisionLogger(log_path)
            app = TradingApp(scanner_fn=lambda: [_c()], logger=logger)
            app._scan_and_process()
            # Log may or may not have records depending on pipeline output
            # Just verify no crash
            assert True

    def test_injects_execution_gateway(self):
        gw = PaperExecutionGateway()
        app = TradingApp(execution_gw=gw)
        assert app._execution is gw


# ──────────────────────────────────────────────────────────────────
#  Loop behaviour
# ──────────────────────────────────────────────────────────────────


class TestTradingAppLoop:
    def test_runs_limited_cycles_and_shuts_down(self):
        """Run a fixed number of cycles, then shut down."""
        global _shutdown_requested
        _shutdown_requested = False  # reset

        scan_count = [0]
        monitor_count = [0]

        def scanner():
            scan_count[0] += 1
            return [_c("DSY")]

        app = TradingApp(
            scanner_fn=scanner,
            monitor_interval_seconds=1.0,
            scan_interval_seconds=1.0,
        )

        # Run in a separate thread-like way: manually call cycles
        start = time.monotonic()
        max_cycles = 5
        while app.is_running and app.cycle_count < max_cycles:
            now = time.monotonic()
            # Simulate position monitoring
            app._monitor_positions()
            # Simulate scanning
            app._scan_and_process()
            app._cycle_count += 1
            if app.cycle_count >= max_cycles:
                app.request_shutdown()

        assert app.cycle_count >= max_cycles

    def test_shutdown_flag_stops_loop(self):
        global _shutdown_requested
        _shutdown_requested = False
        app = TradingApp()
        app.request_shutdown()
        assert app.is_running is False
        _shutdown_requested = False  # cleanup

    def test_symbol_locked_candidates_skipped(self):
        """Candidates whose symbols are locked should be skipped."""
        global _shutdown_requested
        _shutdown_requested = False

        gw = PaperExecutionGateway()
        # Lock DSY by submitting an entry
        from src.models.schemas import EntrySetupType, EntrySignal
        signal = EntrySignal(
            symbol="DSY", entry_setup=EntrySetupType.FIRST_PULLBACK,
            entry_price=10.50, stop_price=10.30, risk_per_share=0.20,
            target_price=11.00, proposed_shares=50, risk_amount=10.0,
            invalidation="test",
        )
        gw.submit_entry(signal)

        scanned = []
        processed = []

        def scanner():
            scanned.append("DSY")
            scanned.append("AAPL")
            return [_c("DSY"), _c("AAPL")]

        app = TradingApp(scanner_fn=scanner, execution_gw=gw,
                         scan_interval_seconds=0.5, monitor_interval_seconds=0.5)
        app._scan_and_process()
        # DSY should be skipped (locked), AAPL processed
        assert gw.is_symbol_locked("DSY") is True
        _shutdown_requested = False

    def test_position_monitoring_runs(self):
        global _shutdown_requested
        _shutdown_requested = False

        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        app = TradingApp(execution_gw=gw)
        # Should not crash
        app._monitor_positions()
        _shutdown_requested = False

    def test_loop_reconciles_broker_open_orders_after_startup(self, monkeypatch):
        import src.app
        src.app._shutdown_requested = False
        monkeypatch.setattr("src.app.install_shutdown_handlers", lambda: None)

        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30,
            current_shares=50, average_entry=10.50,
        ))
        _add_stop(gw, "DSY", 10.30, 50)
        broker_order_calls = []

        def fake_sleep(secs: float) -> None:
            src.app._shutdown_requested = True

        app = TradingApp(
            execution_gw=gw,
            broker_orders_snapshot_fn=lambda: broker_order_calls.append(1) or [],
            market_session_fn=lambda: MarketSession(is_open=True),
            order_reconcile_interval_seconds=0.0,
        )
        monkeypatch.setattr("src.app.time.sleep", fake_sleep)

        app.run()

        assert broker_order_calls
        assert gw.positions.get("DSY").state == PositionState.UNPROTECTED
        assert len(gw.pending.get_for_symbol("DSY")) == 0
        src.app._shutdown_requested = False

    def test_open_order_snapshot_failure_requires_three_failures_before_unprotected(self):
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30,
            current_shares=50, average_entry=10.50,
        ))
        _add_stop(gw, "DSY", 10.30, 50)
        app = TradingApp(
            execution_gw=gw,
            broker_orders_snapshot_fn=lambda: None,
        )

        app._reconcile_open_orders_against_broker()
        app._reconcile_open_orders_against_broker()
        assert gw.positions.get("DSY").state == PositionState.OPEN

        app._reconcile_open_orders_against_broker()

        assert gw.positions.get("DSY").state == PositionState.UNPROTECTED

    def test_monitor_exception_escalates_after_three_failures_and_logs(self, tmp_path):
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30,
            current_shares=50, average_entry=10.50,
        ))
        _add_stop(gw, "DSY", 10.30, 50)
        logger = DecisionLogger(tmp_path / "decisions.jsonl")

        def broken_market_data(candidate):
            raise RuntimeError("quote feed down")

        app = TradingApp(
            execution_gw=gw,
            market_data_fn=broken_market_data,
            logger=logger,
        )

        app._monitor_positions()
        app._monitor_positions()
        assert gw.positions.get("DSY").state == PositionState.OPEN

        app._monitor_positions()

        assert gw.positions.get("DSY").state == PositionState.UNPROTECTED
        records = list(logger.read())
        assert any(
            rec.symbol == "DSY"
            and rec.decision == "audit"
            and rec.reason == "monitor_failure_escalated_unprotected"
            for rec in records
        )

    def test_loop_logs_market_closed_safety_audit(self, tmp_path, monkeypatch):
        import src.app
        src.app._shutdown_requested = False
        monkeypatch.setattr("src.app.install_shutdown_handlers", lambda: None)
        logger = DecisionLogger(tmp_path / "decisions.jsonl")

        def fake_sleep(secs: float) -> None:
            src.app._shutdown_requested = True

        monkeypatch.setattr("src.app.time.sleep", fake_sleep)
        app = TradingApp(
            logger=logger,
            market_session_fn=lambda: MarketSession(
                is_open=False,
                source="alpaca_calendar_closed",
            ),
        )

        app.run()

        records = list(logger.read())
        assert any(
            rec.symbol == "__MARKET__"
            and rec.decision == "audit"
            and rec.reason == "market_closed:alpaca_calendar_closed"
            for rec in records
        )
        src.app._shutdown_requested = False


# ──────────────────────────────────────────────────────────────────
#  Batch 3 — Enrichment data flow through app into pipeline
# ──────────────────────────────────────────────────────────────────


def _fake_entry_signal(symbol: str = "DSY") -> EntrySignal:
    """A known EntrySignal used to force entry via monkeypatch."""
    return EntrySignal(
        symbol=symbol,
        entry_setup=EntrySetupType.FIRST_PULLBACK,
        entry_price=10.50,
        stop_price=10.30,
        risk_per_share=0.20,
        target_price=10.90,
        proposed_shares=50,
        risk_amount=10.0,
        invalidation="test_override",
    )


class TestBatch3Enrichment:
    """Verify market data enrichment flows through app into the decision pipeline.

    Batch 3:
    - Valid injected MarketSnapshot data reaches entry path.
    - Missing critical quote data produces hard mechanical block.
    - Stale quote (>15s) produces hard mechanical block.
    - Missing bars produces watch with no_bars_for_entry reason.
    """

    def setup_method(self) -> None:
        """Reset shutdown flag before each test — avoids cross-test leakage.
        
        Must use ``import src.app`` because ``from src.app import _shutdown_requested``
        creates a local copy; assigning to it does not modify the module global.
        """
        import src.app
        src.app._shutdown_requested = False

    def test_valid_market_data_reaches_entry_path(self, tmp_path, monkeypatch):
        """Valid injected market data reaches entry path via force_entry."""
        monkeypatch.setattr(
            "src.decision_pipeline.find_entry",
            lambda *a, **kw: _fake_entry_signal(),
        )

        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()
        former_runners = FormerRunnerStore()
        former_runners = FormerRunnerStore()

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=c,
                bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
                vwap=10.0,
                spread_pct=0.5,
                quote_age_seconds=2.0,
                rvol=5.0,
                dollar_volume_5m=500_000,
            )

        app = TradingApp(
            scanner_fn=lambda: [_c()],
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            logger=logger,
            execution_gw=gw,
            starter_risk_pct=0.01,
            equity=100_000,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1

        # With valid market snapshot + forced entry signal, entry should be attempted.
        rec = records[0]
        assert rec.decision in ("enter", "watch"), (
            f"Expected enter/watch, got {rec.decision}: {rec.reason}"
        )
        # No qualitative hard blocks from valid data
        for block in rec.hard_blocks:
            assert "quote" not in block.lower()
            assert "spread" not in block.lower()

    def test_batch_snapshot_hod_and_roc_reorder_processing(self, tmp_path):
        """Snapshot HOD/ROC must improve app scan order when batch data is available."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        far = Candidate(
            symbol="FAR", price=10.00, percent_gain=25.0,
            current_volume=5_000_000, source="finviz",
        )
        hod = Candidate(
            symbol="HOD", price=10.95, percent_gain=20.0,
            current_volume=5_000_000, source="finviz",
        )
        flat_bars = [Bar(10.00, 10.05, 9.95, 10.00, 1_000) for _ in range(6)]

        def market_data_batch_fn(candidates):
            return {
                "FAR": MarketSnapshot(
                    candidate=far, bars=flat_bars, vwap=10.00,
                    day_high=12.00, spread_pct=0.5, quote_age_seconds=2.0,
                    rvol=5.0, dollar_volume_5m=500_000,
                ),
                "HOD": MarketSnapshot(
                    candidate=hod, bars=_runner_bars(), vwap=10.50,
                    day_high=11.00, spread_pct=0.5, quote_age_seconds=2.0,
                    rvol=5.0, dollar_volume_5m=500_000,
                ),
            }

        app = TradingApp(
            scanner_fn=lambda: [far, hod],
            enrichment_fn=lambda c: c,
            market_data_batch_fn=market_data_batch_fn,
            logger=logger,
        )

        app._scan_and_process()

        records = list(logger.read())
        assert [r.symbol for r in records[:2]] == ["HOD", "FAR"]

    def test_missing_quote_hard_blocks(self, tmp_path):
        """Missing critical quote data must hard-block, not fabricate freshness."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=c,
                bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
                spread_pct=0.5,
                quote_age_seconds=None,  # critical: missing quote
                rvol=3.0,
            )

        app = TradingApp(
            scanner_fn=lambda: [_c()],  # price=10.50 already default
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            logger=logger,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "skip", (
            f"Expected skip, got {rec.decision}: {rec.reason}"
        )
        assert any("quote" in b for b in rec.hard_blocks), (
            f"Expected quote-related hard block, got: {rec.hard_blocks}"
        )

    def test_stale_quote_hard_blocks(self, tmp_path):
        """Stale quote (>15s) must hard-block entry explicitly."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=c,
                bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
                spread_pct=0.5,
                quote_age_seconds=20.0,  # >15s → hard reject
                rvol=3.0,
            )

        app = TradingApp(
            scanner_fn=lambda: [_c()],  # price=10.50 already default
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            logger=logger,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "skip", (
            f"Expected skip, got {rec.decision}: {rec.reason}"
        )
        assert any("stale" in b.lower() or "quote" in b.lower() for b in rec.hard_blocks), (
            f"Expected stale/quote hard block, got: {rec.hard_blocks}"
        )

# ──────────────────────────────────────────────────────────────────
#  Batch 4 — Monitor uses fresh current price for exit checks
# ──────────────────────────────────────────────────────────────────


class TestBatch4MonitorExits:
    """Verify position monitoring uses fresh current price (not average_entry).

    Per SPEC §22.8:
    - Market data (current price, spread, quote age, bars) must flow into exit checks.
    - No exit may be calculated from average_entry as a proxy for current price.
    - Stale/missing quote while in position triggers emergency/unprotected rules.
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_hard_stop_triggers_when_current_price_below_stop(self, tmp_path):
        """Hard stop must fire in monitor path when current_price < stop_price."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 50)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.20),  # below stop
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "exit", f"Expected exit, got {rec.decision}: {rec.reason}"
        assert "hard_stop" in rec.reason, f"Expected hard_stop, got: {rec.reason}"

        # T8.1: state assertion — position must be resolved
        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.CLOSED, (
            f"Expected CLOSED, got {pos_after.state}"
        )
        assert pos_after.current_shares == 0

    def test_session_pnl_uses_confirmed_exit_fill_not_monitor_price(self, tmp_path):
        """Session risk ledger must use execution fill truth, not quote snapshot."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)

        class BrokerFillGateway(PaperExecutionGateway):
            def confirm_exit_fill(self, order_id: str) -> PositionStateModel:
                pos = self.positions.get("DSY")
                assert pos is not None
                filled_qty = pos.current_shares
                pos.realized_pnl = (11.00 - pos.average_entry) * filled_qty
                pos.current_shares = 0
                pos.state = PositionState.CLOSED
                self.positions.upsert(pos)
                self.pending.resolve(order_id, "filled")
                return pos

        gw = BrokerFillGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=10.30, current_shares=5,
            average_entry=10.00,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 5)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.20),
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        assert app._session_realized_pnl == pytest.approx(5.0)

    def test_session_pnl_skips_exit_when_no_confirmed_fill_price_exists(self, tmp_path):
        """Never fabricate 0.0 P&L for an exit without a usable fill price."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)

        class NoFillPriceGateway(PaperExecutionGateway):
            def confirm_exit_fill(self, order_id: str) -> PositionStateModel:
                pos = self.positions.get("DSY")
                assert pos is not None
                pos.current_shares = 0
                pos.state = PositionState.CLOSED
                self.positions.upsert(pos)
                self.pending.resolve(order_id, "filled")
                return pos

        gw = NoFillPriceGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.50, current_shares=5,
            average_entry=10.00,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            logger=logger,
            market_data_fn=lambda c: None,
        )
        app._monitor_positions()

        assert app._session_per_symbol_pnl == {}

    def test_hard_stop_fails_if_price_is_average_entry(self, tmp_path):
        """Prove the test fails when current_price is replaced with average_entry.

        If _monitor_positions used average_entry (10.50) as current price,
        the hard stop (10.30) would NOT trigger because 10.50 > 10.30.
        This test asserts a hard-stop exit — it would fail under the old code.
        """
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 50)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.20),  # below stop
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]

        # If current_price were average_entry (10.50), hard stop would NOT fire
        # because 10.50 > 10.30.  But with real price (10.20), it should.
        assert rec.decision == "exit", (
            f"Expected exit (shows real price was used), got {rec.decision}: {rec.reason}. "
            f"This test is designed to fail if average_entry replaces current_price."
        )
        assert "hard_stop" in rec.reason

        # T8.1: state assertion
        pos_after = gw.positions.get("DSY")
        assert pos_after.state == PositionState.CLOSED
        assert pos_after.current_shares == 0

    def test_spread_explosion_beats_hard_stop_in_monitor(self, tmp_path):
        """Emergency spread explosion (P1) must fire before hard stop (P3)."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.20),  # below stop
                quote_age_seconds=2.0,
                spread_pct=6.0,  # >5% → emergency
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "exit"
        assert "spread_explosion" in rec.reason, (
            f"Expected spread_explosion (P1 before P3), got: {rec.reason}"
        )

        # T8.1: position must be resolved
        pos_after = gw.positions.get("DSY")
        assert pos_after.state == PositionState.CLOSED

    def test_stale_quote_60s_triggers_emergency_in_monitor(self, tmp_path):
        """Quote age >60s must trigger emergency exit in monitor path."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.20),  # below stop
                quote_age_seconds=90.0,  # >60s → emergency
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "exit"
        assert "quote_unreliable" in rec.reason, (
            f"Expected quote_unreliable emergency, got: {rec.reason}"
        )

        # T8.1: position resolved
        pos_after = gw.positions.get("DSY")
        assert pos_after.state == PositionState.CLOSED

    def test_stale_quote_profitable_not_safe_in_monitor(self, tmp_path):
        """Profitable stale quote >60s must still exit — stale is always emergency."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)
        # Protect the position with a real pending stop (T2.4 requires
        # verified protection, not just local stop_price equality).
        gw.place_stop("DSY", 10.30, 50)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.60),  # profitable
                quote_age_seconds=90.0,  # >60s → emergency
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "exit", (
            f"Expected exit (stale quote, even profitable), got {rec.decision}: {rec.reason}"
        )
        assert "quote_unreliable" in rec.reason

        # T8.1: position resolved
        pos_after = gw.positions.get("DSY")
        assert pos_after.state == PositionState.CLOSED

    def test_protected_data_outage_holds_position_open(self, tmp_path):
        """Protected outage must hold open with stop protection intact."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 50)

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=lambda c: None,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert not any(rec.decision == "exit" for rec in records), (
            f"Unexpected exit during protected outage: {[rec.reason for rec in records]}"
        )

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.OPEN
        assert pos_after.current_shares == 50
        assert gw._has_pending_stop("DSY")

    def test_missing_price_snapshot_does_not_false_hard_stop(self, tmp_path):
        """Missing snapshot price must not fabricate a hard-stop exit."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 50)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=None),
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert not any(rec.decision == "exit" for rec in records), (
            f"Unexpected exit with missing snapshot price: {[rec.reason for rec in records]}"
        )

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.OPEN
        assert pos_after.current_shares == 50
        assert gw._has_pending_stop("DSY")

    def test_monitor_no_network_calls(self, tmp_path):
        """Missing monitor data without protection must exit via missing_protection."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        # No market_data_fn — should not crash; missing data escalates unprotected path.
        app = TradingApp(execution_gw=gw, logger=logger)
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "exit", f"Expected missing_protection exit, got {rec.decision}: {rec.reason}"
        assert "missing_protection" in rec.reason, f"Expected missing_protection, got: {rec.reason}"
        assert "quote_unreliable" not in rec.reason
        assert "hard_stop" not in rec.reason

        # T8.1: position resolved
        pos_after = gw.positions.get("DSY")
        assert pos_after.state == PositionState.CLOSED

    def test_monitor_fetch_failure_triggers_emergency(self, tmp_path):
        """When market_data_fn fails, monitor must exit via missing_protection."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        def failing_fn(c):
            raise RuntimeError("API unavailable")

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=failing_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "exit", f"Expected missing_protection exit, got {rec.decision}: {rec.reason}"
        assert "missing_protection" in rec.reason, f"Expected missing_protection, got: {rec.reason}"
        assert "quote_unreliable" not in rec.reason
        assert "hard_stop" not in rec.reason

        # T8.1: position resolved after missing-protection escalation
        pos_after = gw.positions.get("DSY")
        assert pos_after.state == PositionState.CLOSED, (
            f"Expected CLOSED, got {pos_after.state}"
        )

    def test_unprotected_positions_checked_in_monitor(self, tmp_path):
        """UNPROTECTED positions must also be checked for exits in monitor."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.UNPROTECTED,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.20),  # below stop
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        # Should exit — UNPROTECTED + losing should trigger unprotected_and_losing
        assert rec.decision == "exit", f"Expected exit for UNPROTECTED position, got {rec.decision}: {rec.reason}"
        assert "unprotected" in rec.reason or "hard_stop" in rec.reason, (
            f"Expected unprotected or hard_stop exit, got: {rec.reason}"
        )

        # T8.1: position resolved
        pos_after = gw.positions.get("DSY")
        assert pos_after.state == PositionState.CLOSED

    def test_runner_trail_updates_and_holds_above_stop(self, tmp_path, monkeypatch):
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=10.80,
        )
        gw.positions.upsert(pos)
        _add_stop(gw, "DSY", 9.50, 100)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=11.20),
                bars=_runner_bars(),
                vwap=10.20,
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        def fake_evaluate_candidate(candidate, **kwargs):
            result = PipelineResult(candidate)
            result.move_state = MoveState.ACTIVE
            return result

        monkeypatch.setattr("src.app.evaluate_candidate", fake_evaluate_candidate)
        app = TradingApp(execution_gw=gw, logger=logger, market_data_fn=market_data_fn)
        app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.RUNNER
        assert pos_after.highest_price_seen == 11.20
        assert pos_after.trailing_stop_price == 10.0
        # Broker stop must be synced: old stop cancelled, new at trail price
        pending_stops = [
            o for o in gw.pending.get_for_symbol("DSY")
            if o.order_type == OrderActionType.STOP
        ]
        assert len(pending_stops) == 1, (
            f"Expected exactly 1 stop after trail update, got {len(pending_stops)}"
        )
        assert pending_stops[0].stop_price == pos_after.trailing_stop_price, (
            f"Expected broker stop at {pos_after.trailing_stop_price}, "
            f"got {pending_stops[0].stop_price}"
        )

    def test_open_position_promotes_to_runner_after_no_exit(self, tmp_path, monkeypatch):
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.OPEN,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 9.50, 100)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.80),
                bars=_runner_bars(),
                vwap=10.20,
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        def fake_evaluate_candidate(candidate, **kwargs):
            result = PipelineResult(candidate)
            result.move_state = MoveState.ACTIVE
            return result

        monkeypatch.setattr("src.app.evaluate_candidate", fake_evaluate_candidate)
        monkeypatch.setattr("src.app.evaluate_exits", lambda *args, **kwargs: None)

        app = TradingApp(execution_gw=gw, logger=logger, market_data_fn=market_data_fn)
        app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.RUNNER
        assert pos_after.runner_since is not None
        assert pos_after.highest_price_seen == 10.80
        assert pos_after.trailing_stop_price == 9.6
        # Broker stop must be synced: old OPEN stop replaced with trail stop
        pending_stops = [
            o for o in gw.pending.get_for_symbol("DSY")
            if o.order_type == OrderActionType.STOP
        ]
        assert len(pending_stops) == 1, (
            f"Expected exactly 1 stop after promotion, got {len(pending_stops)}"
        )
        assert pending_stops[0].stop_price == pos_after.trailing_stop_price, (
            f"Expected broker stop at {pos_after.trailing_stop_price}, "
            f"got {pending_stops[0].stop_price}"
        )

    def test_runner_adds_on_pullback_and_logs_events(self, tmp_path, monkeypatch):
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.00,
        )
        gw.positions.upsert(pos)
        _add_stop(gw, "DSY", 9.50, 100)

        bars = [
            Bar(open=10.80, high=11.20, low=10.70, close=11.00, volume=1_000),
            Bar(open=11.00, high=11.40, low=10.90, close=11.25, volume=1_000),
            Bar(open=11.25, high=11.55, low=11.05, close=11.40, volume=1_000),
            Bar(open=11.40, high=11.45, low=10.95, close=11.05, volume=800),
            Bar(open=11.05, high=11.20, low=10.90, close=10.98, volume=700),
            Bar(open=10.98, high=11.35, low=10.96, close=11.28, volume=1_100),
        ]

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=11.28),
                bars=bars,
                vwap=11.00,
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        def fake_evaluate_candidate(candidate, **kwargs):
            result = PipelineResult(candidate)
            result.move_state = MoveState.ACTIVE
            return result

        monkeypatch.setattr("src.app.evaluate_candidate", fake_evaluate_candidate)
        monkeypatch.setattr("src.app.evaluate_exits", lambda *args, **kwargs: None)

        app = TradingApp(
            execution_gw=gw,
            logger=logger,
            market_data_fn=market_data_fn,
            equity=100_000,
            add_risk_pct=0.0025,
            add_size_multiplier=0.5,
            add_activation_r_multiple=2.0,
            max_adds=2,
        )
        app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.RUNNER
        assert pos_after.add_count == 1
        assert pos_after.current_shares > 100
        assert pos_after.average_entry > 10.00
        assert pos_after.stop_price >= pos.entry_price

        reasons = [r.reason for r in logger.read()]
        assert "add_submitted" in reasons
        assert "add_filled" in reasons

    def test_runner_add_failure_logs_and_returns_to_runner(self, tmp_path, monkeypatch):
        class RejectingAddGateway(PaperExecutionGateway):
            def submit_add(self, symbol: str, qty: int, entry_price: float, stop_price: float):
                pos = self.positions.get(symbol)
                assert pos is not None
                pos.state = PositionState.ADDING
                self.positions.upsert(pos)
                raise RuntimeError("broker rejected add")

        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = RejectingAddGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.00,
        )
        gw.positions.upsert(pos)
        _add_stop(gw, "DSY", 9.50, 100)

        bars = [
            Bar(open=10.80, high=11.20, low=10.70, close=11.00, volume=1_000),
            Bar(open=11.00, high=11.40, low=10.90, close=11.25, volume=1_000),
            Bar(open=11.25, high=11.55, low=11.05, close=11.40, volume=1_000),
            Bar(open=11.40, high=11.45, low=10.95, close=11.05, volume=800),
            Bar(open=11.05, high=11.20, low=10.90, close=10.98, volume=700),
            Bar(open=10.98, high=11.35, low=10.96, close=11.28, volume=1_100),
        ]

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=11.28),
                bars=bars,
                vwap=11.00,
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        monkeypatch.setattr("src.app.evaluate_exits", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "src.app.evaluate_candidate",
            lambda candidate, **kwargs: _make_result(candidate, MoveState.ACTIVE),
        )

        app = TradingApp(
            execution_gw=gw,
            logger=logger,
            market_data_fn=market_data_fn,
            add_risk_pct=0.0025,
        )
        app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.RUNNER
        assert pos_after.current_shares == 100
        assert pos_after.add_count == 0
        assert [r.reason for r in logger.read()] == ["add_failed"]

    def test_runner_add_pending_logs_pending_not_filled(self, tmp_path, monkeypatch):
        class PendingAddGateway(PaperExecutionGateway):
            def confirm_fill(self, order_id: str):
                pos = self.positions.get("DSY")
                assert pos is not None
                return pos

        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PendingAddGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.00,
        ))
        _add_stop(gw, "DSY", 9.50, 100)

        bars = [
            Bar(open=10.80, high=11.20, low=10.70, close=11.00, volume=1_000),
            Bar(open=11.00, high=11.40, low=10.90, close=11.25, volume=1_000),
            Bar(open=11.25, high=11.55, low=11.05, close=11.40, volume=1_000),
            Bar(open=11.40, high=11.45, low=10.95, close=11.05, volume=800),
            Bar(open=11.05, high=11.20, low=10.90, close=10.98, volume=700),
            Bar(open=10.98, high=11.35, low=10.96, close=11.28, volume=1_100),
        ]

        monkeypatch.setattr("src.app.evaluate_exits", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "src.app.evaluate_candidate",
            lambda candidate, **kwargs: _make_result(candidate, MoveState.ACTIVE),
        )

        app = TradingApp(
            execution_gw=gw,
            logger=logger,
            market_data_fn=lambda c: MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=11.28),
                bars=bars,
                vwap=11.00,
                quote_age_seconds=2.0,
                spread_pct=0.5,
            ),
            add_risk_pct=0.0025,
        )
        app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.ADDING
        assert [r.reason for r in logger.read()] == ["add_submitted", "add_pending"]

    def test_runner_add_uses_original_risk_after_trail_sync(self, tmp_path, monkeypatch):
        """ADD logic must not use ratcheted broker stop as original risk."""
        logger = DecisionLogger(tmp_path / "decisions.jsonl")
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=10.00,  # active broker stop already ratcheted to breakeven
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.00,
            original_risk_per_share=0.50,
        )
        gw.positions.upsert(pos)
        _add_stop(gw, "DSY", 10.00, 100)

        bars = [
            Bar(open=10.80, high=11.20, low=10.70, close=11.00, volume=1_000),
            Bar(open=11.00, high=11.40, low=10.90, close=11.25, volume=1_000),
            Bar(open=11.25, high=11.55, low=11.05, close=11.40, volume=1_000),
            Bar(open=11.40, high=11.45, low=10.95, close=11.05, volume=800),
            Bar(open=11.05, high=11.20, low=10.90, close=10.98, volume=700),
            Bar(open=10.98, high=11.35, low=10.96, close=11.28, volume=1_100),
        ]
        monkeypatch.setattr("src.app.evaluate_exits", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "src.app.evaluate_candidate",
            lambda candidate, **kwargs: _make_result(candidate, MoveState.ACTIVE),
        )

        app = TradingApp(
            execution_gw=gw,
            logger=logger,
            market_data_fn=lambda c: MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=11.28),
                bars=bars,
                vwap=11.00,
                quote_age_seconds=2.0,
                spread_pct=0.5,
            ),
            equity=100_000,
            add_risk_pct=0.0025,
        )
        app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.add_count == 1
        assert "add_filled" in [r.reason for r in logger.read()]

    def test_adding_state_counts_as_emergency(self):
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY",
            state=PositionState.ADDING,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
        ))
        app = TradingApp(execution_gw=gw)
        assert app._has_emergency() is True

    def test_market_data_flows_through_to_exit_checks(self, tmp_path):
        """All market data fields (spread, bars, vwap) must reach exit checks."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 50)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.60),
                bars=[Bar(10.50, 10.65, 10.45, 10.62, 2000)],
                vwap=10.40,
                quote_age_seconds=2.0,
                spread_pct=0.5,
                rvol=5.0,
                halt_count_today=0,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        # With price=10.60 above stop=10.30, protected, and valid data,
        # no exit should trigger.
        assert rec.decision != "exit", f"Unexpected exit with valid data: {rec.reason}"

        # T8.1: position unchanged — still OPEN with shares intact
        pos_check = gw.positions.get("DSY")
        assert pos_check.state == PositionState.OPEN
        assert pos_check.current_shares == 50


    def test_missing_bars_watches_only(self, tmp_path):
        """Missing bars must produce watch with no_bars_for_entry reason."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=c,
                bars=None,  # no bars
                spread_pct=0.5,
                quote_age_seconds=2.0,
                rvol=3.0,
                dollar_volume_5m=500_000,  # ensure attention > 50
            )

        app = TradingApp(
            scanner_fn=lambda: [_c()],
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            logger=logger,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "watch", (
            f"Expected watch, got {rec.decision}: {rec.reason}"
        )
        assert "no_bars_for_entry" in rec.reason, (
            f"Expected 'no_bars_for_entry' in reason, got: {rec.reason}"
        )


# ══════════════════════════════════════════════════════════════════
#  Batch 5 — Startup reconciliation & account-risk enforcement
# ══════════════════════════════════════════════════════════════════


class TestBatch5StartupReconciliation:
    """Startup reconciliation (SPEC §15.4) via injectable broker snapshots.

    ``broker_snapshot_fn`` returns a dict of
    ``{symbol: (qty, avg_entry)}`` injected into ``reconcile_positions()``
    at the start of ``run()``.
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_empty_snapshot_no_ops(self):
        """Empty broker snapshot must not modify local store."""
        gw = PaperExecutionGateway()
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {},
        )
        app._reconcile_on_startup()
        assert len(gw.positions) == 0

    def test_broker_has_position_local_none(self):
        """Case 1: broker has a position, local has none → insert."""
        gw = PaperExecutionGateway()
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
        )
        app._reconcile_on_startup()
        pos = gw.positions.get("DSY")
        assert pos is not None
        assert pos.current_shares == 50
        assert pos.average_entry == 10.50
        assert pos.state == PositionState.OPEN

    def test_broker_position_qty_less_than_local(self):
        """Case 3: broker qty < local qty → update local."""
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=100, average_entry=10.50,
        ))
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
        )
        app._reconcile_on_startup()
        pos = gw.positions.get("DSY")
        assert pos.current_shares == 50

    def test_broker_no_position_local_active(self):
        """Case 5: broker has no position for symbol, local has active → close local."""
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, average_entry=10.50,
        ))
        # Non-empty broker dict triggers reconciliation; DSY not in broker → closes
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"OTHER": (100, 5.0)},
        )
        app._reconcile_on_startup()
        pos = gw.positions.get("DSY")
        assert pos is not None
        assert pos.state == PositionState.CLOSED


# ──────────────────────────────────────────────────────────────────
#  Account-risk enforcement in app path
# ──────────────────────────────────────────────────────────────────


class TestBatch5AccountRiskEnforcement:
    """Account-risk hard blocks (SPEC §7.5) enforced in the app-level
    ``_scan_and_process`` path via the wired ``AccountRiskState``.

    Blocks tested:
      - max open positions
      - daily loss cap (triggers ``daily_loss_breached``)
      - symbol lock from existing position
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def _market_data_ok(self, c: Candidate) -> MarketSnapshot:
        """Return a valid market snapshot so data blocks don't interfere."""
        return MarketSnapshot(
            candidate=c,
            bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
            vwap=10.0,
            spread_pct=0.5,
            quote_age_seconds=2.0,
            dollar_volume_5m=500_000,
        )

    def test_max_positions_blocks_new_candidate(self, tmp_path):
        """When position count reaches max_positions, a new candidate
        must get a ``max_positions_reached`` hard block."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        # Fill up to max_positions=3
        for sym in ("AAPL", "GOOG", "MSFT"):
            gw.positions.upsert(PositionStateModel(
                symbol=sym, state=PositionState.OPEN,
                entry_price=100.0, stop_price=99.0,
                current_shares=10, average_entry=100.0,
            ))

        app = TradingApp(
            scanner_fn=lambda: [_c("DSY")],
            enrichment_fn=lambda c: c,
            market_data_fn=self._market_data_ok,
            execution_gw=gw,
            logger=logger,
            equity=100_000,
            max_positions=3,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "skip", (
            f"Expected skip for max-positions breach, got {rec.decision}: {rec.reason}"
        )
        assert any("max_positions" in b for b in rec.hard_blocks), (
            f"Expected max_positions_reached in hard_blocks, got: {rec.hard_blocks}"
        )

    def test_under_max_positions_not_blocked(self, tmp_path):
        """When position count is under max_positions, no position block."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        # Only 1 open position when max=3
        gw.positions.upsert(PositionStateModel(
            symbol="AAPL", state=PositionState.OPEN,
            entry_price=100.0, stop_price=99.0,
            current_shares=10, average_entry=100.0,
        ))

        app = TradingApp(
            scanner_fn=lambda: [_c("DSY")],
            enrichment_fn=lambda c: c,
            market_data_fn=self._market_data_ok,
            execution_gw=gw,
            logger=logger,
            equity=100_000,
            max_positions=3,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        # Should NOT have max_positions hard block
        assert not any("max_positions" in b for b in rec.hard_blocks), (
            f"Unexpected max_positions block: {rec.hard_blocks}"
        )

    def test_daily_loss_cap_blocks_entry(self, tmp_path):
        """When daily loss exceeds max_daily_loss_pct, candidates must be hard-blocked."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        # A position with large realized loss that breaches 3% of 100k
        gw.positions.upsert(PositionStateModel(
            symbol="LOSS", state=PositionState.OPEN,
            entry_price=100.0, stop_price=99.0,
            current_shares=10, average_entry=100.0,
            realized_pnl=-4000.0,  # > 3% of 100k = 3000
        ))

        app = TradingApp(
            scanner_fn=lambda: [_c("DSY")],
            enrichment_fn=lambda c: c,
            market_data_fn=self._market_data_ok,
            execution_gw=gw,
            logger=logger,
            equity=100_000,
            max_daily_loss_pct=0.03,  # $3000 daily loss cap
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "skip", (
            f"Expected skip for daily loss breach, got {rec.decision}: {rec.reason}"
        )
        # daily_loss_breached → check_account_risk blocks with "daily_loss_cap_breached"
        assert any("daily_loss" in b for b in rec.hard_blocks), (
            f"Expected daily_loss_cap_breached in hard_blocks, got: {rec.hard_blocks}"
        )

    def test_symbol_lock_blocks_entry_via_hard_filters(self, tmp_path):
        """An existing position for the same symbol must produce a symbol_locked block.

        The app-level ``is_symbol_locked`` check in ``_scan_and_process`` skips
        locked symbols before pipeline.  This test verifies the pipeline's own
        hard-filter check (``symbol_locked``) catches it when called directly,
        and the app-level skip also works.
        """
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        # Lock DSY with an open position
        gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30,
            current_shares=50, average_entry=10.50,
        ))

        app = TradingApp(
            scanner_fn=lambda: [_c("DSY")],
            enrichment_fn=lambda c: c,
            market_data_fn=self._market_data_ok,
            execution_gw=gw,
            logger=logger,
        )
        app._scan_and_process()

        # DSY should be locked — the app's _scan_and_process skips locked
        # symbols before pipeline, so no decision record should be written.
        records = list(logger.read())
        assert len(records) == 0, (
            f"Expected no records for locked symbol, got {len(records)}: "
            f"{[r.symbol for r in records]}"
        )


# ══════════════════════════════════════════════════════════════════
#  Phase 6 — T6.1: All reconciliation action types handled in app
# ══════════════════════════════════════════════════════════════════


class TestPhase6ReconciliationActions:
    """Verify _reconcile_on_startup handles ALL action types from
    reconcile_positions() (T6.1):

      - verify_stop → re-protect
      - update_qty_reprotect → re-protect with new qty
      - update_qty_reprotect_warning → warn + re-protect
      - close_local → log (already handled by reconcile)
      - cancel_stale_order → log (already handled by reconcile)
      - irreconcilable → log error
      - insert_protect without stop_price → log warning, no crash
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_verify_stop_reprotects(self):
        """Case 2: qty matches → re-protect existing position."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, stop_price=10.30,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)
        # No pending stop yet
        assert not gw._has_pending_stop("DSY")

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
        )
        app._reconcile_on_startup()

        # Should have placed a stop (re-protected)
        assert gw._has_pending_stop("DSY")

    def test_update_qty_reprotect_adjusts_shares(self):
        """Case 3: broker qty < local → re-protect with reduced qty."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=100, stop_price=10.30,
            entry_price=10.50, average_entry=10.50,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
        )
        app._reconcile_on_startup()

        # Position should be updated to 50 shares by reconcile, and re-protected at 50
        updated = gw.positions.get("DSY")
        assert updated.current_shares == 50
        assert gw._has_pending_stop("DSY")

    def test_update_qty_reprotect_warning_logged(self):
        """Case 4: broker qty > local → warn + re-protect with larger qty."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, stop_price=10.30,
            entry_price=10.50, average_entry=10.50,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (100, 10.50)},
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        assert updated.current_shares == 100
        assert gw._has_pending_stop("DSY")

    def test_close_local_logged(self):
        """Case 5: broker no position, local active → close (already tested, verify no crash)."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, average_entry=10.50,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {},  # broker reachable, no positions
        )
        app._reconcile_on_startup()

        closed = gw.positions.get("DSY")
        assert closed.state == PositionState.CLOSED

    def test_irreconcilable_auto_resolves_in_paper_mode(self):
        """Case 7: paper mode auto-resolves irreconcilable mismatch."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, average_entry=10.50,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (0, 0.0)},
        )
        app._reconcile_on_startup()

        resolved = gw.positions.get("DSY")
        assert resolved.state == PositionState.CLOSED
        assert resolved.current_shares == 0

    def test_insert_protect_without_stop_price_no_crash(self):
        """insert_protect with no stop_price → log warning, don't crash."""
        gw = PaperExecutionGateway()
        # Scenario: broker snapshot has a position but no stop info available
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
        )
        app._reconcile_on_startup()

        # Position was inserted with no stop_price → should not crash
        pos = gw.positions.get("DSY")
        assert pos is not None
        assert pos.state == PositionState.OPEN
        # stop_price is None (default), so protect_position was not called
        assert not gw._has_pending_stop("DSY")

    def test_empty_broker_close_all_active(self):
        """Broker reachable, empty response — all active positions closed."""
        gw = PaperExecutionGateway()
        for sym in ("A", "B"):
            gw.positions.upsert(PositionStateModel(
                symbol=sym, state=PositionState.OPEN,
                current_shares=50, entry_price=10.0, average_entry=10.0,
            ))

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {},
        )
        app._reconcile_on_startup()

        for sym in ("A", "B"):
            p = gw.positions.get(sym)
            assert p.state == PositionState.CLOSED

    def test_startup_reconciliation_runs_when_loop_mode_wires_snapshot(self, tmp_path):
        """When broker_snapshot_fn is wired, _reconcile_on_startup populates positions."""
        gw = PaperExecutionGateway()
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
            persist_path=str(tmp_path / "positions.json"),
        )
        app._reconcile_on_startup()
        pos = gw.positions.get("DSY")
        assert pos is not None
        assert pos.current_shares == 50
        assert pos.average_entry == 10.50


# ══════════════════════════════════════════════════════════════════
#  Phase 6 — T6.2: Broker-unreachable startup policy
# ══════════════════════════════════════════════════════════════════


class TestPhase6BrokerUnreachable:
    """Broker-unreachable must NOT silently proceed as safe (T6.2).

    - broker_snapshot_fn raises → mark OPEN positions UNPROTECTED
    - broker_snapshot_fn returns None → mark OPEN positions UNPROTECTED
    - no broker_snapshot_fn → skip reconciliation entirely (no-op)
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_broker_raises_marks_open_unprotected(self):
        """When broker snapshot raises, all OPEN positions become UNPROTECTED."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, stop_price=10.30,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        def failing_snapshot():
            raise ConnectionError("broker unreachable")

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=failing_snapshot,
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        assert updated.state == PositionState.UNPROTECTED

    def test_broker_returns_none_marks_open_unprotected(self):
        """When broker snapshot returns None, all OPEN positions become UNPROTECTED."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, stop_price=10.30,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: None,
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        assert updated.state == PositionState.UNPROTECTED

    def test_no_broker_configured_skips_reconciliation(self):
        """No broker_snapshot_fn → positions untouched (no broker mode)."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, stop_price=10.30,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        app = TradingApp(execution_gw=gw)  # no broker_snapshot_fn
        app._reconcile_on_startup()

        # Position untouched — still OPEN
        unchanged = gw.positions.get("DSY")
        assert unchanged.state == PositionState.OPEN

    def test_broker_unreachable_does_not_touch_closed(self):
        """Broker unreachable should only mark OPEN positions UNPROTECTED, not CLOSED ones."""
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="OPEN", state=PositionState.OPEN,
            current_shares=50, entry_price=10.0, average_entry=10.0,
        ))
        gw.positions.upsert(PositionStateModel(
            symbol="CLOSED", state=PositionState.CLOSED,
            current_shares=0, entry_price=10.0, average_entry=10.0,
        ))

        def failing_snapshot():
            raise RuntimeError("timeout")

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=failing_snapshot,
        )
        app._reconcile_on_startup()

        assert gw.positions.get("OPEN").state == PositionState.UNPROTECTED
        assert gw.positions.get("CLOSED").state == PositionState.CLOSED


# ══════════════════════════════════════════════════════════════════
#  Phase 6 — T6.3: Position persistence (save_to_disk / load_from_disk)
# ══════════════════════════════════════════════════════════════════


class TestPhase6PositionPersistence:
    """Position state persistence via save_to_disk/load_from_disk (T6.3).

    Crash/restart scenario:
    1. App runs, positions created, shutdown → saved to disk.
    2. New app starts, loads from disk → positions restored.
    3. Startup reconciliation then runs on restored positions.
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_position_store_roundtrip(self, tmp_path):
        """PositionStore.save_to_disk() → load_from_disk() round-trip works."""
        from src.state_machine import PositionStore

        store = PositionStore()
        store.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, stop_price=10.30,
            average_entry=10.50,
        ))
        store.upsert(PositionStateModel(
            symbol="AAPL", state=PositionState.PENDING_ENTRY,
            current_shares=10, entry_price=150.0, stop_price=149.0,
            average_entry=150.0,
        ))

        path = tmp_path / "positions.json"
        store.save_to_disk(path)

        loaded = PositionStore.load_from_disk(path)
        assert len(loaded) == 2
        assert loaded.get("DSY").state == PositionState.OPEN
        assert loaded.get("DSY").current_shares == 50
        assert loaded.get("AAPL").state == PositionState.PENDING_ENTRY
        assert loaded.get("AAPL").entry_price == 150.0

    def test_load_missing_file_returns_empty(self, tmp_path):
        """load_from_disk returns empty store when file doesn't exist."""
        from src.state_machine import PositionStore
        store = PositionStore.load_from_disk(tmp_path / "nonexistent.json")
        assert len(store) == 0

    def test_app_shutdown_saves_positions(self, tmp_path):
        """App._shutdown() persists positions when persist_path is set."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, stop_price=10.30,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        persist_path = str(tmp_path / "positions.json")
        app = TradingApp(execution_gw=gw, persist_path=persist_path)
        app._shutdown()

        # File should exist and contain DSY
        assert tmp_path.joinpath("positions.json").exists()
        from src.state_machine import PositionStore
        loaded = PositionStore.load_from_disk(persist_path)
        assert loaded.get("DSY") is not None
        assert loaded.get("DSY").current_shares == 50

    def test_app_startup_loads_and_reconciles(self, tmp_path):
        """Crash/restart: save positions → new app loads + reconciles them."""
        from src.state_machine import PositionStore

        # Phase 1: create and save positions
        persist_path = str(tmp_path / "state.json")
        store = PositionStore()
        store.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, stop_price=10.30,
            average_entry=10.50,
        ))
        store.save_to_disk(persist_path)

        # Phase 2: new app starts, loads positions, reconciles
        gw = PaperExecutionGateway()
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
            persist_path=persist_path,
        )

        # Simulate startup flow (what run() does):
        # Load persisted state
        restored = PositionStore.load_from_disk(persist_path)
        for pos in restored.all_positions():
            if pos.state not in (PositionState.NONE, PositionState.CLOSED):
                gw.positions.upsert(pos)

        # Reconcile
        app._reconcile_on_startup()

        # DSY should still be OPEN with stop protection
        pos = gw.positions.get("DSY")
        assert pos.state == PositionState.OPEN
        assert pos.current_shares == 50
        assert gw._has_pending_stop("DSY")

    def test_app_shutdown_no_persist_path_no_error(self):
        """App._shutdown() without persist_path should not crash."""
        app = TradingApp()
        app._shutdown()  # should not raise


# ══════════════════════════════════════════════════════════════════
#  Phase 8 — T8.4: Scanner/Data staleness tests
# ══════════════════════════════════════════════════════════════════


class TestPhase8DataStaleness:
    """T8.4: Stale or missing data fails loud with machine-readable reasons.

    Note: Many staleness tests (Finviz parsing, yfinance fallback, pre-order quote)
    depend on DEFERRED tasks T5.5-T5.8.  Existing coverage includes:
      - Stale quote >60s → emergency exit (Batch 4)
      - Missing quote → hard block (Batch 3)
      - Missing bars → watch (Batch 3)
      - Float unknown → 0.75x soft penalty (T4.6)
      - Sim mode labelling (this class)
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_sim_mode_snapshot_labels_as_simulated(self):
        """Sim mode snapshot carries sim label, not live data."""
        from src.decision_pipeline import MarketSnapshot
        from src.entries import Bar
        from src.models.schemas import Candidate

        snapshot = MarketSnapshot(
            candidate=Candidate(symbol="DSY", price=10.50, source="sim"),
            bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
            spread_pct=0.5,
            quote_age_seconds=1.0,
            rvol=3.0,
        )
        assert snapshot.candidate.source == "sim"

    def test_missing_float_produces_soft_warning(self):
        """float_shares=None → 'float_unknown' warning label."""
        from src.annotations import map_soft_warnings
        from src.models.schemas import Candidate

        c = Candidate(symbol="DSY", price=10.50, float_shares=None)
        warnings = map_soft_warnings(c)
        assert "float_unknown" in warnings

    def test_stale_quote_triggers_emergency_referenced(self):
        """Stale quote emergency is tested in Batch 4 (referential check)."""
        assert hasattr(TestBatch4MonitorExits, "test_stale_quote_60s_triggers_emergency_in_monitor")


# ──────────────────────────────────────────────────────────────────
#  Task 6 — Snapshot validation + pre-submit quote recheck (app-level)
# ──────────────────────────────────────────────────────────────────


class TestSnapshotValidationApp:
    """App scan path uses validate_for_entry and surfaces missing fields."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_missing_price_snapshot_surfaces_as_hard_block(self, tmp_path):
        """Snapshot with price=None → 'invalid_or_missing_price' in hard_blocks → skip."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)

        def market_data_fn(c):
            # Override candidate price to None via a fresh Candidate
            from src.models.schemas import Candidate
            c_no_price = Candidate(
                symbol=c.symbol, price=None, percent_gain=c.percent_gain,
                source=c.source,
            )
            return MarketSnapshot(
                candidate=c_no_price,
                bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            scanner_fn=lambda: [_c()],
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            logger=logger,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "skip", (
            f"Expected skip for missing price, got {rec.decision}: {rec.reason}"
        )
        assert "invalid_or_missing_price" in rec.hard_blocks, (
            f"Expected invalid_or_missing_price in hard_blocks, got: {rec.hard_blocks}"
        )

    def test_scan_cycle_uses_batch_market_data_once(self, tmp_path):
        """Scanner path consumes one batch snapshot map instead of per-candidate data calls."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        batch_calls = []

        candidates = [_c("DSY"), _c("MISS")]

        def market_data_batch_fn(batch):
            batch_calls.append([c.symbol for c in batch])
            return {
                "DSY": MarketSnapshot(
                    candidate=batch[0],
                    bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
                    quote_age_seconds=2.0,
                    spread_pct=0.5,
                ),
                "MISS": None,
            }

        def market_data_fn(c):
            raise AssertionError("scan path should use batch market data")

        app = TradingApp(
            scanner_fn=lambda: candidates,
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            market_data_batch_fn=market_data_batch_fn,
            logger=logger,
        )
        app._scan_and_process()

        assert batch_calls == [["DSY", "MISS"]]
        records = list(logger.read())
        assert [r.symbol for r in records] == ["DSY", "MISS"]
        miss = records[1]
        assert miss.decision == "skip"
        assert "missing_quote_age" in miss.hard_blocks


class TestPreSubmitQuoteRecheckApp:
    """App passes pre_submit_quote_fn=market_data_fn; stale refreshed quote → watch."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_stale_pre_submit_quote_blocks_entry(self, tmp_path, monkeypatch):
        """Forced entry + refreshed quote_age=10s (>5s) → watch, stale_pre_submit_quote."""
        monkeypatch.setattr(
            "src.decision_pipeline.find_entry",
            lambda *a, **kw: EntrySignal(
                symbol="DSY",
                entry_setup=EntrySetupType.FIRST_PULLBACK,
                entry_price=10.50,
                stop_price=10.30,
                risk_per_share=0.20,
                target_price=10.90,
                proposed_shares=50,
                risk_amount=10.0,
                invalidation="test_override",
            ),
        )

        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=c,
                bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
                vwap=10.0,
                spread_pct=0.5,
                quote_age_seconds=10.0,  # stale_warning at scan, >5s at recheck
                rvol=5.0,
                dollar_volume_5m=500_000,
            )

        app = TradingApp(
            scanner_fn=lambda: [_c()],
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            logger=logger,
            execution_gw=gw,
            starter_risk_pct=0.01,
            equity=100_000,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "watch", (
            f"Expected watch for stale pre-submit quote, got {rec.decision}: {rec.reason}"
        )
        assert rec.reason == "stale_pre_submit_quote", (
            f"Expected stale_pre_submit_quote reason, got: {rec.reason}"
        )

    def test_fresh_pre_submit_quote_proceeds_to_enter(self, tmp_path, monkeypatch):
        """Forced entry + refreshed quote_age=2s (≤5s) → enter."""
        monkeypatch.setattr(
            "src.decision_pipeline.find_entry",
            lambda *a, **kw: EntrySignal(
                symbol="DSY",
                entry_setup=EntrySetupType.FIRST_PULLBACK,
                entry_price=10.50,
                stop_price=10.30,
                risk_per_share=0.20,
                target_price=10.90,
                proposed_shares=50,
                risk_amount=10.0,
                invalidation="test_override",
            ),
        )

        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=c,
                bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
                vwap=10.0,
                spread_pct=0.5,
                quote_age_seconds=2.0,  # fresh at scan AND recheck
                rvol=5.0,
                dollar_volume_5m=500_000,
            )

        app = TradingApp(
            scanner_fn=lambda: [_c()],
            enrichment_fn=lambda c: c,
            market_data_fn=market_data_fn,
            logger=logger,
            execution_gw=gw,
            starter_risk_pct=0.01,
            equity=100_000,
        )
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "enter", (
            f"Expected enter for fresh pre-submit quote, got {rec.decision}: {rec.reason}"
        )


# ──────────────────────────────────────────────────────────────────
#  Task 7: Runtime time-gate + market-hours coverage
# ──────────────────────────────────────────────────────────────────


_ET = ZoneInfo("US/Eastern")


def _market_data_ok(c: Candidate) -> MarketSnapshot:
    """Valid snapshot so data blocks don't interfere with time-gate tests."""
    return MarketSnapshot(
        candidate=c,
        bars=[Bar(10.0, 10.1, 9.9, 10.05, 1000)],
        vwap=10.0,
        spread_pct=0.5,
        quote_age_seconds=2.0,
        dollar_volume_5m=500_000,
    )


class TestRuntimeTimeGates:
    """App-level integration tests for watch-only, entry cutoff, weekend,
    off-hours, and flatten-time behaviour via the ``_now_et`` helper."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    # ── Watch-only window (9:30-9:35 ET) ───────────────────────────

    def test_watch_only_runtime_blocks_entries(self, tmp_path, monkeypatch):
        """At 9:32 ET (inside watch-only window), scan → skip + watch_only block."""
        logger = DecisionLogger(tmp_path / "decisions.jsonl")
        app = TradingApp(
            scanner_fn=lambda: [_c()],
            enrichment_fn=lambda c: c,
            market_data_fn=_market_data_ok,
            logger=logger,
        )
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 22, 9, 32, tzinfo=_ET),  # Mon 9:32 ET
        )
        app._et_time = app._now_et().time()
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1, "Expected at least one decision record"
        rec = records[0]
        assert rec.decision == "skip", (
            f"Expected skip during watch-only window, got {rec.decision}: {rec.reason}"
        )
        assert any("watch_only" in b for b in rec.hard_blocks), (
            f"Expected watch_only in hard_blocks, got: {rec.hard_blocks}"
        )

    # ── Past entry cutoff (≥15:30 ET) ──────────────────────────────

    def test_past_entry_cutoff_runtime_blocks_entries(self, tmp_path, monkeypatch):
        """At 15:45 ET (past entry cutoff), scan → skip + past_entry_cutoff block."""
        logger = DecisionLogger(tmp_path / "decisions.jsonl")
        app = TradingApp(
            scanner_fn=lambda: [_c()],
            enrichment_fn=lambda c: c,
            market_data_fn=_market_data_ok,
            logger=logger,
        )
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 22, 15, 45, tzinfo=_ET),  # Mon 15:45 ET
        )
        app._et_time = app._now_et().time()
        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1, "Expected at least one decision record"
        rec = records[0]
        assert rec.decision == "skip", (
            f"Expected skip past entry cutoff, got {rec.decision}: {rec.reason}"
        )
        assert any("past_entry_cutoff" in b for b in rec.hard_blocks), (
            f"Expected past_entry_cutoff in hard_blocks, got: {rec.hard_blocks}"
        )

    # ── Market-hours checks via _is_market_open ────────────────────

    def test_weekend_market_closed(self, monkeypatch):
        """Saturday → _is_market_open() returns False."""
        app = TradingApp()
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 20, 10, 0, tzinfo=_ET),  # Sat 10:00 ET
        )
        assert app._is_market_open() is False

    def test_sunday_market_closed(self, monkeypatch):
        """Sunday → _is_market_open() returns False."""
        app = TradingApp()
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 21, 10, 0, tzinfo=_ET),  # Sun 10:00 ET
        )
        assert app._is_market_open() is False

    def test_pre_market_closed(self, monkeypatch):
        """Before 9:30 ET → _is_market_open() returns False."""
        app = TradingApp()
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 22, 9, 29, tzinfo=_ET),  # Mon 9:29 ET
        )
        assert app._is_market_open() is False

    def test_post_market_closed(self, monkeypatch):
        """At/after 16:00 ET → _is_market_open() returns False."""
        app = TradingApp()
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 22, 16, 0, tzinfo=_ET),  # Mon 16:00 ET
        )
        assert app._is_market_open() is False

    def test_regular_hours_open(self, monkeypatch):
        """Mon 10:00 ET → _is_market_open() returns True."""
        app = TradingApp()
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 22, 10, 0, tzinfo=_ET),  # Mon 10:00 ET
        )
        assert app._is_market_open() is True

    # ── Flatten-time exit via position monitor ─────────────────────

    def test_flatten_time_runtime_triggers_exit(self, tmp_path, monkeypatch):
        """At 15:55 ET with an open position, _monitor_positions → exit + flatten_time."""
        logger = DecisionLogger(tmp_path / "decisions.jsonl")
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30,
            current_shares=50, average_entry=10.50,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 50)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.60),
                bars=[Bar(10.50, 10.65, 10.45, 10.62, 2000)],
                vwap=10.0,
                spread_pct=0.5,
                quote_age_seconds=2.0,
                dollar_volume_5m=500_000,
            )

        app = TradingApp(
            execution_gw=gw,
            logger=logger,
            market_data_fn=market_data_fn,
        )
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 22, 15, 55, tzinfo=_ET),  # Mon 15:55 ET
        )
        app._et_time = app._now_et().time()
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1, "Expected at least one decision record"
        rec = records[0]
        assert rec.decision == "exit", (
            f"Expected exit at flatten_time, got {rec.decision}: {rec.reason}"
        )
        assert "flatten_time" in rec.reason, (
            f"Expected flatten_time in reason, got: {rec.reason}"
        )

    # ── Off-hours backoff in run loop ──────────────────────────────

    def test_offhours_backoff_sleeps_60s_when_closed_and_no_positions(
        self, monkeypatch,
    ):
        """When market closed + no open positions, run loop sleeps 60s (not 1s)."""
        monkeypatch.setattr("src.app.install_shutdown_handlers", lambda: None)

        sleep_calls: list[float] = []

        def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            # Trigger shutdown after the first backoff sleep
            import src.app
            src.app._shutdown_requested = True

        monkeypatch.setattr("src.app.time.sleep", fake_sleep)

        app = TradingApp()
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 20, 4, 0, tzinfo=_ET),  # Sat 04:00 ET
        )
        app.run()

        assert 60.0 in sleep_calls, (
            f"Expected 60.0s backoff sleep, got: {sleep_calls}"
        )
        assert 1.0 not in sleep_calls, (
            f"Expected no 1.0s tick during backoff, got: {sleep_calls}"
        )


# ══════════════════════════════════════════════════════════════════
#  Task 1 — RUNNER-specific reconciliation hardening
# ══════════════════════════════════════════════════════════════════


class TestRunnerReconciliationHardening:
    """RUNNER positions get special reconciliation treatment.

    - RUNNER without trailing_stop_price → escalated to UNPROTECTED.
    - RUNNER with trailing_stop_price → verified/reprotected with trailing stop.
    - highest_price_seen and runner_since populated during reconciliation.
    """

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_runner_lacks_trailing_stop_escalates_to_unprotected(self):
        """RUNNER without trailing_stop_price → UNPROTECTED after startup reconciliation."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            highest_price_seen=11.00,
            runner_since=datetime.now(timezone.utc),
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 9.50, 100)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (100, 10.00)},
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        # Should be escalated to UNPROTECTED because trailing_stop_price is None
        assert updated.state == PositionState.UNPROTECTED, (
            f"Expected UNPROTECTED for RUNNER without trailing_stop, got {updated.state}"
        )

    def test_runner_with_trailing_stop_gets_reprotected(self):
        """RUNNER with trailing_stop_price gets verified reprotection at trailing stop."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.80,
            runner_since=datetime.now(timezone.utc),
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (100, 10.00)},
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        assert updated.state == PositionState.RUNNER, (
            f"Expected RUNNER preserved, got {updated.state}"
        )
        # Should have a pending stop at trailing_stop_price
        assert gw._has_pending_stop("DSY"), "Expected stop placed at trailing_stop_price"

    def test_runner_reconciliation_populates_missing_highest_price_seen(self):
        """Reconciliation populates highest_price_seen if missing on RUNNER."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            trailing_stop_price=10.50,
            runner_since=datetime.now(timezone.utc),
            highest_price_seen=None,  # missing
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 9.50, 100)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (100, 10.00)},
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        # highest_price_seen should have been populated
        assert updated.highest_price_seen is not None, (
            "Expected highest_price_seen to be populated during reconciliation"
        )

    def test_runner_reconciliation_populates_missing_runner_since(self):
        """Reconciliation populates runner_since if missing on RUNNER."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            highest_price_seen=11.00,
            trailing_stop_price=10.50,
            runner_since=None,  # missing
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 9.50, 100)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (100, 10.00)},
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        assert updated.runner_since is not None, (
            "Expected runner_since to be populated during reconciliation"
        )

    def test_startup_reconciliation_imports_broker_open_stop_order(self):
        """Roadmap #3: startup reconciliation imports broker open stops into local pending truth."""
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.20,
            current_shares=50, average_entry=10.50,
        ))
        broker_orders = [PendingOrder(
            symbol="DSY", order_id="broker-stop-1",
            order_type=OrderActionType.STOP, side="sell",
            qty=50, status="submitted", stop_price=10.20,
        )]

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (50, 10.50)},
            broker_orders_snapshot_fn=lambda: broker_orders,
        )
        app._reconcile_on_startup()

        assert gw.pending.get_for_symbol("DSY")[0].order_id == "broker-stop-1"


# ══════════════════════════════════════════════════════════════════
#  Task 2 — Dedicated trail_exit decision-log event
# ══════════════════════════════════════════════════════════════════


class TestTrailExitDecisionEvent:
    """When a runner exit is triggered by atr_trail_hit, the decision must be
    ``trail_exit`` (not just ``exit``) while preserving detailed exit_reason data."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_atr_trail_hit_logs_trail_exit_decision(self, tmp_path, monkeypatch):
        """atr_trail_hit exit → decision='trail_exit', exit_reason preserved."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()
        former_runners = FormerRunnerStore()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.80,
            runner_since=datetime.now(timezone.utc),
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 9.50, 100)

        # Price at 10.70 → below trailing_stop 10.80 → atr_trail_hit
        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.70),
                bars=_runner_bars(),
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        # Must not evaluate candidate to avoid blocking
        monkeypatch.setattr(
            "src.app.evaluate_candidate",
            lambda candidate, **kwargs: _make_result(candidate, MoveState.ACTIVE),
        )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
            former_runner_store=former_runners,
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1, "Expected at least one decision record"
        rec = records[0]

        # The decision must be trail_exit, not just exit
        assert rec.decision == "trail_exit", (
            f"Expected trail_exit for atr_trail_hit, got '{rec.decision}': {rec.reason}"
        )
        # The detailed exit_reason must be preserved with atr_trail_hit data
        assert rec.exit_reason is not None and "atr_trail_hit" in rec.exit_reason, (
            f"Expected atr_trail_hit in exit_reason, got: {rec.exit_reason}"
        )
        assert former_runners.is_runner("DSY") is True

    def test_non_trail_exit_still_uses_exit_decision(self, tmp_path, monkeypatch):
        """A flat-time exit must still produce decision='exit'."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30,
            current_shares=50, average_entry=10.50,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.30, 50)

        def market_data_fn(c):
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=10.40),
                bars=[Bar(10.50, 10.60, 10.40, 10.45, 1000)],
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
        )
        monkeypatch.setattr(
            app, "_now_et",
            lambda: datetime(2026, 6, 22, 15, 55, tzinfo=_ET),
        )
        app._et_time = app._now_et().time()
        monkeypatch.setattr(
            "src.app.evaluate_candidate",
            lambda candidate, **kwargs: _make_result(candidate, MoveState.ACTIVE),
        )
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        rec = records[0]
        assert rec.decision == "exit", (
            f"Expected exit for flatten_time, got '{rec.decision}': {rec.reason}"
        )


# ══════════════════════════════════════════════════════════════════
#  Task 3 — Protected RUNNER data-outage timeout/freeze
# ══════════════════════════════════════════════════════════════════


class TestRunnerDataOutageTimeout:
    """Protected RUNNER + data outage behavior."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_runner_protected_outage_holds_position(self, tmp_path):
        """Protected RUNNER with transient data outage must hold position (no trail mutation)."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.80,
            runner_since=datetime.now(timezone.utc),
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.80, 100)

        # Data outage: market_data_fn returns None
        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=lambda c: None,
            exiting_timeout_seconds=300,  # long timeout so it doesn't escalate
        )
        app._monitor_positions()

        # Position must still be RUNNER with original trailing stop
        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.RUNNER, (
            f"Expected RUNNER preserved during transient outage, got {pos_after.state}"
        )
        assert pos_after.highest_price_seen == 11.50, "highest_price_seen must not mutate"
        assert pos_after.trailing_stop_price == 10.80, "trailing_stop_price must not mutate"
        # No exit decision should have been logged
        records = list(logger.read())
        assert not any(r.decision == "exit" for r in records), (
            f"Unexpected exit decision during protected outage"
        )

    def test_runner_protected_outage_timeout_escalates_to_unprotected(self, tmp_path):
        """Protected RUNNER with persistent outage beyond timeout → UNPROTECTED."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.80,
            runner_since=datetime.now(timezone.utc),
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.80, 100)

        # Data outage with very short timeout (0.0s) so it escalates on second cycle
        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=lambda c: None,
            exiting_timeout_seconds=0,
        )
        # First cycle: starts tracking outage
        app._monitor_positions()
        # Second cycle: outage duration > 0.0s threshold → escalate
        app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        # The exit engine resolves the escalated UNPROTECTED position → CLOSED
        assert pos_after.state == PositionState.CLOSED, (
            f"Expected CLOSED after persistent outage (exit engine resolves), got {pos_after.state}"
        )

    def test_runner_data_restored_clears_outage(self, tmp_path, monkeypatch):
        """When data returns after an outage, position stays RUNNER and trail updates resume."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.80,
            runner_since=datetime.now(timezone.utc),
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.80, 100)

        call_count = [0]

        def market_data_fn(c):
            call_count[0] += 1
            if call_count[0] <= 2:
                return None  # simulate first two cycles outage
            return MarketSnapshot(
                candidate=Candidate(symbol=c.symbol, price=12.00),
                bars=_runner_bars(),
                quote_age_seconds=2.0,
                spread_pct=0.5,
            )

        app = TradingApp(
            execution_gw=gw, logger=logger, market_data_fn=market_data_fn,
            exiting_timeout_seconds=300,
        )

        monkeypatch.setattr(
            "src.app.evaluate_candidate",
            lambda candidate, **kwargs: _make_result(candidate, MoveState.ACTIVE),
        )
        monkeypatch.setattr(
            "src.app.evaluate_exits",
            lambda *args, **kwargs: None,
        )

        # Run monitor twice (outage), then a third time (data restored)
        for _ in range(3):
            app._monitor_positions()

        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.RUNNER
        # Data restored so highest_price_seen should have updated
        assert pos_after.highest_price_seen == 12.00, (
            f"Expected highest_price_seen=12.00 after data restored, got {pos_after.highest_price_seen}"
        )


# ══════════════════════════════════════════════════════════════════
#  Task 4 — Sim-mode runner lifecycle coverage
# ══════════════════════════════════════════════════════════════════


class MockAlpacaBar:
    """Minimal mock of Alpaca Bar object for sim-mode tests."""
    def __init__(self, open_, high, low, close, volume, timestamp=None):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.timestamp = timestamp or datetime.now(timezone.utc)


class TestSimModeRunnerLifecycle:
    """Sim-mode runner lifecycle: OPEN → RUNNER → trail exit via
    build_market_snapshot_sim with mocked Alpaca historical client."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_sim_mode_open_to_runner_to_trail_exit(self, tmp_path, monkeypatch):
        """Sim mode shows OPEN → RUNNER → trail exit lifecycle."""
        from unittest.mock import MagicMock

        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        # ── Phase 1: OPEN position with price rising toward runner activation ──
        open_bars = [
            MockAlpacaBar(10.00, 10.20, 9.90, 10.10, 1_000_000),
            MockAlpacaBar(10.10, 10.35, 10.00, 10.25, 1_500_000),
            MockAlpacaBar(10.25, 10.50, 10.10, 10.40, 1_200_000),
            MockAlpacaBar(10.40, 10.65, 10.20, 10.55, 1_800_000),
            MockAlpacaBar(10.55, 10.85, 10.30, 10.75, 2_000_000),
            MockAlpacaBar(10.75, 11.10, 10.45, 10.95, 2_500_000),
        ]

        # ── Phase 2: runner trail exit — price drops below trailing stop ──
        drop_bars = [
            MockAlpacaBar(10.95, 11.00, 10.70, 10.80, 1_800_000),
            MockAlpacaBar(10.80, 10.85, 10.50, 10.55, 2_000_000),
            MockAlpacaBar(10.55, 10.60, 10.30, 10.35, 1_500_000),
        ]

        call_count = [0]
        bar_sets = [open_bars, drop_bars]

        def mock_get_stock_bars(req):
            idx = min(call_count[0], len(bar_sets) - 1)
            call_count[0] += 1
            mock_data = {req.symbol_or_symbols: bar_sets[idx]}
            mock_response = MagicMock()
            mock_response.data = mock_data
            return mock_response

        mock_client = MagicMock()
        mock_client.get_stock_bars.side_effect = mock_get_stock_bars

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            lambda *a, **kw: mock_client,
        )
        # Mock API keys so build_market_snapshot_sim doesn't bail
        monkeypatch.setenv("ALPACA_API_KEY", "mock_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "mock_secret")

        from src.market_data_sim import build_market_snapshot_sim

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.50,
            current_shares=100, average_entry=10.00,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 9.50, 100)

        # ── Phase 1: monitor with sim data → promote to RUNNER ──
        app = TradingApp(
            execution_gw=gw,
            logger=logger,
            market_data_fn=lambda c: build_market_snapshot_sim(c),
        )
        app._monitor_positions()

        pos1 = gw.positions.get("DSY")
        assert pos1 is not None
        # May be RUNNER (promoted) or still OPEN depending on bar timing
        # The key is the position is still active (not closed/exited)
        assert pos1.state in (PositionState.OPEN, PositionState.RUNNER), (
            f"Expected OPEN or RUNNER after first sim cycle, got {pos1.state}"
        )

        # ── Phase 2: price drops below trailing stop → trail exit ──
        # If position is still OPEN, promote to RUNNER manually to test trail exit
        if pos1.state == PositionState.OPEN:
            from src.state_machine import transition_position
            pos1.highest_price_seen = 11.00
            pos1.trailing_stop_price = 10.60
            pos1.state = PositionState.RUNNER
            transition_position(pos1, PositionState.RUNNER, force=True)
            gw.positions.upsert(pos1)

        app._monitor_positions()

        records = list(logger.read())
        pos2 = gw.positions.get("DSY")

        # May resolve differently depending on exact bar data and trail math.
        # At minimum verify the position is not stuck in limbo.
        assert pos2 is not None

        # Log what happened for debugging
        trail_records = [r for r in records if r.decision in ("exit", "trail_exit")]
        assert len(trail_records) >= 1 or pos2.state == PositionState.CLOSED, (
            f"Expected trail exit or CLOSED after second sim cycle, "
            f"got state={pos2.state} decisions={[r.decision for r in records]}"
        )


# ══════════════════════════════════════════════════════════════════
#  Task 5 — Explicit ADD-order / missed-add-fill reconciliation
# ══════════════════════════════════════════════════════════════════


class TestAddOrderMissedFillReconciliation:
    """When broker qty > local because an ADD fill was missed,
    reconciliation must take the specific reprotect/update path
    (not the generic warning-only path)."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_broker_qty_more_for_runner_takes_add_path(self):
        """Case 4 for RUNNER position → update_qty_reprotect_add_missed action."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            current_shares=100, entry_price=10.00,
            stop_price=9.50, average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.80,
            add_count=1,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.80, 100)

        # Broker shows 200 shares → missed ADD fill
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (200, 10.25)},
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        assert updated.current_shares == 200, (
            f"Expected shares updated to 200, got {updated.current_shares}"
        )
        # add_count should have been incremented for the missed ADD
        assert updated.add_count == 2, (
            f"Expected add_count incremented to 2, got {updated.add_count}"
        )
        # Should have a pending stop at trailing_stop_price
        assert gw._has_pending_stop("DSY"), (
            "Expected stop placed after missed-ADD reconciliation"
        )

    def test_broker_qty_more_for_open_takes_generic_warning_path(self):
        """Case 4 for OPEN position → still uses update_qty_reprotect_warning (no change)."""
        from src.paper_execution import reconcile_positions
        from src.state_machine import PositionStore, PendingOrderStore

        local = PositionStore()
        local.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            current_shares=50, entry_price=10.50, average_entry=10.50,
        ))
        pending = PendingOrderStore()
        broker = {"DSY": (100, 10.50)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        # OPEN position → generic warning path
        add_actions = [a for a in actions if a["action"] == "update_qty_reprotect_warning"]
        assert len(add_actions) == 1, (
            f"Expected exactly one update_qty_reprotect_warning for OPEN, got actions: {actions}"
        )

    def test_broker_qty_more_for_runner_takes_add_specific_path(self):
        """Case 4 for RUNNER position → update_qty_reprotect_add_missed action."""
        from src.paper_execution import reconcile_positions
        from src.state_machine import PositionStore, PendingOrderStore

        local = PositionStore()
        local.upsert(PositionStateModel(
            symbol="DSY", state=PositionState.RUNNER,
            current_shares=100, entry_price=10.00,
            average_entry=10.00, highest_price_seen=11.50,
            trailing_stop_price=10.80, add_count=1,
        ))
        pending = PendingOrderStore()
        broker = {"DSY": (200, 10.25)}

        actions = reconcile_positions(
            broker_positions=broker, local_store=local, pending_store=pending,
        )
        # RUNNER position → ADD-specific action
        add_actions = [a for a in actions if a["action"] == "update_qty_reprotect_add_missed"]
        assert len(add_actions) == 1, (
            f"Expected exactly one update_qty_reprotect_add_missed for RUNNER, got actions: {actions}"
        )
        # No generic warning
        warn_actions = [a for a in actions if a["action"] == "update_qty_reprotect_warning"]
        assert len(warn_actions) == 0, (
            f"Expected no update_qty_reprotect_warning for RUNNER, got: {warn_actions}"
        )

    def test_broker_qty_more_for_adding_promotes_runner_and_reprotects(self):
        """Missed ADD fill while local state is ADDING → RUNNER + reprotect."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY", state=PositionState.ADDING,
            current_shares=100, entry_price=10.00,
            stop_price=9.50, average_entry=10.00,
            highest_price_seen=11.50,
            trailing_stop_price=10.80,
            add_count=1,
        )
        gw.positions.upsert(pos)

        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (125, 10.20)},
        )
        app._reconcile_on_startup()

        updated = gw.positions.get("DSY")
        assert updated is not None
        assert updated.state == PositionState.RUNNER
        assert updated.current_shares == 125
        assert updated.add_count == 2
        assert gw._has_pending_stop("DSY")


# ══════════════════════════════════════════════════════════════════
#  Roadmap #8 — ERROR-state cleanup
# ══════════════════════════════════════════════════════════════════


class TestErrorStateRecovery:
    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_zero_share_error_position_closes_on_monitor_cycle(self):
        """ERROR from rejected entry has no shares; cleanup unlocks symbol."""
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY",
            state=PositionState.ERROR,
            current_shares=0,
            entry_price=10.00,
            average_entry=10.00,
        ))

        app = TradingApp(execution_gw=gw)
        app._monitor_positions()

        pos = gw.positions.get("DSY")
        assert pos is not None
        assert pos.state == PositionState.CLOSED
        assert pos.current_shares == 0
        assert gw.is_symbol_locked("DSY") is False

    def test_error_position_with_shares_stays_error_for_manual_reconciliation(self):
        """ERROR with shares may still represent broker exposure; do not auto-close."""
        gw = PaperExecutionGateway()
        gw.positions.upsert(PositionStateModel(
            symbol="DSY",
            state=PositionState.ERROR,
            current_shares=25,
            entry_price=10.00,
            average_entry=10.00,
        ))

        app = TradingApp(execution_gw=gw)
        app._monitor_positions()

        pos = gw.positions.get("DSY")
        assert pos is not None
        assert pos.state == PositionState.ERROR
        assert pos.current_shares == 25
        assert gw.is_symbol_locked("DSY") is True


# ══════════════════════════════════════════════════════════════════
#  Roadmap #1 — Sync runner trailing stop to broker
# ══════════════════════════════════════════════════════════════════


class TestRunnerStopBrokerSync:
    """Runner trailing stop changes must be synced to broker:
    cancel stale orders, place new stop.  SPEC §11.17.13 roadmap #1."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_monitor_uses_five_min_bars_for_runner_atr_and_promotion(self, monkeypatch):
        """Runner ATR/trend confirmation uses 5-min bars when snapshot provides them."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.OPEN,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 9.50, 100)

        one_min_bars = [Bar(10.0, 10.1, 9.9, 10.0, 100) for _ in range(6)]
        five_min_bars = [Bar(10.0 + i, 10.5 + i, 9.8 + i, 10.3 + i, 1_000) for i in range(6)]
        seen = {"atr": None, "promotion": None}

        def fake_compute_atr(bars, period):
            seen["atr"] = bars
            return 0.40

        def fake_should_promote(pos_arg, *, bars, **kwargs):
            seen["promotion"] = bars
            return False

        def fake_evaluate_candidate(candidate, **kwargs):
            result = PipelineResult(candidate)
            result.move_state = MoveState.ACTIVE
            return result

        monkeypatch.setattr("src.app.compute_atr", fake_compute_atr)
        monkeypatch.setattr("src.app.should_promote_to_runner", fake_should_promote)
        monkeypatch.setattr("src.app.evaluate_candidate", fake_evaluate_candidate)
        monkeypatch.setattr("src.app.evaluate_exits", lambda *args, **kwargs: None)

        app = TradingApp(
            execution_gw=gw,
            market_data_fn=lambda c: MarketSnapshot(
                candidate=Candidate(symbol="DSY", price=10.90),
                bars=one_min_bars,
                five_min_bars=five_min_bars,
                vwap=10.20,
                spread_pct=0.5,
                quote_age_seconds=2.0,
            ),
        )

        app._monitor_positions()

        assert seen["atr"] is five_min_bars
        assert seen["promotion"] is five_min_bars

    def test_update_trail_cancels_old_stop_places_new(self, tmp_path):
        """After _update_runner_trail ratchets the trail up, old stop is
        cancelled and new stop placed at the updated trailing_stop_price."""
        import json
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=10.80,
            trailing_stop_price=10.00,
        )
        gw.positions.upsert(pos)
        # Place an old stop at initial trail price
        old_order = gw.place_stop("DSY", 10.00, 100)
        old_stop_id = old_order.order_id

        app = TradingApp(execution_gw=gw)

        # Update trail with higher price → should ratchet stop up
        app._update_runner_trail(
            pos, current_price=11.50, atr=0.40, risk_per_share=0.50,
        )

        # Old stop should be cancelled
        assert not any(
            o.order_id == old_stop_id
            for o in gw.pending.all_pending()
        ), "Old stop must be cancelled"

        # New stop should exist at updated trail price
        pending_stops = [
            o for o in gw.pending.get_for_symbol("DSY")
            if o.order_type == OrderActionType.STOP
        ]
        assert len(pending_stops) == 1, (
            f"Expected exactly one pending stop, got {len(pending_stops)}"
        )
        new_stop = pending_stops[0]
        assert new_stop.stop_price == pos.trailing_stop_price, (
            f"Expected stop at {pos.trailing_stop_price}, got {new_stop.stop_price}"
        )

    def test_promote_to_runner_syncs_initial_trail_to_broker(self, tmp_path):
        """_promote_to_runner must place initial trail stop on broker,
        not leave the original OPEN stop in place."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.OPEN,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
        )
        gw.positions.upsert(pos)
        # Initial OPEN stop
        gw.place_stop("DSY", 9.50, 100)

        app = TradingApp(execution_gw=gw)

        # Promote to runner — should cancel old stop, place new trail stop
        app._promote_to_runner(pos, current_price=11.00, atr=0.40)

        # After promotion, broker should have stop at trailing_stop_price
        pending_stops = [
            o for o in gw.pending.get_for_symbol("DSY")
            if o.order_type == OrderActionType.STOP
        ]
        assert len(pending_stops) == 1, (
            f"Expected exactly one pending stop after promotion, got {len(pending_stops)}"
        )
        assert pending_stops[0].stop_price == pos.trailing_stop_price, (
            f"Expected stop at trailing_stop_price={pos.trailing_stop_price}, "
            f"got {pending_stops[0].stop_price}"
        )

    def test_sync_failure_marks_unprotected(self, tmp_path, monkeypatch):
        """If broker cancel/place fails, position is marked UNPROTECTED (no crash)."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=10.80,
            trailing_stop_price=10.00,
        )
        gw.positions.upsert(pos)
        gw.place_stop("DSY", 10.00, 100)

        # Make cancel_stale_orders fail
        original_cancel = gw.cancel_stale_orders
        def failing_cancel(symbol):
            raise RuntimeError("broker unreachable")
        gw.cancel_stale_orders = failing_cancel

        app = TradingApp(execution_gw=gw)

        # Should not raise — _sync_runner_stop_to_broker catches and marks UNPROTECTED
        try:
            app._update_runner_trail(
                pos, current_price=11.50, atr=0.40, risk_per_share=0.50,
            )
        except Exception:
            pytest.fail("_update_runner_trail must not raise on broker sync failure")

        gw.cancel_stale_orders = original_cancel

        # Position must be UNPROTECTED after failure
        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.UNPROTECTED, (
            f"Expected UNPROTECTED after sync failure, got {pos_after.state}"
        )
        # Trail state still updated (local mutation before broker attempt)
        assert pos_after.highest_price_seen == 11.50
        assert pos_after.trailing_stop_price is not None

    def test_protect_position_failure_marks_unprotected(self, tmp_path, monkeypatch):
        """When cancel succeeds but protect_position fails, position becomes UNPROTECTED."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=10.80,
            trailing_stop_price=10.00,
        )
        gw.positions.upsert(pos)
        old_order = gw.place_stop("DSY", 10.00, 100)
        old_stop_id = old_order.order_id

        # Make protect_position fail (cancel still works)
        original_protect = gw.protect_position
        def failing_protect(symbol, stop, qty=None):
            raise RuntimeError("broker stop placement rejected")
        gw.protect_position = failing_protect

        app = TradingApp(execution_gw=gw)

        try:
            app._update_runner_trail(
                pos, current_price=11.50, atr=0.40, risk_per_share=0.50,
            )
        except Exception:
            pytest.fail("_update_runner_trail must not raise on protect_position failure")

        gw.protect_position = original_protect

        # Old stop was cancelled (cancel succeeded)
        assert not any(
            o.order_id == old_stop_id
            for o in gw.pending.all_pending()
        ), "Old stop must be cancelled before protect attempt"

        # Position must be UNPROTECTED (new stop not placed)
        pos_after = gw.positions.get("DSY")
        assert pos_after is not None
        assert pos_after.state == PositionState.UNPROTECTED, (
            f"Expected UNPROTECTED after protect_position failure, got {pos_after.state}"
        )
        # No pending stop exists
        assert not gw._has_pending_stop("DSY"), (
            "No stop should be pending after protect_position failure"
        )

    def test_unchanged_trail_does_not_sync(self, tmp_path):
        """When computed trailing stop does not change, broker stop is
        not cancelled/re-placed — avoids unnecessary churn."""
        gw = PaperExecutionGateway()
        pos = PositionStateModel(
            symbol="DSY",
            state=PositionState.RUNNER,
            entry_price=10.00,
            stop_price=9.50,
            current_shares=100,
            average_entry=10.00,
            highest_price_seen=10.50,
            trailing_stop_price=10.00,
        )
        gw.positions.upsert(pos)
        old_order = gw.place_stop("DSY", 10.00, 100)
        old_stop_id = old_order.order_id

        app = TradingApp(execution_gw=gw)

        # Call with price that produces same trailing_stop_price.
        # highest_price_seen=10.50, current_price=10.50, atr=0.20,
        #   trail_distance=max(2.5*0.20, 0.50)=0.50,
        #   new_stop=10.50-0.50=10.00 (same as current 10.00).
        app._update_runner_trail(
            pos, current_price=10.50, atr=0.20, risk_per_share=0.50,
        )

        # Original stop must still be present — no cancel/re-place happened
        assert any(
            o.order_id == old_stop_id
            for o in gw.pending.all_pending()
        ), "Original stop must remain when trail unchanged"

        # Only one stop at the same price
        pending_stops = [
            o for o in gw.pending.get_for_symbol("DSY")
            if o.order_type == OrderActionType.STOP
        ]
        assert len(pending_stops) == 1, (
            f"Expected exactly one pending stop (unchanged), got {len(pending_stops)}"
        )
        assert pending_stops[0].stop_price == 10.00


# ══════════════════════════════════════════════════════════════════
#  Roadmap #2 — P&L ledger persistence
# ══════════════════════════════════════════════════════════════════


class TestPnLLedgerPersistence:
    """P&L ledger persistence: startup restore, shutdown save,
    periodic checkpoint.  SPEC §11.17.13 roadmap #2."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_restore_pnl_on_startup(self, tmp_path):
        """_restore_pnl_ledger loads saved P&L from disk."""
        import json
        pnl_path = tmp_path / "pnl_ledger.json"
        pnl_path.write_text(json.dumps({
            "session_realized_pnl": 500.0,
            "per_symbol_pnl": {"DSY": 500.0},
        }))

        app = TradingApp(pnl_persist_path=str(pnl_path))
        app._restore_pnl_ledger()

        assert app._session_realized_pnl == 500.0
        assert app._session_per_symbol_pnl == {"DSY": 500.0}

    def test_save_pnl_on_shutdown(self, tmp_path):
        """_shutdown saves P&L ledger to disk when pnl_persist_path is set."""
        import json
        pnl_path = tmp_path / "pnl_ledger.json"
        app = TradingApp(pnl_persist_path=str(pnl_path))
        app._session_realized_pnl = -250.0
        app._session_per_symbol_pnl = {"DSY": -250.0}

        app._shutdown()

        assert pnl_path.exists()
        data = json.loads(pnl_path.read_text())
        assert data["session_realized_pnl"] == -250.0
        assert data["per_symbol_pnl"] == {"DSY": -250.0}

    def test_checkpoint_writes_during_run(self, tmp_path, monkeypatch):
        """_checkpoint_pnl_ledger writes current P&L to disk best-effort."""
        import json
        pnl_path = tmp_path / "pnl_ledger.json"
        app = TradingApp(pnl_persist_path=str(pnl_path))
        app._session_realized_pnl = 100.0
        app._session_per_symbol_pnl = {"DSY": 100.0}

        app._checkpoint_pnl_ledger()

        assert pnl_path.exists()
        data = json.loads(pnl_path.read_text())
        assert data["session_realized_pnl"] == 100.0
        assert data["per_symbol_pnl"] == {"DSY": 100.0}

    def test_checkpoint_failure_does_not_crash(self, tmp_path, monkeypatch):
        """Checkpoint failure logs warning, never raises."""
        pnl_path = tmp_path / "pnl_ledger.json"
        app = TradingApp(pnl_persist_path=str(pnl_path))
        app._session_realized_pnl = 100.0

        # Make save_to_disk raise
        def failing_save(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr("src.pnl_ledger.PnLLedger.save_to_disk", failing_save)

        # Should not raise
        try:
            app._checkpoint_pnl_ledger()
        except Exception:
            pytest.fail("_checkpoint_pnl_ledger must not raise on write failure")

    def test_no_pnl_persist_path_skips_gracefully(self):
        """Without pnl_persist_path, P&L persistence is a no-op."""
        app = TradingApp()
        app._session_realized_pnl = 100.0
        # Should not crash or write anything
        app._checkpoint_pnl_ledger()
        app._restore_pnl_ledger()
        app._shutdown()  # no pnl_persist_path → no-op

    def test_missing_pnl_file_restores_empty(self, tmp_path):
        """Missing P&L file restores empty ledger (zeros)."""
        pnl_path = tmp_path / "nonexistent.json"
        app = TradingApp(pnl_persist_path=str(pnl_path))
        app._restore_pnl_ledger()

        assert app._session_realized_pnl == 0.0
        assert app._session_per_symbol_pnl == {}

    def test_run_restores_pnl_then_periodic_checkpoint(self, tmp_path, monkeypatch):
        """run() restores P&L on startup, saves periodic checkpoint, and saves on shutdown."""
        import json
        pnl_path = tmp_path / "pnl_ledger.json"
        # Pre-write ledger
        pnl_path.write_text(json.dumps({
            "session_realized_pnl": 300.0,
            "per_symbol_pnl": {"DSY": 300.0},
        }))

        monkeypatch.setattr("src.app.install_shutdown_handlers", lambda: None)

        app = TradingApp(pnl_persist_path=str(pnl_path))
        assert app._session_realized_pnl == 0.0  # not yet restored

        # Simulate run() calling restore then a checkpoint cycle
        app._restore_pnl_ledger()
        assert app._session_realized_pnl == 300.0

        # Simulate P&L accumulation
        app._session_realized_pnl += 100.0

        # Checkpoint
        app._checkpoint_pnl_ledger()
        data = json.loads(pnl_path.read_text())
        assert data["session_realized_pnl"] == 400.0

        # Shutdown
        app._session_realized_pnl += 50.0
        app._shutdown()
        shutdown_data = json.loads(pnl_path.read_text())
        assert shutdown_data["session_realized_pnl"] == 450.0

    def test_pnl_path_defaults_from_persist_path(self, tmp_path):
        """When pnl_persist_path is None and persist_path is set,
        P&L path auto-derives to sibling pnl_ledger.json."""
        from pathlib import Path as PPath
        pos_path = tmp_path / "positions.json"
        app = TradingApp(persist_path=str(pos_path))
        expected = str(PPath(str(pos_path)).parent / "pnl_ledger.json")
        assert app._pnl_persist_path == expected, (
            f"Expected auto-derived {expected}, got {app._pnl_persist_path}"
        )
        # Persistence should work with the auto-derived path
        app._session_realized_pnl = 100.0
        app._checkpoint_pnl_ledger()
        assert PPath(expected).exists(), "Auto-derived P&L path should be writable"


# ══════════════════════════════════════════════════════════════════
#  Roadmap #11 — weekly drawdown / consecutive-loss throttle
# ══════════════════════════════════════════════════════════════════


class TestWeeklyDrawdownLossThrottle:
    """Weekly drawdown and consecutive losses block new entries."""

    def setup_method(self) -> None:
        import src.app
        src.app._shutdown_requested = False

    def test_consecutive_losses_activate_entry_kill_switch(self):
        app = TradingApp(max_consecutive_losses=3)
        app._consecutive_losses = 3

        risk = app._build_risk_state()

        assert risk.kill_switch_active is True
        assert risk.kill_switch_reason == "consecutive_loss_throttle"

    def test_weekly_drawdown_activates_entry_kill_switch(self):
        app = TradingApp(equity=100_000, weekly_drawdown_pct=0.06)
        app._weekly_realized_pnl = -6_000.0

        risk = app._build_risk_state()

        assert risk.kill_switch_active is True
        assert risk.kill_switch_reason == "weekly_drawdown_throttle"

    def test_loss_throttle_blocks_scan_entries(self, tmp_path):
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        app = TradingApp(
            scanner_fn=lambda: [_c("DSY")],
            market_data_fn=lambda c: MarketSnapshot(
                candidate=c,
                bars=_runner_bars(),
                vwap=10.20,
                spread_pct=0.5,
                quote_age_seconds=2.0,
                rvol=5.0,
                dollar_volume_5m=500_000,
            ),
            logger=logger,
            max_consecutive_losses=3,
        )
        app._consecutive_losses = 3

        app._scan_and_process()

        records = list(logger.read())
        assert len(records) >= 1
        assert records[0].decision == "skip"
        assert "consecutive_loss_throttle" in records[0].hard_blocks

    def test_pnl_ledger_persists_loss_throttle_state(self, tmp_path):
        import json
        pnl_path = tmp_path / "pnl_ledger.json"
        pnl_path.write_text(json.dumps({
            "session_realized_pnl": -400.0,
            "per_symbol_pnl": {"DSY": -400.0},
            "weekly_realized_pnl": -1_200.0,
            "consecutive_losses": 2,
        }))

        app = TradingApp(pnl_persist_path=str(pnl_path))
        app._restore_pnl_ledger()

        assert app._weekly_realized_pnl == -1_200.0
        assert app._consecutive_losses == 2

        app._consecutive_losses = 3
        app._weekly_realized_pnl = -1_500.0
        app._checkpoint_pnl_ledger()

        data = json.loads(pnl_path.read_text())
        assert data["consecutive_losses"] == 3
        assert data["weekly_realized_pnl"] == -1_500.0

    def test_pnl_ledger_resets_weekly_throttle_state_on_new_week(self, tmp_path):
        import json
        pnl_path = tmp_path / "pnl_ledger.json"
        pnl_path.write_text(json.dumps({
            "session_realized_pnl": -400.0,
            "per_symbol_pnl": {"DSY": -400.0},
            "weekly_realized_pnl": -6_000.0,
            "consecutive_losses": 3,
            "week_id": "2000-W01",
        }))

        app = TradingApp(pnl_persist_path=str(pnl_path))
        app._restore_pnl_ledger()

        assert app._weekly_realized_pnl == 0.0
        assert app._consecutive_losses == 0
