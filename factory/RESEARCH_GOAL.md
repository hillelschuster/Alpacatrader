# Research Goal — Top-Gainer Momentum Lifecycle Discovery

## Thesis

We are not merely backtesting "buy top gainers."

We want to reconstruct the **historical lifecycle of stocks after they enter a genuine top-gainer / high-attention state**, and determine whether **statistically identifiable states or transitions predict subsequent price behavior with executable positive expectancy**.

### The Conceptual Lifecycle

```
enters top-gainer state → acceleration → first pullback → reclaim/HOD attempt → continuation or failure → exhaustion
```

The research question:

> *Given everything observable at time T about a stock that has entered a top-gainer state, does its subsequent path have statistically exploitable conditional patterns?*

We want to discover those states **empirically** across millions of historical runner situations — not force the existing Alpacatrader assumptions to be true.

### Why This Might Work

Attention and momentum create temporary non-random behavior:
- Top gainers attract capital flows (momentum chasers, breakout traders, short-covering)
- These flows are partially predictable in direction and magnitude
- The key question is whether the edge survives realistic costs

The existing Alpacatrader thesis (attention-first, HOD behavior, pullbacks/reclaims, runner capture) provides **initial hypotheses**, not unquestioned truth.

## Historical Replay Concept

For every historical minute, reconstruct what a momentum trader **could actually have known** at that moment:

1. Which stocks were top gainers (by intraday % gain, ranked)?
2. What was the observable state: volume acceleration, HOD proximity, VWAP relationship, time-of-day, volatility/extension?
3. What happened next: forward returns at multiple horizons, MFE/MAE, structural outcomes (HOD break, reclaim, exhaustion, halt)?

## Data Architecture

| Component | Dataset | Role |
|-----------|---------|------|
| **Price backbone** | `mito0o852/OHLCV-1m` | 1-min OHLCV, 1992-2026, ~7.4B rows, Finnhub source |
| **PIT universe** | `yolo22/stock-pit-archives` | Exchange universe snapshots 2021+ (prevents survivorship bias) |
| **Split events** | `speb/financial-data` (stock_split_events) | Identifying corporate-action dates to exclude |
| **Validation** | `elkassabgi/hfdatalibrary` | Independent 1-min dataset (1,391 tickers, consolidated tape pre-2022) — validation only |

### Known Data Limitations
- mito0o852 prices are **raw/unadjusted** — splits create artificial discontinuities
- Known duplicate rows exist in recent months (~1-33 per million rows)
- Post-2022 Finnhub coverage may differ from consolidated tape
- No float/shares-outstanding at scale (deferred, not needed for experiment #1)
- OTC/pink-sheet tickers included (~25% sub-$1) — must filter
- Survivorship bias pre-2021 (before PIT universe coverage)

## Research Loop

```
discover → hypothesize → implement → chronological backtest → WFA/OOS → segment → adversarially review → kill/promote → generate next materially different hypothesis → repeat
```

- Start with **deterministic rules** from Alpacatrader hypotheses
- Only introduce ML/clustering after deterministic baselines are established
- Every hypothesis must face chronological/OOS evidence

## Distinctions

| Signal Edge | Executable Edge |
|-------------|-----------------|
| Statistical pattern exists | Pattern survives realistic costs AND is implementable |
| May be pre-cost | Must be post-cost |
| May have lookahead leakage | Strictly chronological |
| May be in-sample only | Must survive OOS/WFA |

We pursue **executable edge**, not academic patterns.

## Top-Gainer Definition (Tradable Attention)

A "top gainer" is not just % gain. For our research, a candidate must satisfy at minimum:

- **Eligible exchange/security**: NYSE, NASDAQ, AMEX common stock (filter OTC, warrants, preferred)
- **Price floor**: reasonable minimum price (avoids sub-$1 noise)
- **Minimum cumulative dollar volume**: at the observation moment, sufficient liquidity for realistic fills
- **Active recent trading**: consecutive recent bars with actual volume (not a halted stock or dead gap-up)
- **Universe-relative ranking**: top-N by intraday % gain within the eligible universe at that minute

Failure to filter correctly will produce false patterns from illiquid, untradable gap-ups.

## Session Definitions

- **Prior close**: Previous regular-session (RTH) close (16:00 ET)
- **Premarket move**: % change from prior close to premarket observation
- **RTH HOD**: High of regular trading hours only (09:30-16:00 ET)
- **Extended-hours HOD**: Premarket high + RTH high combined (04:00-20:00 ET)
- **VWAP**: Cumulative from session open (RTH or extended, must be explicit)

Experiments must specify which session definition applies.

## Label/Outcome Schema

After each observation at time T, compute:

- **Forward returns**: 1m, 3m, 5m, 15m, 30m, 60m (future close vs. close at T)
- **MFE/MAE**: Maximum favorable/adverse excursion within the forward window
- **Structural outcomes** (binary): HOD break, failed breakout, reclaim success/failure, exhaustion signal, halt occurrence, continuation vs. reversal

## Volume Metrics

- **RVOL**: Current cumulative volume / expected volume for this ticker at this time-of-day, based on rolling window of prior sessions. Must use only historical (pre-T) data for expectations.
- **Volume acceleration**: Current-minute volume / recent-minute average volume. Same-session metric — not RVOL.