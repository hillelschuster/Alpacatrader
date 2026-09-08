# Test Gap Analysis — Alpacatrader Rebuild

Generated: 2026-06-13
Scope: All 18 existing test files, 19 source modules, config, main.

---

## 1. Test Inventory

| # | File | Lines | Focus | Gen |
|---|------|-------|-------|-----|
| 1 | `tests/conftest.py` | 75 | Shared fixtures (Candidate, EntrySignal, PaperExecutionGateway) | Gen3 |
| 2 | `tests/test_phase1_schemas.py` | 632 | All schemas, enums, JSON serialization, spec example | Gen3 |
| 3 | `tests/test_phase1_logger.py` | 277 | DecisionLogger write/read/filter/roundtrip | Gen3 |
| 4 | `tests/test_phase2_scanner.py` | 235 | Finviz scanner adapter, manual watchlist (mocked scraper) | Gen3 |
| 5 | `tests/test_phase2_confidence.py` | 239 | Data confidence calculation | Gen3 |
| 6 | `tests/test_phase2_attention.py` | 903 | Attention scoring, themes, FormerRunnerStore, float rotation, soft warnings, DSY regression | Gen3 |
| 7 | `tests/test_phase3_hard_filters.py` | 603 | Hard filter functions, time gates, quote/spread tiers, DSY regression | Gen3 |
| 8 | `tests/test_phase4_classifier.py` | 504 | Move state classification, priority, mode mapping, permission matrix | Gen3 |
| 9 | `tests/test_phase5_entries.py` | 684 | All 6 entry setup detectors, find_entry orchestrator, permission gating | Gen3 |
| 10 | `tests/test_phase6_risk.py` | 152 | Starter sizing, attention multiplier, adjusted risk, share calculation | Gen3 |
| 11 | `tests/test_phase7_state_machine.py` | 304 | Valid/invalid transitions, symbol lock, candidate lifecycle, PositionStore, PendingOrderStore | Gen3 |
| 12 | `tests/test_phase7_execution.py` | 321 | PaperExecutionGateway entry/stop/exit/cancel, all 8 reconcile cases | Gen3 |
| 13 | `tests/test_phase8_exits.py` | 428 | All exit detectors (P1-P11), P&L calc, orchestrator priority | Gen3 |
| 14 | `tests/test_phase9_pipeline.py` | 798 | Full pipeline integration, batch, DSY regression, quote age, no-news, exits, sizing lifecycle | Gen3 |
| 15 | `tests/test_phase10_app.py` | 915 | App init, loop, monitor (batch 4), enrichment (batch 3), reconciliation (batch 5), account risk (batch 5) | Gen3 |
| 16 | `tests/test_settings.py` | 238 | Settings load/validate, legacy sections gone | Gen3 |
| 17 | `tests/test_no_legacy_modules.py` | 82 | Negative import tests for 22 deleted modules | Gen3 |
| 18 | `tests/test_cli_rebuild.py` | 393 | CLI mock/paper/sim paths, no legacy import, market data enrichment | Gen3 |

**Total test code: ~6,700 lines** across 18 files.

### Orphaned `.pyc` evidence (deleted tests likely from Gen2):
These `.pyc` files remain in `tests/__pycache__/` but have no corresponding `.py` source, indicating test files that were deleted during Gen2 cleanup:
- `test_execution_engine` — legacy execution engine
- `test_exit_rules` — legacy exit module
- `test_oco_validation` — OCO order validation
- `test_reconciliation` — broker reconciliation
- `test_providers` — data providers
- `test_premarket` — premarket analysis
- `test_pillar5_validation` — pillar 5 validation
- `test_step18_behavior` — step 18 behavior
- `test_session` — session management
- `test_scanner_enrichment` — scanner enrichment
- `test_risk` — risk manager
- `test_v3_config_behavior` — v3 config
- `test_v3_pipeline_helpers` — v3 pipeline helpers
- `test_trailing_stop` — trailing stop
- `test_persistence` — persistence layer

