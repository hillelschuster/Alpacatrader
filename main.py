"""
Alpacatrader v0.4.0 — Attention-First Top-Gainer Bot
==============================================================
Entry point. Use --help for usage.

Pipeline flow:
  Scan → Attention → Confidence → Soft Warnings → Mechanical Hard Filters
  → Move State → Entry Setup → Sizing → Paper Execution → Exits → DecisionRecord

Modes:
  mock   — Simulation mode, no API keys needed
  paper  — Paper trading via Finviz scan through decision pipeline.
            No live broker orders — uses PaperExecutionGateway only.
  live   — LIVE TRADING (DISABLED BY DEFAULT)

Usage:
  python main.py --mode mock --once
  python main.py --mode paper --once
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
    """Alpacatrader v0.4.0 — Attention-First Top-Gainer Bot.

    MOCK mode: synthetic candidates through decision pipeline, no API keys.
    PAPER mode: Finviz scan through decision pipeline (PaperExecutionGateway).
    """
    click.echo("""
    ╔══════════════════════════════════════════════════════════╗
    ║           ALPACATRADER v0.4.0                            ║
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

    _run(settings, mode, once)



def _run(settings, mode, once):
    """Run the decision pipeline."""
    # Validate Alpaca credentials early for modes that need them.
    _alpaca_modes = {"paper", "sim", "live"}
    if mode in _alpaca_modes:
        try:
            settings.trading.require_alpaca_credentials()
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

    if mode == "mock":
        _run_mock(settings, once)
    elif mode == "paper":
        if not once:
            _run_paper_loop(settings)
        else:
            _run_paper(settings)
    elif mode == "sim":
        _run_sim(settings)
    else:
        logger.error("Unknown mode: %s", mode)
        sys.exit(1)


def _build_components(settings):
    """T7.1: Build gateway, logger, runner-store, and risk config from settings.

    Returns ``(gw, logger_inst, former_runners, risk_kwargs)``.
    All four CLI modes (mock, paper, sim, loop) use this helper.
    """
    from src.journal.decision_logger import DecisionLogger
    from src.paper_execution import AlpacaExecutionGateway
    from src.scanner.attention import FormerRunnerStore

    gw = AlpacaExecutionGateway(
        api_key=settings.trading.alpaca_api_key,
        secret_key=settings.trading.alpaca_secret_key,
    )
    logger_inst = DecisionLogger("data/decisions.jsonl")
    former_runners = FormerRunnerStore()
    p1 = settings.phase1
    risk_kwargs = dict(
        starter_risk_pct=p1.starter_risk_pct,
        max_trade_risk_pct=p1.max_trade_risk_pct,
        max_positions=p1.max_positions,
        max_open_risk_pct=p1.max_open_risk_pct,
        max_daily_loss_pct=p1.max_daily_loss_pct,
        focus_price_min=p1.focus_price_min,
        focus_price_max=p1.focus_price_max,
    )
    return gw, logger_inst, former_runners, risk_kwargs


def _run_mock(settings, once):
    """Mock mode — exercise decision pipeline with synthetic data.

    Creates synthetic Candidate objects and runs them through
    src/decision_pipeline.run_pipeline_batch.
    No network calls.
    """
    from datetime import datetime, timezone

    from src.decision_pipeline import run_pipeline_batch
    from src.entries import Bar
    from src.models.schemas import Candidate

    logger.info("=" * 60)
    logger.info("  Pipeline — Mock Mode")
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

    gw, logger_inst, former_runners, risk_kwargs = _build_components(settings)

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
        **risk_kwargs,
    )

    logger.info("=" * 60)
    logger.info("  Pipeline — Results")
    logger.info("=" * 60)
    for r in results:
        logger.info(
            f"  {r.symbol}: decision={r.decision} "
            f"attention={r.attention_score:.1f} "
            f"hard_passed={r.hard_filter_passed} "
            f"reason={r.decision_reason}"
        )
    logger.info("=" * 60)


