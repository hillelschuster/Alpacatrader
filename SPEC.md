# Alpacatrader v0.4.0 Implementation Specification

Date: 2026-06-23

Status: Active implementation spec and truth audit, reconciled with current code/tests through Task 13 plus scanner fallback wiring on 2026-06-23

## 0. Source Of Truth

There is exactly one active implementation spec: this root `SPEC.md`.

`SOUL.md` is the bot's identity and mental-model document (top-gainer momentum, candidates-first, catch-runners, paper = live rehearsal). It is not an implementation spec and does not compete with or override this document. Read `SOUL.md` for trading philosophy; read this file for implementation authority.

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
- No pillars, regime score, anti-pattern hard blocks, or company-quality filter.
- No AI/LLM approval gate in any execution path. LLM output is annotation-only — never a hard filter, never a sizing input, never an entry/exit gate. LLM is disabled by default, mockable in tests, and never in the critical real-time path. See §11.8 for the v0.5 LLM annotator design.
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

**Follow-up resolution status — 2026-07-05:**

- [x] C3M resolved: session P&L ledger persists closed realized P&L, monitor cycles mark open unrealized P&L, paper exits now set realized P&L from confirmed simulated `exit_price`, and app session P&L uses gateway-confirmed exit-fill results instead of monitor snapshot prices.
- [x] C3X strengthened for the touched paths: new tests assert direct snapshot conversion behavior, confirmed-fill session P&L, no fake `0.0` P&L on no-price exits, trade-ledger records, broker fill prices, partial exits, ADD fills, and broker/order state effects.
- [x] C3V resolved: `PositionStore` persistence is wired, and confirmed runner trail exits now call `FormerRunnerStore.mark()` so the former-runner attention bonus can activate on later scan cycles.

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
- `AlpacaExecutionGateway` is covered by mocked tests for success, rejected, canceled, partial, pending, timeout, missing credentials, maintenance/API failure, and broker mismatch. Snapshot propagation, shared enrichment math, and direct mocked Alpaca-response behavior for `build_market_snapshot()` / `build_market_snapshot_sim()` are covered by regression tests.
- Scanner stale-output detection, float enrichment/penalty behavior, VWAP-missing behavior, and pre-order quote recheck are covered by tests.
- CLI, metadata, docs, and config agree on the release version.

## 10. Gaps, Risks, And Open Questions

### 10.1 Remaining Gaps And Deferred Work

- Spec ambiguity has been resolved: `archive/` and `docs/plans/` contain historical/reference documents; root `SPEC.md` is the sole active spec.
- Current code labels the project `0.4.0`; v0.5 feature work has been implemented on top of the `0.4.0` metadata, and no release-label change is authorized by this spec.
- [x] Direct mocked Alpaca-response unit tests for legacy per-symbol `build_market_snapshot()` / `build_market_snapshot_sim()` — completed in `tests/test_phase5_batch_snapshots.py`.
- [x] Executed trade ledger / confirmed-fill JSONL log — completed; runtime wires `TradeLedger("data/executed_trades.jsonl")` and logs entry/add/exit fills only after confirmation. See §11.17.15.
- [x] Session P&L uses confirmed exit-fill results instead of monitor snapshot price — completed; `_monitor_positions()` records P&L only after filled share count decreases, using gateway-confirmed `realized_pnl`.
- [x] Paper exit fills set realized P&L from simulated confirmed `exit_price`, and no-price/no-realized exits skip fake `0.0` P&L records.
- [x] Paper/live runtime equity now comes from Alpaca `get_account().equity` at startup; scan/loop sizing and risk caps use broker account equity instead of the old hardcoded `$100,000` runtime default. The `$100,000` default remains only for mock/test-style paths.
- [x] `FormerRunnerStore.mark()` is wired into confirmed runner trail exits; former-runner attention bonus is no longer perpetually zero.
- [x] Static yfinance watchlist is downgraded to watch-only fallback: stale/empty Finviz no longer produces automatic trade-discovery candidates from `_VOLATILE_WATCHLIST`. Dynamic top-gainer discovery remains Alpaca movers first, then Finviz.
- News/catalyst awareness remains annotation-only and not part of runtime execution: `has_news`/`has_catalyst` are not populated by deterministic scanner data, while the v0.5 LLM annotator is disabled by default and never read by entry, exit, sizing, or risk paths.
- OCO/bracket order lifecycle remains deferred; single-leg entry plus separate verified protective stop is still the implemented paper-mode model.
- Runner capture, ATR trailing, and scaling-in are implemented in v0.5, and the 2026-06-25 hardening audit items were closed in follow-up implementation: RUNNER-specific reconciliation, dedicated `trail_exit` decision logging, protected RUNNER outage timeout handling, sim-mode runner lifecycle coverage, and missed ADD-fill reconciliation.
- `run_pipeline()` has been split into `evaluate_candidate()` / `execute_entry()` / `evaluate_exits()` for runtime use, but the old `run_pipeline()` symbol remains as a thin compatibility wrapper.

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

## 11. v0.5.0 Implementation Plan — Runner Capture, Scaling-In, And Live Readiness

Date: 2026-06-23 (updated after 3-lane adversarial analysis; implementation-status audit added 2026-06-25)

Status: Implementation audited against current code/tests on 2026-06-25 and hardening gaps closed in a follow-up implementation pass the same day. v0.5 Phases 1-7 are implemented in the current repository state. 2026-07-05 follow-up hardening added confirmed-fill trade logging, direct per-symbol snapshot tests, and confirmed-fill session P&L accounting. Built on a 6-lane deep audit + 3-lane adversarial analysis (strategic critique, Context7 verification, code-level gap audit). Third-party/API claims in the implementation-status update were rechecked against Context7 for `alpaca-py` and the Anthropic Python SDK.

### 11.0 Research Basis

The v0.5 plan is grounded in six parallel research lanes + three adversarial analysis lanes completed on 2026-06-23:

**Research lanes:**
1. **LLM Integration**: definitive YES for pre-market catalyst annotation (Anthropic Claude Haiku 4.5, ~$0.03/day for 30 candidates). Hard NO for execution-path, real-time, or approval-gate use.
2. **Alpaca API**: native `ScreenerClient.get_market_movers()`, `TradingClient.get_calendar()`, `StockSnapshotRequest` batch, `OrderClass.BRACKET`/`OCO`. All verified via Context7.
3. **Scanner Sources**: TradingView Screener API + Yahoo Finance Screener. All free, no API keys.
4. **Runner Capture**: ATR Chandelier trailing (2.5× ATR(5) on 5-min bars). Runner transition at +1.5R + structure + volume + VWAP + ACTIVE.
5. **Scaling-In**: Anti-martingale pyramiding (50%/25% of starter risk). Add at +2R after RUNNER.
6. **Code Audit**: RUNNER/ADDING states defined but never transitioned. `highest_price_seen` never written. P11 is a stub. Zero scaling-in code.

**Adversarial analysis lanes:**
7. **Strategic critique (oracle)**: Phase 2 (scanner) over-engineered for v0.5 — split to batch-snapshots-only, defer full scanner replacement to v0.6. Phase 7 (LLM) over-engineered for "optional" — simplify to one function. Reorder phases so runner capture ships before scanner upgrade. Add trailing-stop minimum distance, error handling matrix, decision log events, sim mode ATR, position persistence test, reconciliation for new states, data-unavailable policy for runners, config wiring gate.
8. **Context7 verification (librarian)**: 24 claims VERIFIED, 1 NO, 2 PARTIAL. CRITICAL FIX: `Scanner.day_gainers` does NOT exist in `tradingview-screener` — must use `Query().where(col('change') > 3).order_by('change', ascending=False)`. `gap` and `Perf.W` field names unconfirmed. All Alpaca/yfinance/Anthropic/ATR claims verified.
9. **Code-level gap audit (explorer)**: 37 gaps found. 13 NOT addressed by original plan (see §11.13-11.15 for resolutions). Key: `check_exits()` doesn't pass `highest_price_seen`/`atr` to P11, `confirm_fill()` doesn't handle ADDING state, `place_stop()` requires OPEN state (not RUNNER), existing P11 tests will break, `_EXITING_TIMEOUT_S` hardcoded, `SCALING_OUT` state is dead.

### 11.1 v0.5 Scope

**IN:**
- Pipeline refactor (split `run_pipeline` into `evaluate_candidate` / `execute_entry` / `evaluate_exits`)
- Sizing recalibration (floor 0.25→0.40, remove lunch multiplier, configurable `dollar_volume_below_min`)
- Runner state transitions + ATR Chandelier trailing stops
- Scaling-in (ADDING state, anti-martingale sizing 50%/25%, protection resizing)
- Scanner batch snapshots only (keep Finviz for v0.5, full scanner replacement deferred to v0.6)
- Live readiness (market calendar, `paper_mode` gating, irreconcilable halt)
- LLM pre-market annotator (optional, simplified to one function, disabled by default)

**OUT (deferred to v0.6+):**
- Full scanner replacement (Alpaca movers + TradingView + Yahoo dynamic fallback) — keep Finviz for v0.5
- SIP data feed ($99/mo) — test with IEX first
- WebSocket streaming — requires async architecture
- OCO/bracket orders — optional, not required (stale-stop hazard already mitigated by T2.3)
- VPS deployment automation — manual setup for paper
- Backtesting framework — different abstractions, would bloat live path
- Plugin system, web dashboard, multi-broker abstraction — never

**Phase ordering rationale:** Runner capture (Phase 3) does NOT depend on scanner replacement. The current Finviz scanner produces candidates that generate entries. Runner capture operates on open positions, not candidates. Shipping the soul before the scanner upgrade means if the scanner upgrade breaks, runner capture still ships. Scanner batch snapshots (Phase 5) are a self-contained optimization that can be done after the soul works.

**The one big thing:** Runner capture + scaling-in. v0.4.0 enters trades but treats every winner as a single-entry scalp. v0.5 must demonstrate: enter small → prove strength → promote to runner → add → trail → exit with meaningful profit.

### 11.2 Phase 1 — Pipeline Refactor

**Goal:** Split `run_pipeline()` into three focused functions before adding runner/scaling-in logic.

**Implementation status — 2026-06-25:** Implemented. `src/decision_pipeline.py` now defines `evaluate_candidate()`, `execute_entry()`, and `evaluate_exits()`, and the app monitor path calls `evaluate_exits()` directly while the scan path calls `evaluate_candidate()` then `execute_entry()`. `evaluate_exits()` carries the runner-ready `highest_price_seen` and `atr` inputs required by Phase 3. The old `check_exits_for_open` flag is gone. `run_pipeline()` still exists only as a backward-compatible wrapper around the split functions, so any older wording saying the symbol must disappear is stale and has been corrected below.

**Files:** `src/decision_pipeline.py`, `src/app.py`, tests.

