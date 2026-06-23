# Alpacatrader v0.4.0 Implementation Specification

Date: 2026-06-23

Status: Active implementation spec and truth audit, reconciled with current code/tests through Task 13 plus scanner fallback wiring on 2026-06-23

## 0. Source Of Truth

There is exactly one active implementation spec: this root `SPEC.md`.

The current codebase is the implementation baseline, not the authority when it conflicts with this document. The audit reports in `researches/` are supporting evidence. Historical planning documents, including `docs/plans/2026-06-20-phase-gap-remediation.md`, and the former `docs/SPEC.md` are archived or retained for reference only — no historical plan has authority over implementation.

Source-of-truth precedence for v0.4.0:

1. Root `SPEC.md`.
2. `researches/00-architecture-decision.md` and `researches/01-06` audit reports.
3. Current `src/`, `config/`, `main.py`, and `tests/` as implementation baseline.
4. `docs/plans/` and `archive/` as historical reference only.

If an implementation choice conflicts with this document, implement this document. If this document is ambiguous, ask before coding.

## 1. Purpose

Alpacatrader is an attention-first, defined-risk top-gainer trading bot.

### 1.1 Mental Model

Each top gainer, especially the top gaining names, is an option to trade. The system looks for ways to participate when logic, rules, execution quality, and risk controls align.

It is highly protective because it will eventually run live, but protection does not mean rejection. Filters exist to manage risk — they do not declare classes of stocks untradeable. The bot supports scaling in when a position proves itself, not only scaling out.

Risk minimization and opportunity-seeking coexist. Top gainers are candidates first, not suspects first.

### 1.2 Core Rule

```text
Top gainer + attention + definable risk + verified execution safety = possible trade.
```

If the system cannot find a way to participate within its rules, it watches. If it can, it enters small, protects immediately, scales in on proven strength, scales out to lock profit, and exits when risk is no longer controlled.

### 1.3 Non-Goals

- No Gen1 or Gen2 resurrection.
- No Ross Cameron pipeline resurrection.
- No pillars, regime score, anti-pattern hard blocks, AI/LLM approval, or company-quality filter.
- No live trading enablement in v0.4.0.
- No qualitative hard rejection for Chinese ADR, no news, biotech, low float, parabolic look, or speculative label.
- No categorical "can never trade" class — any stock that passes mechanical checks is eligible.

## 2. Audit Inputs

This spec is based on the current repository state and these generated reports:

- `researches/00-architecture-decision.md`: Gen3-only architecture decision and source-of-truth order.
- `researches/01-context7-api-verification.md`: third-party API verification for alpaca-py, yfinance, Pydantic v2, pydantic-settings, BeautifulSoup, loguru, and click.
- `researches/02-dead-imports-crossrefs.md`: local import graph, dependency graph, and legacy boundary audit.
- `researches/03-runtime-gap-callgraph.md`: runtime flow, critical execution gaps, and dead safety code.
- `researches/04-overlap-redundancy.md`: Gen3 internal overlaps and refactor candidates.
- `researches/05-config-environment.md`: config/env source audit and runtime wiring gaps.
- `researches/06-test-gap-analysis.md`: existing test inventory and missing safety-critical coverage.

Historical planning documents in `archive/` — including the former `docs/SPEC.md`, consolidated research, and re-audit reports — are preserved for reference only and have no authority over implementation.

Important correction: the handoff description claiming Gen1, Gen2, and Gen3 coexist is stale. Current source is Gen3-only.

## 3. Current Codebase Inventory

### 3.1 Active Runtime Files

Current `src/**/*.py` inventory:

- `src/__init__.py`: package marker.
- `src/app.py`: `TradingApp`, startup reconciliation, monitor loop, scanner loop.
- `src/annotations.py`: soft warning labels and sizing multipliers.
- `src/classifier_features.py`: runtime-derived classifier feature helper layer.
- `src/decision_pipeline.py`: `MarketSnapshot`, `PipelineResult`, `run_pipeline`, `run_pipeline_batch`.
- `src/entries.py`: `Bar`, six entry detectors, `find_entry`.
- `src/exits.py`: prioritized exit engine P1-P11.
- `src/hard_filters.py`: mechanical hard filters and time-gate helper functions.
- `src/market_data.py`: Alpaca live quote/bar enrichment.
- `src/market_data_sim.py`: off-hours historical-bar simulation enrichment.
- `src/move_classifier.py`: move-state classifier and setup permission matrix.
- `src/paper_execution.py`: paper gateway, Alpaca paper gateway, reconciliation.
- `src/sizing.py`: pure risk sizing math.
- `src/state_machine.py`: position transitions, position store, pending order store.
- `src/journal/decision_logger.py`: JSONL decision logging.
- `src/models/schemas.py`: Pydantic v2 enums and models.
- `src/scanner/attention.py`: attention scoring and former-runner store only.
- `src/scanner/confidence.py`: data confidence and scanner-age logic.
- `src/scanner/enrichment.py`: Finviz/Yahoo scraping, stale detection, and bounded float enrichment helpers.
- `src/scanner/scanner.py`: scanner adapters returning `Candidate` objects.

### 3.2 Active Entry And Config Files

- `main.py`: click CLI and mode-specific runtime wiring.
- `config/settings.py`: Pydantic settings models.
- `config/default_config.yaml`: YAML defaults.
- `.env.example`: safe template.
- `.env`: local secrets/config, not a source to quote or commit.
- `pyproject.toml`: package metadata and dependencies.
- `requirements.txt`, `uv.lock`: dependency lock/install artifacts.

### 3.3 Active Tests

The current test suite contains 17 source test files:

- `tests/conftest.py`
- `tests/test_phase1_schemas.py`
- `tests/test_phase1_logger.py`
- `tests/test_phase2_scanner.py`
- `tests/test_phase2_confidence.py`
- `tests/test_phase2_attention.py`
- `tests/test_phase3_hard_filters.py`
- `tests/test_phase4_classifier.py`
- `tests/test_phase5_entries.py`
- `tests/test_phase6_risk.py`
- `tests/test_phase7_state_machine.py`
- `tests/test_phase7_execution.py`
- `tests/test_phase8_exits.py`
- `tests/test_phase9_pipeline.py`
- `tests/test_phase10_app.py`
- `tests/test_settings.py`
- `tests/test_cli_rebuild.py`

### 3.4 Module Integrity

All previously-deleted module paths remain absent from the codebase. No deleted path has been reintroduced. The runtime uses only the files listed in §3.1.

## 4. Architecture Specification

### 4.1 Canonical Flow

The canonical v0.4.0 runtime flow is:

```text
main.py / Settings
  -> TradingApp
     -> startup reconciliation
     -> monitor open positions first
        -> fresh MarketSnapshot or explicit data-unavailable state
        -> exit engine
        -> execution gateway
        -> decision log
     -> scan candidates second
        -> scanner
        -> enrichment
        -> MarketSnapshot validation
        -> attention scoring and ranking
        -> data confidence
        -> soft annotations
        -> mechanical hard filters
        -> move classification
        -> entry detection
        -> risk sizing
        -> order submit
        -> fill confirmation
        -> verified stop protection
        -> decision log
```

Monitoring open positions has priority over scanning new entries. Exits run before entries in every loop iteration where both are due.

### 4.2 Module Boundaries

- `models/schemas.py` owns cross-module data contracts.
- `scanner/scanner.py` and `scanner/enrichment.py` discover candidates; they do not decide trades.
- `scanner/attention.py` scores attention only; soft warning logic now lives in `src/annotations.py`.
- `hard_filters.py` owns mechanical entry blocks and time gates.
- `move_classifier.py` classifies move state from explicit features.
- `entries.py` detects defined-risk setups from bars and context.
- `sizing.py` computes share count and dollar risk only.
- `exits.py` detects exits only; it does not submit orders.
- `paper_execution.py` owns order lifecycle, local state transitions, stop verification, and broker reconciliation.
- `market_data.py` and `market_data_sim.py` build `MarketSnapshot` objects using shared enrichment math.
- `decision_pipeline.py` composes pure decision steps, but should not hide execution safety failures.
- `app.py` orchestrates runtime order: reconcile, monitor, scan, shutdown.
- `main.py` wires settings into runtime modes.

### 4.3 Runtime Invariants

These invariants are non-negotiable:

- A candidate may not enter without a valid `MarketSnapshot` containing current price, quote age, spread, and bars sufficient for the chosen setup.
- No entry may be submitted after the entry cutoff or during watch-only mode.
- No new entry may be submitted while any open position requires emergency handling.
- Every entry must have entry price, stop price, risk per share, proposed shares, dollar risk, and invalidation.
- Every filled entry must either have verified stop protection or transition to `UNPROTECTED` and enter emergency handling.
- Local `stop_price` metadata is not proof of live stop protection.
- Partial exits must remain partial in state, order quantity, and logs.
- Exit execution must use the `ExitDecision.exit_pct` value.
- Data outages must not fabricate `price=0.0` for P&L or hard-stop logic.
- Broker truth wins over local truth after restart.
- Settings values must either be wired or removed; decorative config is forbidden.
- Live trading remains disabled unless a future spec explicitly enables it.

## 5. Historical Critical Gaps That Drove Tasks 1-12

The following gaps were the audit-start findings that produced the Task 1-12 plan below.
Treat them as historical problem statements. Current unresolved/deferred work is tracked in §10, not here.

### 5.1 Exit Execution Crash

`app.py:293-297` treats `submit_exit()` as if it returned a single order, but `paper_execution.py:199-207` returns `(PendingOrder, PositionStateModel)`. The monitor path can raise `AttributeError` and fail to execute exits.

Required outcome: unpack `(order, updated_position)` and confirm the exit fill by `order.order_id`.

### 5.2 Partial Exits Collapse To Full Liquidation

`exits.py:222-274` can produce partial `exit_pct` values, but `app.py:293-296` never forwards `exit_pct`. `paper_execution.py:204` defaults to 100, and `paper_execution.py:228-241` always closes the position.

Required outcome: pass `exit_pct`, reduce shares on partial exits, leave state `OPEN` or `RUNNER` when shares remain, and log remaining shares.

### 5.3 Data Failure Fabricates Crisis Context

`app.py:249-263` converts market-data failure into `current_price=0.0`, `quote_age_seconds=999.0`, and `Candidate(price=0.0)`. This can trigger false emergency/hard-stop exits and false P&L.

Required outcome: represent data unavailable explicitly. Do not run price-based exits from synthetic zero. If policy requires flattening on stale data, submit a market exit with `exit_price=None` and a data-unavailable reason, not a fake zero-price exit.

### 5.4 Phantom Protection

`paper_execution.py:155-171` returns early when local `pos.stop_price == stop_price`, without checking a live/pending stop exists. `_has_pending_stop()` exists at `paper_execution.py:173-177` but is not used in `protect_position()`.

