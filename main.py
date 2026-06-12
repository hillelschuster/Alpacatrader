"""
Alpacatrader v0.4.0 — Rebuild: Attention-First Top-Gainer Bot
==============================================================
Entry point. Use --help for usage.

Rebuild pipeline flow:
  Scan → Attention → Confidence → Soft Warnings → Mechanical Hard Filters
  → Move State → Entry Setup → Sizing → Paper Execution → Exits → DecisionRecord

Modes:
  mock   — Simulation mode, no API keys needed (rebuild decision pipeline)
  paper  — Paper trading via Finviz scan through rebuild decision pipeline.
            No live broker orders — uses PaperExecutionGateway only.
  live   — LIVE TRADING (DISABLED BY DEFAULT)

Usage:
  python main.py --mode mock --once                   # Mock: rebuild path
  python main.py --mode paper --once                  # Paper: rebuild path
"""

import sys
import click
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from config.settings import Settings


def setup_logging(settings: Settings):
    log_dir = Path(settings.logging.dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=settings.logging.level,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
               colorize=True)
    logger.add(log_dir / "alpacatrader_{time:YYYY-MM-DD}.log", level="DEBUG",
               format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
               rotation="00:00", retention=f"{settings.logging.retention_days} days", compression="gz")


@click.command()
@click.option("--mode", "-m", default=None, type=click.Choice(["paper", "mock", "live", "sim"]),
              help="Trading mode")
@click.option("--once/--loop", default=True, help="Run once or loop continuously")
@click.option("--config", "-c", default=None, help="Path to config YAML file")
def main_cli(mode: str, once: bool, config: str):
    """Alpacatrader v0.4.0 — Rebuild: Attention-First Top-Gainer Bot.

    Default path exercises the rebuild decision pipeline (src/decision_pipeline.py).

    MOCK mode: synthetic candidates through rebuild pipeline, no API keys.
    PAPER mode: Finviz scan through rebuild decision pipeline (PaperExecutionGateway).
    """
    click.echo("""
    ╔══════════════════════════════════════════════════════════╗
    ║           ALPACATRADER v0.4.0 — Rebuild                  ║
    ║   Scan → Attention → Confidence → Soft Warnings          ║
    ║   → Mechanical Hard Filters → Move State → Entry Setup   ║
    ║   → Sizing → Paper Execution → Exits → DecisionRecord     ║
    ╚══════════════════════════════════════════════════════════╝
    """)

    settings = Settings.load(config)
    if mode is not None:
        settings.trading.mode = mode
    else:
        mode = settings.trading.mode

    try:
        settings.validate_live_trading()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    setup_logging(settings)
    logger.info(f"Alpacatrader v0.4.0 starting in {mode} mode")

    if mode == "live":
        logger.error("LIVE TRADING IS DISABLED BY DEFAULT. "
                     "Set TRADING_LIVE_TRADING_CONFIRMED=yes_i_accept_the_risks in .env")
        sys.exit(1)

    _run_rebuild(settings, mode, once)



def _run_rebuild(settings, mode, once):
    """Run the rebuild decision pipeline (default path)."""
    if mode == "mock":
        _run_rebuild_mock(settings, once)
    elif mode == "paper":
        if not once:
            _run_paper_loop(settings)
        else:
            _run_rebuild_paper(settings)
    elif mode == "sim":
        _run_rebuild_sim(settings)
    else:
        logger.error("Unknown mode: %s", mode)
        sys.exit(1)


