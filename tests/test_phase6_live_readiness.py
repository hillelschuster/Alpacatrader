"""Phase 6 live-readiness tests from SPEC §11.7."""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from config.settings import Phase1Settings
from src.app import TradingApp
from src.decision_pipeline import MarketSnapshot
from src.entries import Bar
from src.journal.decision_logger import DecisionLogger
from src.models.schemas import Candidate, PositionState, PositionStateModel
from src.paper_execution import (
    MarketSession,
    PaperExecutionGateway,
    get_alpaca_market_session,
)


def _bars() -> list[Bar]:
    return [
        Bar(open=10.00, high=10.20, low=9.90, close=10.10, volume=1_000),
        Bar(open=10.10, high=10.35, low=10.00, close=10.25, volume=1_000),
        Bar(open=10.25, high=10.50, low=10.10, close=10.40, volume=1_000),
    ]


def _snapshot(symbol: str = "DSY", price: float = 10.50) -> MarketSnapshot:
    return MarketSnapshot(
        candidate=Candidate(symbol=symbol, price=price, percent_gain=20.0),
        bars=_bars(),
        vwap=10.25,
        day_high=10.75,
        prior_hod=9.80,
        quote_age_seconds=1.0,
        spread_pct=0.5,
    )


class TestPhase6MarketCalendar:
    def test_alpaca_calendar_half_day_sets_flatten_time(self):
        calls = []

        class FakeCalendar:
            open = "09:30"
            close = "13:00"

        class FakeClock:
            is_open = True
            next_open = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
            next_close = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)

        class FakeClient:
            def get_calendar(self, request):
                calls.append(request)
                return [FakeCalendar()]

            def get_clock(self):
                return FakeClock()

        class FakeGateway:
            client = FakeClient()

        session = get_alpaca_market_session(
            FakeGateway(),
            now_et=datetime(2026, 7, 3, 10, 0, tzinfo=ZoneInfo("US/Eastern")),
        )

        assert calls
        assert calls[0].start == date(2026, 7, 3)
        assert calls[0].end == date(2026, 7, 3)
        assert session.is_open is True
        assert session.close_time == dt_time(13, 0)
        assert session.flatten_time == dt_time(12, 55)

    def test_holiday_blocks_trading_when_calendar_api_unavailable(self):
        class BrokenClient:
            def get_calendar(self, request):
                raise RuntimeError("calendar unavailable")

            def get_clock(self):
                raise RuntimeError("clock unavailable")

        class FakeGateway:
            client = BrokenClient()

        session = get_alpaca_market_session(
            FakeGateway(),
            now_et=datetime(2026, 1, 1, 10, 0, tzinfo=ZoneInfo("US/Eastern")),
        )

        assert session.is_open is False
        assert session.source == "fallback_holiday"

    def test_api_unavailable_fallback_respects_regular_hours(self):
        class BrokenClient:
            def get_calendar(self, request):
                raise RuntimeError("calendar unavailable")

            def get_clock(self):
                raise RuntimeError("clock unavailable")

        class FakeGateway:
            client = BrokenClient()

        session = get_alpaca_market_session(
            FakeGateway(),
            now_et=datetime(2026, 1, 2, 18, 0, tzinfo=ZoneInfo("US/Eastern")),
        )

        assert session.is_open is False
        assert session.source == "fallback_hardcoded"

    def test_app_uses_market_session_instead_of_hardcoded_hours(self):
        app = TradingApp(
            market_session_fn=lambda: MarketSession(
                is_open=False,
                source="alpaca_calendar_closed",
                open_time=dt_time(9, 30),
                close_time=dt_time(16, 0),
                flatten_time=dt_time(15, 55),
            )
        )

        assert app._is_market_open() is False

    def test_app_hardcoded_fallback_sets_default_flatten_time(self, monkeypatch):
        app = TradingApp()
        monkeypatch.setattr(
            app,
            "_now_et",
            lambda: datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("US/Eastern")),
        )

        assert app._is_market_open() is True
        assert app._active_flatten_time == dt_time(15, 55)

    def test_half_day_flatten_time_triggers_monitor_exit(self, tmp_path):
        """Half-day flatten time triggers time-based exit.

        Setup: entry=10.50 = current_price so P5 (scale-out) sees 0 gain
        and does not fire, allowing P10 (time exit) to take effect.
        """
        gw = PaperExecutionGateway()
        gw.positions.upsert(
            PositionStateModel(
                symbol="DSY",
                state=PositionState.OPEN,
                current_shares=10,
                entry_price=10.50,
                average_entry=10.50,
                stop_price=10.00,
            )
        )
        gw.protect_position("DSY", 10.00, 10)
        logger = DecisionLogger(tmp_path / "decisions.jsonl")
        app = TradingApp(
            market_data_fn=lambda candidate: _snapshot(candidate.symbol),
            execution_gw=gw,
            logger=logger,
            market_session_fn=lambda: MarketSession(
                is_open=True,
                source="alpaca_calendar",
                open_time=dt_time(9, 30),
                close_time=dt_time(13, 0),
                flatten_time=dt_time(12, 55),
            ),
        )

        app._is_market_open()
        app._et_time = dt_time(12, 56)
        app._monitor_positions()

        assert gw.positions.get("DSY").state == PositionState.CLOSED
        assert any(
            record.decision == "exit" and record.reason.startswith("flatten_time")
            for record in logger.read()
        )


