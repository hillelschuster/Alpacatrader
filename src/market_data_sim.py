"""
Simulated market data for off-hours testing.

Fetches historical 1-minute bars from Alpaca (yesterday, last market hour)
and derives realistic quote/spread/VWAP/EMA values so the full decision
pipeline can run end-to-end when the market is closed.

Usage::
    python main.py --mode sim --once

No live quotes — bars are timestamped 15:55 ET yesterday.  The pipeline
treats them as if they were current.  Hard filters pass because quotes
are "fresh" and spreads are estimated at 0.5 %.

Isolated from the live paper-mode path in ``src/market_data.py``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from src.decision_pipeline import MarketSnapshot
from src.entries import Bar
from src.market_data import _compute_ema


def build_market_snapshot_sim(candidate) -> Optional[MarketSnapshot]:
    """Fetch yesterday's last-hour bars and build a realistic snapshot.

    Uses Alpaca historical data (no live trading client).  Spread is
    estimated at 0.5 % and quote age at 1 s so hard filters pass.

    Returns ``None`` when Alpaca keys are missing or the API call fails.
    """
    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        logger.info("alpaca-py not installed — sim data unavailable")
        return None

    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        logger.info("Alpaca API keys not configured — sim data unavailable")
        return None

    try:
        client = StockHistoricalDataClient(api_key, secret_key)
        now = datetime.now(timezone.utc)

        # ── Yesterday's last hour of 1-minute bars ────────────
        from datetime import timedelta

        end = now.replace(hour=20, minute=55, second=0, microsecond=0)  # ~15:55 ET
        start = end - timedelta(hours=2)

        bars_req = StockBarsRequest(
            symbol_or_symbols=candidate.symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            limit=120,
        )
        bar_set = client.get_stock_bars(bars_req)
        alpaca_bars = bar_set.data.get(candidate.symbol, [])

        bars: list[Bar] = []
        for ab in alpaca_bars:
            bars.append(Bar(
                open=ab.open,
                high=ab.high,
                low=ab.low,
                close=ab.close,
                volume=ab.volume,
                timestamp=ab.timestamp,
            ))

        if not bars:
            return None

        # ── Update candidate price via model_copy (frozen) ────
        last = bars[-1]
        from src.models.schemas import Candidate as CandidateT

        candidate = CandidateT(
            symbol=candidate.symbol,
            price=last.close,
            percent_gain=candidate.percent_gain,
            current_volume=candidate.current_volume,
            relative_volume=candidate.relative_volume,
            dollar_volume=candidate.dollar_volume,
            sector=candidate.sector,
            industry=candidate.industry,
            country=candidate.country,
            exchange=candidate.exchange,
            source=candidate.source,
            source_timestamp=candidate.source_timestamp,
        )

        # ── Derived enrichment ────────────────────────────────
        close_prices = [b.close for b in bars]
        volumes = [b.volume for b in bars]

        # VWAP
        total_pv = sum(c * v for c, v in zip(close_prices, volumes))
        total_v = sum(volumes)
        vwap = total_pv / total_v if total_v > 0 else None

        # EMA 9
        ema9 = _compute_ema(close_prices, 9)

        # Day high / prior HOD from the simulated window
        day_high = max(b.high for b in bars)
        distinct_highs = sorted({b.high for b in bars}, reverse=True)
        prior_hod = distinct_highs[1] if len(distinct_highs) >= 2 else None

        # Trailing 5-minute dollar volume
        last_5 = bars[-5:]
        dollar_volume_5m = sum(b.close * b.volume for b in last_5) if last_5 else None

        rvol = candidate.relative_volume

        # Simulated: fresh quote, tight spread
        quote_age_seconds = 1.0
        spread_pct = 0.5

        return MarketSnapshot(
            candidate=candidate,
            bars=bars,
            vwap=vwap,
            ema9=ema9,
            day_high=day_high,
            prior_hod=prior_hod,
            quote_age_seconds=quote_age_seconds,
            spread_pct=spread_pct,
            rvol=rvol,
            dollar_volume_5m=dollar_volume_5m,
        )

    except Exception as exc:
        logger.warning("Sim data fetch failed for {}: {}", candidate.symbol, exc)
        return None