def _run_rebuild_mock(settings, once):
    """Mock mode — exercise rebuild decision pipeline with synthetic data.

    Creates synthetic ``Candidate`` objects and runs them through the
    rebuild decision pipeline (``src/decision_pipeline.run_pipeline_batch``).
    No network calls.  No old funnel code (anti-patterns, regime, pillars,
    quality scoring).
    """
    from datetime import datetime, timezone

    from src.decision_pipeline import run_pipeline_batch
    from src.entries import Bar
    from src.journal.decision_logger import DecisionLogger
    from src.models.schemas import Candidate
    from src.paper_execution import AlpacaExecutionGateway
    from src.scanner.attention import FormerRunnerStore

    logger.info("=" * 60)
    logger.info("  Rebuild Pipeline — Mock Mode")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("=" * 60)

    now = datetime.now(timezone.utc)
    candidates = [
        Candidate(
            symbol="GAPR", price=8.50, relative_volume=7.0,
            dollar_volume=42_500_000, previous_close=7.20,
            sector="Technology", industry="Software",
            source="mock", source_timestamp=now,
        ),
        Candidate(
            symbol="CHIPR", price=12.00, relative_volume=6.5,
            dollar_volume=36_000_000, previous_close=10.43,
            sector="Technology", industry="Semiconductors",
            source="mock", source_timestamp=now,
        ),
        Candidate(
            symbol="BIO", price=6.50, relative_volume=9.0,
            dollar_volume=52_000_000, previous_close=4.81,
            sector="Healthcare", industry="Biotech",
            source="mock", source_timestamp=now,
        ),
    ]

    # Mock bars simulating a rising stock
    bars = [
        Bar(open=8.50, high=8.80, low=8.40, close=8.60, volume=500_000, timestamp=now),
        Bar(open=8.60, high=8.85, low=8.55, close=8.75, volume=550_000, timestamp=now),
        Bar(open=8.75, high=8.90, low=8.65, close=8.82, volume=480_000, timestamp=now),
        Bar(open=8.82, high=8.95, low=8.70, close=8.88, volume=520_000, timestamp=now),
        Bar(open=8.88, high=9.10, low=8.80, close=9.05, volume=750_000, timestamp=now),
    ]

    gw = AlpacaExecutionGateway()
    logger_inst = DecisionLogger("data/decisions_rebuild.jsonl")
    former_runners = FormerRunnerStore()

    results = run_pipeline_batch(
        candidates,
        equity=100_000.0,
        execution_gw=gw,
        position_store=gw.positions,
        former_runner_store=former_runners,
        logger=logger_inst,
        bars=bars,
        vwap=8.55,
        ema9=8.45,
        day_high=8.80,
        prior_hod=8.70,
        quote_age_seconds=1.0,
        spread_pct=0.05,
        rvol=7.0,
        dollar_volume_5m=42_500_000,
        halt_count_today=0,
        focus_price_min=1.0,
        focus_price_max=50.0,
    )

    logger.info("=" * 60)
    logger.info("  Rebuild Pipeline — Results")
    logger.info("=" * 60)
    for r in results:
        logger.info(
            f"  {r.symbol}: decision={r.decision} "
            f"attention={r.attention_score:.1f} "
            f"hard_passed={r.hard_filter_passed} "
            f"reason={r.decision_reason}"
        )
    logger.info("=" * 60)


def _run_rebuild_paper(settings):
    """Paper mode — rebuild decision pipeline with Finviz scanner.

    Scans live Finviz top gainers and runs them through the rebuild
    decision pipeline using ``PaperExecutionGateway`` (no live broker).
    If the scanner returns nothing or fails, exits cleanly with a message.

    This handles the ``--once`` case.  For ``--loop``, see ``_run_paper_loop()``.
    """
    from src.decision_pipeline import run_pipeline
    from src.journal.decision_logger import DecisionLogger
    from src.market_data import build_market_snapshot
    from src.paper_execution import AlpacaExecutionGateway
    from src.scanner.attention import FormerRunnerStore
    from src.scanner.scanner import scan_finviz_candidates

    logger.info("=" * 60)
    logger.info("  Rebuild Pipeline — Paper Mode")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("=" * 60)

    # Scan live Finviz top gainers
    try:
        candidates = scan_finviz_candidates()
    except Exception as exc:
        logger.warning("Scanner error: {}", exc)
        candidates = []

    if not candidates:
        logger.info(
            "Paper mode: no candidates from scanner — nothing to process. "
            "Exiting cleanly."
        )
        return

    gw = AlpacaExecutionGateway()
    logger_inst = DecisionLogger("data/decisions_rebuild.jsonl")
    former_runners = FormerRunnerStore()

    # Run pipeline per candidate with individual market data enrichment
    results: list = []
    for candidate in candidates:
        snapshot = build_market_snapshot(candidate)
        if snapshot is not None:
            result = run_pipeline(
                candidate,
                bars=snapshot.bars,
                vwap=snapshot.vwap,
                ema9=snapshot.ema9,
                day_high=snapshot.day_high,
                prior_hod=snapshot.prior_hod,
                quote_age_seconds=snapshot.quote_age_seconds,
                spread_pct=snapshot.spread_pct,
                rvol=snapshot.rvol,
                dollar_volume_5m=snapshot.dollar_volume_5m,
                equity=100_000.0,
                execution_gw=gw,
                position_store=gw.positions,
                former_runner_store=former_runners,
                logger=logger_inst,
            )
        else:
            # No enrichment — pipeline runs with bare candidate;
            # hard filters mechanically block missing quote/spread/bars.
            result = run_pipeline(
                candidate,
                equity=100_000.0,
                execution_gw=gw,
                position_store=gw.positions,
                former_runner_store=former_runners,
                logger=logger_inst,
            )
        results.append(result)

    results.sort(key=lambda r: r.attention_score or 0, reverse=True)

    logger.info("=" * 60)
    logger.info("  Rebuild Pipeline — Results (Paper Mode)")
    logger.info("=" * 60)
    for r in results:
        logger.info(
            f"  {r.symbol}: decision={r.decision} "
            f"attention={r.attention_score:.1f} "
            f"hard_passed={r.hard_filter_passed} "
            f"reason={r.decision_reason}"
        )
    logger.info("=" * 60)