class TestPhase6PaperModeGating:
    def test_paper_mode_auto_resolves_irreconcilable_with_warning(self):
        gw = PaperExecutionGateway()
        gw.positions.upsert(
            PositionStateModel(
                symbol="DSY",
                state=PositionState.OPEN,
                current_shares=50,
                entry_price=10.50,
                average_entry=10.50,
            )
        )
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (0, 0.0)},
            paper_mode=True,
        )

        app._reconcile_on_startup()

        resolved = gw.positions.get("DSY")
        assert resolved.state == PositionState.CLOSED
        assert resolved.current_shares == 0

    def test_irreconcilable_halts_startup_in_live_mode(self):
        gw = PaperExecutionGateway()
        gw.positions.upsert(
            PositionStateModel(
                symbol="DSY",
                state=PositionState.OPEN,
                current_shares=50,
                entry_price=10.50,
                average_entry=10.50,
            )
        )
        app = TradingApp(
            execution_gw=gw,
            broker_snapshot_fn=lambda: {"DSY": (0, 0.0)},
            paper_mode=False,
        )

        with pytest.raises(RuntimeError, match="IRRECONCILABLE"):
            app._reconcile_on_startup()


class TestPhase6ExitingTimeoutConfig:
    def test_exiting_timeout_configurable_in_app(self):
        """EXITING timeout uses configurable threshold.

        ``upsert()`` resets ``updated_at``, so we set the stale timestamp
        *after* upsert so the timeout logic sees the true age.
        """
        gw = PaperExecutionGateway()
        gw.positions.upsert(
            PositionStateModel(
                symbol="DSY",
                state=PositionState.EXITING,
                current_shares=10,
                entry_price=10.00,
                average_entry=10.00,
                stop_price=9.50,
            )
        )
        # Set stale timestamp after upsert (upsert resets updated_at)
        gw.positions.get("DSY").updated_at = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        )
        app = TradingApp(
            market_data_fn=lambda candidate: _snapshot(candidate.symbol),
            execution_gw=gw,
            exiting_timeout_seconds=5,
        )

        app._monitor_positions()

        # The monitor escalates EXITING→UNPROTECTED (timeout), then P4 fires
        # and the exit triggers the full close cycle → CLOSED.
        assert gw.positions.get("DSY").state == PositionState.CLOSED

    def test_phase1_settings_exposes_exiting_timeout(self):
        assert Phase1Settings(exiting_timeout_seconds=7).exiting_timeout_seconds == 7