Required outcome: protection idempotency must verify an active stop order, not just local price equality.

### 5.5 Missing-Protection Exit Is Unreachable

`decision_pipeline.py:402` sets `position_unprotected = pos.state == UNPROTECTED`, but `exits.py:211-219` only fires when `position_unprotected` and `position.state == OPEN` are both true.

Required outcome: missing protection must fire for `UNPROTECTED` and for `OPEN` positions with no verified stop.

### 5.6 `max_open_risk_pct` Is Dead

`hard_filters.py:205-207` explicitly does nothing for open risk, and callers do not enforce it.

Required outcome: compute `account.total_open_risk / equity` and block new entries when it exceeds `max_open_risk_pct`.

### 5.7 Daily And Per-Symbol Loss State Is Incomplete

`app.py:164-205` builds risk state from open positions only. Closed losses disappear, and `per_symbol_loss_capped` is never set.

Required outcome: keep a session loss ledger that survives closed positions and blocks/restricts symbols after configured loss caps.

### 5.8 Time Gates Are Dead

`hard_filters.py:354-384` defines watch-only, lunch, cutoff, and flatten helpers. `TradingApp` stores cutoff strings at `app.py:83-84`, but no runtime caller computes ET time or passes flags.

Required outcome: the app computes Eastern time once per cycle, enforces watch-only and cutoff for entries, and enforces flatten time for exits.

### 5.9 Classifier Is Starved

`decision_pipeline.py:296-299` passes only a small subset of features to `classify_move_state`, while `move_classifier.py:30-76` supports a much richer interface.

Required outcome: derive and pass supported features from bars and snapshot context.

### 5.10 Reconciliation Handles Only One Action In App Layer

`reconcile_positions()` supports multiple cases at `paper_execution.py:506-632`, but `app.py:147-160` only acts on `insert_protect`.

Required outcome: app-level startup reconciliation handles every returned action or explicitly escalates it.

### 5.11 Stale Broker-Side Sell Hazard

`submit_exit()` creates a sell order without cancelling/replacing existing stops. `confirm_exit_fill()` closes local state without proving conflicting broker orders are cancelled.

Required outcome: exit flow cancels/reconciles stale protective orders before or atomically with exit submission.

### 5.12 Config Is Decorative In Critical Paths

`Settings.load()` populates `settings.phase1`, but all runtime paths in `main.py:106-409` ignore those values. Defaults are duplicated in `decision_pipeline.py:200-206`, `app.py:80-92`, `hard_filters.py:27,97,238`, and `sizing.py:84`.

Required outcome: settings are wired into runtime or removed. No dead config keys.

### 5.13 Version Labels Are Currently Consistent

The third-level audit rechecked active version labels and found no active conflict: `main.py:2`, `main.py:50`, `main.py:57`, `main.py:77`, `.env.example:2`, and `pyproject.toml:3` all agree on `v0.4.0` / `0.4.0`.

Required outcome: keep this invariant. Future metadata, CLI banners, docs, and config comments must continue to agree on `0.4.0` for this release.

## 5A. Deep Audit Findings — 2026-06-16

This section records the findings from a comprehensive 6-section audit (3A-3F) of the entire codebase. Each finding cites exact file:line. Severity: CRITICAL / HIGH / MEDIUM / LOW.

### 5A.1 Loop & Market-Hours (3A)

| ID | Finding | File:Line | Severity |
|----|---------|-----------|----------|
| A1 | No market open/closed detection — `while self.is_running` has zero calendar/clock queries | `src/app.py:218` | CRITICAL |
| A2 | `et_time` never computed — defaults `None` in all `run_pipeline()` calls | `src/app.py:207,266,337` | CRITICAL |
| A3 | Watch-only window (9:30-9:35) dead — `in_watch_only_window` defaults `False` | `src/hard_filters.py:232` | CRITICAL |
| A4 | Entry cutoff (15:30) dead — `self._entry_cutoff` stored but never read | `src/app.py:83,107` | CRITICAL |
| A5 | Flatten time (15:55) dead — `check_time_exit` at `exits.py:366` never fires (et_time=None) | `src/app.py:84,108`, `src/exits.py:362-368` | CRITICAL |
| A6 | 2am Saturday → infinite spin, no sleep-until-open | `src/app.py:218-232` | HIGH |
| A7 | `_shutdown()` empty stub (comment only) | `src/app.py:391-394` | HIGH |

### 5A.2 Execution Quality (3B)

| ID | Finding | File:Line | Severity |
|----|---------|-----------|----------|
| B1 | Tuple-unpacking crash: `submit_exit` returns `(PendingOrder, PositionStateModel)`, assigned to scalar. `order.order_id` → AttributeError | `src/app.py:293,297`, `src/paper_execution.py:207,226` | CRITICAL |
| B2 | `exit_pct` dropped — `submit_exit` called with only `symbol, reason`. Scale-outs dead. | `src/app.py:293`, `src/paper_execution.py:204` | CRITICAL |
| B3 | EXITING zombies: exit check only matches `(OPEN, UNPROTECTED)` — stuck EXITING never re-checked | `src/decision_pipeline.py:400` | HIGH |
| B4 | Phantom protection: `protect_position` trusts local `pos.stop_price` only, not `_has_pending_stop()` | `src/paper_execution.py:155-171` | HIGH |
| B5 | `confirm_exit_fill` (Alpaca) unconditional CLOSE — `pos.state=CLOSED` at line 490 runs regardless of fill status | `src/paper_execution.py:483-493` | CRITICAL |
| B6 | Ghost positions survive after manual broker close — no periodic reconciliation | `src/app.py:218-233` | HIGH |
| B7 | Startup reconciliation skipped when broker `{}` (empty): `if broker:` is falsy | `src/app.py:146` | HIGH |
| B8 | `reconcile_positions` returns 7 action types, `_reconcile_on_startup` handles only `insert_protect` | `src/app.py:147-160` | HIGH |
| B9 | Data outage → `current_price=0.0` → false emergency exits, fake P&L | `src/app.py:259-261` | CRITICAL |

### 5A.3 Entry Detection (3C)

| ID | Finding | File:Line | Severity |
|----|---------|-----------|----------|
| C1 | Classifier starved: 24/32 params never passed. All candidates → EARLY | `src/decision_pipeline.py:296-300` | CRITICAL |
| C2 | 4/6 entry setups unreachable: micro_pullback, hod_reclaim, consolidation_breakout, scalp_reclaim | `src/entries.py:356,631`, `src/move_classifier.py:348-355` | HIGH |
| C3 | `att_mult > 0.25` strict inequality blocks attention floor (score 49→0.25→fails gate) | `src/decision_pipeline.py:310` | MEDIUM |
| C4 | `detect_first_pullback` has no MoveState dependency — works correctly | `src/entries.py:265` | NONE |
| C5 | No hard blocks for Chinese ADR, biotech, low-float — verified clean | `src/hard_filters.py:5-7` | NONE |

### 5A.4 MACD Analysis (3D)

| ID | Finding | Severity |
|----|---------|----------|
| D1 | MACD needs ≥26 bars — live path has 20. Not computable without raising limit. | CRITICAL |
| D2 | Bot already implements Cameron volume pattern: `_is_controlled_selling()` checks pullback vol ≤70% surge vol | NONE |
| D3 | MACD adds no new information beyond price-action criteria already measured (retrace, volume, reclaim) | — |
| D4 | Best-value spot if MACD were added: bearish-divergence exit signal (exits lack momentum checks) | — |
| D5 | OBV divergence is a superior free alternative — cumulative, simpler, no bar-count requirement | — |
| D6 | Recommendation: do NOT add MACD. Explore OBV divergence as soft annotation. | — |

### 5A.5 Silent Failures & Safety (3E)

| ID | Finding | File:Line | Severity |
|----|---------|-----------|----------|
| E1 | `except Exception: pass` in Alpaca confirm_exit_fill | `src/paper_execution.py:488-489` | HIGH |
| E2 | PENDING_ENTRY stranding: confirm_fill failure → forever locked, no timeout | `src/decision_pipeline.py:347-353` | HIGH |
| E3 | Missing-protection P4 dead: requires `position_unprotected AND state==OPEN` — contradictory | `src/exits.py:217`, `src/decision_pipeline.py:402` | CRITICAL |
| E4 | `FormerRunnerStore.mark()` never called — store always empty | `src/scanner/attention.py:361-387` | MEDIUM |
| E5 | All 11+ config fields never read at runtime — `main.py` hardcodes all values | `src/main.py:396-405` | CRITICAL |
| E6 | `save_to_disk`/`load_from_disk` exist but never called | `src/state_machine.py:225,232` | HIGH |

### 5A.6 Over-engineering vs Under-wired (3F)

| ID | Finding | File:Line | Severity |
|----|---------|-----------|----------|
| F1 | Time gate functions exist, zero callers | `src/hard_filters.py:354-384` | CRITICAL |
| F2 | `score_candidates()` (batch ranking) never called | `src/scanner/attention.py:646` | MEDIUM |
| F3 | `max_trade_risk_pct` accepted in `entry_sizing` but never used in body | `src/sizing.py:84` | HIGH |
| F4 | `max_open_risk_pct` stored but never compared | `src/app.py:112`, `src/hard_filters.py:205-207` | HIGH |
| F5 | `run_pipeline` conflates entry + exit in one function | `src/decision_pipeline.py:179-440` | MEDIUM |
| F6 | Daily loss cap from open positions only — closed losses vanish | `src/app.py:164-205` | HIGH |
| F7 | `cancel_order`/`cancel_stale_orders` never called in exit flow | `src/paper_execution.py:181` | MEDIUM |

### 5A.7 Ranked Fix Order (Top 5)

1. **Exit tuple-unpacking + exit_pct** (`app.py:293,297`) — exits literally cannot execute. Fix: unpack `order, _ = self._execution.submit_exit(...)` and pass `exit_pct=result.exit_decision.exit_pct`.
2. **Compute et_time + wire time gates** (`app.py:207-233`, `decision_pipeline.py:213,280,415`) — Fix: compute `et_time = datetime.now(ZoneInfo("US/Eastern")).time()` per cycle, pass to pipeline, call time gate functions.
3. **Bar-feature extraction → wire classifier** (`decision_pipeline.py:296-300`) — Fix: derive 20+ features from bars and pass to `classify_move_state()`. Unlocks all 5 states → all 6 entry setups.
4. **Fix confirm_exit_fill unconditional CLOSE + data-outage price=0** (`paper_execution.py:483-493`, `app.py:259-261`) — Fix: move CLOSE inside fill-confirmed block; use `current_price=None` not 0.0.
5. **Fix missing-protection P4 + phantom protection** (`exits.py:211-219`, `paper_execution.py:155-171`) — Fix: remove contradictory OPEN guard; use `_has_pending_stop()` for idempotency.

## 5B. Second-Level Deep Audit — 2026-06-16