**Action:**
- `evaluate_candidate()` — steps 1-6 only (data confidence, attention, soft warnings, hard filters, move classification, entry detection). Pure analysis, no side effects, testable. Returns `PipelineResult` (accumulator for steps 1-6).
- `execute_entry()` — sizing + order submission + fill confirm + protect position. Takes `PipelineResult` from `evaluate_candidate()`. Owns `pre_submit_quote_fn` (moves here from `run_pipeline`).
- `evaluate_exits()` — exit engine orchestration (P1-P11). Returns `Optional[ExitDecision]` directly, NOT `PipelineResult` (monitor path doesn't need entry fields). Signature includes `highest_price_seen: Optional[float]` and `atr: Optional[float]` for P11 (threaded through to `check_runner_trail`).
- Monitor path calls `evaluate_exits()` only. Scan path calls `evaluate_candidate()` → `execute_entry()`.
- Remove `check_exits_for_open` flag — it becomes dead after the split (monitor path calls `evaluate_exits()` directly).

**Depends on:** none. Can run in parallel with Phase 2 (sizing) — zero file overlap.
**Verification:** `pytest` — all existing tests must pass; current exact count must be verified by a fresh full-suite run.
**Acceptance:** `run_pipeline()` no longer owns the monolithic decision flow; it may remain only as a thin backward-compatible wrapper around `evaluate_candidate()`, `execute_entry()`, and `evaluate_exits()`. All existing tests pass unchanged. `evaluate_exits()` signature includes `highest_price_seen` and `atr` params for future P11 use.

### 11.3 Phase 2 — Sizing Recalibration

**Goal:** Fix death-by-multipliers. Make sizing meaningful for attention ≥70.

**Implementation status — 2026-06-25:** Implemented. The soft-warning and sizing multiplier floors are both 0.40, and tests cover the floor. The `lunch_window` sizing multiplier has been removed from annotation/sizing flow; `is_lunch_window()` remains in `hard_filters.py` but is not part of sizing. `dollar_volume_min` is configurable through `Phase1Settings` and wired into hard filters at the v0.5 default of `$50,000`. The sizing recalibration keeps `data_confidence` as a real data-quality multiplier while the 0.40 floor prevents stacked soft warnings from crushing a valid top-gainer to trivial risk.

**Files:** `src/sizing.py`, `src/annotations.py`, `src/hard_filters.py`, `config/settings.py`, `config/default_config.yaml`, tests.

**Action:**
- Raise soft multiplier floor from 0.25 → 0.40 (`annotations.py:233`).
- Raise `floor_soft` in `sizing.py:57` from 0.25 → 0.40 for consistency.
- Remove `lunch_window` multiplier entirely (`annotations.py:215`). Remove `is_lunch` parameter from `map_soft_warnings()`. Keep `is_lunch_window()` in `hard_filters.py` (used for watch-only gate, not sizing).
- Make `dollar_volume_below_min` configurable via `Phase1Settings` (default $50,000, down from hardcoded $100,000 at `hard_filters.py:126,250`).
- Keep `max_positions=3` (already configurable).
- Keep `stop_too_tight_for_spread_and_slippage` (tight stops on volatile stocks are genuinely dangerous).
- Address `data_confidence` multiplier: currently ~0.70 from `calculate_data_confidence()`. This cuts 30% after all other multipliers. Document that `data_confidence` is a real data-quality signal (stale quotes, missing fields) and should remain, but verify it doesn't combine with other multipliers to crush sizing below the 0.40 floor.
- Target: attention ≥70 with typical multipliers should produce ≥$100 effective risk (0.10% of $100K equity).

**Depends on:** none (can run in parallel with Phase 1 — zero file overlap).
**Verification:** sizing tests updated. New assertions on effective risk at attention 70, 85, and worst-case multiplier stacks. Floor 0.40 verification. Lunch multiplier removed verification.
**Acceptance:** a typical top gainer with attention ≥70 and missing float data receives ≥$100 risk, not $25-65. `data_confidence` impact documented.

### 11.4 Phase 3 — Runner Capture & Trailing Stops

**Goal:** Implement the soul. OPEN → RUNNER transition, ATR Chandelier trailing, P5 pause for runners, P11 rewrite.

**Implementation status — 2026-06-25:** Implemented. `src/runner.py` implements `should_promote_to_runner()`, `compute_atr()`, and `compute_runner_stop()`, and `TradingApp` promotes OPEN positions to RUNNER, ratchets `highest_price_seen`, and updates an ATR Chandelier stop during monitoring. `PositionStateModel` includes `runner_since`, `trailing_stop_price`, `highest_price_seen`, and `add_count`. P11 uses ATR Chandelier logic through `check_runner_trail()`, the old `trail_hit` parameter is removed, P5 scale-outs are paused for RUNNER positions, startup reconciliation hardens RUNNER metadata/protection, protected RUNNER outage timeout handling is covered, sim-mode runner lifecycle coverage exists, and runner trail exits are logged as dedicated `trail_exit` decisions while preserving detailed exit reasons.

**Files:** `src/runner.py` (new), `src/exits.py`, `src/app.py`, `src/models/schemas.py`, `src/state_machine.py`, `src/classifier_features.py`, `src/market_data_sim.py`, `config/settings.py`, `config/default_config.yaml`, tests.

**Action:**

**Runner transition criteria** (all must be true):
- Position state is OPEN.
- Unrealized profit ≥ +1.5R. **R is always computed from ORIGINAL entry risk** (`risk_per_share at entry`), not adjusted for partial exits. If P5 fired a 33% scale-out before promotion, R is still the original risk_per_share × original shares.
- ≥2 distinct higher lows in recent bar window (structure intact).
- Latest push bar volume ≥ 1.5× average of prior 10 bars.
- Price ≥ VWAP (for longs).
- Move state is ACTIVE (not EXTENDED, BACKSIDE, or HALT_RISK).

**New module `src/runner.py`:**
- `should_promote_to_runner(pos, bars, current_price, vwap, move_state) -> bool`
- `compute_atr(bars, period=5) -> float` — Wilder's smoothing (standard). TR = max(H-L, abs(H-prev_close), abs(L-prev_close)).
- `compute_runner_stop(highest_price_seen, atr, multiplier=2.5, current_stop, original_risk) -> float` — ATR Chandelier with ratchet (stop never moves down). **Minimum distance: `max(2.5×ATR, 1.0×original_risk)`** — prevents too-tight trails on small-caps where ATR is tiny and the trail exits before adds can fire.

**Schema additions** (`PositionStateModel`):
- `runner_since: Optional[datetime] = None`
- `trailing_stop_price: Optional[float] = None`

**App loop** (`_monitor_positions()`):
- After `evaluate_exits()` returns None (no exit triggered), check `should_promote_to_runner()`.
- On promotion: `transition_position(pos, RUNNER)`, set `runner_since`, initialize `highest_price_seen = current_price`, initialize `trailing_stop_price`.
- Every cycle for RUNNER positions: ratchet `highest_price_seen = max(highest_price_seen, current_price)`.
- Every cycle for RUNNER positions: compute ATR from bars, update `trailing_stop_price = compute_runner_stop(...)`.
- Consider extracting runner management into `_manage_runners(pos, current_price, bars, vwap)` called from `_monitor_positions()` to avoid bloating the monitor method.

**P11 rewrite** (`exits.py:411-436`):
- Replace `trail_hit` flag + 2-red-bar heuristic with ATR Chandelier computation.
- Use `highest_price_seen` (currently accepted but unused).
- Exit fires when `current_price <= trailing_stop_price`.
- **Thread `highest_price_seen` and `atr` through `check_exits()` → `check_runner_trail()`**. Currently `check_exits()` at lines 525-528 only passes `current_price, risk_per_share, bars, trail_hit`. Must add `highest_price_seen: Optional[float]` and `atr: Optional[float]` params.
- Remove `trail_hit` parameter entirely after P11 rewrite (it's always False, dead parameter).

**P5 scale-out pause:**
- Add guard at `check_scale_out()` entry: `if position.state == PositionState.RUNNER: return None`.
- Trailing stop IS the exit manager for runners.
- If trailing stop fires → 100% exit.

**Data-unavailable policy for RUNNER positions:**
- Protected RUNNER (verified stop exists) + data outage → hold/retry (same as §6.4 for OPEN). Trailing stop freezes (no update). Log incident.
- Unprotected RUNNER + data outage → mark UNPROTECTED, escalate via P4.
- After 120s persistent outage → mark UNPROTECTED (same timeout as EXITING recovery).

**Decision log events:**
- `promoted_to_runner`: log symbol, R-multiple, move_state, bar structure evidence, trailing_stop_price.
- `trail_updated`: log symbol, highest_price_seen, atr, trailing_stop_price (debug level).
- `trail_exit`: log symbol, exit_price, trailing_stop_price, profit R-multiple, bars held as runner.

**Sim mode ATR:**
- `market_data_sim.py` must compute ATR from its bar window for runner testing. Currently doesn't compute ATR. Add ATR computation to sim snapshot builder or compute on-demand in `runner.py`.
- Sim mode hardcodes quote_age=1.0, spread=0.5 — acceptable for basic runner testing but can't test edge cases (stale quotes during runner, spread expansion). Document as limitation.

**Position persistence:**
- `PositionStore.save_to_disk()` / `load_from_disk()` must correctly serialize/deserialize new fields (`runner_since`, `trailing_stop_price`, `highest_price_seen`).
- Test: RUNNER position saved, loaded, resumes trailing correctly. `highest_price_seen` must be reloaded from persisted state.

**Reconciliation for RUNNER state:**
- `reconcile_positions()` must handle RUNNER state: verify trailing stop exists, verify `highest_price_seen` is populated, verify `runner_since` is set.
- If broker position exists but local state is RUNNER with no trailing stop → escalate to UNPROTECTED.

**Config additions:**
```yaml
runner:
  activation_r_multiple: 1.5
  atr_period: 5
  trail_multiplier: 2.5
  higher_lows_required: 2
  volume_confirm_multiplier: 1.5
```

**Depends on:** Phase 1 (pipeline refactor — `evaluate_exits()` must exist with `highest_price_seen`/`atr` params).
**Verification:**
- `test_position_promotes_to_runner()` — +1.5R + structure → RUNNER
- `test_runner_not_promoted_prematurely()` — small profit stays OPEN
- `test_atr_computation()` — ATR(5) from bars, Wilder's smoothing
- `test_runner_trail_updates_highest_price()` — ratchet
- `test_runner_trail_exits_on_pullback()` — price drops below trail → P11 fires
- `test_runner_trail_minimum_distance()` — trail never tighter than 1.0×original_risk
- `test_p5_paused_for_runner()` — scale-out skipped in RUNNER state
- `test_runner_trail_not_hit_before_breath()` — normal pullback doesn't trigger
- `test_runner_persistence_across_restart()` — save/load/restart
- `test_runner_data_outage_holds()` — protected runner + outage → hold
- `test_sim_mode_runner_lifecycle()` — sim bars produce runner transition + trail exit
- **Existing P11 tests WILL BREAK** — `test_phase8_exits.py:381-395` tests `trail_hit=True` and 2-red-bar logic. These must be REPLACED (not just updated) with ATR Chandelier tests. Remove `test_trail_hit`, `test_two_red_bars`, `test_not_runner_no_trail`. Replace with `test_atr_chandelier_trail_fires`, `test_atr_chandelier_ratchets`, `test_atr_chandelier_minimum_distance`.
**Acceptance:** a position that reaches +1.5R with confirming structure transitions to RUNNER, gets an ATR trailing stop with minimum distance, and exits when the trail is breached. P5 partials do not fire during RUNNER state. RUNNER state survives restart. Data outage on protected runner holds/retries.

### 11.5 Phase 4 — Scaling-In

**Goal:** Add to winning positions. Anti-martingale pyramiding (50%/25%) with protection resizing.

**Implementation status — 2026-06-25:** Implemented. `src/runner.py` implements `should_add_to_runner()`, `src/sizing.py` implements `add_sizing()`, `PaperExecutionGateway` and `AlpacaExecutionGateway` implement `submit_add()`, and `TradingApp` wires runner add detection, sizing, submission, fill confirmation, protection resizing, and failure fallback. `confirm_fill()` handles ADDING state and returns confirmed adds to RUNNER; `place_stop()` accepts RUNNER state for protection resize. `ScalingSettings` and `default_config.yaml` provide `max_adds`, `add_risk_multiplier`, and `add_activation_r_multiple`. Tests cover anti-martingale sizing, cap blocking, add trigger gating, ADDING→RUNNER transitions, stop resizing, add failure fallback, persistence fields, stop-not-below-entry, Alpaca partial add fill behavior, and missed ADD-fill reconciliation paths for RUNNER/ADDING startup recovery.

**Files:** `src/sizing.py`, `src/entries.py`, `src/paper_execution.py`, `src/runner.py`, `src/app.py`, `src/state_machine.py`, `src/models/schemas.py`, `config/settings.py`, tests.

**Action:**

**Add trigger** (only when position is RUNNER):
- Position state is RUNNER.
- Unrealized profit ≥ +2R from original entry (R = original entry risk, not adjusted for partials).
- Price has established a new higher low and reclaimed (structure-based entry).
- Total open risk after add would not exceed `max_open_risk_pct`.
- Add count < `max_adds` (default 2).

**Add entry detection spec:**
- Valid add setups: `first_pullback` (surge + pullback + reclaim) and `vwap_reclaim` (pullback to VWAP + reclaim). These are the same detectors used for initial entries.
- **Known limitation**: parabolic moves without pullbacks offer no add opportunity. This is acceptable — the bot doesn't add to parabolic extensions (that would be chasing). The trailing stop protects the existing position.
- If no add setup fires, the position continues as RUNNER under trailing stop protection. No add is forced.

**Sizing** (`src/sizing.py` — new `add_sizing()` function):
- Signature: `add_sizing(equity, starter_risk_pct, add_count, risk_per_share_at_add, max_open_risk_pct, total_open_risk) -> tuple[int, float, float]` returning `(add_shares, add_risk_amount, total_risk_after_add)`.
- Add 1: 50% of starter risk (anti-martingale decreasing).
- Add 2: 25% of starter risk.
- Each add uses its own `risk_per_share` based on add entry price and add stop price.
- Must check `max_open_risk_pct` — if total open risk after add exceeds cap, block the add.

**Stop adjustment after add:**
- Move stop for ALL shares to `max(add_entry_price, original_entry_price)` — **stop never goes below original entry**. This prevents ambiguous downward stops on deep pullbacks.
- Cancel existing protective order, place new stop for combined quantity at blended stop.
- After add 1: all shares protected at add1 entry → original risk is "house money."
- After add 2: all shares protected at add2 entry → add1 risk is "house money."

**State transitions:**
- RUNNER → ADDING → RUNNER (add confirmed).
- `PositionState.ADDING` already exists in schema, transition defined in state machine, never used until now.
- **Add failure fallback**: if `submit_add()` fails (API timeout, rejected), transition ADDING → RUNNER (not ERROR — the main position survives). Log the failure. Do not escalate to UNPROTECTED (existing stop still protects original quantity).

**Schema additions:**
- `add_count: int = 0` on `PositionStateModel`.

**Paper execution** (`paper_execution.py`):
- `submit_add(symbol, qty, entry_price)` — submit buy, confirm fill, cancel old stop, place new stop for combined quantity.
- **`confirm_fill()` must handle ADDING state**: currently transitions PENDING_ENTRY → OPEN only. For adds, position is in ADDING state and should transition ADDING → RUNNER on fill. Add ADDING to the valid pre-fill states.
- **`place_stop()` must accept RUNNER state**: currently raises `ValueError` if state is not OPEN (`paper_execution.py:137`). Protection resizing after add happens when state is RUNNER. Add RUNNER to valid states for `place_stop()`.
- `AlpacaExecutionGateway` also needs `submit_add()` override — submit a limit buy to Alpaca, confirm fill, cancel old stop, place new stop.
- Protection resizing: cancel existing stop order, place new stop covering combined shares at new stop price.

**Reconciliation for ADD orders:**
- `reconcile_positions()` must handle ADD order types: case where broker has more shares than local due to missed add fill confirmation. Action: `update_qty_reprotect` (already exists in reconciliation actions).

**Dead code cleanup:**
- Remove `ModeType.ADD_ON_CONFIRMATION` from `schemas.py:80` — never referenced in runtime.

**Config additions:**
```yaml
scaling:
  max_adds: 2
  add_risk_multiplier: 0.5
  add_activation_r_multiple: 2.0
```

**Depends on:** Phase 3 (runner state must exist before scaling-in).
**Verification:**
- `test_add_sizing_anti_martingale()` — 50%, 25% of starter
- `test_add_blocked_by_risk_cap()` — `max_open_risk_pct` blocks add
- `test_protection_resized_after_add()` — old stop cancelled, new stop for combined qty
- `test_add_trigger_at_2r()` — add only after +2R in RUNNER
- `test_add_failure_returns_to_runner()` — add rejected → state back to RUNNER
- `test_stop_never_below_entry()` — combined stop = max(add_entry, original_entry)
- `test_confirm_fill_handles_adding()` — ADDING → RUNNER on fill
- `test_place_stop_accepts_runner()` — no ValueError on RUNNER state
- `test_full_cycle_enter_runner_add_trail_exit()` — integration test (the "soul test")
**Acceptance:** a RUNNER position at +2R with confirming structure receives an add. Stop is resized to cover combined quantity, never below original entry. Total open risk stays under cap. Add failure returns to RUNNER. Full lifecycle works on paper.

### 11.6 Phase 5 — Scanner Batch Snapshots (Only)

**Goal:** Reduce scan cycle HTTP calls by ~30x. Keep Finviz for v0.5. Full scanner replacement deferred to v0.6.

**Implementation status — 2026-06-25:** Implemented. `src/market_data.py` provides `build_market_snapshots()` using Alpaca `StockSnapshotRequest(symbol_or_symbols=[...])` and `get_stock_snapshot()` for one batch response keyed by symbol, consistent with Context7-verified `alpaca-py` docs. `TradingApp._scan_and_process()` prefers the injected batch market-data function and falls back to the legacy per-candidate market-data function only when no batch function is configured. Tests cover a successful multi-symbol batch snapshot, API failure returning explicit `None` per candidate, and an app scan cycle proving the batch function is called once while the per-candidate function is not called. The old per-candidate `build_market_snapshot()` remains as fallback/compatibility, so wording below intentionally says "uses one batch call when wired" rather than "deletes" the old path.

**Files:** `src/market_data.py`, `src/app.py`, tests.

**Action:**
- Add `StockSnapshotRequest(symbol_or_symbols=candidate_symbols)` to `market_data.py` — 1 call returns latest trade + quote + minute bar + daily bar for all candidates.
- Use one batch snapshot call in `_scan_and_process()` when `market_data_batch_fn` is wired; retain the old per-candidate market-data function only as fallback/compatibility.
- Keep Finviz scanner as primary. Keep yfinance static watchlist as fallback. Keep `_finviz_is_stale()` detection.
- Do NOT add Alpaca movers, TradingView, or Yahoo dynamic fallback in v0.5 — these are v0.6.

**v0.6 scanner note (for future reference):**
- `Scanner.day_gainers` does NOT exist in `tradingview-screener` library (verified via Context7). Must use `Query().select(...).where(col('change') > 3).order_by('change', ascending=False).limit(30).get_scanner_data()`.
- `gap` and `Perf.W` TradingView field names unconfirmed — verify before use.
- TradingView rate limit (~60 calls/min) is undocumented.

**Depends on:** Phase 1 (pipeline refactor for cleaner integration).
**Verification:** batch snapshot tests with mocked Alpaca response. Scan cycle HTTP call count verification.
**Acceptance:** scan cycle uses 1 batch snapshot call instead of ~90 per-candidate REST calls when batch market data is configured. Finviz scanner preserved. No behavior change in candidate selection.

### 11.7 Phase 6 — Live Readiness

**Goal:** Clear the remaining blockers between paper and live mode.

**Implementation status — 2026-06-25:** Implemented for the listed v0.5 live-readiness blockers; live trading is still not enabled by default. `get_alpaca_market_session()` uses Alpaca calendar/clock APIs with a 2026 holiday fallback and supports half-day flatten-time adjustment. `TradingApp._is_market_open()` uses the injected market-session function when present and falls back to the local weekday/time check. `paper_mode` is read during reconciliation: irreconcilable positions auto-resolve with warning in paper mode and raise in live mode. `_EXITING_TIMEOUT_S` has been promoted to `Phase1Settings.exiting_timeout_seconds` and wired through `main.py` into `TradingApp`. `SCALING_OUT`, `ModeType.ADD_ON_CONFIRMATION`, and `check_exits_for_open` are removed.

**Files:** `src/app.py`, `src/paper_execution.py`, `src/decision_pipeline.py`, `config/settings.py`, tests.

**Action:**

**Market calendar** (replaces hardcoded `_is_market_open()`):
- Use `TradingClient.get_calendar(GetCalendarRequest(start=today, end=today))`.
- If no calendar entry → market closed (holiday).
- If `close != "16:00"` → half-day, adjust flatten time.
- Use `TradingClient.get_clock()` for real-time `is_open` / `next_open` / `next_close`.
- Fallback: hardcoded `US_HOLIDAYS_2026` set if API unavailable.

**`paper_mode` flag gating:**
- Paper mode: fill simulation remains acceptable for local paths, and `irreconcilable` reconciliation auto-resolves with warning.
- Live mode: `irreconcilable` reconciliation halts startup and requires manual intervention.
- `self._paper_mode` is now read by the reconciliation path; old statements that it is stored but never read are stale.

**Promote `_EXITING_TIMEOUT_S` to config:**
- Completed: `exiting_timeout_seconds: int = 120` exists in `Phase1Settings`, default config, main wiring, and `TradingApp` EXITING timeout handling.

**Dead code cleanup:**
- Completed: `SCALING_OUT` state is removed from schemas/state machine, `ModeType.ADD_ON_CONFIRMATION` is removed, and `check_exits_for_open` is removed after the Phase 1 pipeline split.

**Optional — bracket orders:**
- If the order submission flow is naturally touched during runner/scaling-in work, adopt `OrderClass.BRACKET` for atomic entry+stop.
- Eliminates the stale-stop hazard entirely (stop is attached at submission time).
- Not a hard requirement — T2.3 already mitigates stale stops via `cancel_stale_orders()`.

**Depends on:** Phases 1-4 (all core features working first).
**Verification:** holiday calendar tests, half-day flatten tests, `paper_mode` gating tests, irreconcilable-halts-startup test, `_EXITING_TIMEOUT_S` config wiring test.
**Acceptance:** bot does not trade on US holidays. Half-days adjust flatten time. `paper_mode` flag gates behavior. `irreconcilable` halts in live mode. `_EXITING_TIMEOUT_S` is configurable. `SCALING_OUT` dead state removed.

### 11.8 Phase 7 — LLM Pre-Market Annotator (Optional, Simplified)

**Goal:** Enrich candidates with catalyst context for human review. NOT in the execution path. Minimal implementation.

**Implementation status — 2026-06-25:** Implemented as annotation-only and disabled by default. `src/annotations.py` contains one `enrich_with_llm()` function, `LLMAdvisorSettings.enabled` defaults to false, and no execution-path module calls or reads the LLM annotation. The function uses the Anthropic Python SDK `client.messages.create(...)` pattern and returns `response.content[0].text.strip()` on success or `""` on missing key/import/API failure. Context7 verification shows the current SDK exposes `output_config`, not the old `output_format=CatalystAnnotation` wording, so this spec now documents the implemented plain-text response path. Tests cover disabled-by-default config, YAML/env override, graceful failure, success text extraction, API key non-leakage, and execution-path isolation.

**Files:** `src/annotations.py` (add one function), `config/settings.py`, `config/default_config.yaml`, tests.

**Action:**

**One function in `src/annotations.py`:**
- `enrich_with_llm(symbol, price, gain_pct, volume, api_key) -> str` — calls Anthropic API, returns a catalyst summary string. On any failure (timeout, API error, missing key), returns empty string. No Pydantic model, no provider abstraction, no dedicated module.
- Pre-market batch only. Called after scanner, before market open.
- LLM output is a string annotation written to decision log. NO execution-path module reads it.
- LLM failure = empty string. Trade proceeds normally.
- LLM never sees credentials, never submits orders, never modifies position state.
- Provider: Anthropic Claude Haiku 4.5 (~$0.03/day for 30 candidates).
- API: `client.messages.create(model="claude-haiku-4-5", max_tokens=300, messages=[...])`, then read `response.content[0].text` when present. No `CatalystAnnotation` model or `output_format=` parameter is used in v0.5; Context7 verification for the current Anthropic Python SDK shows `output_config` exists for structured output, while this implementation intentionally keeps the advisor as a plain-text annotation.

**Config:**
```yaml
llm_advisor:
  enabled: false  # disabled by default — opt-in only
```
Only one config field. API key via `ANTHROPIC_API_KEY` env var. No model/tokens/timeout/batch_size config — hardcode sensible defaults. If user wants richer config in v0.6, add it then.

**Safety boundaries (non-negotiable):**
- LLM output is annotation only, never a hard filter, never a sizing input, never an entry/exit gate.
- LLM failure degrades gracefully — no trade is skipped because LLM is unavailable.
- LLM is disabled by default.
- LLM is never called during `_scan_and_process()` or `_monitor_positions()`.
- Tests assert LLM annotation is not referenced in any execution-path module.

**Depends on:** Phase 5 (scanner produces candidate list — but works with Finviz too).
**Verification:** LLM advisor tests: graceful degradation on API failure, disabled-by-default, annotation-not-in-execution-path.
**Acceptance:** LLM enriches candidates with catalyst summaries for decision log review. Bot behavior is identical with or without LLM enabled. Implementation is one function, not a module.

### 11.9 New Config Fields Summary

```yaml
# Phase1Settings additions
dollar_volume_min: 50000
exiting_timeout_seconds: 120

# New config sections
runner:
  activation_r_multiple: 1.5
  atr_period: 5
  trail_multiplier: 2.5
  higher_lows_required: 2
  volume_confirm_multiplier: 1.5

scaling:
  max_adds: 2
  add_risk_multiplier: 0.5
  add_activation_r_multiple: 2.0

llm_advisor:
  enabled: false
```

**Config wiring gate:** Every new config field must have a tested runtime read site. No decorative config. This is the v0.4.0 lesson (SPEC §5.12). Each phase that adds config fields must include a test proving the field is read at runtime and changes behavior.

### 11.10 Test Plan For v0.5

**Phase 1 (pipeline refactor):** all existing v0.4.0 tests pass unchanged, with additional v0.5 tests covering the split helpers and compatibility wrapper.

**Phase 2 (sizing):** effective risk assertions at attention 70/85/worst-case. Floor 0.40 verification. Lunch multiplier removed verification. `data_confidence` impact documented.

**Phase 3 (runner capture):**
- `test_position_promotes_to_runner()` — +1.5R + structure → RUNNER
- `test_runner_not_promoted_prematurely()` — small profit stays OPEN
- `test_atr_computation()` — ATR(5) from bars, Wilder's smoothing
- `test_runner_trail_updates_highest_price()` — ratchet
- `test_runner_trail_exits_on_pullback()` — price drops below trail → P11 fires
- `test_runner_trail_minimum_distance()` — trail never tighter than 1.0×original_risk
- `test_p5_paused_for_runner()` — scale-out skipped in RUNNER state
- `test_runner_trail_not_hit_before_breath()` — normal pullback doesn't trigger
- `test_runner_persistence_across_restart()` — save/load/restart
- `test_runner_data_outage_holds()` — protected runner + outage → hold
- `test_sim_mode_runner_lifecycle()` — sim bars produce runner transition + trail exit
- **REPLACE** existing P11 tests (`test_trail_hit`, `test_two_red_bars`, `test_not_runner_no_trail`) with ATR Chandelier tests

**Phase 4 (scaling-in):**
- `test_add_sizing_anti_martingale()` — 50%, 25% of starter
- `test_add_blocked_by_risk_cap()` — `max_open_risk_pct` blocks add
- `test_protection_resized_after_add()` — old stop cancelled, new stop for combined qty
- `test_add_trigger_at_2r()` — add only after +2R in RUNNER
- `test_add_failure_returns_to_runner()` — add rejected → state back to RUNNER
- `test_stop_never_below_entry()` — combined stop = max(add_entry, original_entry)
- `test_confirm_fill_handles_adding()` — ADDING → RUNNER on fill
- `test_place_stop_accepts_runner()` — no ValueError on RUNNER state
- `test_full_cycle_enter_runner_add_trail_exit()` — integration test (the "soul test")

**Phase 5 (scanner batch snapshots):** batch snapshot tests with mocked Alpaca response. Scan cycle HTTP call count verification proves the batch path is called once when configured.

**Phase 6 (live readiness):**
- `test_holiday_blocks_trading()` — calendar API mocked
- `test_half_day_adjusts_flatten()` — early close
- `test_paper_mode_gates_behavior()` — paper vs live distinction
- `test_irreconcilable_halts_in_live()` — startup halt
- `test_exiting_timeout_configurable()` — config field wired

**Phase 7 (LLM):**
- `test_llm_disabled_by_default()`
- `test_llm_failure_returns_empty_string()`
- `test_llm_annotation_not_in_execution_path()`

**Fresh verification on 2026-06-25:** `UV_LINK_MODE=copy .venv/bin/python -m pytest --tb=short -q` → `893 passed in 80.59s (0:01:20)`.

**Fresh verification on 2026-07-05:** `python3 -m pytest -q` → `969 passed in 72.46s (0:01:12)`.

### 11.11 Acceptance Criteria For v0.5.0

v0.5.0 is complete only when:

- Pipeline is split into `evaluate_candidate()` / `execute_entry()` / `evaluate_exits()`. All v0.4.0 tests pass.
- `evaluate_exits()` returns `Optional[ExitDecision]` directly. `check_exits_for_open` flag removed.
- `run_pipeline()` does not own the runtime decision flow; if retained, it is only a thin backward-compatible wrapper around the split functions.
- Sizing floor is 0.40. Lunch multiplier removed. `dollar_volume_below_min` configurable. Attention ≥70 produces ≥$100 effective risk. `data_confidence` impact documented.
- OPEN → RUNNER transition fires on +1.5R + structure + volume + VWAP + ACTIVE. R is always from original entry risk.
- `highest_price_seen` is ratcheted every monitor cycle.
- P11 uses ATR Chandelier trailing stop (2.5× ATR(5) with minimum 1.0×original_risk), not `trail_hit` flag or 2-red-bar heuristic. `trail_hit` parameter removed.
- `check_exits()` passes `highest_price_seen` and `atr` to `check_runner_trail()`.
- P5 scale-out is paused for RUNNER state.
- RUNNER state survives restart (position persistence test).
- Data outage on protected RUNNER holds/retries (same as §6.4).
- Decision log contains `promoted_to_runner`, `trail_updated`, `trail_exit` events.
- Scaling-in: ADDING state activates at +2R in RUNNER. Anti-martingale sizing (50%, 25%). Stop resized to combined quantity, never below original entry. Max 2 adds. Add failure returns to RUNNER.
- `confirm_fill()` handles ADDING state. `place_stop()` accepts RUNNER state.
- Full lifecycle integration test passes: scan → enter → runner → add → trail → exit.
- Batch snapshots replace per-ticker REST calls in scan cycle.
- Market calendar API handles holidays and half-days.
- `paper_mode` flag gates behavior (fill fidelity, logging, irreconcilable handling).
- `_EXITING_TIMEOUT_S` promoted to config.
- `SCALING_OUT` dead state removed. `ModeType.ADD_ON_CONFIRMATION` dead enum removed.
- LLM advisor is disabled by default, annotation-only, one function, graceful degradation, never in execution path.
- Every new config field has a tested runtime read site (config wiring gate).
- Full cycle (monitor 3 positions + scan 20 candidates) completes in <5 seconds.
- All tests pass. No decorative config. No over-engineering.
- The audited hardening gaps recorded in §11.16 are implemented in the current repository state.

### 11.12 v0.5 Addresses These §10.1 Gaps

| Former §10.1 gap | v0.5 Phase | Resolution |
|-----------|-----------|------------|
| Runner state exists but no runtime transition creates runners | Phase 3 | `should_promote_to_runner()` + `transition_position(pos, RUNNER)` |
| Runner trailing accepts `highest_price_seen` but doesn't use it | Phase 3 | ATR Chandelier trailing uses `highest_price_seen` with ratchet |
| Runner exits are incomplete | Phase 3 | P11 rewritten with real trailing math |
| Scaling-in has no implementation | Phase 4 | `add_sizing()` + `submit_add()` + protection resizing |
| yfinance fallback is static watchlist | v0.6 | Deferred — keep Finviz + static watchlist for v0.5 |
| News/catalyst awareness unwired | Phase 7 | LLM pre-market annotator (optional, one function, annotation-only) |
| `run_pipeline()` mixes entry/execution/exit | Phase 1 | Split into three focused functions; old symbol retained only as compatibility wrapper |
| No US holiday calendar | Phase 6 | `TradingClient.get_calendar()` |
| `paper_mode` flag dead | Phase 6 | Wired to gate fill fidelity, logging, reconciliation strictness |

### 11.13 Dead Code Cleanup (Cross-Phase)

These items are cleaned up during the phases where they're naturally touched:

| Item | Location | Cleanup Phase | Action |
|------|----------|---------------|--------|
| `SCALING_OUT` state | `schemas.py`, `state_machine.py` | Phase 6 | Remove — never entered by runtime code |
| `ModeType.ADD_ON_CONFIRMATION` | `schemas.py:80` | Phase 4 | Remove — never referenced |
| `trail_hit` parameter | `exits.py:418,471` | Phase 3 | Remove after P11 rewrite |
| `check_exits_for_open` flag | `decision_pipeline.py` | Phase 1 | Remove after pipeline split |
| `_EXITING_TIMEOUT_S` hardcoded | `decision_pipeline.py:475` | Phase 6 | Promote to `Phase1Settings.exiting_timeout_seconds` |
| `paper_mode` flag dead | `app.py:121` | Phase 6 | Wire to gate behavior |

**Implementation status — 2026-06-25:** `SCALING_OUT`, `ModeType.ADD_ON_CONFIRMATION`, `trail_hit`, `check_exits_for_open`, `_EXITING_TIMEOUT_S` hardcoding, and the dead `paper_mode` flag are all cleaned up or wired. `run_pipeline()` remains intentionally as a compatibility wrapper, not as the runtime owner of entry/exit logic. Future cleanup must not reintroduce these symbols as active runtime concepts without a new spec.

### 11.14 Error Handling Matrix

| Scenario | State Before | Action | State After |
|----------|-------------|--------|-------------|
| `should_promote_to_runner()` raises exception | OPEN | Log error, skip promotion this cycle | OPEN (unchanged) |
| `submit_add()` fails (API timeout/rejected) | ADDING | Log failure, transition back | RUNNER |
| `compute_runner_stop()` produces stop below current price (ATR spike) | RUNNER | Use minimum distance `max(2.5×ATR, 1.0×original_risk)`, log warning | RUNNER (stop updated) |
| `protect_position()` fails after add (old stop cancelled, new stop fails) | RUNNER | Mark UNPROTECTED, escalate via P4 | UNPROTECTED |
| `confirm_fill()` for add fails | ADDING | Log failure, transition back | RUNNER |
| Data outage on protected RUNNER | RUNNER | Hold/retry, freeze trailing stop | RUNNER (frozen trail) |
| Data outage on unprotected RUNNER | RUNNER | Mark UNPROTECTED, escalate via P4 | UNPROTECTED |
| Persistent data outage >120s on RUNNER | RUNNER | Mark UNPROTECTED | UNPROTECTED |
| Reconciliation finds RUNNER with no trailing stop | RUNNER | Escalate to UNPROTECTED | UNPROTECTED |

### 11.15 v0.6 Deferred Items

The following are explicitly deferred to v0.6+ and are NOT required for v0.5:

- **Full scanner replacement**: Alpaca `ScreenerClient.get_market_movers()` (primary) + TradingView Screener `Query().where(col('change') > 3)` (enrichment, RVOL+float) + Yahoo `Screener().set_predefined_body('day_gainers')` (dynamic fallback). Cross-source verification. All verified via Context7.
- **SIP data feed** ($99/mo Algo Trader Plus) — test with IEX first.
- **WebSocket streaming** (`StockDataStream`) — requires async architecture.
- **OCO/bracket orders** (`OrderClass.BRACKET`/`OCO`) — optional, stale-stop hazard already mitigated.
- **VPS deployment automation** — manual setup for paper.
- **Direct mocked Alpaca-response unit tests for `build_market_snapshot*()`** — completed in v0.5 follow-up hardening; no longer deferred.
- **Backtesting framework** — different abstractions.
- **Plugin system, web dashboard, multi-broker abstraction** — never.

### 11.16 v0.5 Implementation Audit — 2026-06-25

This audit compared the current repository against §11.2-§11.8 and updated this spec to document the work that has actually been implemented.

#### Phase 1 — Pipeline Refactor

Implemented. The runtime has `evaluate_candidate()`, `execute_entry()`, and `evaluate_exits()`, and the app uses the split functions directly for scan and monitor paths. `evaluate_exits()` includes the runner inputs needed for P11. `check_exits_for_open` is removed. `run_pipeline()` remains as a thin compatibility wrapper, so release acceptance is about removing monolithic ownership, not deleting the public symbol.

#### Phase 2 — Sizing Recalibration

Implemented. The floor is 0.40 in sizing and soft-warning multiplier logic, the lunch sizing multiplier is removed, and `dollar_volume_min` is configurable and wired into runtime filters. Tests document the floor and recalibrated behavior. `is_lunch_window()` remains defined for possible time-gate use but is not part of sizing. This phase is documented as complete.

#### Phase 3 — Runner Capture & Trailing Stops

Implemented. Runner promotion, ATR calculation, Chandelier stop computation, `highest_price_seen` ratcheting, P11 ATR trail exit, P5 pause for RUNNER, RUNNER startup reconciliation, protected RUNNER outage timeout handling, and sim-mode runner lifecycle coverage are all in place. Persistence fields exist for runner metadata. Trail exits are logged as dedicated `trail_exit` decisions while preserving the underlying `atr_trail_hit` exit detail.

#### Phase 4 — Scaling-In

Implemented. Add detection, anti-martingale add sizing, ADDING state transitions, `submit_add()` for paper and Alpaca gateways, fill handling, stop resize, failure fallback, config, and missed ADD-fill reconciliation are all present. Tests cover sizing, caps, trigger gating, transitions, protection resizing, failures, persistence, stop floor, Alpaca partial fills, and startup recovery when broker quantity proves an ADD filled without local confirmation.

#### Phase 5 — Scanner Batch Snapshots

Implemented. The scan path uses one Alpaca batch snapshot call when `market_data_batch_fn` is configured, while preserving the old per-candidate path as fallback. Batch snapshot behavior is covered with mocked Alpaca responses and app-level call-count tests. Finviz remains the primary scanner and yfinance remains the static-watchlist fallback. No full scanner replacement is authorized for v0.5.

#### Phase 6 — Live Readiness

Implemented for v0.5 readiness, not live enablement. The code has Alpaca calendar/clock integration, holiday fallback, half-day flatten support, `paper_mode` reconciliation behavior, configurable exiting timeout, and dead-state cleanup. Live mode still remains gated by explicit confirmation and is not broadly enabled. This phase is documented as complete for the listed v0.5 blockers.

#### Phase 7 — LLM Pre-Market Annotator

Implemented. The LLM advisor is one plain-text Anthropic function, disabled by default, returns `""` on failure, and is not wired into scan, monitor, entry, exit, sizing, or risk decisions. Tests cover config behavior, failure/success behavior, API key non-leakage, and execution-path isolation. The previous `output_format=CatalystAnnotation` wording was stale; the implementation uses plain `messages.create(...)` and reads `response.content[0].text`.

### 11.17 v0.5 Trading Logic Audit and Safety Roadmap

**Date:** 2026-06-28  
**Verdict:** Conceptually viable, not live-money ready. The bot's identity (top-gainer attention + defined risk + runner capture) is a real momentum framework. The current implementation has edge-quality and live-safety gaps that could cause under-trading, false confidence, or unsafe broker behavior in production.

---

#### 11.17.1 What Is Solid

- The system correctly treats top gainers as candidates, not qualitative rejects — no discretionary filtering, no manual approval gate.
- Risk-based sizing is structurally correct: shares derive from stop distance (`src/sizing.py:20-26`).
- Exit/protection architecture (P1–P11 cascade, `src/exits.py:505-555`) is substantially stronger than a typical hobby bot. Priority ordering covers emergency, loss caps, hard stop, invalidation, protection gaps, scale-out, technical loss, spread, volume, time, and runner trail.
- Runner/scaling states (RUNNER, ADDING) and their transitions are implemented and wired in `src/app.py:716-829`.
- Daily, open, and per-symbol risk controls exist in some form: daily loss cap 3%, max open risk 3%, max positions 3 (`src/risk.py`).

---

#### 11.17.2 Dangerous Gaps — Resolved / Remaining

| Gap | Status | Current note |
|-----|--------|--------------|
| Batch snapshot returns only 1 minute_bar | **Resolved (#0)** | Batch market-data path now carries 20 recent 1-min bars per symbol for entries/ATR. |
| Price-gain normalization capped at 50% | **Resolved (#4)** | Attention score uses 30/40/30 weighting and 25% price-gain cap. |
| HOD/ROC not passed to score_attention | **Resolved (#4)** | HOD proximity, recent-new-HOD, and 1/3/5m ROC are wired through pipeline/app scoring. |
| Runner trail is local-only | **Resolved (#1)** | Runner promotion/trail updates sync broker stops; sync failure marks UNPROTECTED. |
| Startup reconciliation lacks open-order truth | **Resolved (#3, #12)** | Startup imports/drops broker open orders; loop now periodically reconciles broker open-order truth. |

---

#### 11.17.3 Immediate Fixes — Current Status

1. **Market-data windows:** resolved by #0 and #10.
2. **Attention/HOD/ROC calibration:** resolved by #4.
3. **Broker/live safety:** resolved by #1, #2, #3, #8, #11, #12, #13, and #14. Remaining tiny-live prerequisite is paper/replay evidence, not another strategy change.

---

#### 11.17.4 Scanner Logic

**Current implementation.** Dynamic scanner uses Alpaca movers first, then Finviz free top gainers (`v=111&s=ta_topgainers`, `src/scanner/enrichment.py:18`) as the fallback dynamic source. Free-tier columns: ticker, company, sector, industry, country, market cap, P/E, price, change, volume (`src/scanner/enrichment.py:44-48`). No RVOL, float, short float, real catalyst, or halt data. Stale detection fires when ≥80% of rows have zero change or zero volume (`src/scanner/enrichment.py:252-277`). The static yfinance `_VOLATILE_WATCHLIST` remains available as watch-only utility data, but stale/empty Finviz no longer feeds it into automatic trade discovery.

**Assessment.** Finviz free is acceptable for candidate discovery, not final ranking. It returns real top-% movers, but without RVOL/HOD/catalyst/float it cannot distinguish real momentum from low-liquidity noise. A static yfinance watchlist is not a scanner; if dynamic sources fail, the bot should watch rather than pretend curated names are today's top gainers.

**Recommendation.** Keep Finviz as seed source only. Add Alpaca `ScreenerClient.get_market_movers()` as a primary or secondary dynamic source (`src/scanner/enrichment.py`). Add most-active / high-trade-count scan. Treat yfinance static watchlist as watch-only fallback, not trade discovery. Rank by % gain/gap, RVOL, 5-min dollar volume, HOD proximity/new-high behavior, and float (if available).

| Component | Action | Priority |
|-----------|--------|----------|
| Finviz free | Keep as seed source | Now |
| Alpaca movers | Wire `ScreenerClient.get_market_movers()` | Phase 2 |
| yfinance watchlist | Downgrade to watch-only fallback | Done |
| RVOL/float | Add as enrichment fields | Phase 2 |

---

#### 11.17.5 Attention Scoring

**Current implementation.** Weights: price 40, volume 35, HOD acceleration 25 (`src/scanner/attention.py:36-38`). Price scoring uses `pts = min(40, best_gain / 50.0 * 40)` (`src/scanner/attention.py:218`). Volume contributes RVOL up to 20 pts and dollar volume up to 15 pts (`src/scanner/attention.py:222-257`). HOD: within 1% = 15 pts, within 3% ≈ 8 pts, ROC up to 10 pts (`src/scanner/attention.py:260-296`).

**Assessment.** Model structure is correct, but runtime calibration is weak. A 10% gainer gets only 8/40 raw price points. If HOD/ROC inputs are missing (which they currently are — `src/decision_pipeline.py:258-261` does not pass `hod_price`, `roc_1m_pct`, `roc_3m_pct`, or `roc_5m_pct` to `score_attention()`), many real candidates fall below the 70 attention tier.

**Recommendation.** Redistribute: price/gap 30%, volume/RVOL 40%, HOD acceleration 30%. Normalize price gain against a 20–25% cap (not 50%). RVOL bands: <2x weak, 2–3x moderate, 3–5x strong, ≥5x capped. HOD within 1% = strong. New HOD in last 3–5 bars = strong. Compute 1/3/5-min ROC from bars. Scanner/attention bonus should decay over subsequent scan cycles so stale scanner hits are not repeatedly rewarded. Former-runner bonus is wired: confirmed runner trail exits call `FormerRunnerStore.mark()`.

| Parameter | Current | Recommended |
|-----------|---------|-------------|
| Price weight | 40 | 30 |
| Volume weight | 35 | 40 |
| HOD weight | 25 | 30 |
| Price normalization cap | 50% | 20–25% |
| R bands | — | <2x weak, 2–3x moderate, 3–5x strong, ≥5x capped |

---

#### 11.17.6 Entry Setup Quality

**Current priority order:** `first_pullback` → `hod_reclaim` → `consolidation_breakout` → `micro_pullback` → `vwap_reclaim` → `scalp_reclaim`.

**Priority recommendation:** `first_pullback` → `vwap_reclaim` → `consolidation_breakout` → `hod_reclaim` → `micro_pullback` → `scalp_reclaim`.

| Setup | Assessment | Recommendation |
|-------|-----------|----------------|
| **first_pullback** | Best core setup. Current 20% retrace is shallow; 2–8 bars acceptable. Logical-level check passes if VWAP/EMA/HOD missing. | Require at least one anchor level or ATR fallback. Use 25–50% retrace or ≥1–2 ATR. Keep controlled-selling rule. |
| **hod_reclaim** | Real edge but common trap. Current volume check too permissive. | Require close above HOD, not wick. Breakout volume ≥1.5–2x prior 10-bar avg. Avoid repeated failed HOD behavior. |
| **consolidation_breakout** | Range length fixed at 5 bars (`src/entries.py:501-502`), lacks breakout-candle volume confirmation. | Dynamic 5–20 bar base. Require breakout volume ≥1.5x range avg. Use ATR-normalized tightness, not fixed 2% range. |
| **micro_pullback** | Reasonable for active runners. Sensitive to noisy 1-min bars. | Require higher-low structure and meaningful volume floor. |
| **vwap_reclaim** | Good automatable setup, current volume rule weak. Elevate priority. | Require VWAP flat/rising, reclaim close above VWAP, volume ≥1–1.5x recent avg. |
| **scalp_reclaim** | Allowed in EXTENDED/HALT_RISK. Dangerous in halted stocks. | EXTENDED scalp only with tiny risk. HALT_RISK should mean no new longs unless halt resolved, spread tight, quote stable. |

**Validation required** (per-setup): win rate, average R, median R, MAE, MFE, false-breakout rate, time-of-day performance, setup grade (A/B/C), random-entry baseline. Minimum 200 trades per setup before trusting calibration.

---

#### 11.17.7 Sizing Logic

**Current implementation.** Starter risk: 0.25% of \$100k = \$250 (`src/sizing.py:20-26`, `config/default_config.yaml:35`). Attention multiplier: 85+ = 1.00, 70+ = 0.75, 50+ = 0.50, <50 = 0.25 (`src/sizing.py:29-48`). Soft multiplier floor: 0.40 (`src/sizing.py:57`, `src/annotations.py:286`).

**Assessment.** For paper, 0.25% is acceptable. For eventual profitable momentum, conservative. Multiplier stacking can shrink a 70-score trade to \$52.50 — too small to validate runner strategy.

**Recommendation.** Paper phase: keep 0.25% until 100+ real paper trades; report intended risk vs actual risk; ensure A-setups risk at least \$100 on \$100k paper equity. Post-validation: B-setups 0.25–0.50%, A-setups 0.50%, A+ high-attention 0.75–1.00%, hard cap 1.00%. Attention bands: 85–100 → 1.00, 70–84 → 0.85, 50–69 → 0.60, <50 → watch or 0.40 tiny probe. Add notional cap: max position value 20–25% of account.

| Score Band | Current Mult | Recommended Mult |
|-----------|-------------|-----------------|
| 85–100 | 1.00 | 1.00 |
| 70–84 | 0.75 | 0.85 |
| 50–69 | 0.50 | 0.60 |
| <50 | 0.25 | 0.40 (watch / probe) |

---

#### 11.17.8 Exit Engine

**Current priority cascade:** P1 emergency, P2 loss caps, P3 hard stop, P3b invalidation, P4 missing protection, P5 scale-out, P6 failed reclaim, P7 VWAP loss, P8 spread, P9 volume disappearance, P10 time, P11 runner trail (`src/exits.py:505-555`).

**Assessment.** Skeleton is sound. Two concrete issues:

1. **Time flatten vs scale-out ordering.** At 15:55, if a non-runner is also at +1R, P5 scale-out fires before P10 time exit, selling only 33% instead of flattening the full position.
2. **P7 VWAP loss sensitivity.** Fires on a single bar below VWAP without requiring confirmation.

**Recommendations.**

| Rule | Issue | Fix |
|------|-------|-----|
| P5/P10 ordering | Scale-out fires before time flatten | Move P10 time flatten before P5 partial exits, or make flatten override all non-emergency partials. |
| P5 scale-out % | 33% at +1R is aggressive | Reduce to 25%, or suppress 1R scale-out for high-attention ACTIVE names until runner decision at 1.5R. |
| P7 VWAP loss | Single bar triggers exit | Require 2 closes below VWAP or failed reclaim within 2 bars. |
| P1 spread explosion | No relative widening check | Require absolute + relative widening: `spread_pct > 1.0 and spread_pct > entry_spread * 3`, or `spread_pct > 5.0`. |

---

#### 11.17.9 Runner Logic

**Current implementation.** Promotion requires state OPEN, +1.5R, higher lows, latest volume ≥1.5x prior avg, price ≥ VWAP, and state ACTIVE (`src/runner.py:81-139`). ATR trail period 5, multiplier 2.5, min distance = max(2.5x ATR, original risk) (`src/runner.py:174-205`).

**Assessment.** Concept is sound. +1.5R is a reasonable activation threshold. Minimum trail distance is smart. Four problems:

1. **`_count_higher_lows()`** counts bar-to-bar higher lows, not true swing lows (`src/runner.py:142-152`). This inflates the count and admits false structure.
2. **No absolute volume floor.** A quiet stock with a relative volume spike can qualify.
3. **ATR starvation with batch snapshots.** ATR is computed from whatever bars are passed; one-bar snapshots make ATR unavailable.
4. **Local trail never pushed to broker stop.**

**Recommendations.**

| Issue | Fix |
|-------|-----|
| Higher-lows detection | Count swing lows, not raw bar-to-bar lows. |
| Volume floor | Require minimum latest-bar dollar volume or raw volume. |
| ATR timeframe | Use 5-min bars for runner ATR, or 1-min ATR(14). |
| Broker stop sync | Push updated trail to broker after every `_update_runner_trail()`. Cancel old stop, place new. |

---

#### 11.17.10 Move Classification

**Current implementation.** Five states: HALT_RISK, BACKSIDE, EXTENDED, ACTIVE, EARLY (`src/models/schemas.py:26-34`). Permission matrix mapped in `src/move_classifier.py:348-355`.

**Issues.**

| Issue | Location | Problem |
|-------|----------|---------|
| `hod_behavior_repeated` → ACTIVE | `src/classifier_features.py:196-202`, `src/move_classifier.py:300-303` | Repeated failed HOD behavior contributes to ACTIVE — likely inverted. Should push toward BACKSIDE. |
| HALT_RISK fires on any single signal | `src/move_classifier.py:165-199` | `halt_count_today > 0` alone triggers HALT_RISK (`signals > 0`). Too sensitive — a stock with one resolved halt should not block all entries. |
| Hard block on `halt_count_today > 0` | `src/decision_pipeline.py:288` | `is_halted = (halt_count_today > 0)` makes a resumed stock with one prior halt untradeable for the entire day. |
| No state hysteresis | Entire `classifier_features.py` | State can flip every monitor cycle. |

**Recommendations.**

| Fix | Action |
|-----|--------|
| HOD repeated → BACKSIDE | Move `hod_behavior_repeated` from ACTIVE core signals to BACKSIDE evidence. |
| HALT_RISK threshold | Require halt count > 1 or 2+ risk signals to classify HALT_RISK. |
| Soft halt warning | One resolved halt → reduced size / soft warning, not day-long hard block. |
| Hysteresis | Require 2 consecutive cycles before BACKSIDE or HALT_RISK blocks new entries. |

---

#### 11.17.11 Architecture Assessment

| Concern | Current State | Assessment |
|---------|--------------|------------|
| **Sync loop** | 1-second tick with monitor/scan cadences | Fine for paper. Marginal for live small-cap scalping. Blocking scanner/yfinance/market-data calls can delay exits. Acceptable only if broker stops are always current. |
| **Persistence** | JSON saves on graceful shutdown only (`src/app.py:1080-1091`). Session P&L ledger not persisted. | Crash/restart resets daily loss accounting. Add periodic checkpoint every 30–60 seconds via atomic JSON or SQLite. |
| **Reconciliation** | Startup reconciliation improved, but broker snapshot fetches positions only (`src/paper_execution.py:1191-1208`). | Must reconcile: broker positions, broker open stop orders, local positions, local pending orders, local P&L ledger. |
| **State machine** | States mostly reachable. ERROR locks symbols but excluded from `all_open()` monitoring (`src/state_machine.py:187-190`). | Add explicit ERROR → CLOSED/NONE cleanup path after broker rejection reconciliation. |
| **Error handling** | Scanner failures: log and continue. Position monitor failures: no escalation. | After 3 consecutive monitor exceptions on a symbol, escalate to UNPROTECTED or manual intervention. |
| **Risk architecture** | Daily loss cap 3%, per-symbol cap effectively same as daily cap, max open risk 3%, max positions 3. | Per-symbol cap too loose at 3% — use 1%. Max open risk 3% should include worst-case open stops + pending adds. |

**Missing risk controls:** max consecutive losses, weekly drawdown throttle, market regime kill switch, symbol cooldown after loss, sector/theme concentration, realized P&L persistence, broker open-order reconciliation, slippage/spread post-trade analytics.

---

#### 11.17.12 Deferred / Future Phases

These are recognized as valuable directions but explicitly deferred past the immediate roadmap. They are documented here as spec reference, not as active tasks.

| Future Phase | Description | Preconditions |
|-------------|-------------|---------------|
| **Scanner upgrade** | Alpaca movers/most-active, Finviz cross-check, Yahoo/TradingView fallback enrichment, float from yfinance/Finviz Elite/manual provider. | Do not build full data platform yet — use batch snapshots + per-symbol bar fetching. |
| **Pre-market scanning** | Start 30–60 minutes before open. Track premarket gain %, high/low, volume, RVOL vs normal, catalyst, float, top-gainer persistence. No execution until regular-hours policy is explicit. | Requires pre-market data source. |
| **News/catalyst** | LLM annotation remains non-execution-path; logs/watchlist only. Deterministic catalyst fields may later influence confidence, not hard gates. | Post-stats regime. |
| **Multi-timeframe** | Add 5-min bars. Use 1-min trigger, 5-min trend/ATR trail/VWAP/EMA structure, daily/premarket context. | Phase 3 (recommended). |
| **Adaptive parameters** | After sufficient stats: adapt by time of day, market volatility, scanner quality, spread/liquidity regime. | Post-analytics, post-200 trades. |
| **Analytics** | Track R-multiple, MAE/MFE, setup type, attention bucket, time of day, RVOL/dollar volume bucket, runner promoted, add count, exit reason, slippage, realized vs intended risk, profit factor, max drawdown, expectancy by setup. | Add per-setup analytics dashboard or report. |

---

#### 11.17.13 Priority Roadmap

| # | Item | Complexity | Impact | Phase |
|---|------|-----------|--------|-------|
| 0 | Fix batch snapshot bar starvation — ensure multi-bar data for entries and ATR | Medium | Critical | 1 |
| 1 | Sync runner trailing stop to broker (cancel old, place new) | Low | Critical | 3 |
| 2 | Persist P&L ledger + periodic checkpoint (30–60s) | Medium | Critical | 3 |
| 3 | Reconcile broker open orders, not only positions | Medium | Critical | 3 |
| 4 | Recalibrate attention scoring + wire HOD/ROC | Medium | Important | 2 |
| 5 | Add per-setup analytics dashboard / report | Medium | Important | 2 |
| 6 | Fix classifier polarity (HOD repeated → BACKSIDE) and halt handling | Low | Important | 2 |
| 7 | Move time flatten (P10) before partial scale-outs (P5) | Low | Important | 2 |
| 8 | Add ERROR-state recovery (ERROR → CLOSED cleanup) | Low | Important | 3 |
| 9 | Add dynamic scanner source (Alpaca movers) | Medium | Important | 2 |
| 10 | Add 5-min timeframe for runner ATR and trend confirmation | Medium | Important | 2 |
| 11 | Add weekly drawdown / consecutive-loss throttle | Low | Important | 3 |

Implementation checklist/log:
- [x] #0 Fix batch snapshot bar starvation — completed: batch market data fetches 20 recent 1-min bars per symbol and snapshot enrichment uses those bars instead of single minute-bar fallback; entry/ATR starvation tests pass.
- [x] #1 Sync runner trailing stop to broker — completed: runner promotion/trail updates cancel stale symbol orders and place verified replacement stops; sync failure marks position UNPROTECTED; focused tests pass.
- [x] #2 Persist P&L ledger + periodic checkpoint — completed: PnL ledger restores at startup and checkpoints periodically with atomic save path; focused persistence tests pass.
- [x] #3 Reconcile broker open orders, not only positions — completed: startup reconciliation imports broker open orders, removes stale local orders, and flags missing stop protection; focused reconciliation tests pass.
- [x] #4 Recalibrate attention scoring + wire HOD/ROC — completed: 30/40/30 scoring, 25% price cap, RVOL bands, HOD proximity/recent-new-HOD + 1/3/5m ROC wired through pipeline/app, scanner seen-count decay; focused tests/review pass.
- [ ] #5 Add per-setup analytics dashboard/report — **DEFERRED / not wanted for current roadmap**. Do not build or expand dashboard/report work unless explicitly requested by the user; existing analytics helpers are non-trading-path support only.
- [x] #6 Fix classifier polarity and halt handling — completed: repeated HOD now BACKSIDE not ACTIVE; HALT_RISK needs repeated halt or 2+ risk signals; single resolved halt soft warning, tests/review done.
- [x] #7 Move time flatten before partial scale-outs — completed: P10 time flatten now runs before P5 scale-outs, so 15:55 exits flatten 100% instead of partial scaling at +1R; focused test added.
- [x] #8 Add ERROR-state recovery — completed: zero-share ERROR positions with no pending orders close on monitor cycle, unlocking rejected-entry symbols; ERROR positions with shares stay locked for manual/broker reconciliation; focused tests added.
- [x] #9 Add dynamic scanner source — completed: Alpaca movers map top gainers into Candidate source `alpaca_movers`; dynamic scanner tries Alpaca first, falls back to Finviz/yfinance; paper/live/sim scan paths use it; focused tests added.
- [x] #10 Add 5-min timeframe for runner ATR and trend confirmation — completed: market snapshots carry dedicated 5-min bars; monitor path uses them for runner ATR and promotion trend checks while 1-min bars remain entry/exit trigger data; focused tests pass.
- [x] #11 Add weekly drawdown / consecutive-loss throttle — completed: app tracks/persists weekly realized P&L and consecutive losses with week-boundary reset; entry kill switch blocks new trades at weekly drawdown or loss-streak thresholds; config defaults and focused tests pass.
- [x] #12 Add periodic broker open-order reconciliation during loop — completed: main loop now rechecks broker open orders on cadence, drops local pending stops missing at broker, escalates affected OPEN/RUNNER positions to UNPROTECTED, and requires 3 consecutive broker-order snapshot failures before loop-time mass UNPROTECTED escalation; focused loop tests pass.
- [x] #13 Add consecutive monitor-failure escalation — completed: per-symbol monitor exceptions are counted, the third consecutive failure marks the position UNPROTECTED, and an audit decision-log record `monitor_failure_escalated_unprotected` is written; focused monitor test passes.
- [x] #14 Add market-closed safety audit — completed: loop records a once-per-session decision-log audit `market_closed:<source>` when Alpaca calendar/clock or fallback rules report the market closed; no new strategy filter or entry rule was added; focused loop test passes.

---

#### 11.18 Data Pipeline & Spread Restructuring — "Top-Gainer Data Integrity"

**Date:** 2026-07-06

**Trigger:** First paper run exposed critical data gaps. LHSW (top gainer, +250%, $7 price) showed `dollar_volume_5m = $15,207` — impossibly low. Investigation revealed: IEX feed (2.5% of market) with no scaling, RVOL permanently `None` (dead code, 25 attention points silent), no daily volume fetched, spread 5% hardcoded binary gate blocking most top gainers. The bot was flying blind on its two most important inputs: volume and spread.

**Research basis:** 4 parallel research lanes (2 codebase investigators, 2 external researchers). Codebase findings verified at exact file:line. Alpaca API verified via Context7 (`/alpacahq/alpaca-py`). Professional trading research via web (30+ sources: practitioner blogs, academic papers, platform docs).

**Mental mode:** The bot trades top gainers. Top gainers have wide spreads — that's where the inefficiency lives. Spread is a sizing dial, not a gate. RVOL is the #1 professional signal and is currently dead code. % gain is the primary ranking signal. Paper mode is for experimentation — the bot must TAKE TRADES to learn, not block everything.

**Key architectural decisions (locked by user):**

1. **IEX volume × 40** — IEX is ~2.5% of market. Scale to approximate total. Remove scaling if SIP ($99/mo) is added later.
2. **Spread → tiered position sizing** (not binary gate): 0-2% → 100%, 2-5% → 75%, 5-8% → 50%, 8-20% → 25%, >20% → block. Floor at 25%.
3. **Daily RVOL** from 20-day daily bars + today's cumulative volume. One API call per candidate, cached intraday.
4. **60 bars** (1 hour of 1-min data) — up from 20. More context for entry setup detection.
5. **% gain as primary ranking** — highest gainers evaluated first.
6. **Dynamic threshold: skipped.** Fixed spread tiers are clear, testable, sufficient for paper experimentation.
7. **5-second charts: not available** via Alpaca REST (min is 1-min). WebSocket streaming is a separate future project.

---

##### 11.18.1 Task #15 — Market Data Fixes: IEX Feed, Volume Scaling, Bar Count, Bar Limit

**Files:** `src/market_data.py`

**Problem:** (a) `StockHistoricalDataClient` initialized without `feed=` parameter — defaults silently to IEX with no logging. (b) `dollar_volume_5m` uses raw IEX volume (2.5% of market) — a $7 stock shows $15K instead of ~$600K. (c) No `len(bars) >= 5` guard — if Alpaca returns 3 bars, `bars[-5:]` silently undercounts. (d) `limit=20` is arbitrary — 60 bars gives entry detection more context.

**Changes:**

(a) Explicit IEX feed on all data requests:
```python
# Context7: /alpacahq/alpaca-py — StockBarsRequest, StockSnapshotRequest, StockLatestQuoteRequest
# All accept feed= parameter. "iex" is free real-time for paper accounts.
StockBarsRequest(..., feed="iex")
StockSnapshotRequest(..., feed="iex")
StockLatestQuoteRequest(..., feed="iex")
```
Add `IEX_SCALE = 40.0` constant at module top with comment: `# ponytail: IEX ≈ 2.5% of US market volume. Scale to approximate total. Remove if SIP activated.`

(b) Scale dollar volume:
```python
# src/market_data.py:65 — derive_bar_enrichment()
dollar_volume_5m = sum(bar.close * bar.volume for bar in bars[-5:]) * IEX_SCALE if bars else None
```

(c) Bar count guard:
```python
# Guard before bars[-5:] — if fewer than 5 bars, don't undercount
if bars and len(bars) >= 5:
    dollar_volume_5m = sum(bar.close * bar.volume for bar in bars[-5:]) * IEX_SCALE
else:
    dollar_volume_5m = None  # insufficient data — hard filter will skip, not undercount
```

(d) Bar limit 20 → 60:
```python
# 4 locations: market_data.py lines ~193, ~205, ~311, ~320
limit=60  # was 20. 1 hour of 1-min context for entry setup detection.
```

**Verification:** Run paper mode, confirm LHSW (or similar top gainer) shows dollar_volume_5m ≈ $600K (40× previous). Confirm no `dollar_volume_below_min` blocks on genuinely active stocks.

**Test:** Unit test `derive_bar_enrichment()` with 3 bars → returns `None` for dollar_volume_5m. With 5+ bars → returns scaled value.

---

##### 11.18.2 Task #16 — Fetch Daily Volume from Snapshot

**Files:** `src/market_data.py`, `src/decision_pipeline.py`

**Problem:** The `StockSnapshotRequest` already returns `daily_bar` with cumulative daily volume, but the bot never extracts it. No total daily volume exists anywhere in the pipeline.

**Changes:**

(a) Extract daily volume in snapshot builders (`_snapshot_from_alpaca_snapshot` at `market_data.py:83`, `build_market_snapshot` at `market_data.py:278`):
```python
# Context7: /alpacahq/alpaca-py — Snapshot.daily_bar contains cumulative day OHLCV
daily_bar = getattr(alpaca_snapshot, "daily_bar", None)
daily_volume = getattr(daily_bar, "volume", None) if daily_bar else None
```

(b) Add `daily_volume: Optional[float]` field to `MarketSnapshot` (`decision_pipeline.py:97`).

(c) Wire `daily_volume` into snapshot construction at both paths.

**Verification:** Run paper mode, confirm `daily_volume` is populated in decision records for candidates with snapshot data.

**Test:** Unit test snapshot builder with mock Alpaca snapshot containing `daily_bar.volume = 5000000` → `snapshot.daily_volume == 5000000`.

---

##### 11.18.3 Task #17 — Compute Daily RVOL

**Files:** `src/market_data.py`, `src/scanner/attention.py`, `src/app.py`

**Problem:** `relative_volume` field exists on `Candidate` (`schemas.py:101`) and `MarketSnapshot` (`decision_pipeline.py:96`), threaded through attention scoring (25 points, `attention.py:258-260`). But it is **never computed** — always `None` in production. 25 points of attention scoring are dead code.

**Changes:**

(a) Fetch 20-day daily bars per candidate (new function in `market_data.py`):
```python
# Context7: /alpacahq/alpaca-py — StockBarsRequest with TimeFrame.Day
# Returns daily OHLCV bars. limit=20 gives 20 trading days.
def fetch_avg_daily_volume(symbol: str, api_key: str, secret_key: str, lookback: int = 20) -> Optional[float]:
    """Fetch 20-day average daily volume for RVOL computation. Cache intraday."""
    client = StockHistoricalDataClient(api_key, secret_key)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
        limit=lookback,
        feed="iex",
    )
    bars = client.get_stock_bars(req)
    symbol_bars = bars.get(symbol, []) if hasattr(bars, 'get') else []
    if not symbol_bars:
        return None
    volumes = [b.volume for b in symbol_bars if b.volume and b.volume > 0]
    return sum(volumes) / len(volumes) if volumes else None
```

(b) Compute RVOL in snapshot builder:
```python
# RVOL = today's cumulative volume / 20-day average daily volume
avg_vol = fetch_avg_daily_volume(symbol, api_key, secret_key)
if avg_vol and avg_vol > 0 and daily_volume:
    rvol = daily_volume / avg_vol
else:
    rvol = None
```

(c) Populate `snapshot.rvol` — activates 25 attention points in `attention.py:258-260`.

(d) **Cache:** The 20-day baseline doesn't change intraday. Cache per-symbol in a module-level dict with date key. Refresh once per day per symbol. `# ponytail: per-symbol daily cache, TTL=1 trading day.`

**Verification:** Run paper mode, confirm `rvol` is populated in decision records. Confirm attention scores for high-volume top gainers increase significantly (25 points activated).

**Test:** Unit test `fetch_avg_daily_volume()` with mock bars. Test RVOL computation: `daily_volume=500000, avg=100000 → rvol=5.0`.

---

##### 11.18.4 Task #18 — Replace Spread Hard Block with Tiered Position Sizing

**Files:** `src/hard_filters.py`, `src/annotations.py`, `src/decision_pipeline.py`, `config/settings.py`, `config/default_config.yaml`

**Problem:** `spread_pct > 5.0%` → hard block, hardcoded at `hard_filters.py:48-54`. Not configurable. No override for high-attention top gainers. A 250% gainer with 97 attention gets blocked the same as a 5% gainer. Professional consensus: spread is a sizing dial, not a gate.

**Changes:**

(a) Remove spread from hard filters. In `hard_filters.py:run_hard_filters()`, remove the `spread_hard_reject` block (lines ~133-134). Spread no longer hard-blocks at 5%.

(b) Add `spread_sizing_multiplier()` function (in `annotations.py` or `decision_pipeline.py`):
```python
def spread_sizing_multiplier(spread_pct: Optional[float]) -> float:
    """Tiered position sizing based on spread. Returns 0.0 = block."""
    if spread_pct is None:
        return 0.0  # missing spread = block (can't assess execution cost)
    if spread_pct <= 2.0:
        return 1.0   # full size
    elif spread_pct <= 5.0:
        return 0.75
    elif spread_pct <= 8.0:
        return 0.50
    elif spread_pct <= 20.0:
        return 0.25  # floor — wide spread top gainers still tradeable
    else:
        return 0.0   # block — too illiquid
```

(c) Wire into `execute_entry()` sizing (`decision_pipeline.py:422-433`):
```python
# Existing: soft_mult = soft_warning_multiplier(...)
# Add:
spread_mult = spread_sizing_multiplier(snapshot.spread_pct)
if spread_mult == 0.0:
    result.decision = "skip"
    result.decision_reason = f"spread_block:{spread_pct}"
    return result
combined_mult = soft_mult * spread_mult
shares, starter, adj_risk, risk_amount = entry_sizing(
    ..., soft_multiplier=combined_mult, ...
)
```

(d) Make configurable in `config/settings.py`:
```python
spread_full_size_threshold: float = 2.0
spread_block_threshold: float = 20.0
```

(e) Update `entries.py:594` (vwap_reclaim spread > 5% reject) and `entries.py:633` (scalp_reclaim spread > 3% reject) — these are **secondary entry gates**, not hard filters. Relax to match the new tiered system: vwap_reclaim allows up to 20%, scalp_reclaim allows up to 8% (scalps need tighter spreads).

**Verification:** Run paper mode. Confirm a top gainer with 6% spread now enters at 50% size instead of being blocked. Confirm >20% spread still blocks.

**Test:** Unit test `spread_sizing_multiplier()`: 1% → 1.0, 3% → 0.75, 6% → 0.50, 15% → 0.25, 25% → 0.0, None → 0.0.

---

##### 11.18.5 Task #19 — Increase % Gain Weight in Attention Scoring

**Files:** `src/scanner/attention.py`

**Problem:** `percent_gain` is one factor among many in attention scoring. For a top-gainer bot, it should be the primary driver. The highest % gainers should rank first.

**Changes:**

(a) Review current attention weights in `attention.py` (30/40/30 split per task #4). Increase the `% gain` component weight so a 250% gainer scores significantly higher than a 30% gainer.

(b) Ensure candidates are ranked by attention score descending before pipeline processing — highest gainers evaluated first. This may already exist; verify in `app.py` scan loop.

**Verification:** Run paper mode. Confirm a 200% gainer with weak volume scores higher than a 30% gainer with strong volume. Confirm scan processes highest-gain candidates first.

**Test:** Unit test attention scoring with `percent_gain=250` vs `percent_gain=30` — 250% should score significantly higher.

---

##### 11.18.6 Task #20 — Log % Gain and RVOL in Decision Records

**Files:** `src/models/schemas.py`, `src/decision_pipeline.py`, `src/journal/decision_logger.py`

**Problem:** Skip decisions don't log `percent_gain` or `rvol`. The user can't see why a 250% gainer was skipped or what its volume profile was. Bad data visibility = bad debugging.

**Changes:**

(a) Add `percent_gain: Optional[float]` and `rvol: Optional[float]` fields to `DecisionRecord` (`schemas.py:301`).

(b) Populate from `candidate.percent_gain` and `snapshot.rvol` in `to_decision_record()` or equivalent.

(c) Confirm `decisions.jsonl` entries include these fields for ALL candidates (enter, watch, skip).

**Verification:** Run paper mode. Read `data/decisions.jsonl` — confirm every entry has `percent_gain` and `rvol` fields.

**Test:** Unit test `to_decision_record()` includes both fields.

---

#### 11.18.7 Dependency Order

```
#15 (market data fixes)     ──┐
#16 (daily volume)          ──┤
#17 (RVOL computation)      ──┤── depends on #16 (daily_volume)
#18 (spread tiered sizing) ──┤
#19 (% gain weight)         ──┤
#20 (log % gain + RVOL)     ──┘── depends on #17 (rvol populated)
```

**Parallelizable:** #15, #16, #18, #19 can run in parallel (no write conflicts — different files).
**Sequential:** #17 after #16. #20 after #17.

---

#### 11.18.8 What This Phase Does NOT Do (Deferred)

- **5-second charts:** Not available via Alpaca REST. WebSocket streaming is a separate project.
- **Intraday time-of-day RVOL:** Requires 20 days × intraday bars per candidate — too many API calls for free tier. Daily RVOL is the start. Upgrade later with caching.
- **Dynamic spread threshold (volatility-relative):** Skipped. Fixed tiers are clear, testable, sufficient for paper experimentation. Add later if fixed tiers prove too rigid.
- **SIP feed ($99/mo):** Deferred to v0.6 per SOUL.md. IEX × 40 scaling is the interim solution.
- **MostActivesRequest (volume-ranked movers):** Alpaca movers endpoint doesn't provide volume. `MostActivesRequest` does, but it's a different list (most active, not top gainers). Could supplement scanner later.

---

#### 11.18.9 Research Sources

**Codebase (verified file:line):** `src/market_data.py:65,179,279,193,205,311,320` · `src/hard_filters.py:48-54,133-134` · `src/scanner/attention.py:258-260` · `src/scanner/scanner.py:73-83` · `src/models/schemas.py:101` · `src/decision_pipeline.py:96,422-433` · `src/entries.py:594,633`

**Alpaca API (Context7 `/alpacahq/alpaca-py`):** `StockBarsRequest` with `TimeFrame.Day` for daily bars; `StockSnapshotRequest` returns `daily_bar` with cumulative volume; `feed="iex"` parameter on all data requests; IEX is free real-time for paper accounts.

**Professional trading research:** Avramov, Cheng, Hameed (2016) — momentum profits larger in liquid markets; Korajczyk & Sadka (2004) — liquidity-weighted momentum; Lesmond et al. (2004) — momentum returns concentrated in high-cost stocks; Pomegra — spread-adjusted position sizing tiers; Trade Ideas — RVOL uses 30-day 15-min interval baseline; TOSindicators — RVOL 1.5-2.0 has best risk-adjusted returns.

**Consensus:** RVOL is the #1 signal. Spread is a sizing dial, not a gate. Wide spreads on high-RVOL top gainers are tradeable at reduced size.

**Phase 1 — Paper Truth / Data Integrity.** Completed core data-integrity hardening: batch snapshots provide multi-bar data, runner ATR receives 5-min bars, broker stops/orders reconcile, and P&L checkpoints persist. Next: run paper/replay validation before changing thresholds.

**Phase 2 — Edge Calibration.** Completed current edge-calibration items: attention weights/RVOL/HOD/ROC, dynamic scanner source, classifier polarity, halt handling, time-flatten ordering, and 5-min runner confirmation. Dashboard/report work is nonessential unless later desired.

**Phase 3 — Live Safety Hardening.** Completed current safety-hardening items: broker stop sync, broker order reconciliation, periodic P&L checkpointing, ERROR cleanup, and weekly drawdown / consecutive-loss throttle. Before tiny live size: paper/replay evidence and any newly discovered broker-truth gaps.

---

#### 11.17.15 Executed Trade Ledger / Fill Log

Implemented. Runtime component construction wires `TradeLedger("data/executed_trades.jsonl")` into the execution gateway. The ledger is JSONL and appends one concise record per confirmed fill, separate from `DecisionLogger` decision/audit records and separate from `PnLLedger` risk-throttle persistence.

Logged when available: `event` (`entry_fill` / `add_fill` / `exit_fill`), `symbol`, `side`, fill confirmation time, entry/exit order id, fill price, filled quantity, current/remaining shares, realized P&L, win/loss/breakeven, R multiple when original per-share risk is known for the exited shares, entry setup, exit reason, and intended risk. Alpaca records use broker-confirmed `filled_avg_price` / `filled_qty`; paper records use the simulated confirmed prices passed through the execution path. No metrics are fabricated when prices are unavailable, and combined positions after scale-in defer R multiple until combined-risk tracking exists.

Deferred by design: analytics dashboard/reporting, complex database, ML/stat layer, generic market metadata, and MAE/MFE unless later captured cheaply from already-monitored bars/prices.

---

#### 11.17.16 Research Sources

**Practitioner:** Warrior Trading / Ross Cameron (stock selection PDF, top-gainer methodology, RVOL/float thresholds); Bulls on Wall Street; Tim Grittani (scanner methodology, small-ticket trading, per-setup discipline); Timothy Sykes / Grittani (risk-per-trade framework); Mark Minervini (SEPA / VCP summaries); TradeSim (position-sizing simulation); Investopedia (reference).

**Academic:** Gao, Han, Li & Zhou — Market Intraday Momentum (1/3/5-min ROC persistence); Baltussen et al. — cross-asset momentum; Li, Sakkas & Urquhart — intraday momentum profitability; Heston, Korajczyk & Sadka — volatility and momentum efficiency; Rosa — news-driven momentum decay; Herberger, Horn & Oehler — retail vs institutional momentum capture; Su, Huang & Hsu — stop-loss and trailing-stop efficacy.

**Technical / API:** Context7 Alpaca-py docs (`/alpacahq/alpaca-py`); Alpaca StockSnapshotRequest batch API; Alpaca calendar and clock API; Alpaca order lifecycle documentation.

**GitHub / Open-source:** `alpacahq/alpaca-py`; `alpacahq/Momentum-Trading-Example`; `lit26/finvizfinance`; `oscar0812/pyfinviz`; `ryanJHamby/stock-screener`; `Sensible-Analytics/qullamaggie_scanner`; `luram94/momentum_trader`; `quantive3/vwap-reclaim`; `azseza/smallfish_`; `Snack-JPG/quantflow`.

**Scope note:** The audit did not inspect `.env` (credentials, keys, secrets) or any other private configuration files. All claims are based on public source code, configuration defaults, and verified API documentation only.

---

## 11.19 Execution & Broker Sync Hardening — "Live-Truth Architecture"

**Date:** 2026-07-07

**Trigger:** Deep-dive research (5 parallel lanes: execution pipeline, broker communication, Alpaca-py best practices, WebSocket cost/benefit, mental-mode alignment) revealed critical execution bugs that would cause real money loss in live mode. The data pipeline is solid (§11.18), runner capture infrastructure exists end-to-end, but the execution layer has sync gaps, P&L corruption, and no periodic broker reconciliation.

**Mental mode:** Ponytail — lean code, minimum change that fixes each bug correctly. No overengineering, no unneeded abstractions, no speculative tests. Each task is the smallest correct fix. Mark `[x]` when complete.

**Research basis:** 5 subagent lanes (2 explorer, 2 librarian, 1 oracle). Codebase findings verified at file:line. Alpaca API verified via Context7 (`/alpacahq/alpaca-py`). Professional trading research via web (30+ sources).

---

### Phase A — Critical Execution Fixes (before any more paper trading)

These bugs would lose real money in live mode. Fix first.

---

#### 11.19.0 Task #21a — Schema prerequisites (MUST be first)

**Files:** `src/models/schemas.py`, `src/paper_execution.py`

**Problem:** Tasks #22, #27, #32 reference fields that don't exist on `PositionStateModel` (`schemas.py:201-222`). The code won't compile without these schema changes.

**Fix:** Add three fields to `PositionStateModel`:

```python
# schemas.py — PositionStateModel
pending_order_id: Optional[str] = None    # Task #22: track entry/add order for fill confirmation
entry_setup: Optional[str] = None         # Task #32: pass setup to exit engine
# created_at: use existing opened_at field (set at submit_entry time)
```

Populate `pending_order_id` and `entry_setup` in `submit_entry()` (`paper_execution.py:300-310`) when the position is created.

**Verification:** Unit test: create position, confirm fields exist and are populated.

- [ ] Complete

---

#### 11.19.1 Task #21 — Prevent double-exit on confirm_fill failure

**Files:** `src/app.py`

**Problem:** `app.py:1095-1116` — Exit submitted → `confirm_exit_fill()` raises (network blip) → position stays EXITING → EXITING timeout expires (`app.py:909-916`) → escalates to UNPROTECTED → next cycle P4 fires → **second exit submitted on already-closed position → SHORT SELL**. Losses increase without bound.

**Fix:** Track confirmed exit order IDs. Don't re-submit exit while one is pending.

```python
# ponytail: track pending exit order_id per symbol, prevent re-submission
# In _monitor_positions(), before submit_exit:
if pos.symbol in self._pending_exit_orders:
    pending_id = self._pending_exit_orders[pos.symbol]
    # Check if it filled at broker before re-submitting
    try:
        order = self._execution.client.get_order_by_id(pending_id)
        if str(order.status) in ("filled", "partially_filled"):
            # Exit already happened — process it
            self._execution.confirm_exit_fill(pending_id)
            del self._pending_exit_orders[pos.symbol]
            return
        # Still pending — don't re-submit
        return
    except Exception:
        pass  # Can't verify — don't re-submit, wait

# After submit_exit:
self._pending_exit_orders[pos.symbol] = order.order_id
# On successful confirm_exit_fill:
self._pending_exit_orders.pop(pos.symbol, None)
```

Add `self._pending_exit_orders: dict[str, str] = {}` to `__init__`.

**Verification:** Paper mode. Simulate confirm_exit_fill failure. Confirm no second exit submitted.

- [ ] Complete

---

#### 11.19.2 Task #22 — Defer confirm_fill to monitor loop (entry + add)

**Files:** `src/decision_pipeline.py`, `src/app.py`

**Problem:** `decision_pipeline.py:479-486` — `submit_entry()` places LIMIT order → immediately calls `confirm_fill()` → order is still pending (LIMIT orders don't fill instantly) → `protect_position()` fails (position not OPEN) → `mark_unprotected()` → position UNPROTECTED with no stop, even though order is alive. Same for `submit_add()` at `app.py:1031` — zombie ADD orders.

This is the AXTL bug. The §11.18 fix (allow PENDING_ENTRY in mark_unprotected) was cosmetic — stopped the crash but position still ends up unprotected.

**Fix:** Don't call `confirm_fill()` in the same cycle as submit. The monitor loop already polls pending orders. Let it handle fill confirmation.

```python
# decision_pipeline.py execute_entry() — remove confirm_fill call
# Keep submit_entry, remove the immediate confirm_fill + protect_position block
# Position stays PENDING_ENTRY. Monitor loop confirms fill next cycle.
# When fill confirmed → transition to OPEN → place stop.

# app.py _monitor_positions() — add PENDING_ENTRY handling:
for pos in self._positions.all_open():
    if pos.state == PositionState.PENDING_ENTRY:
        try:
            self._execution.confirm_fill(pos.pending_order_id)
            # confirm_fill transitions to OPEN on fill
            # Then protect_position on next cycle when state is OPEN
        except Exception:
            logger.debug("Pending entry {} not yet filled", pos.symbol)
```

**Note:** `pending_order_id` field must be added to `PositionStateModel` — see Task #21a (schema prerequisite, MUST be done first).

**Verification:** Paper mode. Submit entry on a LIMIT order. Confirm position stays PENDING_ENTRY (not UNPROTECTED) until fill. Confirm stop placed after fill confirmed.

- [ ] Complete

---

#### 11.19.3 Task #23 — Fix realized_pnl accumulation (partial exits)

**Files:** `src/paper_execution.py`

**Problem:** `paper_execution.py:548-549` — `pos.realized_pnl = (exit_price - avg_entry) * exit_qty` uses `=` not `+=`. Each partial exit overwrites the previous. After 3 partial exits, only the last one's P&L is stored. Cumulative P&L destroyed.

**Fix:** Use `+=`.

```python
# paper_execution.py:548-549 (Paper gateway)
exit_pnl = (exit_price - pos.average_entry) * exit_qty
pos.realized_pnl = (pos.realized_pnl or 0.0) + exit_pnl

# Same fix in Alpaca gateway confirm_exit_fill() at ~line 1117-1118
```

**Verification:** Paper mode. Partial exit (33%), then another partial exit. Confirm `pos.realized_pnl` is the sum, not just the last exit.

- [ ] Complete

---

#### 11.19.4 Task #24 — Fix double-count of partial exit P&L in risk state

**Files:** `src/app.py`

**Problem:** `app.py:659-661` — `realized = session_realized_pnl + sum(pos.realized_pnl for open_positions)`. The session P&L already includes partial exits (via `_record_realized_trade_pnl`), but `pos.realized_pnl` for OPEN positions still holds the partial value → counted twice → inflated realized P&L → wrong risk caps.

**Fix:** Don't double-count. `pos.realized_pnl` tracks partial exits for OPEN positions. `session_realized_pnl` tracks closed positions. They're complementary, not additive.

```python
# app.py:659-661 — fix
realized = self._session_realized_pnl + sum(
    pos.realized_pnl or 0.0 for pos in open_positions
    if pos.state in (PositionState.OPEN, PositionState.RUNNER, 
                     PositionState.ADDING, PositionState.UNPROTECTED)
)
# CLOSED positions' P&L is already in session_realized_pnl
# Only add OPEN positions' partial exit P&L
```

**Verification:** Paper mode. Partial exit on a position. Confirm risk state `realized` doesn't double-count.

- [ ] Complete

---

#### 11.19.5 Task #25 — Add periodic position reconciliation in loop

**Files:** `src/app.py`

**Problem:** `_reconcile_on_startup()` runs once. The loop (`app.py:760-767`) only reconciles open orders (every 30s), never positions. If broker closes a position (stop hit, liquidation, manual), the bot holds a phantom position forever → wrong sizing, wrong risk caps.

**Fix:** Call `reconcile_positions()` (module-level function in `paper_execution.py:1213`) periodically in the loop. Apply the returned actions.

```python
# app.py — add to loop, alongside order reconciliation
# ponytail: reuse existing module function, apply returned actions
from src.paper_execution import reconcile_positions

if now - self._last_position_reconcile >= self._position_reconcile_interval:
    try:
        broker_snapshot = self._broker_snapshot_fn()
        actions = reconcile_positions(
            broker_positions=broker_snapshot,
            local_store=self._positions,
            pending_store=self._execution.pending,
        )
        # Apply actions (same pattern as _reconcile_on_startup)
        for action in actions:
            self._apply_reconciliation_action(action)
        self._last_position_reconcile = now
    except Exception:
        logger.warning("Periodic position reconciliation failed")
```

Add `self._last_position_reconcile = 0` and `self._position_reconcile_interval = 60.0` to `__init__`.

**Verification:** Paper mode. Manually close a position at Alpaca. Confirm bot detects and reconciles within 60s.

- [ ] Complete

---

#### 11.19.6 Task #26 — Add SDK timeout on all broker API calls

**Files:** `src/paper_execution.py`

**Problem:** `submit_order()`, `get_order_by_id()`, `get_all_positions()` all block forever on network partition. The entire bot freezes — no monitoring, no exits, no protection.

**Fix:** Wrap broker calls with timeout. Alpaca SDK doesn't accept timeout directly — use `concurrent.futures`.

```python
# ponytail: one helper, used everywhere
import concurrent.futures

def _call_with_timeout(fn, *args, timeout=10.0, **kwargs):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(f"Broker API call timed out after {timeout}s")

# Usage:
order = _call_with_timeout(self.client.submit_order, req, timeout=10.0)
```

Apply to: `submit_order`, `get_order_by_id`, `get_all_positions`, `get_orders`, `cancel_order_by_id`, `get_account`.

**Verification:** Paper mode. Simulate network delay. Confirm bot doesn't freeze — raises RuntimeError, caught by exception handlers.

- [ ] Complete

---

#### 11.19.7 Task #27 — Add pending order timeout escalation

**Files:** `src/app.py`, `src/paper_execution.py`

**Problem:** A LIMIT entry order can stay PENDING_ENTRY forever if it never fills and never rejects. No cleanup. Same for pending ADD orders. The position occupies a slot, blocks new entries (max_positions), and never resolves.

**Fix:** Track submission time. Escalate to ERROR after timeout.

```python
# app.py _monitor_positions() — add PENDING_ENTRY timeout
PENDING_TIMEOUT = 300.0  # 5 minutes

for pos in self._positions.all_open():
    if pos.state == PositionState.PENDING_ENTRY:
        # ponytail: use opened_at (set at submit_entry time), not non-existent created_at
        age = (now - pos.opened_at).total_seconds() if pos.opened_at else 0
        if age > PENDING_TIMEOUT:
            # Cancel the order at broker, transition to ERROR
            try:
                if pos.pending_order_id:
                    self._execution.cancel_order(pos.pending_order_id)
            except Exception:
                pass
            pos.state = PositionState.ERROR
            self._positions.upsert(pos)
            logger.warning("Pending entry {} timed out after {}s — escalated to ERROR", 
                          pos.symbol, age)
```

**Verification:** Paper mode. Submit a LIMIT order far from market. Confirm it escalates to ERROR after 5 min.

- [ ] Complete

---

### Phase B — Strategic Alignment

These fix the gap between SOUL.md's mental mode and code reality.

---

#### 11.19.8 Task #28 — Revert % gain cap to 25% (or sqrt curve)

**Files:** `src/scanner/attention.py`

**Problem:** `attention.py:44` — `_PRICE_NORMALIZATION_CAP_PCT` changed 25→100 in task #19. A 50% gainer now scores 15 price-attention points instead of 30. For a bot whose thesis is "top gainers are candidates first," this weakens the primary signal for everyday top gainers (20-80%). The 100% cap creates false differentiation — 250% gainers already scored max at 25% cap.

**Fix:** Revert to 25%.

```python
# attention.py:44
_PRICE_NORMALIZATION_CAP_PCT = 25.0  # ponytail: 25% cap — 25%+ gainers get full price points
```

**Alternative (if user wants differentiation above 25%):** Use sqrt curve:
```python
# Non-linear: 25% → 30pts, 100% → 30pts, 250% → 30pts (capped)
# But 50% gets more than 15pts
price_pts = min(30.0, 30.0 * (percent_gain / 25.0) ** 0.5)
```

**Verification:** Paper mode. Confirm 50% gainer scores ~30 price points (not 15). Confirm 250% gainer still scores 30.

- [ ] Complete

---

#### 11.19.9 Task #29 — Update SOUL.md Current Reality

**Files:** `SOUL.md`

**Problem:** SOUL.md §Current Reality claims runner capture and scaling-in are "unimplemented" and "deferred." They ARE implemented (`runner.py`, `app.py:982-1080`, `app.py:1131-1226`). This misleads future agents and undermines the doc's authority.

**Fix:** Update §Current Reality to reflect v0.5 implementation.

```markdown
## Current Reality (honest gaps)

- **Runner capture IS implemented.** `should_promote_to_runner()`, `_promote_to_runner()`, 
  `_update_runner_trail()`, `check_runner_trail()` all wired. ATR Chandelier trailing active.
  BUT: MoveState ACTIVE required for promotion — classifier biased to EARLY (see gap below).
- **Scaling-in IS implemented.** `should_add_to_runner()`, `add_sizing()`, `submit_add()`, 
  full add lifecycle with re-protection. BUT: immediate confirm_fill after submit_add 
  creates zombie orders (Task #22).
- **yfinance fallback is static watchlist** — still true.
- **News/catalyst awareness unwired** — still true.
- **Sizing can crush to trivial size** — partially mitigated (float penalty reduced, 
  spread tiered sizing added).
- **Paper-mode realism gaps** — no holiday calendar, no pre-market, 60s poll.
- **MoveState ACTIVE bias blocks runner promotion** — classifier needs 3/5 signals, 
  fresh top gainers rarely reach ACTIVE. See Task #30.
- **% gain cap regression** — if not reverted (Task #28).
```

**Verification:** Read SOUL.md. Confirm no false claims about unimplemented features.

- [ ] Complete

---

#### 11.19.10 Task #30 — Relax runner promotion gate for high-R positions

**Files:** `src/runner.py`

**Problem:** `runner.py:108-111` — Runner promotion requires MoveState ACTIVE. ACTIVE needs 3/5 signals (`move_classifier.py:302-337`: higher_low_structure, pullbacks_bought, strong_volume, spread_ok, stop_in_range). Fresh top gainers with 20 bars rarely reach ACTIVE → never promote → "catch runners" thesis blocked by the classifier.

**Fix:** Allow promotion at +2R from EARLY state when volume + R-multiple are strong. Keep VWAP check unconditionally — a stock below VWAP is not a runner.

```python
# runner.py should_promote_to_runner() — relax ACTIVE requirement
# ponytail: high R-multiple + volume compensates for missing structure, but VWAP is non-negotiable
if pos.state != PositionState.OPEN:
    return False

r_multiple = compute_r_multiple(pos)
if r_multiple < 1.5:
    return False

# VWAP check — keep unconditionally (stock below VWAP is not a runner)
if vwap is not None and pos.current_price < vwap:
    return False

# Existing: requires move_state == ACTIVE
# New: allow EARLY if r_multiple >= 2.0 and rvol >= 2.0
if move_state == MoveState.ACTIVE:
    return True
elif move_state == MoveState.EARLY and r_multiple >= 2.0:
    # High R compensates for missing structure
    if rvol is not None and rvol >= 2.0:
        return True
    return False
return False
```

**Verification:** Paper mode. Enter a trade that reaches +2R with strong volume but EARLY state. Confirm promotion to RUNNER.

- [ ] Complete

---

#### 11.19.11 Task #31 — Fix hard stop blind gap (15-60s quotes)

**Files:** `src/exits.py`

**Problem:** `exits.py:163-166` — `check_hard_stop()` requires `quote_age_seconds <= 15`. If quote is 16-60s old, hard stop check is skipped entirely. Between 15s and 60s, the bot won't exit even if price is below stop. Only P1 emergency (>60s) catches it.

**Fix:** Use fallback pricing when quote is stale but bars are available.

```python
# exits.py check_hard_stop() — use bar close as fallback price
# ponytail: stale quote → use latest bar close, not skip the check
if quote_age_seconds is not None and quote_age_seconds <= 15.0:
    price = current_price
elif bars and len(bars) > 0:
    price = bars[-1].close  # fallback: latest bar close
    logger.debug("Hard stop check using bar close (quote age {}s)", quote_age_seconds)
else:
    return None  # no price data at all — can't check

if price is not None and stop_price is not None and price <= stop_price:
    return ExitDecision(reason="hard_stop", ...)
```

**Verification:** Paper mode. Stale quote (>15s) with price below stop. Confirm exit fires.

- [ ] Complete

---

#### 11.19.12 Task #32 — Pass entry_setup through monitor path to exit engine

**Files:** `src/app.py`

**Problem:** `app.py:949` — `evaluate_exits()` called with `entry_setup=None`. P3b invalidation (setup-specific) and P5 scale-out (scalp mode) don't have setup context → scalp_reclaim fast-exit logic never fires.

**Fix:** Store entry_setup on the position (Task #21a adds the field), pass it to evaluate_exits.

```python
# ponytail: read entry_setup from position (populated at entry time per Task #21a)
# app.py _monitor_positions():
entry_setup = pos.entry_setup  # read from position
result = evaluate_exits(pos, ..., entry_setup=entry_setup, ...)
```

**Verification:** Paper mode. Enter scalp_reclaim setup. Confirm P5 scalp mode exit logic fires.

- [ ] Complete

---

#### 11.19.13 Task #33 — Tighten stop after partial scale-out

**Files:** `src/exits.py`, `src/paper_execution.py`

**Problem:** `exits.py:293` — After selling 33% at 1R, the new stop for remaining shares uses `pos.stop_price` (original), not a tightened trailing stop. Profit-taking should ratchet the stop up.

**Fix:** After partial exit, ratchet stop to breakeven or better.

```python
# paper_execution.py confirm_exit_fill() — after partial exit
# ponytail: ratchet stop to max(current_stop, entry_price) after partial profit
if pos.state in (PositionState.OPEN, PositionState.RUNNER) and pos.current_shares > 0:
    new_stop = max(pos.stop_price or 0.0, pos.average_entry or 0.0)
    if new_stop > (pos.stop_price or 0.0):
        pos.stop_price = new_stop
        # Re-place stop at broker
        self.cancel_stale_orders(pos.symbol)
        self.place_stop(pos.symbol, new_stop, pos.current_shares)
```

**Verification:** Paper mode. Partial exit at 1R. Confirm stop ratcheted to breakeven.

- [ ] Complete

---

### Phase C — Live Readiness

These are needed before going live with real money.

---

#### 11.19.14 Task #34 — Add periodic position state persistence

**Files:** `src/app.py`

**Problem:** `app.py:718-731` — Positions saved on shutdown only. If the bot crashes (SIGKILL, power loss), all open positions since startup are lost. P&L ledger checkpoints every 45s, but position state doesn't.

**Fix:** Save positions periodically.

```python
# app.py loop — add periodic save
# ponytail: reuse existing save_to_disk, just call it periodically
if now - self._last_position_save >= 30.0:
    try:
        self._positions.save_to_disk("data/positions.json")
        self._last_position_save = now
    except Exception:
        logger.warning("Periodic position save failed")
```

Add `self._last_position_save = 0.0` to `__init__`.

**Verification:** Paper mode. Open a position. Kill the bot (SIGKILL). Restart. Confirm position restored.

- [ ] Complete

---

#### 11.19.15 Task #35 — Check market session before order submission

**Files:** `src/app.py`, `src/paper_execution.py`

**Problem:** `app.py:282-320` — `_is_market_open()` exists but only used for scan suppression. Broker orders submitted even if market is closed. Alpaca rejects them, but local state transitions still happen → phantom PENDING_ENTRY.

**Fix:** Check market session before submit_entry, submit_add, submit_exit.

```python
# app.py — before each submit call
# ponytail: one guard, three call sites
if not self._is_market_open():
    logger.warning("Market closed — skipping order submission for {}", symbol)
    return None
```

**Verification:** Paper mode (off-hours). Confirm no orders submitted when market closed.

- [ ] Complete

---

#### 11.19.16 Task #36 — Add client_order_id for idempotent order submission

**Files:** `src/paper_execution.py`

**Problem:** No `client_order_id` on order submissions. On network timeout, can't safely retry — might create duplicate orders.

**Fix:** Generate unique client_order_id per order.

```python
# ponytail: uuid-based client_order_id, idempotent retry
import uuid

def _client_order_id(self, prefix: str, symbol: str) -> str:
    return f"{prefix}_{symbol}_{uuid.uuid4().hex[:8]}"

# In submit_entry:
req = LimitOrderRequest(
    ...,
    client_order_id=self._client_order_id("entry", symbol),
)
```

**Context7:** `/alpacahq/alpaca-py` — `client_order_id` enables idempotent retry. Re-submitting same ID = no-op.

**Verification:** Paper mode. Submit order. Confirm `client_order_id` appears in Alpaca dashboard.

- [ ] Complete

---

#### 11.19.17 Task #37 — Separate realized vs unrealized in daily loss cap

**Files:** `src/app.py`

**Problem:** `app.py:662-665` — Daily loss cap includes unrealized P&L. A position in temporary drawdown trips the kill switch prematurely, blocking new entries even though the position may recover.

**Fix:** Use realized-only for the hard cap (both daily and per-symbol). Use realized + unrealized for a softer caution.

```python
# app.py _build_risk_state()
# ponytail: realized = money actually lost. unrealized = paper drawdown.
realized_daily = self._session_realized_pnl
unrealized_daily = sum(pos.unrealized_pnl or 0.0 for pos in open_positions)

# Hard cap: realized only (daily)
if realized_daily < -equity * max_daily_loss_pct:
    daily_loss_breached = True

# Per-symbol cap: realized only
per_symbol_realized = dict(self._session_per_symbol_pnl)  # realized only, no unrealized
for sym, loss in per_symbol_realized.items():
    if loss < -equity * max_daily_loss_pct:
        per_symbol_loss_capped = True

# Soft caution: realized + unrealized (logs warning, doesn't block)
if realized_daily + unrealized_daily < -equity * max_daily_loss_pct:
    logger.warning("Daily P&L (incl unrealized) below threshold — caution")
```

**Verification:** Paper mode. Open position in drawdown. Confirm kill switch doesn't trip on unrealized only.

- [ ] Complete

---

#### 11.19.18 Task #38 — Fix R-multiple computation for scaled positions

**Files:** `src/paper_execution.py`

**Problem:** `paper_execution.py:258-264` — `if pos.add_count == 0` — R-multiple never computed for scaled positions. Performance tracking is blind to scaled-in trades.

**Fix:** Compute R-multiple using weighted average risk.

```python
# ponytail: use combined risk_per_share after adds
if pos.original_risk_per_share and pos.original_risk_per_share > 0:
    # For scaled positions, use the current weighted risk_per_share
    risk_per_share = pos.risk_per_share or pos.original_risk_per_share
    r_multiple = round(realized_pnl / (risk_per_share * filled_qty), 2)
else:
    r_multiple = None
```

**Verification:** Paper mode. Scale into a position. Exit. Confirm R-multiple computed.

- [ ] Complete

---

#### 11.19.19 Task #38a — Fix cancel/place race in stop updates

**Files:** `src/app.py`, `src/paper_execution.py`

**Problem:** `_sync_runner_stop_to_broker()` at `app.py:1210-1211` cancels old stop BEFORE placing new one. Between cancel and place, the position has zero stop protection at broker. If the stock gaps down in that window, the position is completely unprotected.

**Fix:** Place new stop FIRST, then cancel old one. Alpaca allows multiple stops on the same symbol.

```python
# ponytail: place new before cancelling old — never unprotected
# app.py _sync_runner_stop_to_broker():
# OLD: cancel_stale_orders → place_stop (race window)
# NEW: place_stop → cancel_stale_orders (always protected)

self._execution.place_stop(symbol, new_stop_price, shares)  # place new FIRST
self._execution.cancel_stale_orders(symbol, except_order=new_stop_id)  # cancel old, keep new
```

**Verification:** Paper mode. Update trailing stop. Confirm new stop placed before old cancelled. Confirm no unprotected window.

- [ ] Complete

---

#### 11.19.20 Task #38b — Verify partial-fill cancel succeeded

**Files:** `src/paper_execution.py`

**Problem:** `confirm_fill()` at `paper_execution.py:808-814` cancels unfilled remainder best-effort. If cancel fails and remainder fills later, the bot never knows → shares undercounted → wrong sizing, wrong risk caps.

**Fix:** After cancel request, verify the order is actually cancelled.

```python
# ponytail: verify cancel, don't assume
# paper_execution.py confirm_fill() — after partial fill cancel attempt:
try:
    self.client.cancel_order_by_id(order_id)
    # Verify it's actually cancelled
    order = self.client.get_order_by_id(order_id)
    if str(order.status) not in ("canceled", "cancelled"):
        logger.warning("Cancel of %s order %s did not confirm — status: %s", 
                      symbol, order_id, order.status)
        # Position may have more shares than local state tracks
        # Flag for reconciliation
except Exception:
    logger.warning("Cancel of unfilled %s order %s failed — continuing", symbol, order_id)
```

**Verification:** Paper mode. Simulate partial fill. Confirm cancel verified or warning logged.

- [ ] Complete

---

#### 11.19.21 Task #38c — Persist pending orders to disk

**Files:** `src/state_machine.py`, `src/app.py`

**Problem:** `PendingOrderStore` is never serialized. After crash, rebuilt from broker `get_orders()` but `_pending_order_from_alpaca_order()` is lossy — ADD orders classified as ENTRY (both are `side="buy"`, `type="limit"`).

**Fix:** Persist `PendingOrderStore` alongside `PositionStore`.

```python
# state_machine.py PendingOrderStore — add save/load
# ponytail: same pattern as PositionStore
def save_to_disk(self, path: str) -> None:
    data = [o.model_dump() for o in self._orders.values()]
    Path(path).write_text(json.dumps(data, default=str))

def load_from_disk(self, path: str) -> None:
    # ... load and restore

# app.py — periodic save alongside positions
if now - self._last_position_save >= 30.0:
    self._positions.save_to_disk("data/positions.json")
    self._execution.pending.save_to_disk("data/pending_orders.json")  # ADD THIS
```

**Verification:** Paper mode. Submit entry order. Kill bot. Restart. Confirm pending order restored with correct `order_type` (ENTRY, not misclassified).

- [ ] Complete

---

#### 11.19.22 Task #38d — Refresh equity periodically

**Files:** `src/app.py`

**Problem:** `_require_alpaca_account_equity()` at `main.py:147-155` fetches equity ONCE at startup. Never refreshed. A drawn-down account continues to size positions as if it had the initial equity → over-positioned → losses amplify.

**Fix:** Refresh equity periodically in the loop.

```python
# app.py — add to loop
# ponytail: refresh equity every 60s, update self._equity
if now - self._last_equity_refresh >= 60.0:
    try:
        self._equity = self._execution.get_account_equity()
        self._last_equity_refresh = now
    except Exception:
        logger.warning("Equity refresh failed — using stale value")
```

Add `self._last_equity_refresh = 0.0` to `__init__`. Add `get_account_equity()` method to gateway if not exists.

**Verification:** Paper mode. Confirm equity refreshed every 60s in logs.

- [ ] Complete

---

#### 11.19.23 Task #38e — Rebuild risk state after each entry in scan cycle

**Files:** `src/app.py`

**Problem:** `_build_risk_state()` called once at start of `_scan_and_process()` (`app.py:1255`). If candidate A enters a position, candidates B and C still see old risk state (wrong `open_position_count`, wrong `total_open_risk`). Can exceed `max_positions` and `max_open_risk_pct` within one cycle.

**Fix:** Rebuild risk state after each successful entry.

```python
# app.py _scan_and_process() — after entry
# ponytail: one line, prevents over-positioning in batch
if result.decision == "enter":
    self._risk_state = self._build_risk_state()
```

**Verification:** Paper mode. Multiple candidates in one scan cycle. Confirm max_positions not exceeded.

- [ ] Complete

---

#### 11.19.24 Task #38f — Add valid state transitions for force=True paths

**Files:** `src/state_machine.py`

**Problem:** `mark_unprotected()` at `paper_execution.py:604` uses `force=True` for `EXITING → UNPROTECTED` transition. But `EXITING → UNPROTECTED` is NOT in the valid transitions table (`state_machine.py:46`: `EXITING: {CLOSED, ERROR}`). `force=True` masks real bugs.

**Fix:** Add the missing transitions to the valid table.

```python
# state_machine.py — add missing transitions
# ponytail: make force=True unnecessary, let the state machine validate
PositionState.EXITING: {PositionState.CLOSED, PositionState.ERROR, PositionState.UNPROTECTED},
PositionState.PENDING_ENTRY: {PositionState.OPEN, PositionState.CLOSED, PositionState.ERROR, 
                              PositionState.UNPROTECTED},
```

**Verification:** Unit test: `EXITING → UNPROTECTED` without `force=True` succeeds.

- [ ] Complete

---

#### 11.19.25 Task #38g — Extend pending timeout to ADDING state

**Files:** `src/app.py`

**Problem:** Task #27 handles PENDING_ENTRY timeout but not ADDING state. If an ADD order never fills, the position stays in ADDING forever — blocks other operations (new adds, exit promotion).

**Fix:** Extend Task #27's timeout to cover ADDING state.

```python
# app.py _monitor_positions() — extend PENDING_ENTRY timeout to ADDING
# ponytail: same pattern, different state
for pos in self._positions.all_open():
    if pos.state in (PositionState.PENDING_ENTRY, PositionState.ADDING):
        age = (now - pos.opened_at).total_seconds() if pos.opened_at else 0
        if age > PENDING_TIMEOUT:
            if pos.pending_order_id:
                try:
                    self._execution.cancel_order(pos.pending_order_id)
                except Exception:
                    pass
            # ADDING → back to RUNNER (the state before add was attempted)
            target_state = PositionState.RUNNER if pos.state == PositionState.ADDING else PositionState.ERROR
            pos.state = target_state
            self._positions.upsert(pos)
            logger.warning("Pending {} {} timed out after {}s — escalated to {}", 
                          pos.state.value, pos.symbol, age, target_state.value)
```

**Verification:** Paper mode. Submit ADD order far from market. Confirm it escalates to RUNNER after 5 min.

- [ ] Complete

---

#### 11.19.26 Task #38h — Wire or remove lunch window dead code

**Files:** `src/hard_filters.py`, `src/annotations.py`

**Problem:** `is_lunch_window()` at `hard_filters.py:382-384` is defined (11:30-14:00 ET) but has zero callers. Dead code gives false confidence that lunch behavior exists. SOUL.md line 55 names "lunch" as a sizing penalty.

**Fix:** Wire as a soft annotation (sizing multiplier), not a hard block.

```python
# annotations.py map_soft_warnings() — add lunch warning
# ponytail: lunch = lower participation, reduce size, don't block
if is_lunch_window(now_et):
    warnings.append("lunch_window")
    # sizing multiplier handled in soft_warning_multiplier()

# annotations.py soft_warning_multiplier() — add lunch penalty
if "lunch_window" in warnings:
    mult = min(mult, 0.75)  # 25% reduction during lunch
```

**Alternative:** Remove `is_lunch_window()` entirely if lunch sizing is not wanted.

**Verification:** Paper mode during 11:30-14:00 ET. Confirm `lunch_window` warning appears and sizing reduced 25%.

- [ ] Complete

---

#### 11.19.27 Task #38i — Fix attention gate cliff

**Files:** `src/decision_pipeline.py`

**Problem:** `decision_pipeline.py:385` — `att_mult > 0.25` (strict inequality). `attention_multiplier()` returns 0.25 at attention=49, 0.50 at attention=50. A stock at 49.9 attention fails the gate. For a bot whose thesis is "missing non-critical data reduces confidence/size" not "declares untradeable" (SOUL.md line 22), a 0.1-point miss shouldn't be a binary gate.

**Fix:** Change to non-strict inequality, or remove the gate and let sizing handle it.

```python
# decision_pipeline.py:385
# ponytail: >= not > — a 49.9 attention stock still gets 0.25x sizing, that's enough protection
if att_mult >= 0.25 and bars:
```

**Verification:** Paper mode. Candidate with attention=49.9. Confirm entry detection runs (not blocked by gate).

- [ ] Complete

---

#### 11.19.28 Task #38j — Lower _is_active() threshold from 3 to 2 signals

**Files:** `src/move_classifier.py`

**Problem:** `_is_active()` at `move_classifier.py:302-337` requires 3/5 signals (higher_low_structure, pullbacks_bought, strong_volume, spread_ok, stop_in_range). Fresh top gainers with 20 bars rarely reach 3 → most candidates default to EARLY → ACTIVE-only entry setups (micro_pullback, hod_reclaim, consolidation_breakout) are dead code in practice.

**Fix:** Lower threshold from 3 to 2 signals.

```python
# move_classifier.py _is_active()
# ponytail: 2/5 signals is enough — fresh top gainers with volume + structure are active
ACTIVE_THRESHOLD = 2  # was 3
if signal_count >= ACTIVE_THRESHOLD:
    return MoveState.ACTIVE
```

**Verification:** Paper mode. Top gainer with strong_volume + higher_low_structure (2 signals). Confirm classified as ACTIVE, not EARLY.

- [ ] Complete

---

#### 11.19.29 Task #38k — Apply faster polling intervals (quick win)

**Files:** `config/default_config.yaml`, `config/settings.py`

**Problem:** Current 30s scan / 10s monitor is adequate but not optimal. Faster polling gives ~25% quicker data at zero engineering cost. Stays well under 200 req/min free-tier limit.

**Fix:** Change config defaults.

```yaml
# config/default_config.yaml
scanner_interval_seconds: 20   # was 30
monitor_interval_seconds: 8    # was 10
```

**Verification:** Paper mode. Confirm scan runs every 20s, monitor every 8s. Confirm no rate limit errors.

- [ ] Complete

---

#### 11.19.30 Task #38l — Document scalp_reclaim dead zone

**Files:** `SPEC.md`, `SOUL.md`

**Problem:** `detect_scalp_reclaim()` requires `quote_age_seconds <= 5.0`. With 10s monitor polling, this detector never fires. The only setup permitted in EXTENDED/HALT_RISK move states is effectively disabled.

**Fix:** Document as known gap. Will unblock with WebSocket (Task #39, Phase 8).

```markdown
# Add to §11.19.21 (What This Phase Does NOT Do):
- scalp_reclaim entry setup effectively disabled — requires quote_age <= 5s with 10s polling 
  → never fires. Will unblock with WebSocket (Phase 8, Task #39).
```

**Verification:** Spec updated. SOUL.md notes the gap.

- [ ] Complete

---

#### 11.19.31 Task #38m — Update SOUL.md §Future Direction

**Files:** `SOUL.md`

**Problem:** `SOUL.md:59-61` still says "v0.5.0 mandate: runner capture + scaling-in + live readiness." Runner capture and scaling-in ARE implemented. This is stale alongside Task #29's Current Reality update.

**Fix:** Update §Future Direction.

```markdown
## Future Direction

- **v0.5.0:** Runner capture + scaling-in **implemented**. Live readiness in progress 
  (§11.19 Phase C). Paper validation next.
- **VPS running 24/7:** wake 30–60 min before market open, monitor premarket/top gainers, 
  trade market hours with discipline.
- **SIP data feed** (~$99/mo) if the cost/logic tradeoff is worth it for execution quality. 
  Deferred to v0.6 — test with IEX first.
- **WebSocket streaming** (Phase 8): real-time quotes + trades for sub-second entry detection. 
  After v0.5.0 stable. See SPEC.md §11.19 Task #39.
```

**Verification:** Read SOUL.md. Confirm no stale claims.

- [ ] Complete

---

### Phase D — WebSocket Streaming (Deferred)

---

#### 11.19.19 Task #39 — WebSocket for quotes (Phase 8, after v0.5.0 stable)

**Status:** DEFERRED — not for current sprint.

**Rationale:** Current 30s/10s REST polling is adequate for paper strategy validation. WebSocket would cut entry detection latency from ~70s → ~2s, but the strategy must be validated first. If the strategy doesn't work with 10s latency, it's not a latency problem.

**When to build:** After v0.5.0 (runner capture + scaling-in + live readiness) is stable in paper.

**Plan:**
1. `src/streaming.py` — `StreamManager` (thread-based feeder, not async rewrite)
2. `StockDataStream` with `subscribe_quotes` for current position symbols
3. Dynamic subscribe/unsubscribe based on position list (30-symbol limit)
4. Fallback: REST quotes when WebSocket unavailable
5. Later: `subscribe_trades` for real-time trailing stops
6. Later: `on_second_bar` for sub-minute entry detection

**Quick win now (no WebSocket needed):** Change `scanner_interval_seconds` 30→20, `monitor_interval_seconds` 10→8. Stays under 200 req/min. ~25% faster data at zero engineering cost.

- [ ] Deferred

---

### 11.19.20 Dependency Order

```
Phase A.0 (schema prerequisite, MUST be first):
  #21a (schema fields)     ──── before #22, #27, #32

Phase A (critical, sequential or parallel):
  #21 (double-exit)        ──┐
  #22 (defer confirm)      ──┤── depends on #21a
  #23 (pnl +=)             ──┤
  #24 (double-count)       ──┤── depends on #23
  #25 (periodic recon)     ──┤
  #26 (SDK timeout)        ──┤
  #27 (pending timeout)    ──┤── depends on #22, #21a
  #38a (cancel/place race) ──┤
  #38b (verify cancel)     ──┤
  #38c (persist pending)   ──┤
  #38d (equity refresh)    ──┤
  #38e (rebuild risk)      ──┤
  #38f (state transitions) ──┤
  #38g (ADDING timeout)    ──┘── depends on #27

Phase B (strategic, parallel):
  #28 (gain cap revert)    ──┐
  #29 (SOUL.md update)     ──┤
  #30 (runner gate)        ──┤
  #31 (hard stop gap)      ──┤
  #32 (entry_setup)        ──┤── depends on #21a
  #33 (tighten stop)       ──┤── depends on #23
  #38h (lunch window)      ──┤
  #38i (attention gate)    ──┤
  #38j (_is_active 2/5)    ──┘

Phase C (live readiness, parallel):
  #34 (periodic save)      ──┐
  #35 (market session)     ──┤
  #36 (client_order_id)    ──┤
  #37 (loss cap split)     ──┤
  #38 (R-multiple)         ──┤
  #38k (faster polling)    ──┤
  #38l (scalp_reclaim doc) ──┤
  #38m (SOUL future dir)  ──┘

Phase D (deferred):
  #39 (WebSocket)          ──── after v0.5.0
```

---

### 11.19.21 What This Phase Does NOT Do (Deferred)

- **WebSocket streaming:** Deferred to Phase 8 (Task #39). Strategy validation first.
- **5-second bars:** Not available via Alpaca REST. WebSocket `on_second_bar` is the path — deferred with WebSocket.
- **scalp_reclaim entry setup effectively disabled:** Requires `quote_age <= 5s` with 10s polling → never fires. Will unblock with WebSocket (Phase 8, Task #39).
- **TrailingStopOrderRequest:** Bot computes trail manually and places StopOrderRequest. Alpaca's native trailing stop could be used, but current approach works. Upgrade later if needed.
- **TradingStream for fill notifications:** Still REST polling. Upgrade with WebSocket (Task #39).
- **Rate limit throttling:** Not needed at current cadences (~54 req/min, well under 200). Add if cadence increases.
- **Paper gateway rejection simulation:** Paper gateway always fills. Could simulate rejection/pending, but paper mode is for strategy validation, not failure-mode testing. Live mode exercises real failure paths.
- **client_order_id retry wiring:** Task #36 adds the idempotency key but doesn't wire a retry loop. The key exists for safety; retry logic is deferred until network reliability becomes a measured problem.

---

### 11.19.22 Research Sources

**Codebase (verified file:line):** `src/app.py:649-686,718-731,760-767,909-916,949,982-1080,1095-1116,1131-1226,1255` · `src/paper_execution.py:429,466-470,548-549,696-719,745-901,994-1018,1117-1118,1213-1415` · `src/decision_pipeline.py:479-486` · `src/exits.py:163-166,293,415-459` · `src/runner.py:108-111,174-205,213-262` · `src/scanner/attention.py:44` · `src/move_classifier.py:302-337` · `src/state_machine.py:33-52`

**Alpaca API (Context7 `/alpacahq/alpaca-py`):** `client_order_id` for idempotency; `TradingStream.subscribe_trade_updates` for fill notifications; `StockDataStream` for real-time quotes/trades/bars; `TrailingStopOrderRequest` for native trailing stops; `get_order_by_client_id` for idempotent retry; 200 req/min free-tier limit; 30 WebSocket symbols on free tier.

**Professional trading research:** Avramov, Cheng, Hameed (2016) — momentum profits larger in liquid markets; Korajczyk & Sadka (2004) — liquidity-weighted momentum; Lesmond et al. (2004) — momentum returns concentrated in high-cost stocks; Trade Ideas — RVOL uses 30-day 15-min interval baseline; TOSindicators — RVOL 1.5-2.0 has best risk-adjusted returns; Pomegra — spread-adjusted position sizing tiers.

**Consensus:** RVOL is the #1 signal. Spread is a sizing dial, not a gate. Broker truth must be reconciled periodically. Synchronous fill confirmation on LIMIT orders is a fundamental design flaw — defer to monitor loop.
