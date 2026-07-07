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
    elif mode == "live":
        if not once:
            _run_live_loop(settings)
        else:
            _run_live(settings)
    elif mode == "sim":
        _run_sim(settings)
    else:
        logger.error("Unknown mode: %s", mode)
        sys.exit(1)


def _build_components(settings, *, paper: bool = True):
    """T7.1: Build gateway, logger, runner-store, and risk config from settings.

    Returns ``(gw, logger_inst, former_runners, risk_kwargs)``.
    All CLI modes (mock, paper, sim, loop, live) use this helper.
    """
    from src.journal.decision_logger import DecisionLogger
    from src.paper_execution import AlpacaExecutionGateway
    from src.scanner.attention import FormerRunnerStore
    from src.trade_ledger import TradeLedger

    gw = AlpacaExecutionGateway(
        api_key=settings.trading.alpaca_api_key,
        secret_key=settings.trading.alpaca_secret_key,
        paper=paper,
        trade_ledger=TradeLedger("data/executed_trades.jsonl"),
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
        dollar_volume_min=p1.dollar_volume_min,
    )
    return gw, logger_inst, former_runners, risk_kwargs


def _require_alpaca_account_equity(gw, *, label: str) -> float:
    """Fetch broker account equity for startup sizing/risk caps."""
    from src.paper_execution import build_alpaca_account_equity

    equity = build_alpaca_account_equity(gw)
    if equity is None or equity <= 0:
        raise RuntimeError(f"{label} mode failed to fetch Alpaca account equity")
    logger.info("{} mode using Alpaca account equity ${:.2f}", label, equity)
    return equity


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