Challenged every first-audit finding with 5 independent subagents. Key corrections and new findings below.

### 5B.1 PARTIAL CORRECTION: Classifier Starvation Is Not Always A Critical Entry Blocker

The first audit claimed 4/6 entry setups are unreachable and entry conversion rate is de facto zero. This is **wrong**.

**What actually happens**: `detect_first_pullback` and `detect_vwap_reclaim` have NO internal MoveState dependencies — they work purely from bar data. Both are allowed from EARLY state. Combined, they cover **~70-85% of top-gainer entry opportunities**. The bot CAN and WILL enter trades through first_pullback on valid pullback setups.

**What IS actually lost**:
- `micro_pullback` (needs ACTIVE state, genuinely unreachable) — for established runners with multi-bar patterns
- `hod_reclaim` and `consolidation_breakout` — blocked by permission matrix from EARLY, but rarely applicable to fresh gainers
- `scalp_reclaim` — edge case, needs EXTENDED/HALT_RISK
- **BACKSIDE false positive**: when spread>1% AND price >2% from VWAP, a valid pullback gets misclassified as BACKSIDE, which blocks `first_pullback` (the best setup). `move_classifier.py:234-236`

**Severity downgrade**: CRITICAL → HIGH. Not blocking entries, but costs ~15-30% of specialized setups + causes BACKSIDE false-positive entry blocks.

**Third-level correction**: this downgrade is only partially correct. If VWAP exists, `vwap_reclaim` can rescue some `first_pullback` failures, including pullbacks longer than the first-pullback 2-8 bar window. If VWAP is missing and spread is >1%, `_can_reclaim(price, vwap)` returns false, the candidate can be classified as BACKSIDE by `move_classifier.py:234-236`, BACKSIDE permits only `vwap_reclaim`, and `vwap_reclaim` cannot run without VWAP. In that compound path, the best setup (`first_pullback`) is hard-blocked. Treat classifier/starved-enrichment interaction as CRITICAL until VWAP validation, classifier feature derivation, and permission-matrix behavior are repaired together.

### 5B.2 OVERTURNED CORRECTION: Price=0.0 Fabrication Is Actively Dangerous

P1 emergency exit at `exits.py:103` fires first (quote_age_seconds=999 > 60) before P3 hard_stop ever evaluates `current_price <= stop_price`. The 0.0 never reaches P&L computation because P1 exits before it. This is a **coupling fragility**, not an active crisis. The fix is still needed, but it's not causing false liquidations today.

**Third-level correction**: this statement is wrong. P1 at `exits.py:102-105` computes P&L using the supplied `price`, and the monitor fallback supplies `current_price=0.0` with `quote_age_seconds=999.0` at `app.py:258-273`. The emergency exit therefore creates an active false-exit path and fake P&L path, including for positions with verified broker-side protection. Required outcome remains: data unavailable is not a price, and a transient outage on a verified-protected position must hold/retry rather than flatten.

### 5B.3 NEW CRITICAL: P&L Ledger Catastrophe

`_build_risk_state()` at `app.py:170` calls `self._positions.all_open()`, which excludes CLOSED positions (`state_machine.py:187`). Every closed position's realized P&L **vanishes** from the daily running total.

**Proof by scenario**: $100K equity, 3% daily cap = $3,000. Open 10 trades, each loses $300. Every trade's $300 loss disappears when CLOSED. `daily_pnl_val` = $0. Daily loss cap **never triggers**. You can lose unlimited by opening/closing repeatedly.

Additionally: `per_symbol_loss_capped` defaults `False` at `decision_pipeline.py:211` and is **never set True**. Per-symbol loss tracking is completely dead. The `per_symbol_daily_loss` dict in `AccountRiskState` is populated but never read.

**Severity**: CRITICAL — defeats the primary risk control (daily loss cap).

### 5B.4 NEW CRITICAL: Stale Broker Stops Never Cancelled

`cancel_stale_orders()` exists at `paper_execution.py:189-195`. **Called zero times in production.** When a position is fully exited:
- No stop cancellation in `submit_exit` (`paper_execution.py:199-226`)
- No stop cancellation in `confirm_exit_fill` (`paper_execution.py:228-242`)
- Alpaca override same gap (`paper_execution.py:477-498`)

In Alpaca paper mode, the broker retains a live GTC stop-sell order after the position is closed. If price drops to the stale stop, it creates an unintended short position. Even if broker rejects it, local `PendingOrderStore` retains zombie orders with no cleanup.

**Severity**: CRITICAL for live — creates real broker-side hazard.

### 5B.5 NEW HIGH: 45-Second Data Outage Cliff

Between quote_age=15s (blocks new entries, `hard_filters.py:116`) and quote_age=60s (emergency exit, `exits.py:103`), the bot **holds positions with stale data and takes no action**. No partial scaling, no stop tightening, no degradation. For volatile top-gainer stocks, 30 seconds without fresh quotes is an eternity.

### 5B.6 NEW HIGH: Death-By-Multipliers Sizing

For a typical small-cap top gainer with attention~75:
- `starter_risk` = $250
- `attention_multiplier(75)` = 0.75 → $187
- `float_unknown` = 0.50x → $93 (hits virtually every small-cap)
- `data_confidence` ≈ 0.70x → $65
- **Final risk: $65 (0.065% of account). Institutional-level conservative, not day-trader behavior.**

The `float_unknown` penalty at 0.5x (`attention.py:610`) is particularly aggressive — most free-tier scanner stocks lack float data. `no_news` at attention<70 adds another 0.75x. The bot sizes like it's managing a pension fund.

### 5B.7 NEW HIGH: Philosophy Mismatch — Candidates First?

SPEC says "Top gainers are candidates first, not suspects first." The code defaults to "watch" (6 paths in `decision_pipeline.py` vs 2 "enter" paths). Every candidate starts as suspect. `float_unknown` halves risk by default. The bot is a risk-avoidance system wearing a trader's hat.

### 5B.8 UPDATED RANKED FIX ORDER (Top 7)

1. **Exit tuple-unpacking + exit_pct** (`app.py:293,297`) — exits literally don't work.
2. **Compute et_time + wire time gates** (`app.py:207-233`) — prevents off-hours trading.
3. **Session P&L ledger** (NEW — `app.py:164-205`) — persist closed P&L for daily loss cap enforcement. Without this, the primary risk control is fake.
4. **Cancel stale orders on exit** (NEW — `paper_execution.py:189-195` wiring) — call `cancel_stale_orders` in exit flow. Prevents broker-side hazard.
5. **Fix confirm_exit_fill unconditional CLOSE** (`paper_execution.py:483-493`) — move CLOSE inside fill-confirmed block.
6. **Fix missing-protection P4 + phantom protection** (`exits.py:211-219`, `paper_execution.py:155-171`).
7. **Bar-feature extraction → wire classifier** (`decision_pipeline.py:296-300`) — downgraded from #3 to #7. Unlocks 15-30% additional setups + fixes BACKSIDE false positives.

## 5C. Third-Level Adversarial Audit — 2026-06-17

This section records the third-level adversarial audit. It supersedes conflicting interpretations in §5A and §5B, especially the understatements in §5B.1 and §5B.2. The audit did not change application code; it documents implementation tasks and fail-loud requirements.

### 5C.1 Cross-Validation Corrections

