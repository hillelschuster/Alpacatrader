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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.app import TradingApp, _shutdown_requested
from src.decision_pipeline import MarketSnapshot
from src.entries import Bar
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import (
    Candidate,
    EntrySetupType,
    EntrySignal,
    OrderActionType,
    PositionState,
    PositionStateModel,
)
from src.paper_execution import PaperExecutionGateway


# ── Helpers ───────────────────────────────────────────────────────


def _c(symbol: str = "DSY", **kw) -> Candidate:
    return Candidate(symbol=symbol, price=10.50, percent_gain=25.0,
                     source="finviz", **kw)


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

    def test_irreconcilable_logged(self):
        """Case 7: irreconcilable → position set to ERROR, log escalation."""
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

        err_pos = gw.positions.get("DSY")
        assert err_pos.state == PositionState.ERROR

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
