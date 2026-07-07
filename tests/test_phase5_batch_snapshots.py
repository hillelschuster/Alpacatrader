from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.decision_pipeline import MarketSnapshot
from src.entries import Bar
from src.models.schemas import Candidate


def _candidate(symbol: str, price: float = 10.00) -> Candidate:
    return Candidate(symbol=symbol, price=price, percent_gain=20.0, source="finviz")


class TestBuildMarketSnapshots:
    def test_batch_snapshot_request_builds_per_symbol_snapshots(self, monkeypatch):
        from src import market_data

        seen_requests = []
        now = datetime.now(timezone.utc)

        class FakeClient:
            def __init__(self, api_key, secret_key):
                assert api_key == "ak"
                assert secret_key == "sk"

            def get_stock_snapshot(self, request):
                seen_requests.append(request)
                return {
                    "DSY": SimpleNamespace(
                        latest_quote=SimpleNamespace(
                            timestamp=now - timedelta(seconds=3),
                            bid_price=10.40,
                            ask_price=10.60,
                        ),
                        minute_bar=SimpleNamespace(
                            open=10.10,
                            high=10.80,
                            low=10.00,
                            close=10.50,
                            volume=12_000,
                            timestamp=now,
                        ),
                        daily_bar=SimpleNamespace(high=11.20),
                        previous_daily_bar=SimpleNamespace(high=9.90),
                    )
                }

            def get_stock_bars(self, request):
                class FakeBarSet:
                    data = {
                        "DSY": [
                            SimpleNamespace(
                                open=10.10, high=10.80, low=10.00,
                                close=10.50, volume=12_000, timestamp=now,
                            ),
                        ],
                    }
                return FakeBarSet()

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshots = market_data.build_market_snapshots(
            [_candidate("DSY"), _candidate("MISS")],
            api_key="ak",
            secret_key="sk",
        )

        assert len(seen_requests) == 1
        assert seen_requests[0].symbol_or_symbols == ["DSY", "MISS"]
        dsy = snapshots["DSY"]
        assert isinstance(dsy, MarketSnapshot)
        assert dsy.bars is not None
        assert dsy.quote_age_seconds is not None
        assert dsy.candidate.price == pytest.approx(10.50)
        assert dsy.spread_pct == pytest.approx(1.9047619)
        assert 0.0 <= dsy.quote_age_seconds < 15.0
        assert dsy.bars[0].close == 10.50
        assert dsy.day_high == 11.20
        assert dsy.prior_hod == 9.90
        assert snapshots["MISS"] is None

    def test_batch_snapshot_failure_returns_explicit_none_per_candidate(self, monkeypatch):
        from src import market_data

        class FakeClient:
            def __init__(self, api_key, secret_key):
                pass

            def get_stock_snapshot(self, request):
                raise RuntimeError("alpaca unavailable")

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshots = market_data.build_market_snapshots(
            [_candidate("DSY"), _candidate("MISS")],
            api_key="ak",
            secret_key="sk",
        )

        assert snapshots == {"DSY": None, "MISS": None}

    def test_batch_snapshot_provides_multi_bars(self, monkeypatch):
        """build_market_snapshots() returns >=5 bars per symbol (not just minute_bar)."""
        from src import market_data

        now = datetime.now(timezone.utc)

        class FakeClient:
            def __init__(self, api_key, secret_key):
                assert api_key == "ak"
                assert secret_key == "sk"

            def get_stock_snapshot(self, request):
                return {
                    "DSY": SimpleNamespace(
                        latest_quote=SimpleNamespace(
                            timestamp=now - timedelta(seconds=3),
                            bid_price=10.40,
                            ask_price=10.60,
                        ),
                        minute_bar=SimpleNamespace(
                            open=10.10, high=10.80, low=10.00, close=10.50,
                            volume=12_000, timestamp=now,
                        ),
                        daily_bar=SimpleNamespace(high=11.20),
                        previous_daily_bar=SimpleNamespace(high=9.90),
                    ),
                    "MSFT": SimpleNamespace(
                        latest_quote=SimpleNamespace(
                            timestamp=now - timedelta(seconds=5),
                            bid_price=200.0,
                            ask_price=201.0,
                        ),
                        minute_bar=SimpleNamespace(
                            open=200.0, high=202.0, low=199.0, close=201.0,
                            volume=50_000, timestamp=now,
                        ),
                        daily_bar=SimpleNamespace(high=205.0),
                        previous_daily_bar=SimpleNamespace(high=198.0),
                    ),
                }

            def get_stock_bars(self, request):
                class FakeBarSet:
                    data = {
                        "DSY": [
                            SimpleNamespace(
                                open=10.00 + i * 0.1, high=10.05 + i * 0.1,
                                low=9.95 + i * 0.1, close=10.02 + i * 0.1,
                                volume=10_000 + i * 100,
                                timestamp=now - timedelta(minutes=5 - i),
                            )
                            for i in range(6)
                        ],
                        "MSFT": [
                            SimpleNamespace(
                                open=199.0 + i, high=200.0 + i,
                                low=198.0 + i, close=199.5 + i,
                                volume=40_000 + i * 1000,
                                timestamp=now - timedelta(minutes=5 - i),
                            )
                            for i in range(5)
                        ],
                    }
                return FakeBarSet()

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshots = market_data.build_market_snapshots(
            [_candidate("DSY", price=10.50), _candidate("MSFT", price=201.0)],
            api_key="ak",
            secret_key="sk",
        )

        dsy = snapshots["DSY"]
        assert dsy is not None
        assert len(dsy.bars) >= 5, f"DSY has {len(dsy.bars)} bars, expected >=5"

        msft = snapshots["MSFT"]
        assert msft is not None
        assert len(msft.bars) >= 5, f"MSFT has {len(msft.bars)} bars, expected >=5"

    def test_batch_snapshot_provides_five_min_bars_for_runner_timeframe(self, monkeypatch):
        """Runner ATR/trend gets dedicated 5-min bars, separate from 1-min trigger bars."""
        from src import market_data

        now = datetime.now(timezone.utc)
        bar_requests = []

        class FakeClient:
            def __init__(self, api_key, secret_key):
                pass

            def get_stock_snapshot(self, request):
                return {
                    "DSY": SimpleNamespace(
                        latest_quote=SimpleNamespace(
                            timestamp=now - timedelta(seconds=3),
                            bid_price=10.40,
                            ask_price=10.60,
                        ),
                        minute_bar=SimpleNamespace(
                            open=10.10, high=10.80, low=10.00, close=10.50,
                            volume=12_000, timestamp=now,
                        ),
                        daily_bar=SimpleNamespace(high=11.20),
                        previous_daily_bar=SimpleNamespace(high=9.90),
                    )
                }

            def get_stock_bars(self, request):
                bar_requests.append(request)
                close_base = 10.0 if len(bar_requests) == 1 else 20.0

                class FakeBarSet:
                    data = {
                        "DSY": [
                            SimpleNamespace(
                                open=close_base + i,
                                high=close_base + i + 0.5,
                                low=close_base + i - 0.5,
                                close=close_base + i + 0.25,
                                volume=10_000 + i,
                                timestamp=now - timedelta(minutes=i),
                            )
                            for i in range(6)
                        ]
                    }
                return FakeBarSet()

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshots = market_data.build_market_snapshots(
            [_candidate("DSY", price=10.50)],
            api_key="ak",
            secret_key="sk",
        )

        dsy = snapshots["DSY"]
        assert dsy is not None
        assert dsy.bars is not None
        assert dsy.five_min_bars is not None
        assert dsy.bars[0].close == 10.25
        assert dsy.five_min_bars[0].close == 20.25
        assert len(bar_requests) == 2

    def test_bars_failure_preserves_snapshot_data(self, monkeypatch):
        """When get_stock_bars fails, build_market_snapshots still returns
        snapshot data (quote/spread/daily_bar) with bars=None, falling back
        to minute_bar."""
        from src import market_data

        now = datetime.now(timezone.utc)
        call_log: list[str] = []

        class FakeClient:
            def __init__(self, api_key, secret_key):
                pass

            def get_stock_snapshot(self, request):
                call_log.append("snapshot")
                return {
                    "DSY": SimpleNamespace(
                        latest_quote=SimpleNamespace(
                            timestamp=now - timedelta(seconds=3),
                            bid_price=10.40,
                            ask_price=10.60,
                        ),
                        minute_bar=SimpleNamespace(
                            open=10.10, high=10.80, low=10.00, close=10.50,
                            volume=12_000, timestamp=now,
                        ),
                        daily_bar=SimpleNamespace(high=11.20),
                        previous_daily_bar=SimpleNamespace(high=9.90),
                    ),
                    "MSFT": SimpleNamespace(
                        latest_quote=SimpleNamespace(
                            timestamp=now - timedelta(seconds=5),
                            bid_price=200.0,
                            ask_price=201.0,
                        ),
                        minute_bar=SimpleNamespace(
                            open=200.0, high=202.0, low=199.0, close=201.0,
                            volume=50_000, timestamp=now,
                        ),
                        daily_bar=SimpleNamespace(high=205.0),
                        previous_daily_bar=SimpleNamespace(high=198.0),
                    ),
                }

            def get_stock_bars(self, request):
                call_log.append("bars")
                raise RuntimeError("Alpaca bars API timeout")

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshots = market_data.build_market_snapshots(
            [_candidate("DSY", price=10.50), _candidate("MSFT", price=201.0)],
            api_key="ak",
            secret_key="sk",
        )

        # Snapshot plus 1-min and 5-min bars were attempted.
        assert call_log == ["snapshot", "bars", "bars"], f"Unexpected call order: {call_log}"

        # DSY: snapshot data preserved, bars fall back to minute_bar
        dsy = snapshots["DSY"]
        assert dsy is not None, "DSY snapshot must not be None despite bars failure"
        assert dsy.candidate.price == pytest.approx(10.50), "Quote mid-price preserved"
        assert dsy.spread_pct == pytest.approx(1.9047619), "Spread preserved"
        assert dsy.day_high == 11.20, "Daily bar high preserved"
        assert dsy.prior_hod == 9.90, "Prior HOD preserved"
        # Bars fall back to minute_bar since initial_bars=None
        assert dsy.bars is not None, "Bars fallback to minute_bar"
        assert len(dsy.bars) == 1, "Expected 1 bar (minute_bar fallback)"

        # MSFT also preserved
        msft = snapshots["MSFT"]
        assert msft is not None, "MSFT snapshot must not be None despite bars failure"
        assert msft.candidate.price == pytest.approx(200.5), "MSFT mid-price preserved"
        assert msft.spread_pct == pytest.approx(0.4987531, rel=1e-3), "MSFT spread preserved"
        assert msft.day_high == 205.0, "MSFT daily bar high preserved"

    def test_single_snapshot_preserves_quote_when_five_min_bars_fail(self, monkeypatch):
        """Single-symbol enrichment keeps quote/1-min bars if 5-min bars fail."""
        from src import market_data

        now = datetime.now(timezone.utc)
        calls: list[str] = []

        class FakeClient:
            def __init__(self, api_key, secret_key):
                pass

            def get_stock_latest_quote(self, request):
                calls.append("quote")
                return {
                    "DSY": SimpleNamespace(
                        timestamp=now - timedelta(seconds=3),
                        bid_price=10.40,
                        ask_price=10.60,
                    )
                }

            def get_stock_bars(self, request):
                calls.append("bars")
                if len(calls) == 3:
                    raise RuntimeError("five-minute bars down")

                class FakeBarSet:
                    data = {
                        "DSY": [
                            SimpleNamespace(
                                open=10.10, high=10.80, low=10.00,
                                close=10.50, volume=12_000, timestamp=now,
                            )
                        ]
                    }
                return FakeBarSet()

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshot = market_data.build_market_snapshot(
            _candidate("DSY", price=10.50), api_key="ak", secret_key="sk",
        )

        assert snapshot is not None
        assert snapshot.candidate.price == pytest.approx(10.50)
        assert snapshot.bars is not None
        assert snapshot.five_min_bars is None
        assert calls == ["quote", "bars", "bars"]