| ID | Finding | File:Line | Severity | Required outcome |
|----|---------|-----------|----------|------------------|
| C3A | Exit tuple crash is an EXITING zombie factory: `submit_exit()` mutates state before the app crashes on tuple access, then `EXITING` is excluded from future exit checks. | `src/app.py:293,297`; `src/paper_execution.py:213,226`; `src/decision_pipeline.py:400` | CRITICAL | Fix tuple unpacking, preserve exit state transitions, and add explicit `EXITING` recovery/timeout/reconciliation. |
| C3B | `exit_pct` is lost at app submission and base confirmation always closes the position, so partial exits cannot work even after tuple unpacking alone. | `src/app.py:293`; `src/paper_execution.py:204,228-235` | CRITICAL | Pass `ExitDecision.exit_pct`, reduce shares, resize protection, and close only when remaining shares reach zero. |
| C3C | Data outage is active danger, not harmless coupling: fallback `price=0.0` and `quote_age_seconds=999.0` trigger P1 emergency and fake P&L. | `src/app.py:258-273`; `src/exits.py:102-105` | CRITICAL | Represent data unavailable explicitly; never compute P&L or price-based exits from synthetic zero. |
| C3D | Verified-protected positions are flattened on quote outage, directly conflicting with §10.3. | `src/exits.py:102-105` | CRITICAL | Protected + verified stop + transient data outage = hold/retry/log, not flatten. |
| C3E | `broker_unreachable_seconds` is accepted by exits but never passed by the runtime, and the exit helper intentionally returns `None` expecting the caller to mark `UNPROTECTED`. | `src/exits.py:117-119,415`; `src/decision_pipeline.py:403-416` | HIGH | Runtime must track broker-unreachable duration and mark positions requiring verification or `UNPROTECTED` according to policy. |
| C3F | Missing-protection P4 is impossible: runtime sets `position_unprotected` only when state is `UNPROTECTED`, while P4 also requires `state == OPEN`. | `src/decision_pipeline.py:402`; `src/exits.py:217` | CRITICAL | Define protection truth from live/pending stop verification, not only local state. |
| C3G | Alpaca entry confirmation treats rejected, pending, canceled, partial, and API-failed orders as local `OPEN`. | `src/paper_execution.py:382-392` | CRITICAL | Only confirmed filled quantities may become local `OPEN`; rejected/canceled orders must fail loud and release locks. |
| C3H | Alpaca position state is committed before broker success, then synthetic IDs replace broker truth on exceptions. | `src/paper_execution.py:328-337,352-354,399-435,437-475` | CRITICAL | Broker mode must not fabricate order IDs/fills/stops as success. Use explicit pending/error states. |
| C3I | `confirm_exit_fill()` in Alpaca mode swallows exceptions and always closes locally, which can hide real broker exposure. | `src/paper_execution.py:488-492` | CRITICAL | Closing local state requires confirmed filled exit quantity; API failure must remain unresolved and fail loud. |
| C3J | Stale GTC stops are not cancelled/resized during full or partial exits and can create unintended short exposure. | `src/paper_execution.py:189-195,437-498` | CRITICAL | Full exits cancel protective orders; partial exits resize or replace protection for remaining quantity. |
| C3K | Broker reconciliation is effectively unwired in loop mode because `broker_snapshot_fn` defaults to `{}` and `_run_paper_loop` does not provide a broker snapshot. | `src/app.py:103`; `main.py:393-405` | HIGH | Paper-broker mode must wire a real broker snapshot and fail loud when broker truth is unavailable. |
| C3L | Startup reconciliation drops all actions except `insert_protect`. | `src/app.py:147-160`; `src/paper_execution.py:528-632` | HIGH | Handle or escalate every reconciliation action. |
| C3M | Realized/unrealized P&L inputs are not reliable: base paper exits never set realized P&L, `unrealized_pnl` has no runtime writer, and closed positions are excluded from `_build_risk_state()`. | `src/paper_execution.py:228-242`; `src/app.py:185-194`; `src/state_machine.py:185-188` | CRITICAL | Add a session ledger and mark-to-market update path used by entry gates and loss-cap exits. |
| C3N | `max_open_risk_pct` and `max_trade_risk_pct` are independent dead controls. | `src/hard_filters.py:205-207`; `src/sizing.py:84` | HIGH | Enforce both or remove the config keys. For v0.4.0, enforce both. |
| C3O | ACTIVE move state is unreachable from runtime because `_is_active()` needs at least 3 core signals and the pipeline supplies at most spread and RVOL. | `src/decision_pipeline.py:296-300`; `src/move_classifier.py:295-332` | HIGH | Derive bar features before classification or simplify the state model. |
| C3P | VWAP-missing + spread>1% can falsely classify BACKSIDE, whose permission matrix blocks `first_pullback` and permits only `vwap_reclaim`, which itself requires VWAP. | `src/move_classifier.py:234-236,335-355`; `src/entries.py:570-582` | CRITICAL | Validate VWAP before state-gated setup restriction or make missing VWAP produce data-degraded EARLY/watch behavior, not BACKSIDE lockout. |
| C3Q | Attention gate `att_mult > 0.25` can block legitimate 10-19% gainers when HOD/ROC data is unavailable. | `src/decision_pipeline.py:310`; `src/scanner/attention.py:73-189` | HIGH | Recalibrate attention gating so missing enrichment does not become categorical rejection. |
| C3R | `float_shares` is never populated in production, so every candidate receives `float_unknown` and a 0.50x sizing penalty. | `src/scanner/scanner.py:61-73`; `src/scanner/attention.py:522-523,610`; `src/models/schemas.py:110` | HIGH | Populate float from a bounded enrichment source or reduce/remove the universal penalty. Missing float is a sizing signal, not a default suspicion. |
| C3S | `scrape_yfinance_gainers()` exists but is dead; there is no real fallback when Alpaca enrichment is unavailable. | `src/scanner/enrichment.py:196-249` | HIGH | Either wire a bounded fallback or remove the dead path; do not imply fallback safety that does not exist. |
| C3T | `_finviz_is_stale()` exists but is never called, and sim mode can fake fresh quotes for stale historical scanner output. | `src/scanner/enrichment.py:252-275`; `src/market_data_sim.py:130-131` | HIGH | Detect stale scanner output before candidate processing; sim freshness must be labeled simulated, not live-fresh. |
| C3U | Serial candidate processing plus no pre-order price recheck can submit entries from stale snapshots. | `src/app.py:324`; `src/decision_pipeline.py:342-343` | MEDIUM | Recheck quote/price/spread immediately before order submission when execution gateway is present. |
| C3V | `PositionStore.save_to_disk()` / `load_from_disk()` and `FormerRunnerStore.mark()` are test-only/dead in runtime. | `src/state_machine.py:225,232`; `src/scanner/attention.py:371` | MEDIUM | Wire them deliberately or remove/defer them; no silent pseudo-persistence. |
| C3W | `model_copy(update=...)` does not revalidate `EntrySignal` constraints after sizing updates. | `src/decision_pipeline.py:340` | MEDIUM | Reconstruct or validate sized entry signals before order submission. |
| C3X | Tests give false confidence by asserting decision logs, directly injected classifier states, or full-close behavior instead of runtime state/broker outcomes. | `tests/test_phase10_app.py`; `tests/test_phase4_classifier.py`; `tests/test_phase7_execution.py` | HIGH | Replace weak assertions with state, pending-order, broker-call, and ledger assertions. |
| C3Y | Version labels are already consistent at `0.4.0`; the prior version-conflict item is overturned. | `main.py:2,50,57,77`; `.env.example:2`; `pyproject.toml:3` | NONE | Preserve consistency; no release-blocking version fix is required today. |

### 5C.2 Compound Failure Chains

These chains must be considered during implementation. Fixes must not solve one link while leaving the compound hazard active.

1. **Exit decision → EXITING zombie → no future exits**: any exit mutates to `EXITING`, app tuple-crashes, pipeline excludes `EXITING`, and no periodic reconciliation rescues it. Fix tuple handling, partial lifecycle, `EXITING` recovery, and reconciliation together.
2. **Phantom protection → no `mark_unprotected()` → impossible P4**: local stop metadata suppresses re-protection, so the only state flag P4 uses is never set, while P4's guard is contradictory. Fix protection truth and P4 semantics together.
3. **VWAP missing → false BACKSIDE → permission lockout**: missing data becomes categorical setup rejection. Missing enrichment must degrade confidence/size or produce explicit watch/skip, not pretend bearish structure exists.
4. **Closed losses vanish → per-symbol cap dead → open-risk cap dead**: no loss cap safety net is active. Ledger, per-symbol cap, open-risk cap, and max-trade-risk cap must all be tested as one safety surface.
5. **No market-hours guard → stale scanner → no flatten**: off-hours or weekend cycles can process stale candidates and hold positions with no scheduled flatten. Market calendar, ET gates, stale scanner detection, and shutdown persistence interact.
6. **Attention threshold → multiplier crush**: candidates just below threshold are rejected; candidates just above threshold get economically meaningless size. Missing data must not be treated as an implicit disqualifier.
7. **Broker market exit → stale GTC stop → unintended short**: exit flow must cancel/replace protective orders before/with sell orders and must reconcile broker truth after fills.

### 5C.3 Philosophy Alignment Guardrail

The code must continue to embody: **top gainers are candidates first, not suspects first**. This does not mean every stock trades. It means the bot must seek a safe participation path before rejecting.

Implementation rules:

- Chinese ADR, biotech, no-news, low-float, float-unknown, speculative label, and missing non-critical enrichment are sizing/confidence annotations, not categorical hard blocks.
- Mechanical execution-safety failures remain hard blocks: invalid quote, stale/unavailable required market data for entries, impossible risk definition, spread beyond configured hard reject, halt status, locked symbol, loss caps, market-hours blocks, broker safety failures.
- Missing data must be explicit and fail-loud. It may reduce confidence, reduce size, or produce a machine-readable watch/skip reason. It must not masquerade as bearish price action.
- Permission matrices may prevent false positives, but they must not convert missing enrichment into categorical rejection. If a state cannot be proven from available features, use a conservative descriptive state and log the missing features.
- Sizing penalties should be bounded and explainable. A universal data-gap penalty that silently reduces every trade to negligible size violates the attention-first philosophy.

### 5C.4 Third-Level Ranked Fix Order

If only seven implementation batches are allowed before paper verification, use this order:

1. **Exit lifecycle safety**: tuple unpacking, `exit_pct`, partial share reduction, full-close semantics, `EXITING` timeout/recovery, and state/pending-order assertions.
2. **Broker-truth Alpaca paper gateway**: no synthetic success in broker mode, explicit order statuses, credential validation, fill/reject/partial handling, fail-loud unresolved states.
3. **Protection and stale-order safety**: live/pending stop verification, missing-protection exit reachability, broker-unreachable policy, cancel/resize protective orders on full/partial exits.
4. **Data outage and market-hours policy**: no synthetic zero price, protected-outage hold/retry, stale quote handling, ET gates, market-open detection, flatten time.
5. **Risk and P&L ledger**: realized/unrealized ledger, daily/per-symbol caps, open-risk cap, max-trade-risk cap, ledger persistence for the session.
6. **Entry/scanner viability**: snapshot validation, stale Finviz detection, float enrichment or penalty recalibration, pre-order quote recheck, candidate ranking, classifier feature derivation, VWAP-missing permission behavior.
7. **Config and test truthfulness**: wire `phase1`, validate unknown/mistyped config, replace weak tests with runtime state/broker/ledger assertions, and remove or document dead helpers.

## 6. Risk And Safety Specification

### 6.1 Entry Safety

Before submitting any entry:

- Settings must be loaded and wired.
- ET time gates must allow entries.
- Global daily loss cap must not be breached.
- Per-symbol daily loss cap must not be breached.
- Open risk must be below `max_open_risk_pct`.
- Open position count must be below `max_positions`.
- The symbol must not be locked by open, pending, exiting, or unresolved orders.
- Candidate price, bid/ask/spread, quote timestamp, and bars must be present and valid.
- A defined-risk setup must exist.
- Risk per share must be positive.
- Sizing must produce at least one share and not exceed `max_trade_risk_pct`.

### 6.2 Protection Safety

After an entry fill:

- A stop order must be submitted for the filled quantity.
- The stop order must be stored as a pending protective order.
- The gateway must be able to verify protection status.
- If stop submission or verification fails, the position transitions to `UNPROTECTED`.
- `UNPROTECTED` positions must be monitored before scanning any new entries.

### 6.3 Exit Safety

Exit execution must be deterministic:

- The app must unpack `(order, position)` from `submit_exit()`.
- `ExitDecision.exit_pct` must be passed to `submit_exit()`.
- Full exits close the position only after fill confirmation.
- Partial exits reduce `current_shares` and keep the position open if shares remain.
- Exit orders must cancel or reconcile stale protective orders for the symbol.
- Every exit attempt must create a decision log entry with reason, requested pct, order id, and resulting state.

### 6.4 Market Data Failure Policy

Data unavailable is a state, not a price.

When market data fails for an open position:

- Do not set `current_price=0.0`.
- Do not compute P&L from fabricated data.
- Do not trigger hard-stop logic from fabricated data.
- Mark quote state as unavailable/stale.
- If the position lacks verified protection, trigger emergency handling.
- If protection is verified, suppress new entries, log the incident, retry data on the next monitor cycle, and optionally flatten only under an explicit configured fail-safe.

When market data fails for a candidate:

- Do not enter.
- Log `skip` with machine-readable missing-data reasons.

### 6.5 Reconciliation Safety

On startup:

- Query broker position truth when a broker gateway is configured.
- Load local position state when persistence exists.
- Compare broker positions, local positions, and pending orders.
- Handle every reconciliation action: `insert_protect`, `verify_stop`, `update_qty_reprotect`, `update_qty_reprotect_warning`, `close_local`, `cancel_stale_order`, `irreconcilable`, and broker-unreachable fallback.
- Never assume local state is correct when broker truth differs.

## 7. Dependency-Ordered Implementation Plan

Each task below should be implemented in a small verified batch. Do not combine unrelated tasks.

### Phase 0 — Specification And Guardrails

#### T0.1 Freeze Source-Of-Truth Order