def _run_rebuild_sim(settings):
    """Sim mode — use yesterday's Alpaca historical bars instead of live quotes.

    Same pipeline as ``--mode paper``, but ``build_market_snapshot_sim()``
    fetches yesterday's last-hour bars and sets quote_age=1s, spread=0.5%.
    Works when the market is closed — hard filters pass.

    Usage:  ``python main.py --mode sim --once``
    """
    from src.decision_pipeline import run_pipeline
    from src.journal.decision_logger import DecisionLogger
    from src.market_data_sim import build_market_snapshot_sim
    from src.paper_execution import AlpacaExecutionGateway
    from src.scanner.attention import FormerRunnerStore
    from src.scanner.scanner import scan_finviz_candidates

    logger.info("=" * 60)
    logger.info("  Rebuild Pipeline — Simulation Mode")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("  (Historical bars, simulated spread/quote age)")
    logger.info("=" * 60)

    try:
        candidates = scan_finviz_candidates()
    except Exception as exc:
        logger.warning("Scanner error: {}", exc)
        candidates = []

    if not candidates:
        logger.info("Sim mode: no candidates from scanner. Exiting cleanly.")
        return

    gw = AlpacaExecutionGateway()
    logger_inst = DecisionLogger("data/decisions_rebuild.jsonl")
    former_runners = FormerRunnerStore()

    results: list = []
    for candidate in candidates:
        snapshot = build_market_snapshot_sim(candidate)
        if snapshot is not None:
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
                equity=100_000.0,
                execution_gw=gw,
                position_store=gw.positions,
                former_runner_store=former_runners,
                logger=logger_inst,
            )
        else:
            result = run_pipeline(
                candidate,
                equity=100_000.0,
                execution_gw=gw,
                position_store=gw.positions,
                former_runner_store=former_runners,
                logger=logger_inst,
            )
        results.append(result)

    results.sort(key=lambda r: r.attention_score or 0, reverse=True)

    logger.info("=" * 60)
    logger.info("  Rebuild Pipeline — Results (Sim Mode)")
    logger.info("=" * 60)
    for r in results:
        logger.info(
            f"  {r.symbol}: decision={r.decision} "
            f"attention={r.attention_score:.1f} "
            f"hard_passed={r.hard_filter_passed} "
            f"reason={r.decision_reason}"
        )
    logger.info("=" * 60)


def _run_paper_loop(settings):
    """Paper loop mode — continuous scanning and monitoring via TradingApp.

    Wires the ``TradingApp`` main loop (``src/app.py``) with real
    scanner/enrichment/market-data callbacks and runs until shutdown
    or session-end.  Uses ``PaperExecutionGateway`` — no live broker.
    """
    from src.app import TradingApp
    from src.journal.decision_logger import DecisionLogger
    from src.market_data import build_market_snapshot
    from src.paper_execution import AlpacaExecutionGateway
    from src.scanner.scanner import scan_finviz_candidates

    logger.info("=" * 60)
    logger.info("  Rebuild Pipeline — Paper Loop Mode")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("  Loop: monitor=10s  scan=30s  SIGINT to stop")
    logger.info("=" * 60)

    gw = AlpacaExecutionGateway()
    logger_inst = DecisionLogger("data/decisions_rebuild.jsonl")

    app = TradingApp(
        scanner_fn=scan_finviz_candidates,
        market_data_fn=build_market_snapshot,
        logger=logger_inst,
        execution_gw=gw,
        position_store=gw.positions,
        monitor_interval_seconds=10.0,
        scan_interval_seconds=30.0,
        paper_mode=True,
    )
    app.run()


if __name__ == "__main__":
    main_cli()