class TestBuildMarketSnapshotDirect:
    """Direct mocked Alpaca-response tests for the single-symbol build_market_snapshot()."""

    def test_single_symbol_quote_and_bars_produce_full_snapshot(self, monkeypatch):
        from src import market_data

        now = datetime.now(timezone.utc)
        call_count: list[int] = [0]

        class FakeClient:
            def __init__(self, api_key, secret_key):
                pass

            def get_stock_latest_quote(self, request):
                return {
                    "DSY": SimpleNamespace(
                        timestamp=now - timedelta(seconds=3),
                        bid_price=10.40,
                        ask_price=10.60,
                    )
                }

            def get_stock_bars(self, request):
                call_count[0] += 1
                # First bars call → 1-min; second → 5-min
                if call_count[0] == 1:
                    class FakeBarSet1:
                        data = {
                            "DSY": [
                                SimpleNamespace(
                                    open=10.00 + 0.10 * i,
                                    high=10.00 + 0.10 * i,
                                    low=10.00 + 0.10 * i,
                                    close=10.00 + 0.10 * i,
                                    volume=10_000 + 1_000 * i,
                                    timestamp=now - timedelta(minutes=9 - i),
                                )
                                for i in range(10)
                            ],
                        }
                    return FakeBarSet1()
                else:
                    class FakeBarSet2:
                        data = {
                            "DSY": [
                                SimpleNamespace(
                                    open=20.00 + 0.5 * i,
                                    high=20.00 + 0.5 * i,
                                    low=20.00 + 0.5 * i,
                                    close=20.00 + 0.5 * i,
                                    volume=50_000 + 5_000 * i,
                                    timestamp=now - timedelta(minutes=5 * (5 - i)),
                                )
                                for i in range(6)
                            ],
                        }
                    return FakeBarSet2()

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshot = market_data.build_market_snapshot(
            _candidate("DSY", price=10.50), api_key="ak", secret_key="sk",
        )

        assert snapshot is not None
        # Quote-derived fields
        assert snapshot.candidate.price == pytest.approx(10.50, rel=1e-3)
        assert snapshot.spread_pct == pytest.approx(1.9047619, rel=1e-3)
        assert snapshot.quote_age_seconds == pytest.approx(3.0, abs=1)
        # 1-min bars
        assert snapshot.bars is not None
        assert len(snapshot.bars) == 10
        for i, b in enumerate(snapshot.bars):
            assert isinstance(b, Bar)
            assert b.open == 10.00 + 0.10 * i
            assert b.close == 10.00 + 0.10 * i
            assert b.volume == 10_000 + 1_000 * i
        # 5-min bars
        assert snapshot.five_min_bars is not None
        assert len(snapshot.five_min_bars) == 6
        # Enrichment
        assert snapshot.vwap is not None
        assert snapshot.day_high == 10.90
        assert snapshot.prior_hod == 10.80          # second-highest unique high
        assert snapshot.dollar_volume_5m is not None and snapshot.dollar_volume_5m > 0
        assert snapshot.ema9 is not None             # 10 bars > period=9