- Files: `SPEC.md`, `researches/00-architecture-decision.md`, `archive/`.
- Action: root `SPEC.md` is the sole implementation authority. The former `docs/SPEC.md` has been moved to `archive/historical-docs-spec-2026-06-12.md`. All historical planning reports are in `archive/`.
- Depends on: none.
- Status: **COMPLETED** — no ambiguity remains about spec authority.
- Acceptance: exactly one active spec exists; no file outside `archive/` claims to be an alternative spec.

#### T0.2 Verify No Stale Module Imports

- Files: `main.py`, CLI tests.
- Action: the codebase imports only active modules listed in §3.1. No deleted module paths exist or are importable.
- Depends on: none.
- Status: **COMPLETED** — all deleted module paths verified absent; no import guards needed.
- Acceptance: all imports resolve to active `src/` modules only.

### Phase 1 — Config, Version, And Runtime Wiring

#### T1.1 Resolve Version Label

- Files: `pyproject.toml`, `main.py`, `.env.example`, docs.
- Action: keep the already-consistent `0.4.0` / `v0.4.0` labels in metadata, banners, comments, and docs. No version rename is required unless a future grep finds drift.
- Depends on: T0.1.
- Status: **COMPLETED AS OF THIRD-LEVEL AUDIT** — active labels were verified consistent.
- Acceptance: `main.py`, `.env.example`, `pyproject.toml`, and root docs agree on `0.4.0` / `v0.4.0`; old labels appear only in intentional history/changelog text.

#### T1.2 Wire `settings.phase1` Into All Runtime Paths

- Files: `main.py`, `src/app.py`, `src/decision_pipeline.py`, `src/hard_filters.py`, `src/sizing.py`.
- Action: pass `settings.phase1` values into `TradingApp` and `run_pipeline` calls in mock, paper, sim, and paper loop modes.
- Depends on: T1.1.
- Status: **COMPLETED** — all 4 runtime modes (mock, paper, paper loop, sim) now pass `starter_risk_pct`, `max_trade_risk_pct`, `max_positions`, `max_open_risk_pct`, `max_daily_loss_pct`, `focus_price_min`, and `focus_price_max` from `settings.phase1`. Cadence values (`monitor_interval_seconds`, `scanner_interval_seconds`) wired in paper loop mode.
- Acceptance: changing `PHASE1_MONITOR_INTERVAL_SECONDS`, `PHASE1_SCANNER_INTERVAL_SECONDS`, `PHASE1_STARTER_RISK_PCT`, `PHASE1_MAX_TRADE_RISK_PCT`, `PHASE1_MAX_POSITIONS`, `PHASE1_MAX_OPEN_RISK_PCT`, `PHASE1_MAX_DAILY_LOSS_PCT`, `PHASE1_FOCUS_PRICE_MIN`, and `PHASE1_FOCUS_PRICE_MAX` changes runtime behavior in tests.

#### T1.3 Remove Or Wire Dead Config Keys

- Files: `config/settings.py`, `config/default_config.yaml`, `tests/test_settings.py`.
- Action: removed unwired fields from `Phase1Settings` and `default_config.yaml`: `fresh_quote_seconds`, `max_quote_age_seconds`, `max_candidates`. `max_trade_risk_pct` was later re-added once it had tested runtime read sites and real enforcement (T4.4 + scan-path wiring).
- Depends on: T1.2.
- Status: **COMPLETED** — three dead keys remain removed; `max_trade_risk_pct` is now live again with tested runtime wiring through settings → main → app/pipeline → sizing.
- Acceptance: no settings key exists without at least one tested runtime read site.

#### T1.4 Add Broker/Alpaca Settings Or Startup Validation

- Files: `config/settings.py`, `src/market_data.py`, `src/market_data_sim.py`, `src/paper_execution.py`, `main.py`.
- Action: added `alpaca_api_key` / `alpaca_secret_key` to `TradingSettings` with `validation_alias` reading from `ALPACA_*` env vars. Added `require_alpaca_credentials()` method. Credentials validated at startup in `_run()` for paper/sim/live modes. Passed through `AlpacaExecutionGateway`, `build_market_snapshot`, and `build_market_snapshot_sim` params (with `os.getenv` fallback for backward compat).
- Depends on: T1.2.
- Status: **COMPLETED** — single startup validation with one clear error message replaces three scattered `os.getenv` failures.
- Acceptance: missing credentials produce one clear startup/mode message, not three separate raw `os.getenv()` behaviors.

#### T1.5 Make Configuration Fail Loud

- Files: `config/settings.py`, `config/default_config.yaml`, `.env.example`, `tests/test_settings.py`.
- Action: added 9 tests: `TestPhase1EnvVarIngestion` (env overrides defaults/YAML, invalid values rejected), `TestMisPrefixedKeySafety` (mis-prefixed keys silently ignored per `extra='ignore'` policy, now documented in model docstrings), `TestAlpacaCredentialValidation` (passes with creds, raises without, picks up from env). Documented `extra='ignore'` in `TradingSettings` and `Phase1Settings` docstrings.
- Depends on: T1.2, T1.4.
- Status: **COMPLETED** — 682 tests pass (673 baseline + 9 new). Env var ingestion, mis-prefix safety, and credential validation all covered.
- Acceptance: typo/mis-prefix cases are either rejected with a clear message or explicitly documented as ignored; phase1 config changes are proven through runtime behavior, not only Pydantic model loading.

### Phase 2 — Exit Execution And Protection Safety

#### T2.1 Fix Exit Tuple Unpacking

- Files: `src/app.py`, `src/paper_execution.py`, `tests/test_phase10_app.py`.
- Action: unpack `order, pos_after = self._execution.submit_exit(...)` and pass `order.order_id` to `confirm_exit_fill()`. Also pass `exit_pct` from `result.exit_decision.exit_pct`.
- Depends on: T0.2.
- Status: **COMPLETED** — `app.py:293` now unpacks `order, pos_after` and passes `exit_pct`. `paper_execution.py` confirm_exit_fill (both paper and Alpaca versions) reduces shares instead of unconditionally closing. 3 tests updated to set up verified pending stop orders for protection.
- Acceptance: a monitor-path exit closes or reduces a position without `AttributeError`.

#### T2.2 Preserve Partial Exits

- Files: `src/app.py`, `src/paper_execution.py`.
- Action: pass `result.exit_decision.exit_pct`; make `confirm_exit_fill()` reduce shares for partials and close only on zero shares.
- Depends on: T2.1.
- Status: **COMPLETED** — implemented jointly with T2.1. `confirm_exit_fill` computes `remaining = max(current_shares - filled_qty, 0)`, sets `pos.current_shares = remaining`, and only sets `state=CLOSED` when `remaining == 0`.
- Acceptance: P5 scale-outs at 25, 33, and 50 percent leave correct remaining shares and state.

#### T2.3 Cancel Or Reconcile Protective Orders During Exit

- Files: `src/paper_execution.py`.
- Action: call `self.cancel_stale_orders(symbol)` before creating the sell order in both paper and Alpaca `submit_exit()` methods.
- Depends on: T2.2.
- Status: **COMPLETED** — `cancel_stale_orders(symbol)` added to both `PaperExecutionGateway.submit_exit()` and `AlpacaExecutionGateway.submit_exit()` immediately after position lookup and before state transition.
- Acceptance: no stale pending stop remains for a fully closed position; partial exit leaves one protective order for remaining quantity.

#### T2.4 Make Protection Truth Verifiable

- Files: `src/paper_execution.py`, `tests/test_phase10_app.py`.
- Action: update `protect_position()` to require `_has_pending_stop()` in addition to `pos.stop_price == stop_price` before returning None.
- Depends on: T2.3.
- Status: **COMPLETED** — line 169 now reads `if pos.stop_price == stop_price and self._has_pending_stop(symbol): return None`. 3 app monitor tests updated to call `gw.place_stop()` before exercising exit checks, ensuring verified protection.
- Acceptance: an open position with `stop_price` but no pending stop is treated as unprotected and receives a new stop or transitions to `UNPROTECTED`.

#### T2.5 Make Missing-Protection Exit Reachable

- Files: `src/exits.py`, `src/decision_pipeline.py`, `src/paper_execution.py`.
- Action: fix `check_missing_protection()` to accept `UNPROTECTED` state directly (not just `OPEN + position_unprotected`). Derive `position_unprotected` from state AND stop verification via `execution_gw._has_pending_stop()`.
- Depends on: T2.4.
- Status: **COMPLETED** — `exits.py:217` now fires for `position_unprotected OR pos.state == UNPROTECTED`. `decision_pipeline.py:402` derives `position_unprotected` from both `pos.state == UNPROTECTED` and `not execution_gw._has_pending_stop(pos.symbol)`.
- Acceptance: an unprotected losing position triggers P1/P4 emergency behavior as specified, and a protected position does not.

#### T2.6 Add `EXITING` Recovery And Timeout

- Files: `src/decision_pipeline.py`, `src/paper_execution.py`.
- Action: EXITING positions are now included in exit checks. After 120s timeout, stale EXITING positions are escalated to UNPROTECTED via `mark_unprotected()` (extended to accept EXITING state). Within timeout, EXITING positions skip exit checks.
- Depends on: T2.1, T2.2.
- Status: **COMPLETED** — `decision_pipeline.py:400` includes `EXITING` in the state filter. `mark_unprotected()` now accepts `EXITING` in addition to `OPEN`. Timeout constant at 120s with TODO to promote to `Phase1Settings`.
- Acceptance: a simulated confirm failure cannot leave a position permanently invisible to exit checks.

#### T2.7 Validate Sized Entry Signals After Sizing

- Files: `src/decision_pipeline.py`, `src/models/schemas.py`, entry/pipeline tests.
- Action: replace or wrap `model_copy(update=...)` so sizing updates are validated before order submission. Per Context7 docs, `model_copy(update=...)` does NOT validate; this must use `model_validate()` or reconstruct.
- Depends on: none — it was temporarily blocked by `max_trade_risk_pct` wiring, but the actual implementation is independent once sizing output exists.
- Status: **COMPLETED** — `run_pipeline()` now rebuilds sized signals with `EntrySignal.model_validate({...})` instead of `model_copy(update=...)`. Field constraints and the `model_validator(mode='after')` now run on sized entry signals before submission.
- Acceptance: zero-share, zero-risk, and risk-per-share mismatch sized signals fail validation before order submission.

### Phase 3 — Market Data Failure And Time Gates

#### T3.1 Remove Fabricated Zero-Price Monitor Context

- Files: `src/app.py`.
- Action: removed `Candidate(price=0.0)` and `quote_age_seconds=999.0` fabrication. Data unavailable is now handled per §6.4 policy: protected positions hold/retry, unprotected positions escalate to UNPROTECTED.
- Depends on: T2.1.
- Status: **COMPLETED** — `_monitor_positions()` now implements SPEC §6.4 data-unavailable policy. Protected + verified stop → skip cycle. Unprotected → mark UNPROTECTED, fall through to exit engine. No more fake exit_price=0.0 or PnL from synthetic zero.
- Acceptance: market-data exception does not produce fake `exit_price=0.0` or fake P&L.

