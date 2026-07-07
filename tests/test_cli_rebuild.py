"""CLI path tests.

Verifies:
  1. Default CLI (--mode mock --once) exercises the pipeline.
  2. Paper mode runs the pipeline via scanner (monkeypatched).
  3. Live mode remains blocked.
  4. Paper mode market-data enrichment works.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from main import main_cli


# ──────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────


def _run_cli(*args: str) -> tuple[int, str]:
    """Invoke ``main_cli`` via Click's CliRunner and return (exit_code, output)."""
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(main_cli, list(args), catch_exceptions=False)
    return result.exit_code, result.output


# ──────────────────────────────────────────────────────────────────
#  1. Default mock --once exercises the pipeline
# ──────────────────────────────────────────────────────────────────


class TestDefaultMockOnce:
    """python main.py --mode mock --once runs the pipeline."""

    def test_exit_code_zero(self):
        """Default CLI exits successfully."""
        ec, out = _run_cli("--mode", "mock", "--once")
        assert ec == 0, f"exit code {ec}, output:\n{out}"

    def test_banner_shows_pipeline_flow(self):
        """Startup banner contains the pipeline steps."""
        ec, out = _run_cli("--mode", "mock", "--once")
        assert "Attention" in out
        assert "Hard Filters" in out
        assert "Entry Setup" in out
        assert "Sizing" in out
        assert "DecisionRecord" in out

    def test_log_shows_pipeline_flow(self):
        """Logger output contains the pipeline steps."""
        ec, out = _run_cli("--mode", "mock", "--once")
        assert "Scan " in out
        assert "Attention" in out
        assert "Confidence" in out
        assert "Soft Warnings" in out
        assert "Hard Filters" in out
        assert "Move State" in out

    def test_module_names_in_output(self):
        """Output references the decision pipeline."""
        ec, out = _run_cli("--mode", "mock", "--once")
        assert "Pipeline" in out
        assert "decision=" in out, f"Pipeline results missing:\n{out}"
        assert "hard_passed" in out or "hard_" in out, \
            f"Hard filter results missing:\n{out}"


# ──────────────────────────────────────────────────────────────────
#  Paper mode — pipeline path (via Finviz scanner)
# ──────────────────────────────────────────────────────────────────


def _make_paper_candidates():
    """Return deterministic synthetic candidates for paper-mode tests."""
    from datetime import datetime, timezone
    from src.models.schemas import Candidate

    now = datetime.now(timezone.utc)
    return [
        Candidate(
            symbol="GAPR", price=8.50, relative_volume=7.0,
            dollar_volume=42_500_000, previous_close=7.20,
            sector="Technology", industry="Software",
            source="finviz", source_timestamp=now,
        ),
        Candidate(
            symbol="CHIPR", price=12.00, relative_volume=6.5,
            dollar_volume=36_000_000, previous_close=10.43,
            sector="Technology", industry="Semiconductors",
            source="finviz", source_timestamp=now,
        ),
    ]


class TestPaperMode:
    """Paper mode runs the pipeline via Finviz scanner (monkeypatched)."""

    def test_paper_once_with_candidates(self):
        """--mode paper --once exits 0 and shows pipeline results."""
        with patch("src.scanner.scanner.scan_dynamic_candidates",
                   return_value=_make_paper_candidates()), \
             patch("src.market_data.build_market_snapshot",
                   return_value=None):
            ec, out = _run_cli("--mode", "paper", "--once")
        assert ec == 0, f"exit code {ec}, output:\n{out}"
        assert "Paper Mode" in out, f"Paper Mode header missing:\n{out}"
        assert "Pipeline" in out, f"Pipeline header missing:\n{out}"
        assert "decision=" in out, f"Pipeline results missing:\n{out}"
        assert "GAPR" in out, f"Candidate GAPR not in output:\n{out}"
        assert "CHIPR" in out, f"Candidate CHIPR not in output:\n{out}"

    def test_paper_once_no_candidates(self):
        """--mode paper --once with empty scanner exits 0, no-candidates message."""
        with patch("src.scanner.scanner.scan_dynamic_candidates",
                   return_value=[]):
            ec, out = _run_cli("--mode", "paper", "--once")
        assert ec == 0, f"exit code {ec}, output:\n{out}"
        assert "no candidates" in out.lower(), (
            f"Expected no-candidates message in output:\n{out}"
        )

    def test_paper_loop_starts_trading_app(self):
        """--mode paper --loop imports and instantiates TradingApp."""
        import builtins

        original_import = builtins.__import__
        trading_app_called = [False]

        def _guard_import(name, *args, **kwargs):
            if "TradingApp" in str(name) and "src.app" in str(name):
                trading_app_called[0] = True
                # Allow the import to proceed — module-level imports are fine.
                # We just track it was accessed.
            return original_import(name, *args, **kwargs)

        with patch("src.app.TradingApp.run", return_value=None), \
             patch("src.scanner.scanner.scan_dynamic_candidates", return_value=[]), \
             patch("builtins.__import__", _guard_import):
            ec, out = _run_cli("--mode", "paper", "--loop")
            # Loop may exit immediately if scanner returns empty (TradingApp
            # loop runs one monitor cycle and one scan cycle before checking
            # is_running again — mocked run is a no-op, so fine).
            assert ec == 0, f"exit code {ec}:\n{out}"
            assert "Paper Loop" in out, f"Loop mode header missing:\n{out}"
            assert "Pipeline" in out, f"Pipeline header missing:\n{out}"