---

## 2. Generation Coverage

### Gen3 (Rebuild) — Fully covered by dedicated phase tests:
All Phase 1–10 source modules have corresponding `test_phaseN_*.py` files:

| Source Module | Test File | Coverage Quality |
|---|---|---|
| `models/schemas.py` | `test_phase1_schemas.py` | Excellent — all models, enums, validators, JSON roundtrip |
| `journal/decision_logger.py` | `test_phase1_logger.py` | Excellent — write/read/filter/roundtrip/edge cases |
| `scanner/scanner.py` | `test_phase2_scanner.py` | Good — all data mapping, filtering-by-absence, frozen |
| `scanner/confidence.py` | `test_phase2_confidence.py` | Excellent — penalties, floors, rounding |
| `scanner/attention.py` | `test_phase2_attention.py` | Excellent — all factors, redistribution, themes, soft warnings, multiplier |
| `hard_filters.py` | `test_phase3_hard_filters.py` | Excellent — all checks, old filters forbidden, DSY regression |
| `move_classifier.py` | `test_phase4_classifier.py` | Excellent — all states, priority, mode mapping, permission matrix |
| `entries.py` | `test_phase5_entries.py` | Excellent — all 6 detectors, find_entry orchestrator, permission gating |
| `sizing.py` | `test_phase6_risk.py` | Excellent — all functions, edge cases |
| `state_machine.py` | `test_phase7_state_machine.py` | Excellent — transitions, locks, persistence |
| `paper_execution.py` | `test_phase7_execution.py` | **Good** — misses `AlpacaExecutionGateway` entirely |
| `exits.py` | `test_phase8_exits.py` | Excellent — all detectors, priority, P&L |
| `decision_pipeline.py` | `test_phase9_pipeline.py` | Excellent — full pipeline, batch, edge cases |
| `app.py` | `test_phase10_app.py` | **Good** — init, monitor, enrichment, reconciliation, account risk |
| `config/settings.py` | `test_settings.py` | **Good** — load/validate/legacy-gone, missing validator coverage |
| `main.py` | `test_cli_rebuild.py` | **Excellent** — mock/paper/sim, no-legacy guard |

### Gen2 — Zero tests (legacy modules deleted):
All legacy modules listed in `test_no_legacy_modules.py` are confirmed deleted. The orphan `.pyc` files confirm Gen2 tests were removed alongside their source modules.

### Gen1 (Original RossCameronPipeline) — Blocked by import guard:
`test_cli_rebuild.py` proves `RossCameronPipeline` is never imported by the rebuild path.

---

## 3. Untested Safety-Critical Paths

### 3.1 Fill confirmation failure — pipeline exception handling
**File**: `src/decision_pipeline.py:347-353`
**Risk**: When `confirm_fill()` raises, the pipeline logs and continues without advancing past PENDING_ENTRY. The position is stuck in LIMBO — exists in the store but never transitions to OPEN. No test verifies the monitor correctly handles PENDING_ENTRY positions that never filled.
**Why missing**: The `force_entry` monkeypatch in tests bypasses real confirm_fill. The fill-failure branch is never exercised.

### 3.2 Protection failure chain (pipeline-level)
**File**: `src/decision_pipeline.py:355-371`
**Risk**: When `protect_position()` fails, the pipeline calls `mark_unprotected()`. If *that* also fails, the position remains OPEN with no stop. No integration test verifies this two-step error chain produces a correct UNPROTECTED position.
**Why missing**: Unit tests test `mark_unprotected` in isolation. Pipeline integration tests use `force_entry` which skips the entire submission/protection flow.

### 3.3 `AlpacaExecutionGateway` — entire class
**File**: `src/paper_execution.py:291-498`
**Risk**: The live broker gateway (`TradingClient`, `submit_order`, `get_order_by_id`, fallback-to-synthetic) has **zero tests**. This is the bridge between simulation and real money. Every method (`submit_entry`, `confirm_fill`, `place_stop`, `submit_exit`, `confirm_exit_fill`) has failover logic that's untested.
**Why missing**: Requires `alpaca-py` and API keys. Would need mock of `TradingClient`.