#### T3.2 Add Explicit Data-Unavailable Exit Policy

- Files: `src/app.py`.
- Action: implemented jointly with T3.1. Data-unavailable policy: (a) protected + verified stop = hold, log, retry next cycle; (b) unprotected = mark UNPROTECTED via `mark_unprotected()`, let P4 exit engine handle.
- Depends on: T3.1.
- Status: **COMPLETED** — `_monitor_positions()` checks `_has_pending_stop(pos.symbol)` before decide. Protected positions with data outage cycle through without pipeline call.
- Acceptance: tests cover protected position with data outage, unprotected position with data outage.

#### T3.3 Wire Eastern Time Gates Into Runtime

- Files: `src/app.py`, `src/decision_pipeline.py`, `src/hard_filters.py`.
- Action: compute `et_time = datetime.now(ZoneInfo("US/Eastern")).time()` per cycle in `app.py:run()`. Pass to `run_pipeline()` for both monitor and scan paths. Derive `past_entry_cutoff`/`in_watch_only_window` in pipeline from `et_time` and pass to `run_hard_filters()`. Flatten time (P10) wired via `check_time_exit(et_time=...)`.
- Depends on: T1.2.
- Status: **COMPLETED** — ET time computed each loop cycle. Pipeline derives time-gate flags and passes to hard filters. Exit engine's P10 receives et_time for 15:55 flatten.
- Acceptance: 09:30-09:35 watch-only blocks entries via hard filters, 15:30 cutoff blocks entries, 15:55 triggers flatten exits via P10.

#### T3.4 Enforce Monitor-Before-Scan Priority

- Files: `src/app.py`.
- Action: monitor check runs before scan check in loop (already the case). Added `_has_emergency()` helper — if any position is UNPROTECTED or EXITING, scan is suppressed until emergency is resolved.
- Depends on: T3.2, T3.3.
- Status: **COMPLETED** — `_has_emergency()` checks for UNPROTECTED/EXITING positions. Scan suppressed when emergencies exist.
- Acceptance: if a monitor exit and scanner entry are both due, exit handling occurs first and can suppress scanning if unresolved.

#### T3.5 Add Market-Open Guard And Sleep Policy

- Files: `src/app.py`.
- Action: added `_is_market_open()` helper (Mon-Fri 9:30-16:00 ET). Scan suppressed outside regular trading hours. Monitor continues to run for existing positions regardless of market state.
- Depends on: T3.3.
- Status: **COMPLETED** — `_is_market_open()` checks weekday and time window. Off-hours/weekend scanning suppressed.
- Acceptance: premarket blocks entries; post-cutoff blocks entries; after-hours blocks new entries; weekends prevent scanning.

#### T3.6 Cross-Validate Stop Triggers Against Fresh Data

- Files: `src/exits.py`, `src/market_data.py`, `src/app.py`, tests.
- Action: ensure hard-stop decisions use fresh, valid execution data. For wide-spread or stale quotes, prefer explicit stale-data policy over treating a bad quote as a real stop trigger.
- Depends on: T3.1, T3.2.
- Status: **COMPLETED** — `check_hard_stop()` and invalidation exits now require fresh quote data (`quote_age_seconds` present and <=15s). Stale quotes (>60s) route through the emergency/quote-unreliable path instead of manufacturing a hard-stop trigger. Exit tests prove a 30s stale quote blocks hard-stop logic.
- Acceptance: stale or missing quote data cannot trigger a false hard-stop exit.

### Phase 4 — Risk State And Entry Gates

#### T4.1 Enforce `max_open_risk_pct`

- Files: `src/hard_filters.py`, `src/decision_pipeline.py`.
- Action: added `max_open_risk_pct` and `equity` params to `check_account_risk()` and `run_hard_filters()`. Blocks entries with `"open_risk_pct_exceeded"` when `total_open_risk / equity > max_open_risk_pct`.
- Depends on: T1.2.
- Status: **COMPLETED** — `check_account_risk()` now compares `total_open_risk / equity` against threshold. `run_pipeline()` passes both params from settings to hard filters.
- Acceptance: a test with open risk above threshold returns a hard block and submits no entry.

#### T4.2 Add Session Loss Ledger

- Files: `src/app.py`.
- Action: added `_session_realized_pnl` and `_session_per_symbol_pnl` accumulators to `TradingApp`. After `confirm_exit_fill()` in monitor path, realized P&L from exited shares is added to the session ledger. `_build_risk_state()` combines session accumulator with per-position `realized_pnl` fields.
- Depends on: T2.2.
- Status: **COMPLETED** — closed position P&L persists in `_session_realized_pnl` across cycles. `_build_risk_state()` sums session + open-position P&L for daily cap enforcement.
- Acceptance: closed losing trades remain in `AccountRiskState`; same-symbol re-entry can be blocked by per-symbol cap.

#### T4.3 Wire Per-Symbol Loss Cap To Entries And Exits

- Files: `src/app.py`, `src/decision_pipeline.py`, `src/hard_filters.py`, tests.
- Action: per-symbol loss check added to `_build_risk_state()`. Wired `per_symbol_loss_capped` into both monitor and scan pipeline calls based on per-symbol accumulated loss vs `max_daily_loss_pct * equity`. Hard-filter path now surfaces a machine-readable block for scan-path entries.
- Depends on: T4.2.
- Status: **COMPLETED** — monitor path passes per-symbol cap flag to `run_pipeline()` for exit checks, and scan path passes it to `run_hard_filters()` so capped symbols are blocked from re-entry.
- Acceptance: per-symbol cap blocks new entries and can trigger loss-cap exits when configured.

#### T4.4 Use `max_trade_risk_pct` Or Remove It

- Files: `config/settings.py`, `main.py`, `src/app.py`, `src/decision_pipeline.py`, `src/sizing.py`, tests.
- Action: re-added `max_trade_risk_pct` to `Phase1Settings` and runtime wiring. Capped adjusted risk in `entry_sizing()` at `equity * max_trade_risk_pct`. The parameter is now both configured and enforced.
- Depends on: T1.3.
- Status: **COMPLETED** — `adjusted = min(adjusted, equity * max_trade_risk_pct)` after size multipliers, and `max_trade_risk_pct` now flows from settings into scan/runtime calls.
- Acceptance: no unused `max_trade_risk_pct` parameter remains.

#### T4.5 Track Mark-To-Market P&L For Open Positions

- Files: `src/app.py`.
- Action: mark-to-market added in `_monitor_positions()` — when position does not exit, `pos.unrealized_pnl` is updated from fresh market price. Combined with T4.2 session ledger for complete P&L picture.
- Depends on: T3.1, T4.2.
- Status: **COMPLETED** — open positions receive `unrealized_pnl = (current_price - average_entry) * current_shares` each monitor cycle where fresh data is available.
- Acceptance: an open losing position contributes to daily/per-symbol risk state before it closes.

#### T4.6 Keep Philosophy-Aligned Sizing Penalties

- Files: `src/scanner/attention.py`, `tests/test_phase2_attention.py`.
- Action: raised `float_unknown` multiplier from 0.50 → 0.75 and `stale_quote` from 0.50 → 0.75. Missing data is a confidence signal, not a 50% penalty. Updated 2 test assertions to reflect new values.
- Depends on: T5.6.
- Status: **COMPLETED** — bounded 25% reduction for missing float/stale quote, not 50%. Full float enrichment was completed later in T5.6.
- Acceptance: a typical valid top-gainer with missing float but otherwise strong attention receives a documented bounded reduction.

### Phase 5 — Scanner, Market Snapshot, And Classifier Quality

#### T5.1 Validate MarketSnapshot Before Entry Pipeline

- Files: `src/decision_pipeline.py`.
- Action: added `MarketSnapshot.validate_for_entry()` method checking price > 0, quote_age_seconds not None, spread_pct not None. Returns `(valid, missing_fields)`.
- Depends on: T3.1.
- Status: **COMPLETED** — `MarketSnapshot.validate_for_entry()` defined. Callable from pipeline/app. Existing hard filters handle starved snapshots by producing skip decisions with block reasons.
- Acceptance: data-starved snapshots produce explicit hard/skip reasons and cannot enter.

#### T5.2 Extract Shared Enrichment Math

- Files: `src/market_data.py`, `src/market_data_sim.py`.
- Action: extract VWAP, EMA9, day high, prior HOD, and trailing dollar volume calculation into one helper.
- Depends on: T5.1.
- Status: **COMPLETED** — `derive_bar_enrichment(bars)` now lives in `src/market_data.py` and is reused by both live and sim snapshot builders. `EMA9` remains computed exactly once per builder via `_compute_ema()`.
- Acceptance: live and sim market data use the same helper.

#### T5.3 Rank Candidates By Attention Before Processing Entries

- Files: `src/app.py`.
- Action: `score_candidates()` called in `_scan_and_process()` after enrichment, before processing loop. Candidates sorted by attention score descending.
- Depends on: T1.3, T5.1.
- Status: **COMPLETED** — attention ranking added at `app.py:384-389`. Lower-attention candidates no longer consume position slots before higher-attention candidates.
- Acceptance: lower-attention candidates cannot consume position slots before higher-attention candidates in the same cycle.

#### T5.4 Derive Full Classifier Features

- Files: `src/decision_pipeline.py`, `src/move_classifier.py`, helper module if needed.
- Action: derive supported classifier features from bars/snapshot and pass to `classify_move_state()`.
- Depends on: none — this work did not actually require T5.2 shared market-data math extraction.
- Status: **COMPLETED** — `src/classifier_features.py` derives runtime bar features (avg range, lower highs, consecutive below VWAP, structure/volume/reclaim flags, stop distance, etc.). `run_pipeline()` now feeds those derived features into `classify_move_state()` and runtime-path tests prove bar-driven classification and setup reachability.
- Acceptance: runtime-path tests prove ACTIVE/HALT-RISK/EXTENDED/non-BACKSIDE safeguards and setup reachability from real bar context; pure classifier unit tests continue to cover direct state detector behavior.

#### T5.5 Test Scanner Enrichment With Mocked HTTP/Yahoo

- Files: `src/scanner/enrichment.py`, tests.
- Action: add mock tests for Finviz HTML parsing, yfinance fallback, and `_finviz_is_stale()` thresholds.
- Depends on: T5.3.
- Status: **COMPLETED** — `tests/test_phase2_scanner.py` now covers realistic mocked Finviz HTML parsing, rate-limit/non-200 failure modes, `_finviz_is_stale()` boundaries, and mocked yfinance fallback filtering of ETF-like quote types.
- Acceptance: scanner markup drift and stale scanner output are covered by tests.

#### T5.6 Populate Or Recalibrate Float Data