# ──────────────────────────────────────────────────────────────────
#  5. Live mode is blocked
# ──────────────────────────────────────────────────────────────────


class TestLiveMode:
    """Live mode: blocked without confirmation, proceeds with confirmation."""

    def test_live_mode_blocked_without_confirmation(self, monkeypatch):
        """--mode live --once exits non-zero without explicit confirmation."""
        monkeypatch.delenv("TRADING_LIVE_TRADING_CONFIRMED", raising=False)
        ec, out = _run_cli("--mode", "live", "--once")
        assert ec != 0, (
            f"Live mode without confirmation should exit non-zero, got {ec}. Output:\n{out}"
        )

    def test_live_confirmed_proceeds_no_candidates(self, monkeypatch):
        """--mode live --once with confirmation and no candidates exits cleanly."""
        monkeypatch.setenv("TRADING_LIVE_TRADING_CONFIRMED", "yes_i_accept_the_risks")
        with patch("src.scanner.scanner.scan_dynamic_candidates", return_value=[]):
            ec, out = _run_cli("--mode", "live", "--once")
        assert ec == 0, (
            f"Confirmed live mode with no candidates should exit 0, got {ec}. Output:\n{out}"
        )
        assert "no candidates" in out.lower(), (
            f"Expected no-candidates message. Output:\n{out}"
        )

    def test_live_loop_starts_trading_app(self, monkeypatch):
        """--mode live --loop instantiates TradingApp with paper_mode=False."""
        monkeypatch.setenv("TRADING_LIVE_TRADING_CONFIRMED", "yes_i_accept_the_risks")
        monkeypatch.setattr(
            "src.paper_execution.build_alpaca_account_equity",
            lambda gw: 54321.09,
            raising=False,
        )
        with patch("src.app.TradingApp.run", return_value=None), \
             patch("src.scanner.scanner.scan_dynamic_candidates", return_value=[]):
            ec, out = _run_cli("--mode", "live", "--loop")
        assert ec == 0, f"exit code {ec}:\n{out}"
        assert "Live Loop" in out, f"Live Loop header missing:\n{out}"


# ──────────────────────────────────────────────────────────────────
#  6. Paper mode market-data enrichment (SPEC §22.15 items 17, 20)
# ──────────────────────────────────────────────────────────────────


def _mock_market_snapshot(candidate, **kwargs):
    """Return a deterministic MarketSnapshot for paper enrichment tests.

    Uses real prices so hard filters pass the spread/quote checks.
    """
    from datetime import datetime, timezone
    from src.decision_pipeline import MarketSnapshot
    from src.entries import Bar

    now = datetime.now(timezone.utc)
    bars = [
        Bar(open=8.50, high=8.80, low=8.40, close=8.60, volume=500_000, timestamp=now),
        Bar(open=8.60, high=8.85, low=8.55, close=8.75, volume=550_000, timestamp=now),
        Bar(open=8.75, high=8.90, low=8.65, close=8.82, volume=480_000, timestamp=now),
        Bar(open=8.82, high=8.95, low=8.70, close=8.88, volume=520_000, timestamp=now),
        Bar(open=8.88, high=9.10, low=8.80, close=9.05, volume=750_000, timestamp=now),
    ]
    return MarketSnapshot(
        candidate=candidate,
        bars=bars,
        vwap=8.55,
        ema9=8.40,
        day_high=9.10,
        prior_hod=8.95,
        quote_age_seconds=1.0,
        spread_pct=0.05,
        rvol=candidate.relative_volume,
        dollar_volume_5m=42_500_000,
    )


# ──────────────────────────────────────────────────────────────────
#  7. Paper loop wires broker truth and persistence
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def settings() -> "Settings":
    """Minimal Settings for paper-loop wiring tests."""
    from config.settings import Settings
    return Settings()