class TestBuildMarketSnapshotSim:
    """Direct mocked Alpaca-response tests for build_market_snapshot_sim()."""

    def test_sim_snapshot_uses_last_close_and_fake_quote_age(self, monkeypatch):
        from src import market_data_sim

        now = datetime.now(timezone.utc)

        class FakeClient:
            def __init__(self, api_key, secret_key):
                pass

            def get_stock_bars(self, request):
                class FakeBarSet:
                    data = {
                        "DSY": [
                            SimpleNamespace(
                                open=10.00 + 0.10 * i,
                                high=10.00 + 0.10 * i,
                                low=10.00 + 0.10 * i,
                                close=10.00 + 0.10 * i,
                                volume=10_000 + 1_000 * i,
                                timestamp=now - timedelta(minutes=9 - i),
                            )
                            for i in range(10)
                        ],
                    }
                return FakeBarSet()

        monkeypatch.setattr(
            "alpaca.data.historical.stock.StockHistoricalDataClient",
            FakeClient,
        )

        snapshot = market_data_sim.build_market_snapshot_sim(
            _candidate("DSY", price=10.50), api_key="ak", secret_key="sk",
        )

        assert snapshot is not None
        # Candidate price = last bar's close
        assert snapshot.candidate.price == pytest.approx(10.90, rel=1e-3)
        # Simulated fixed values
        assert snapshot.quote_age_seconds == 1.0
        assert snapshot.spread_pct == 0.5
        # Bars preserved
        assert snapshot.bars is not None
        assert len(snapshot.bars) == 10
        for i, b in enumerate(snapshot.bars):
            assert isinstance(b, Bar)
            assert b.close == 10.00 + 0.10 * i
        # No 5-min bars in sim
        assert snapshot.five_min_bars is None
        # Enrichment
        assert snapshot.vwap is not None
        assert snapshot.day_high == 10.90
        assert snapshot.prior_hod == 10.80
        assert snapshot.dollar_volume_5m is not None and snapshot.dollar_volume_5m > 0
        assert snapshot.ema9 is not None