- Files: `src/scanner/enrichment.py`, `src/scanner/scanner.py`, `src/annotations.py`, tests.
- Action: populate `Candidate.float_shares` for the Finviz scan path from a bounded yfinance enrichment source, while keeping `news_unknown`/`catalyst_unknown` annotation-only.
- Depends on: T5.1.
- Status: **COMPLETED** — `enrich_float_shares(symbol)` now reads `yf.Ticker(symbol).info.get("floatShares")` with defensive fallback-to-None behavior. `scan_finviz_candidates()` populates `Candidate.float_shares`, so production Finviz candidates no longer receive a universal `float_unknown` penalty.
- Acceptance: production candidates no longer all receive float_unknown, or penalty is philosophy-aligned.

#### T5.7 Wire Stale Scanner Detection

- Files: `src/scanner/enrichment.py`, `src/scanner/scanner.py`.
- Action: `scan_finviz_candidates()` now calls `_finviz_is_stale()` after scraping. If stale (≥80% zeros in ≥3 rows), returns empty. Small results (<3 rows) not flagged.
- Depends on: T5.1.
- Status: **COMPLETED** — stale detection wired at `scanner.py:55-57`. `_finviz_is_stale` threshold adjusted to skip small-result sets.
- Acceptance: stale/cached Finviz pages produce a fail-loud scanner result.

#### T5.8 Recheck Execution Quote Before Entry Submission

- Files: `src/decision_pipeline.py`, `src/app.py`, `src/market_data.py`, tests.
- Action: verify quote/price/spread validity immediately before calling `submit_entry()`.
- Depends on: T5.1, T3.6.
- Status: **COMPLETED** — `run_pipeline()` now accepts `pre_submit_quote_fn` and revalidates refreshed snapshot fields plus quote staleness immediately before `submit_entry()`. `TradingApp` passes `self._market_data_fn` through scan path.
- Acceptance: a candidate processed late in a serial scan cannot submit an order using an outdated snapshot.

#### T5.9 Fix VWAP-Missing Permission Behavior

- Files: `src/move_classifier.py`.
- Action: `_is_backside()` no longer counts `spread > 1% AND not _can_reclaim(price, vwap)` as a BACKSIDE signal when `vwap is None`. Missing VWAP cannot masquerade as bearish structure.
- Depends on: T5.2, T5.4.
- Status: **COMPLETED** — `move_classifier.py:234` now requires `vwap is not None` for the spread/reclaim BACKSIDE signal.
- Acceptance: spread>1% with VWAP missing does not categorically block `first_pullback`.

### Phase 6 — Reconciliation, Persistence, And Broker Gateway

#### T6.1 Handle All Reconciliation Actions In App Layer

- Files: `src/app.py`, `src/paper_execution.py`, tests.
- Action: process every action returned by `reconcile_positions()`.
- Depends on: T2.4.
- Status: **COMPLETED** — `_reconcile_on_startup()` handles all 7 action types: `insert_protect`, `verify_stop`, `update_qty_reprotect`, `update_qty_reprotect_warning`, `close_local`, `cancel_stale_order`, `irreconcilable`. Tests in `test_phase10_app.py::TestPhase6ReconciliationActions`.
- Acceptance: app tests cover insert_protect, verify_stop, update_qty_reprotect, update_qty_reprotect_warning, close_local, cancel_stale_order, irreconcilable, and broker unreachable.

#### T6.2 Implement Broker-Unreachable Startup Policy

- Files: `src/app.py`, `src/paper_execution.py`.
- Action: if broker snapshot cannot be obtained, mark locally open positions as requiring protection verification or `UNPROTECTED` according to policy.
- Depends on: T6.1.
- Status: **COMPLETED** — `broker_snapshot_fn` raising or returning `None` marks all OPEN positions UNPROTECTED. `broker_snapshot_fn` is `None` → skip reconciliation (no broker configured). Tests in `test_phase10_app.py::TestPhase6BrokerUnreachable`.
- Acceptance: broker-unreachable startup cannot silently proceed as if everything is safe.

#### T6.3 Wire Position Persistence Into Shutdown And Startup

- Files: `src/app.py`, `src/state_machine.py`, config if path is configurable.
- Action: use existing `save_to_disk()` and `load_from_disk()` or remove them if persistence is deferred.
- Depends on: T6.1.
- Status: **COMPLETED** — `TradingApp` accepts `persist_path`; `run()` loads saved positions before reconciliation; `_shutdown()` saves positions. Tests in `test_phase10_app.py::TestPhase6PositionPersistence`.
- Acceptance: crash/restart test proves position state can be saved, loaded, and reconciled.

#### T6.4 Test `AlpacaExecutionGateway` With Mocked Alpaca Client

- Files: `src/paper_execution.py`, tests.
- Action: mock `TradingClient`, `submit_order`, `get_order_by_id`, and failure modes.
- Depends on: T2.3.
- Status: **COMPLETED** — 19 tests in `test_phase7_execution.py` covering submit_entry, confirm_fill, place_stop, submit_exit, confirm_exit_fill with mocked Alpaca client. All statuses tested: filled, partially_filled, rejected, canceled, pending, and API failures.
- Acceptance: every Alpaca gateway method is tested without real keys or network.

#### T6.5 Remove Silent Synthetic Fills From Broker Mode

- Files: `src/paper_execution.py`.
- Action: in Alpaca paper gateway, API failure must not silently become success unless explicitly running in local simulation mode.
- Depends on: T6.4.
- Status: **COMPLETED** — `AlpacaExecutionGateway` raises `RuntimeError` on API failure in submit_entry, place_stop, submit_exit, confirm_fill, confirm_exit_fill. No synthetic order_ids, no simulated fills. `submit_entry` cleans up local position on failure.
- Acceptance: broker API failures produce explicit failed/pending/error states, not fake fills.

#### T6.6 Wire Broker Snapshot In Paper-Broker Loop Mode

- Files: `main.py`, `src/paper_execution.py`, `src/app.py`, tests.
- Action: provide a real broker snapshot function when using `AlpacaExecutionGateway`. The default `lambda: {}` is acceptable only for pure local simulation, not broker-backed paper mode.
- Depends on: T1.4, T6.1.
- Status: **COMPLETED** — `build_alpaca_broker_snapshot(gateway)` added to `paper_execution.py`. Returns `{symbol: (qty, avg_entry)}` on success, `None` on failure. Wired into `TradingApp` via injectable `broker_snapshot_fn`. Tests in `test_phase7_execution.py::TestBuildAlpacaBrokerSnapshot`.
- Acceptance: startup reconciliation in Alpaca paper mode compares against broker truth; an unreachable broker or empty-but-unverified broker response fails loud.

#### T6.7 Handle Broker Order Statuses Explicitly

- Files: `src/paper_execution.py`, tests.
- Action: distinguish `filled`, `partially_filled`, `new`/`accepted`/`pending`, `rejected`, `canceled`, timeout, and API failure for entry, stop, and exit orders. State transitions must follow filled quantity, not intent.
- Depends on: T6.4, T6.5.
- Status: **COMPLETED** — `AlpacaExecutionGateway._map_alpaca_status()` normalises statuses. `confirm_fill` and `confirm_exit_fill` handle filled, partially_filled, pending, rejected, canceled, expired. Partial fills open only filled qty. Rejected/canceled orders set position ERROR. Pending orders keep position in current state for retry.
- Acceptance: rejected entry does not create an open position; partial fill opens only filled quantity and protects only filled quantity; rejected/canceled/failed exits leave state unresolved and escalated.

### Phase 7 — Cleanup And Internal Refactors

#### T7.1 Unify CLI Mode Builders

- Files: `main.py`.
- Action: reduce duplicated setup across mock, paper, sim, and loop modes.
- Depends on: T1.2.
- Status: **COMPLETED** — `_build_components(settings)` helper extracts gateway/logger/runner-store/risk-config construction. `_run_scan_pipeline()` unifies the paper/sim scan→enrich→pipeline loop. All 4 mode functions refactored. CLI tests (10/10) pass.
- Acceptance: one helper builds gateway/logger/runner-store components and tests still cover all modes.

#### T7.2 Move Soft Warning Logic Out Of `scanner/attention.py`

- Files: `src/scanner/attention.py`, `src/annotations.py` (new), imports/tests.
- Action: keep attention scoring separate from soft annotations.
- Depends on: T5.3.
- Status: **COMPLETED** — `map_soft_warnings()` and `soft_warning_multiplier()` moved to `src/annotations.py`. All imports updated in `decision_pipeline.py`, `test_phase9_pipeline.py`, `test_phase2_attention.py`. Attention module docstring clarified.
- Acceptance: `scanner/attention.py` scores attention only; soft warning tests still pass.

#### T7.3 Remove Or Document Dead Candidate Lifecycle Helpers

- Files: `src/state_machine.py`, tests.
- Action: remove dead lifecycle helpers or wire them to actual runtime usage.
- Depends on: T6.3.
- Status: **COMPLETED** — documented as reserved spec artifacts for future lifecycle-aware pipeline wiring. Existing tests preserved (7/7 pass). No dead code removed — justified by documented intent.
- Acceptance: no dead helper remains without a documented reason.

#### T7.4 Clarify Placeholder Entry Size

- Files: `src/entries.py`.
- Action: document that detector-level `proposed_shares=1` is a placeholder overwritten by pipeline sizing, or refactor detectors to omit size until sizing.
- Depends on: T4.4.
- Status: **COMPLETED** — comment updated: `proposed_shares=1` → `# detector-level placeholder — pipeline sizing overwrites`. Same for `risk_amount`.
- Acceptance: no reader can mistake detector placeholder shares for final risk sizing.

### Phase 8 — Test Truthfulness And Fail-Loud Coverage

#### T8.1 Replace Log-Only App Assertions With State Assertions

- Files: `tests/test_phase10_app.py`, `tests/test_phase7_execution.py`, app/execution tests.
- Action: tests that trigger monitor exits must assert position state, pending orders, remaining shares, broker calls, and decision logs. A decision log alone is not proof that execution succeeded.
- Depends on: T2.1, T2.2, T2.6.
- Status: **COMPLETED** — All 10 Batch 4 (`TestBatch4MonitorExits`) tests now assert position state (CLOSED/OPEN/UNPROTECTED), shares, and pending orders after `_monitor_positions()`. The pre-fix tuple crash would now fail these tests.
- Acceptance: the pre-fix tuple crash would fail the test because the position remains unresolved.

#### T8.2 Add Runtime-Path Classifier Tests

- Files: `tests/test_phase4_classifier.py`, `tests/test_phase9_pipeline.py`, classifier helper tests.
- Action: keep pure classifier unit tests, but add pipeline tests that derive features from real bar fixtures. Do not rely only on direct injection of classifier parameters that runtime never passes.
- Depends on: T5.4, T5.9.
- Status: **COMPLETED** — runtime-path coverage now lives in `TestRuntimeClassifierWiring` (`tests/test_phase9_pipeline.py`) plus `TestDeriveClassifierFeatures` (`tests/test_phase4_classifier.py`). Coverage proves bar-derived feature flow into `classify_move_state()`, VWAP-missing safeguard behavior, and micro-pullback setup reachability through the real pipeline.
- Acceptance: runtime-path tests prove bar/snapshot-derived features reach the classifier and gate real setup selection; direct unit tests remain for raw detector behavior.