class TestPaperLoopWiring:
    """Verify _run_paper_loop wires broker_snapshot_fn and persist_path."""

    def test_run_paper_loop_wires_broker_snapshot_and_persist_path(
        self, monkeypatch, settings,
    ):
        """_run_paper_loop must pass broker_snapshot_fn and persist_path to TradingApp."""
        from main import _run_paper_loop

        captured: dict = {}

        class FakeApp:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self):
                return None

        monkeypatch.setattr("src.app.TradingApp", FakeApp)

        _run_paper_loop(settings)

        assert callable(captured["broker_snapshot_fn"]), (
            f"Expected callable broker_snapshot_fn, got {captured.get('broker_snapshot_fn')!r}"
        )
        assert callable(captured["broker_orders_snapshot_fn"]), (
            "Expected callable broker_orders_snapshot_fn, got "
            f"{captured.get('broker_orders_snapshot_fn')!r}"
        )
        assert captured["persist_path"] == "data/positions.json", (
            f"Expected persist_path='data/positions.json', got {captured.get('persist_path')!r}"
        )

    def test_run_paper_loop_passes_alpaca_account_equity(self, monkeypatch, settings):
        """Paper loop must size/risk from Alpaca account equity, not hardcoded $100k."""
        from main import _run_paper_loop

        captured: dict = {}

        class FakeApp:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self):
                return None

        monkeypatch.setattr("src.app.TradingApp", FakeApp)
        monkeypatch.setattr(
            "src.paper_execution.build_alpaca_account_equity",
            lambda gw: 54321.09,
            raising=False,
        )

        _run_paper_loop(settings)

        assert captured["equity"] == 54321.09


class TestPaperEquityWiring:
    def test_run_scan_pipeline_uses_alpaca_account_equity_for_all_candidates(
        self, monkeypatch, settings,
    ):
        """Paper once path must use Alpaca account equity in both snapshot branches."""
        from main import _run_scan_pipeline

        fake_gw = SimpleNamespace(positions=object())
        equities: list[float] = []

        def fake_build_components(_settings, *, paper=True):
            return fake_gw, object(), object(), {}

        def fake_run_pipeline(candidate, **kwargs):
            equities.append(kwargs["equity"])
            return SimpleNamespace(
                symbol=candidate.symbol,
                attention_score=0.0,
                hard_filter_passed=False,
                decision="skip",
                decision_reason="test",
            )

        calls = {"n": 0}

        def fake_build_snapshot(candidate, **kwargs):
            calls["n"] += 1
            return _mock_market_snapshot(candidate, **kwargs) if calls["n"] == 1 else None

        monkeypatch.setattr("main._build_components", fake_build_components)
        monkeypatch.setattr(
            "src.scanner.scanner.scan_dynamic_candidates",
            lambda: _make_paper_candidates(),
        )
        monkeypatch.setattr(
            "src.paper_execution.build_alpaca_account_equity",
            lambda gw: 54321.09,
            raising=False,
        )
        monkeypatch.setattr("src.decision_pipeline.run_pipeline", fake_run_pipeline)

        _run_scan_pipeline(settings, "Paper", fake_build_snapshot)

        assert equities == [54321.09, 54321.09]


class TestPaperMarketData:
    """Paper mode market-data enrichment — SPEC §22.15 item 17.

    Verifies that paper-mode candidates with injected market data can
    pass hard filters, and that missing enrichment blocks mechanically.
    """

    def test_paper_with_enrichment_passes_hard_filters(self):
        """Injected enrichment -> no quote/spread hard blocks."""
        with patch("src.scanner.scanner.scan_dynamic_candidates",
                   return_value=_make_paper_candidates()), \
             patch("src.market_data.build_market_snapshot",
                   side_effect=_mock_market_snapshot):
            ec, out = _run_cli("--mode", "paper", "--once")
        assert ec == 0, f"exit code {ec}, output:\n{out}"
        # Must NOT hard-block for missing quote or spread
        assert "no_quote_timestamp" not in out, (
            f"Unexpected no_quote_timestamp block:\n{out}"
        )
        assert "spread_hard_reject" not in out, (
            f"Unexpected spread_hard_reject block:\n{out}"
        )
        assert "missing_bid_ask" not in out, (
            f"Unexpected missing_bid_ask block:\n{out}"
        )
        # At least one candidate passes hard filters
        assert "hard_passed=True" in out, (
            f"No candidate passed hard filters:\n{out}"
        )

    def test_paper_without_enrichment_mechanically_blocks(self):
        """When enrichment returns None, hard filters mechanically block."""
        with patch("src.scanner.scanner.scan_dynamic_candidates",
                   return_value=_make_paper_candidates()), \
             patch("src.market_data.build_market_snapshot",
                   return_value=None):
            ec, out = _run_cli("--mode", "paper", "--once")
        assert ec == 0, f"exit code {ec}, output:\n{out}"
        # Candidates mechanically hard-blocked (missing quote/spread)
        assert "no_quote_timestamp" in out or "missing_bid_ask" in out, (
            f"Expected mechanical hard block:\n{out}"
        )
