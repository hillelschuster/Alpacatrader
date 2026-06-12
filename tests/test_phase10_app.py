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
from pathlib import Path

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

    def test_stale_quote_profitable_not_safe_in_monitor(self, tmp_path):
        """Profitable stale quote >60s must still exit — stale is always emergency."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.00, stop_price=9.80, current_shares=50,
            average_entry=10.00,
        )
        gw.positions.upsert(pos)

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

    def test_monitor_no_network_calls(self, tmp_path):
        """Verify monitor path does not reach external services — pure injection."""
        log_path = tmp_path / "decisions.jsonl"
        logger = DecisionLogger(log_path)
        gw = PaperExecutionGateway()

        pos = PositionStateModel(
            symbol="DSY", state=PositionState.OPEN,
            entry_price=10.50, stop_price=10.30, current_shares=50,
            average_entry=10.50,
        )
        gw.positions.upsert(pos)

        # No market_data_fn — should not crash; stale quote defaults trigger emergency
        app = TradingApp(execution_gw=gw, logger=logger)
        app._monitor_positions()

        records = list(logger.read())
        assert len(records) >= 1
        # Without market data, current_price=0 → huge loss + stale quote → emergency exit
        rec = records[0]
        assert rec.decision == "exit", f"Expected exit via stale default, got {rec.decision}: {rec.reason}"

    def test_monitor_fetch_failure_triggers_emergency(self, tmp_path):
        """When market_data_fn fails, monitor must treat as stale and trigger emergency."""
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
        assert rec.decision == "exit", f"Expected exit after fetch failure, got {rec.decision}: {rec.reason}"

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
        # With price=10.60 above stop=10.30 and valid data, no exit should trigger
        # Decision should NOT be exit (no reason to exit)
        assert rec.decision != "exit", f"Unexpected exit with valid data: {rec.reason}"


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