#### T8.3 Add Broker Failure Matrix Tests

- Files: broker/execution tests for `src/paper_execution.py`.
- Action: mock Alpaca success, rejected, canceled, partial fill, timeout, maintenance/API failure, missing credentials, stale stop cancellation, and broker snapshot mismatch.
- Depends on: T6.4, T6.5, T6.6, T6.7.
- Status: **COMPLETED** — 15 broker failure tests added across 5 classes: `TestBrokerFailureTimeout` (submit_entry/confirm_fill/submit_exit timeouts), `TestBrokerMaintenanceError` (503 on submit/stop), `TestBrokerMissingCredentials` (no-keys behavior), `TestBrokerStaleStopCancellation` (exit cancels stops), `TestBrokerSnapshotMismatch` (irreconcilable qty, insert missing). Combined with T6.4 (19 tests), total of 34 broker gateway tests.
- Acceptance: no broker failure test can pass through a synthetic success path.

#### T8.4 Add Scanner/Data Staleness Tests

- Files: scanner/enrichment tests, market-data tests, app scan tests.
- Action: test stale Finviz pages, Alpaca outage, yfinance/dead fallback behavior, missing float behavior, stale pre-order quote, and sim-mode freshness labeling.
- Depends on: T5.5, T5.6, T5.7, T5.8.
- Status: **COMPLETED** — coverage now includes sim-mode labelling + float/quote warning behavior (`TestPhase8DataStaleness`), Finviz HTML/rate-limit/staleness boundaries, mocked yfinance fallback filtering, missing snapshot field surfacing, and stale pre-submit quote blocking.
- Acceptance: stale or missing data fails loud with machine-readable reasons and cannot silently become a live-style trade.

#### T8.5 Add Risk Ledger Tests

- Files: app/risk/execution tests.
- Action: test realized losses after close, unrealized losses while open, per-symbol loss caps, daily loss caps, open-risk cap, and max-trade-risk cap through runtime calls.
- Depends on: T4.1, T4.2, T4.3, T4.4, T4.5.
- Status: **COMPLETED** — 10 risk ledger tests in `test_phase6_risk.py`: `TestRiskLedgerRealizedLosses` (2 tests), `TestRiskLedgerUnrealizedPnL` (2), `TestRiskLedgerDailyLossCap` (2), `TestRiskLedgerPerSymbolCaps` (2), `TestRiskLedgerOpenRiskCap` (2). Covers realized P&L accumulation, unrealized mark-to-market, daily cap breaching, per-symbol loss tracking, and open-risk calculation.
- Acceptance: repeated closed losses cannot bypass the daily cap; same-symbol losses can block/restrict re-entry; open risk above cap blocks new entries.

## 8. Test Plan

### 8.1 Required P0 Tests

- Exit tuple-unpacking monitor path: exit order confirms without `AttributeError`.
- Partial exit lifecycle: 25, 33, 50, and 100 percent exits update shares/state correctly.
- `EXITING` recovery: confirm failure, timeout, or broker-unreachable state cannot leave a position permanently skipped by exit checks.
- Protection verification: stop metadata without pending/broker stop is unprotected.
- Stale protective orders: full exit cancels stops; partial exit resizes/replaces stops for remaining quantity.
- Missing-protection emergency: `UNPROTECTED` position triggers the correct exit path.
- Data outage monitor path: no fabricated zero price or fake P&L; verified-protected outage holds/retries instead of flattening.
- Broker-unreachable runtime policy: unreachable broker marks positions requiring verification or `UNPROTECTED` according to policy and fails loud.
- Time gates: watch-only, cutoff, and flatten integration through app/pipeline.
- Market-hours guard: no new entries outside configured regular-hours policy; weekend/after-hours stale scanner data cannot create live-style entries.
- `max_open_risk_pct`: entry blocked above threshold.
- `max_trade_risk_pct`: adjusted per-trade risk is capped or setting is removed.
- Session loss ledger: closed losses persist in daily/per-symbol risk state; open unrealized losses affect risk state from fresh marks.
- Reconciliation: app handles every action returned by `reconcile_positions()`.
- Mocked `AlpacaExecutionGateway`: submit, fill confirm, stop, exit, rejected/canceled/partial/pending statuses, API failures, and stale-stop cancellation.
- Snapshot propagation and shared enrichment math are covered through app/pipeline/CLI regression tests.
- Runtime config wiring: changing representative `PHASE1_*` values changes behavior, not only loaded settings objects.

### 8.2 Required P1 Tests

- Finviz scraper with realistic mocked HTML.
- yfinance fallback with mocked `Ticker.fast_info` and `Ticker.info`, or proof that the fallback path was intentionally removed.
- `_finviz_is_stale()` boundary behavior.
- Stale Finviz page does not produce live-style trade decisions.
- Float enrichment or recalibrated `float_unknown` penalty behavior.
- VWAP-missing + spread>1% does not falsely BACKSIDE-lock valid `first_pullback` opportunities.
- Pre-order quote recheck blocks stale candidate snapshots.
- Fill failure leaves a recoverable state and does not silently enter.
- Protection failure marks `UNPROTECTED` and monitor escalates.
- Crash recovery: save, shutdown, load, reconcile.
- Broker-unreachable startup policy.
- Candidate attention ranking before processing.
- Full classifier feature derivation from bars.
- Runtime-path classifier tests prove states from bar/snapshot features, not only direct parameter injection.
- App monitor tests assert position state, pending orders, remaining shares, and broker calls, not only decision logs.

### 8.3 Required P2 Tests

- OCO/bracket lifecycle if v0.4.0 chooses to implement it.
- `market_data_sim` date math and enrichment.
- Per-symbol loss cap as exit trigger.
- Soft-warning module extraction regression tests.
- CLI builder refactor coverage.
- Former-runner marking behavior if retained.
- Position persistence behavior if retained.

### 8.4 Verification Commands

Default verification after each implementation batch:

```bash
pytest
```

If linting is configured and stable:

```bash
ruff check .
```

Never claim a batch is complete without running the relevant tests or stating exactly why verification could not be run.

## 9. Acceptance Criteria For v0.4.0

v0.4.0 is complete only when all of the following are true:

- Root `SPEC.md` and implementation agree.
- No deleted modules are reintroduced.
- All P0 tests pass.
- Existing test suite passes.
- Tests assert runtime state, broker/order state, and ledger effects for safety-critical paths; log-only assertions do not count as coverage.
- Runtime settings are wired or removed; no decorative config remains.
- Mistyped or mis-prefixed critical config fails loud or is explicitly documented as ignored.
- No monitor path can fabricate a zero-price crisis.
- Exit execution works for full and partial exits.
- `EXITING` positions cannot become permanent zombies.
- Protection verification is based on live/pending order truth.
- Full and partial exits cancel or resize protective orders so stale stops cannot create unintended shorts.
- Time gates are enforced in runtime.
- Market-hours behavior prevents new entries from stale/off-hours scanner output.
- Open risk, max trade risk, daily loss, and per-symbol loss caps are enforced from real ledger inputs.
- Startup reconciliation handles every action or escalates safely.
- Runtime broker reconciliation uses broker truth in Alpaca paper mode and fails loud when unavailable.
- Market snapshot failures block entries explicitly; protected-position data outage follows §10.3 hold/retry policy.
- `AlpacaExecutionGateway` is covered by mocked tests for success, rejected, canceled, partial, pending, timeout, missing credentials, maintenance/API failure, and broker mismatch. Snapshot propagation and shared enrichment math are covered by regression tests; direct mocked Alpaca-response unit tests for `build_market_snapshot*()` remain optional hardening work, not a claimed completed proof point.
- Scanner stale-output detection, float enrichment/penalty behavior, VWAP-missing behavior, and pre-order quote recheck are covered by tests.
- CLI, metadata, docs, and config agree on the release version.

## 10. Gaps, Risks, And Open Questions

### 10.1 Remaining Gaps And Deferred Work

- Spec ambiguity has been resolved: `archive/` and `docs/plans/` contain historical/reference documents; root `SPEC.md` is the sole active spec.
- Current code labels the project `0.4.0`; this spec now targets `v0.4.0` (version question resolved — see §10.3).
- Market data functions are untested against mocked Alpaca responses.
- Automatic fallback from stale/empty Finviz is now wired into the primary scanner path. Remaining hardening work is response-level market-data unit coverage, not scanner fallback wiring.
- Direct mocked Alpaca-response unit tests for `build_market_snapshot()` / `build_market_snapshot_sim()` are still absent; current coverage is snapshot propagation plus shared-math regression, not response-level parser tests.
- OCO/bracket enums exist but no lifecycle exists.
- Runner state exists but no runtime transition creates runners.
- Runner trailing logic accepts `highest_price_seen` but does not use it; runner exits are incomplete.
- `run_pipeline()` mixes entry evaluation, execution, and exit checks; this can remain temporarily but should be simplified after safety fixes.
- Scaling-in (adding to winning positions) is specified in the mental model but has no implementation plan, no module, no state transitions, and no sizing logic in v0.4.0. This gap should be addressed after exit/protection safety is verified.

### 10.2 Risks During Implementation

- Fixing data-outage behavior may require updating existing canary tests that expect `quote_age_seconds=999` behavior.
- Partial exit support touches state, pending orders, exit logs, and protection resizing; implement in one focused batch.
- Centralizing settings may break CLI tests if modes rely on hardcoded defaults.
- Removing silent Alpaca synthetic fills may expose previously hidden broker/API failures.
- Attention-first ranking changes cycle ordering and may require updated integration expectations.
- Fixing weak tests may reveal existing bugs that were previously hidden by log-only or direct-unit assertions.
- Reducing universal data-gap penalties may increase trade size; verify caps and loss ledger first.

### 10.3 Resolved Questions

- **Release version**: `v0.4.0`. Metadata, CLI banner, docs, and config comments must agree on `0.4.0`.
- **OCO/bracket orders**: Explicitly deferred past v0.4.0. Single-leg entry + separate stop order is sufficient for paper verification. OCO adds complexity (parent-child order lifecycle, atomic cancel/replace) that is not needed until live execution quality demands it.
- **Data outage on protected open position**: Hold and retry. A protected position with a verified live stop does not need to be flattened on transient data outage. After a configured timeout (e.g. 120s of persistent data unavailability), mark the position `UNPROTECTED` and escalate. Never fabricate `price=0.0` or fake P&L.
- **`APCA_*` credential names**: Remove. Standardize on `ALPACA_*` only.
