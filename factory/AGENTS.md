# Top-Gainer Momentum Research Factory — Agent Rules

## Before Any Work
1. Read `RESEARCH_GOAL.md` — understand the thesis and what we're trying to discover.
2. Read `STATE.md` — know where we are, what's active, what's blocked.
3. If CBM is configured, check `get_architecture` for codebase context.

## Core Directives

### Objective
Discover whether statistically identifiable states/transitions in top-gainer stocks predict subsequent price behavior with **executable positive expectancy after realistic costs**.

### Integrity Rules (NON-NEGOTIABLE)
- **No lookahead.** Ranking, feature computation, and filtering must use only data observable at timestamp T.
- **Chronological integrity.** Train/validate always on past data relative to test/OOS periods.
- **Realistic costs.** Assume spread, slippage, and commission. A signal that shows 0.3% edge pre-cost is dead.
- **No survivorship cheating.** PIT universe join mandatory for 2021+. For pre-2021 data, acknowledge survivorship limitation.
- **Preserve failures.** Dead hypotheses go in HYPOTHESES.jsonl with verdict. Do not delete them.

### Scope Boundaries
- This is a **research subproject** inside Alpacatrader. Do not modify live trading code.
- Do not refactor Alpacatrader infrastructure unless it directly blocks an experiment.
- Do not add datasets, dependencies, or infrastructure "just in case." Every addition must serve a specific active hypothesis.
- No cosmetic cleanup. No architecture for architecture's sake.

### Data Rules
- **Primary backbone**: `mito0o852/OHLCV-1m` (7.4B rows, 1-min OHLCV, 1992-2026, Finnhub, monthly Parquet)
- **PIT universe**: `yolo22/stock-pit-archives` (exchange universe snapshots 2021+)
- **No unadjusted data without explicit handling**: mito0o852 prices are raw (not split-adjusted). Exclude known split/corporate-action dates initially. Do not silently trust raw % gains.
- **Market hours filter**: Regular trading hours (09:30-16:00 ET) by default. Premarket available but must be flagged explicitly.
- **Duplicate handling**: `(timestamp, ticker)` dedup required — dataset has known duplicates.

### Engineering Rules
- Start with pandas for one month. Switch to Polars/DuckDB if profiling shows it's slow or memory-heavy.
- Do not permanently materialize full-universe per-minute ranked data. Process → extract events → discard.
- Keep `STATE.md` updated after meaningful work. A new agent must be able to resume without prior conversation.
- Append to `HYPOTHESES.jsonl` and `EXPERIMENTS.jsonl` after every experiment cycle. Do not batch.

### Codebase Memory MCP
If available (`codebase-memory-mcp`):
- The project is auto-indexed. Use `search_graph`, `trace_call_path`, `get_architecture` for codebase questions.
- The graph persists at `~/.cache/codebase-memory-mcp/`.
- Re-index with `codebase-memory-mcp cli index_repository --repo-path .` after significant structural changes.

### Verification
- Every experiment must produce a verifiable numeric result, not a narrative.
- Backtests must report: train period, OOS period, sample size, win rate, expectancy, net returns after realistic costs, parameter stability.
- If a signal looks promising, segment by regime (volatility, time-of-day, market cap proxy) before believing it.

## Key Paths
- Research root: `factory/`
- Canonical files: `factory/RESEARCH_GOAL.md`, `factory/STATE.md`
- Ledgers: `factory/HYPOTHESES.jsonl`, `factory/EXPERIMENTS.jsonl`
- Data: `data/` (downloaded monthly Parquet, cached processed outputs)
- Output artifacts: `factory/artifacts/` (charts, CSVs, result files)