def _run_live(settings):
    """Live mode — same scan/pipeline machinery as paper, but live Alpaca routing.

    Uses ``_run_scan_pipeline`` with ``paper=False`` so the execution gateway
    creates ``TradingClient(paper=False)`` for live broker orders.
    """
    from src.market_data import build_market_snapshot
    _run_scan_pipeline(settings, "Live", build_market_snapshot, paper=False)


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
                       extra_header: str = "", paper: bool = True):
    """T7.1: Shared scan→enrich→pipeline loop for paper, sim, and live modes.

    Scans dynamic top-gainer candidates, enriches each with market data, and runs
    the decision pipeline.  Results are logged per-candidate.
    """
    from src.decision_pipeline import run_pipeline
    from src.scanner.scanner import scan_dynamic_candidates

    logger.info("=" * 60)
    logger.info(f"  Pipeline — {label} Mode{extra_header}")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("=" * 60)

    try:
        candidates = scan_dynamic_candidates()
    except Exception as exc:
        logger.warning("Scanner error: {}", exc)
        candidates = []

    if not candidates:
        logger.info(
            f"{label} mode: no candidates from scanner — nothing to process. "
            "Exiting cleanly."
        )
        return

    gw, logger_inst, former_runners, risk_kwargs = _build_components(settings, paper=paper)
    runtime_equity = _require_alpaca_account_equity(gw, label=label)
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
                equity=runtime_equity,
                execution_gw=gw,
                position_store=gw.positions,
                former_runner_store=former_runners,
                logger=logger_inst,
                **risk_kwargs,
            )
        else:
            result = run_pipeline(
                candidate,
                equity=runtime_equity,
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


def _run_live_loop(settings):
    """Live loop mode — same TradingApp machinery as paper loop, but live Alpaca routing.

    Uses ``paper=False`` for both the execution gateway (``TradingClient(paper=False)``)
    and the ``TradingApp`` ``paper_mode`` flag, so broker orders are sent to the live
    Alpaca environment.
    """
    from functools import partial

    from src.app import TradingApp
    from src.market_data import build_market_snapshot, build_market_snapshots
    from src.paper_execution import (
        build_alpaca_broker_snapshot,
        build_alpaca_open_order_snapshot,
        get_alpaca_market_session,
    )
    from src.scanner.scanner import scan_dynamic_candidates

    logger.info("=" * 60)
    logger.info("  Pipeline — Live Loop Mode")
    logger.info("  Scan → Attention → Confidence → Soft Warnings")
    logger.info("  → Mechanical Hard Filters → Move State → Entry Setup")
    logger.info("  → Sizing → Paper Execution → Exits → DecisionRecord")
    logger.info("  Loop: monitor=10s  scan=30s  SIGINT to stop")
    logger.info("  LIVE TRADING — broker orders go to real Alpaca account")
    logger.info("=" * 60)

    ak = settings.trading.alpaca_api_key
    sk = settings.trading.alpaca_secret_key
    gw, logger_inst, _, risk_kwargs = _build_components(settings, paper=False)
    runtime_equity = _require_alpaca_account_equity(gw, label="Live")

    p1 = settings.phase1
    runner = settings.runner
    scaling = settings.scaling
    app = TradingApp(
        scanner_fn=scan_dynamic_candidates,
        market_data_fn=partial(build_market_snapshot, api_key=ak, secret_key=sk),
        market_data_batch_fn=partial(build_market_snapshots, api_key=ak, secret_key=sk),
        logger=logger_inst,
        execution_gw=gw,
        position_store=gw.positions,
        broker_snapshot_fn=partial(build_alpaca_broker_snapshot, gw),
        broker_orders_snapshot_fn=partial(build_alpaca_open_order_snapshot, gw),
        market_session_fn=partial(get_alpaca_market_session, gw),
        persist_path="data/positions.json",
        # Cadence
        monitor_interval_seconds=p1.monitor_interval_seconds,
        scan_interval_seconds=p1.scanner_interval_seconds,
        # Risk
        equity=runtime_equity,
        **risk_kwargs,
        max_consecutive_losses=p1.max_consecutive_losses,
        weekly_drawdown_pct=p1.weekly_drawdown_pct,
        runner_activation_r=runner.activation_r,
        runner_atr_period=runner.atr_period,
        runner_trail_multiplier=runner.trail_multiplier,
        # Scaling
        add_risk_pct=scaling.add_risk_pct,
        add_size_multiplier=scaling.add_size_multiplier,
        add_activation_r_multiple=scaling.add_activation_r_multiple,
        max_adds=scaling.max_adds,
        paper_mode=False,
        exiting_timeout_seconds=p1.exiting_timeout_seconds,
    )
    app.run()


def _run_paper_loop(settings):
    """Paper loop mode — continuous scanning and monitoring via TradingApp.

    Wires the ``TradingApp`` main loop (``src/app.py``) with real
    scanner/enrichment/market-data callbacks and runs until shutdown
    or session-end.  Uses ``PaperExecutionGateway`` — no live broker.
    """
    from functools import partial

    from src.app import TradingApp
    from src.market_data import build_market_snapshot, build_market_snapshots
    from src.paper_execution import (
        build_alpaca_broker_snapshot,
        build_alpaca_open_order_snapshot,
        get_alpaca_market_session,
    )
    from src.scanner.scanner import scan_dynamic_candidates

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
    runtime_equity = _require_alpaca_account_equity(gw, label="Paper")

    p1 = settings.phase1
    runner = settings.runner
    scaling = settings.scaling
    app = TradingApp(
        scanner_fn=scan_dynamic_candidates,
        market_data_fn=partial(build_market_snapshot, api_key=ak, secret_key=sk),
        market_data_batch_fn=partial(build_market_snapshots, api_key=ak, secret_key=sk),
        logger=logger_inst,
        execution_gw=gw,
        position_store=gw.positions,
        broker_snapshot_fn=partial(build_alpaca_broker_snapshot, gw),
        broker_orders_snapshot_fn=partial(build_alpaca_open_order_snapshot, gw),
        market_session_fn=partial(get_alpaca_market_session, gw),
        persist_path="data/positions.json",
        # Cadence
        monitor_interval_seconds=p1.monitor_interval_seconds,
        scan_interval_seconds=p1.scanner_interval_seconds,
        # Risk
        equity=runtime_equity,
        **risk_kwargs,
        max_consecutive_losses=p1.max_consecutive_losses,
        weekly_drawdown_pct=p1.weekly_drawdown_pct,
        runner_activation_r=runner.activation_r,
        runner_atr_period=runner.atr_period,
        runner_trail_multiplier=runner.trail_multiplier,
        # Scaling
        add_risk_pct=scaling.add_risk_pct,
        add_size_multiplier=scaling.add_size_multiplier,
        add_activation_r_multiple=scaling.add_activation_r_multiple,
        max_adds=scaling.max_adds,
        paper_mode=True,
        exiting_timeout_seconds=p1.exiting_timeout_seconds,
    )
    app.run()


if __name__ == "__main__":
    main_cli()