### 3.4 `build_market_snapshot` — Alpaca market data connection
**File**: `src/market_data.py:52-195`
**Risk**: The entire Alpaca market data enrichment function is untested. The Alpaca-bar-to-Bar conversion (`market_data.py:130-138`), quote parsing, VWAP/EMA/day_high derivation, and error handling have no test coverage. If Alpaca changes their bar or quote response format, nothing catches it.
**Why missing**: Requires `alpaca-py` and API keys. No mock-based test exists.

### 3.5 `build_market_snapshot_sim` — simulation market data
**File**: `src/market_data_sim.py:31-148`
**Risk**: Same as above — zero tests. The sim path is used for off-hours testing and has complex date math (`timedelta`, `replace hour=20`) that can fail at boundary conditions.
**Why missing**: Same dependency issue.

### 3.6 Scanner enrichment — real scraper
**File**: `src/scanner/enrichment.py:55-275`
**Risk**: `scrape_finviz_gainers()`, `scrape_yfinance_gainers()`, and `_finviz_is_stale()` are untested. The Finviz scraper depends on HTML table structure (11-column layout). If Finviz changes their markup, the scanner silently returns empty dicts. `_finviz_is_stale()` has a `>=80%` threshold that might false-positive on low-volume days.
**Why missing**: Requires real HTTP. No mock-HTTP test exists. `test_phase2_scanner.py` mocks `scrape_finviz_gainers` at the function level.