def _run_paper(settings):
    """Paper mode — decision pipeline with Finviz scanner.

    Scans live Finviz top gainers and runs them through the
    decision pipeline (PaperExecutionGateway, no live broker).
    If the scanner returns nothing or fails, exits cleanly with a message.

    This handles the --once case. For --loop, see _run_paper_loop().
    """
    from src.market_data import build_market_snapshot
    _run_scan_pipeline(settings, "Paper", build_market_snapshot)


def _run_sim(settings):
    """Sim mode — use yesterday's Alpaca historical bars instead of live quotes.

    Same pipeline as paper mode, but build_market_snapshot_sim()
    fetches yesterday's last-hour bars and sets quote_age=1s, spread=0.5%.
    Works when the market is closed — hard filters pass.

    Usage:  python main.py --mode sim --once
    """
    from src.market_data_sim import build_market_snapshot_sim
    _run_scan_pipeline(settings, "Simulation", build_market_snapshot_sim,
                       extra_header=" (Historical bars, simulated spread/quote age)")


def _run_scan_pipeline(settings, label: str, build_snapshot_fn, *,
                       extra_header: str = ""):
    """T7.1: Shared scan→enrich→pipeline loop for paper and sim modes.

    Scans Finviz candidates, enriches each with market data, and runs
    the decision pipeline.  Results are logged per-candidate.
    """
    from src.decision_pipeline import run_pipeline
    from src.scanner.scanner import scan_finviz_candidates

    logger.info("=" * 60)
    logger.info(f"  Pipeline — {label} Mode{extra_header}")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("=" * 60)

    try:
        candidates = scan_finviz_candidates()
    except Exception as exc:
        logger.warning("Scanner error: {}", exc)
        candidates = []

    if not candidates:
        logger.info(
            f"{label} mode: no candidates from scanner — nothing to process. "
            "Exiting cleanly."
        )
        return

    gw, logger_inst, former_runners, risk_kwargs = _build_components(settings)
    ak = settings.trading.alpaca_api_key
    sk = settings.trading.alpaca_secret_key

    results: list = []
    for candidate in candidates:
        snapshot = build_snapshot_fn(candidate, api_key=ak, secret_key=sk)
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
                **risk_kwargs,
            )
        else:
            result = run_pipeline(
                candidate,
                equity=100_000.0,
                execution_gw=gw,
                position_store=gw.positions,
                former_runner_store=former_runners,
                logger=logger_inst,
                **risk_kwargs,
            )
        results.append(result)

    results.sort(key=lambda r: r.attention_score or 0, reverse=True)

    logger.info("=" * 60)
    logger.info(f"  Pipeline — Results ({label} Mode)")
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
    from functools import partial

    from src.app import TradingApp
    from src.market_data import build_market_snapshot
    from src.paper_execution import build_alpaca_broker_snapshot
    from src.scanner.scanner import scan_finviz_candidates

    logger.info("=" * 60)
    logger.info("  Pipeline — Paper Loop Mode")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("  Loop: monitor=10s  scan=30s  SIGINT to stop")
    logger.info("=" * 60)

    ak = settings.trading.alpaca_api_key
    sk = settings.trading.alpaca_secret_key
    gw, logger_inst, _, risk_kwargs = _build_components(settings)

    p1 = settings.phase1
    app = TradingApp(
        scanner_fn=scan_finviz_candidates,
        market_data_fn=partial(build_market_snapshot, api_key=ak, secret_key=sk),
        logger=logger_inst,
        execution_gw=gw,
        position_store=gw.positions,
        broker_snapshot_fn=partial(build_alpaca_broker_snapshot, gw),
        persist_path="data/positions.json",
        # Cadence
        monitor_interval_seconds=p1.monitor_interval_seconds,
        scan_interval_seconds=p1.scanner_interval_seconds,
        # Risk
        **risk_kwargs,
        paper_mode=True,
    )
    app.run()


if __name__ == "__main__":
    main_cli()
