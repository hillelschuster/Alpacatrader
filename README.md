# Alpacatrader — Attention-First Top-Gainer Trading Bot

> **Source of truth:** `SPEC.md`

An attention-first top-gainer system: find the stocks the market is focused on, identify where risk is definable, enter small, add only when right, scale out fast, and exit when risk control disappears.

Pipeline:

`Top Gainers → Attention Ranking → Catastrophic Tradeability Checks → Move State → Price-Action Entry → Starter/Add/Scale-Out → Emergency-First Exits`

**Key principle**: AI may assist analysis, but deterministic code is the execution boundary.

---

## Features

- **Attention-first top-gainer** exploitation model in `SPEC.md`
- **Finviz top-gainer scanner** with yfinance fallback
- **Mock mode** for end-to-end dry runs without API keys
- **Paper execution gateway** for order simulation
- **State machine** for position / scaling / kill-switch tracking
- **Emergency-first exit monitoring** for scale-out, technical, and time-based exits
- **Live trading disabled by default**

---

## Quick Start

```bash
# 1. Clone and install
git clone <repo-url> && cd Alpacatrader
pip install -r requirements.txt

# 2. Run in mock mode (no API keys needed!)
python main.py --mode mock --once

# 3. Run in paper mode (needs Finviz access)
cp .env.example .env
python main.py --mode paper --once
```

---

## Prerequisites

- **Python 3.11+**

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```ini
# Trading mode
TRADING_MODE=paper

# Logging
LOGGING_LEVEL=INFO
LOGGING_DIR=./logs
```

### 3. Verify

```bash
python main.py --mode mock --once   # Test without any API keys
python main.py --mode paper --once  # Test with Finviz scanner
```

---

## Usage

```bash
# Mock mode — simulation, no keys required
python main.py --mode mock --once

# Paper trading — Finviz top-gainer scan through decision pipeline
#   (Optionally set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env
#    for real-time quote/bar enrichment. Paper mode still uses the
#    local PaperExecutionGateway — no live broker orders are placed.)
python main.py --mode paper --once

# Custom config file
python main.py --mode paper --once -c config/my_config.yaml

# View all options
python main.py --help
```

### Options

| Flag | Description |
|------|-------------|
| `--mode, -m` | Trading mode: `paper`, `mock`, or `live` |
| `--once / --loop` | Run pipeline once or loop continuously |
| `--config, -c` | Path to custom YAML config file |

---

## Configuration

Configuration is loaded from `config/default_config.yaml` and overridden by environment variables. Environment variables take precedence.

### High-level settings

| Setting | Default | Description |
|---------|---------|-------------|
| `TRADING_MODE` | `paper` | `paper`, `mock`, or `live` |
| `TRADING_LIVE_TRADING_CONFIRMED` | `no` | Required live-trading confirmation gate |
| `LOGGING_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `LOGGING_DIR` | `./logs` | Log output directory |

Phase 1 settings (`PHASE1_*` env prefix) control scanner, attention ranking, tradeability, risk, and execution defaults. See `config/default_config.yaml` and `SPEC.md §7`.

---

## Safety

**Live trading is DISABLED by default.** To enable it, you must explicitly:

1. Set `TRADING_MODE=live` in `.env`
2. Set `TRADING_LIVE_TRADING_CONFIRMED=yes_i_accept_the_risks` in `.env`

The system **refuses to start** if both conditions are not met. This prevents accidental real-money trading.

---

## Project Structure

```
├── main.py                          # CLI entry point
│
├── config/
│   ├── default_config.yaml          # Config defaults
│   └── settings.py                  # Pydantic settings models
│
├── src/
│   ├── decision_pipeline.py         # Main decision pipeline
│   ├── app.py                       # Loop controller (scan → pipeline → sleep)
│   ├── entries.py                   # Bar model, entry signal detection
│   ├── exits.py                     # Scale-out, time, technical exits
│   ├── hard_filters.py              # Catastrophic tradeability checks
│   ├── sizing.py                    # Position sizing & risk limits
│   ├── move_classifier.py           # Move state classification
│   ├── paper_execution.py           # Paper execution gateway
│   ├── state_machine.py             # Position state store
│   ├── utils.py                     # Timezone, retry helpers
│   │
│   ├── models/
│   │   └── schemas.py               # Candidate, DecisionRecord, etc.
│   │
│   ├── scanner/
│   │   ├── scanner.py               # Finviz scanner adapter
│   │   ├── enrichment.py            # FinvizRow, Finviz scrape, yfinance fallback
│   │   ├── attention.py             # Attention scoring, FormerRunnerStore
│   │   └── confidence.py            # Data confidence scoring
│   │
│   └── journal/
│       └── decision_logger.py       # Decision record journal
│
├── tests/                           # Test suite
│
├── archive/                         # Historical planning documents
│
├── data/                            # Runtime data files
└── logs/                            # Runtime logs
```

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Architecture Decisions

### Why deterministic validation over AI?

AI agents can hallucinate catalysts, miss risks, or be overly optimistic. The risk manager, technical filter, and execution engine are pure deterministic code with no AI dependency. They serve as a hard safety boundary: kill switches, position limits, buying power checks.

### Why Finviz for ticker discovery?

Finviz free-tier top gainers provide fresh ticker discovery with price/volume cross-check. Float, RVOL, and fundamental data can be enriched via yfinance. This separation keeps the scanner lightweight and avoids lock-in to a single data vendor.

### Why paper execution gateway?

A simulated execution environment lets the decision pipeline run end-to-end without broker connectivity. Position state, fill simulation, and P&L tracking all work in mock/paper mode. The same gateway can swap to a live broker implementation.