### 3.7 Daily loss ledger computation
**File**: `src/app.py:164-205 (`_build_risk_state`**)
**Risk**: This method computes `daily_realized_pnl`, `daily_unrealized_pnl`, `per_symbol_daily_loss`, `daily_loss_breached` from the position store. It has **no direct unit test**. The test in `test_phase10_app.py:845-878` only checks that a pre-seeded loss breaches the cap — it doesn't test the computation logic (realized vs unrealized, per-symbol tracking).
**Why missing**: The method is private on `TradingApp`. No fixture sets up positions with mixed realized/unrealized P&L.

### 3.8 `max_open_risk_pct` enforcement
**File**: `src/decision_pipeline.py:203` (parameter exists), `src/app.py:89` (parameter stored), `src/hard_filters.py:307-310` (only checks `max_positions`)
**Risk**: The parameter `max_open_risk_pct` is passed through the system but **no function enforces it**. `check_account_risk()` only gates on `open_position_count` and `symbol_locked`. Open risk as a percentage of equity is computed in `_build_risk_state` (`total_open_risk`) but never compared against `max_open_risk_pct` to block new entries. This is a spec gap or an incomplete feature.
**Why missing**: Enforcement logic was never wired into the hard filters or pipeline.

### 3.9 Crash recovery — full cycle
**File**: `src/state_machine.py:225-238` (save/load tested), `src/app.py:391-394` (`_shutdown` is a TODO stub)
**Risk**: `_shutdown()` is a no-op with comments about future work. If the process crashes, positions are lost. The `save_to_disk()`/`load_from_disk()` functions exist and are unit-tested, but the app never calls them. No integration test verifies: run → save state → restart → load state → reconciliation.
**Why missing**: The app's `_shutdown` is not implemented.

### 3.10 Time gate integration in full pipeline
**File**: `src/hard_filters.py:333-387` (gates tested), `src/decision_pipeline.py` (pipeline doesn't take time)
**Risk**: Individual time gate functions (`is_watch_only_window`, `is_past_entry_cutoff`, `is_flatten_time`) are tested. But the pipeline (`run_pipeline`) does not accept an `et_time` parameter for entry gating, nor does `check_time_gate` get called during the scan path. Time gates only affect exits (`check_time_exit`). The spec says entry cutoff and flatten time should gate new entries, but this is not wired.
**Why missing**: No `et_time` flowing into `_scan_and_process()`.

### 3.11 Broker reconciliation Case 8 (unreachable)
**File**: `src/paper_execution.py:526`
**Risk**: The comment says "Case 8: broker unreachable → mark UNPROTECTED (handled by caller)." The `reconcile_positions` function does not implement it, and no caller code implements the fallback. If the broker is unreachable at startup, nothing marks positions as UNPROTECTED.
**Why missing**: The case was deferred to the caller and never implemented.

### 3.12 Bracket/OCO orders
**File**: `src/models/schemas.py:71` (`OCO = "oco"` exists), `src/models/schemas.py:67` (`TARGET = "target"` exists)
**Risk**: The enums exist but **no bracket or OCO order logic is implemented** anywhere. `PaperExecutionGateway` has no bracket order submission. No stop-loss + take-profit pair is submitted as a bracket. No OCO cancellation logic exists.
**Why missing**: Feature was never built.

### 3.13 Pattern detector against Alpaca-style bars
**File**: `src/market_data.py:130-138` (Alpaca bar → Bar conversion)
**Risk**: The 6 entry detectors operate on generic `Bar` objects. The Alpaca-to-Bar mapping (`ab.open → Bar(open=ab.open, ...)`) is untested. If Alpaca changes field names (`ab.open` → `ab.OpenPrice`) or returns bars in a different format, the conversion silently produces empty or incorrect bars.
**Why missing**: No unit test for the conversion helper.

---

## 4. Likely Failures

### 4.1 Price-dependent hard filter tolerances
**File**: `tests/test_phase3_hard_filters.py:257-275`
**Scenario**: `test_stop_too_tight_for_spread` and `test_stop_wide_enough` hardcode `entry_price=10.0`, `spread_pct=2.0`, `estimated_slippage=0.01`. The formula `min_meaningful = 1.5 * (spread_dollars + slippage)` is sensitive to `spread_dollars = entry_price * spread_pct / 100`. If the constant `1.5` or the slippage constant changes, these tests fail.
**Impact**: Low — intentional regression guard.

### 4.2 Monitor-path price fallback assertions
**File**: `tests/test_phase10_app.py:378-418`
**Scenario**: `test_hard_stop_fails_if_price_is_average_entry` is **designed to fail** if `_monitor_positions` reverts to using `average_entry` instead of current price. This is a canary test — it will fail if the monitor path is refactored to use position data instead of market data.
**Impact**: High — designed as a regression canary. If it fails, the monitor is using wrong price source.

### 4.3 Exit priority in monitor path
**File**: `tests/test_phase10_app.py:420-451`
**Scenario**: `test_spread_explosion_beats_hard_stop_in_monitor` asserts spread explosion (P1) fires before hard stop (P3). The monitor path calls `run_pipeline` with `check_exits_for_open=True`, which calls `check_exits` which iterates in priority order. If the exit orchestrator order changes, spread may not fire before hard stop.
**Impact**: Medium — depends on the exit engine priority remaining stable.

### 4.4 Monkeypatch import paths
**File**: `tests/test_phase9_pipeline.py:792-797`, `tests/test_phase10_app.py:221-223`
**Scenario**: `force_entry` fixture monkeypatches `"src.decision_pipeline.find_entry"`. If `find_entry` is renamed, moved to another module, or the import in `decision_pipeline.py` changes to `from src.entries import find_entry as find_entry_setup`, the monkeypatch silently fails and no entry signal is forced.
**Impact**: Medium — tests become meaningless (they test the default pipeline response, not the forced entry path).

### 4.5 Duplicate-entry blocking path
**File**: `tests/test_phase9_pipeline.py:255-269`
**Scenario**: `test_duplicate_entry_blocked` calls `run_pipeline` twice with same symbol. The second call should return `skip`. The blocking mechanism depends on `execution_gw.is_symbol_locked()` having been set by the first call. If the gateway's lock mechanism changes (e.g., from position state-based to explicit lock flag), the test passes vacuously.
**Impact**: Low — the test is explicit enough to catch full regressions.

### 4.6 Monitor default quote age (999s)
**File**: `tests/test_phase10_app.py:519-540`
**Scenario**: `test_monitor_no_network_calls` expects exit when no `market_data_fn` is provided, because the default quote_age becomes 999s (stale). If the default stale threshold changes from 999s, or the monitor stops treating missing data as stale, this test fails.
**Impact**: Medium — intentional regression guard for stale-quote handling.

---

## 5. Required Tests for SPEC Completion

### Priority P0 — Missing safety-critical coverage

| # | Test | SPEC Ref | Current Gap |
|---|------|----------|-------------|
| 1 | `AlpacaExecutionGateway` — mocked `TradingClient` for all 7 methods | §14.2, §15.1 | Zero coverage for real broker path |
| 2 | `build_market_snapshot` — mocked Alpaca client with realistic response | §22.15.17 | Alpaca-to-Bar conversion untested |
| 3 | Protection failure → UNPROTECTED → emergency exit (full integration) | §12.5, §14.3 | Pipeline error chain never tested end-to-end |
| 4 | `_build_risk_state` — positions with mixed realized/unrealized, per-symbol loss tracking | §13.5 | Computation logic untested, only breach detection tested |
| 5 | `max_open_risk_pct` enforcement — gate entry when open risk exceeds % equity | §7.5, §11.1 | No enforcement function exists |

### Priority P1 — Important edge coverage

| # | Test | SPEC Ref | Current Gap |
|---|------|----------|-------------|
| 6 | `scrape_finviz_gainers` — mocked HTTP with realistic HTML | §6.1 | Scraper itself untested (only adapter tested) |
| 7 | `scrape_yfinance_gainers` — mocked yfinance `Ticker.fast_info` | §6.1 | Fallback scanner untested |
| 8 | `_finviz_is_stale` — boundary at 80%, edge cases with 0/1/2 rows | §6.2 | Staleness detection untested |
| 9 | Time-gate integration — `et_time` flowing into `_scan_and_process` | §7.4, §22.7 | Pipeline doesn't receive time for entry gating |
| 10 | Fill-failure recovery — PENDING_ENTRY stuck position handled in monitor | §14.3, §13.2 | Pipeline's `except Exception` branch untested |
| 11 | Crash recovery — `save_to_disk` → shutdown → `load_from_disk` → reconcile | §15.4, §13.4 | `_shutdown()` is a stub |
| 12 | Reconciliation Case 8 — broker unreachable marks UNPROTECTED | §15.4.8 | Not implemented anywhere |

### Priority P2 — Spec gap / not yet spec'd

| # | Test | SPEC Ref | Current Gap |
|---|------|----------|-------------|
| 13 | Bracket/OCO order submission and lifecycle | §9.4, §12.3 | Enum exists but no implementation |
| 14 | `market_data_sim.build_market_snapshot_sim` — date math, bar derivation | §22.15 | Sim path untested |
| 15 | Per-symbol daily loss cap enforcement (as exit, not just entry block) | §12.2 | `per_symbol_loss_capped` param exists but never set |

---

## Summary

**Gen3 coverage is strong for isolated unit tests** (Phases 1–8) and the pipeline (Phase 9). The app layer (Phase 10) has good integration coverage for the monitor path, enrichment data flow, and reconciliation.

**Critical gaps cluster in three areas:**
1. **Real broker connection** — `AlpacaExecutionGateway`, `build_market_snapshot`, and `build_market_snapshot_sim` have zero tests.
2. **Error recovery chains** — Fill failure → stuck position, protection failure → UNPROTECTED, crash → recovery, broker unreachable → UNPROTECTED.
3. **Risk enforcement** — `max_open_risk_pct` is never gated, `_build_risk_state` computation is untested, per-symbol loss caps are never wired as exit triggers.

**No tests guard against the existing 6-entry-detector logic producing incorrect signals against real Alpaca bar data**, because the Alpaca-bar-to-Bar conversion path is entirely untested.
