# Runtime Gap & Call-Graph Analysis

**Audit date:** 2026-06-13
**Sources:** `src/app.py`, `src/decision_pipeline.py`, `src/paper_execution.py`, `src/state_machine.py`, `src/entries.py`, `src/exits.py`, `src/hard_filters.py`, `src/move_classifier.py`, `src/sizing.py`, `src/scanner/attention.py`, `src/scanner/confidence.py`, `src/scanner/scanner.py`, `src/scanner/enrichment.py`, `src/market_data.py`, `src/market_data_sim.py`, `src/models/schemas.py`, `src/journal/decision_logger.py`
**Files requested but missing:** `src/pipeline/v3_pipeline.py`, `src/execution/engine.py` — do not exist in the codebase.

---

## 1. Entry Points

| Entry | File:Line | Triggers | Invoked By |
|---|---|---|---|
| `TradingApp.run()` | `app.py:207` | `install_shutdown_handlers()`, `_reconcile_on_startup()`, then main loop | External caller (CLI, `main.py`) |
| `TradingApp._monitor_positions()` | `app.py:236` | Every cycle (`monitor_interval=10s`) | `run()` loop |
| `TradingApp._scan_and_process()` | `app.py:307` | Every 30s cadence | `run()` loop |
| `_reconcile_on_startup()` | `app.py:138` | Once at `run()` start | `run()` |
| `run_pipeline()` | `decision_pipeline.py:179` | Per-candidate in both monitor and scan paths | `_monitor_positions()`, `_scan_and_process()` |
| `run_pipeline_batch()` | `decision_pipeline.py:430` | Unused in runtime | Dead code |
| `PaperExecutionGateway` methods | `paper_execution.py:41` | Order lifecycle | `run_pipeline()` and `_monitor_positions()` |
| `AlpacaExecutionGateway` (extends Paper) | `paper_execution.py:291` | Live paper-mode orders | Substituted via `execution_gw` param |
| `reconcile_positions()` | `paper_execution.py:506` | Startup reconciliation | `_reconcile_on_startup()` |
| `scan_finviz_candidates()` | `scanner/scanner.py:23` | Scanner cycle | `TradingApp._scanner_fn` default |
| `build_market_snapshot()` | `market_data.py:52` | Market data enrichment | Injected as `market_data_fn` |

---

## 2. Monday 9:30 AM Flow

### What SHOULD happen (per SPEC §7.1, §15.3):
- **9:30–9:35** → Watch-only window: no new entries, only monitoring
- **Scanner discovery** → Enrichment → Attention ranking → Hard filters → Move classification → Entry detection → Sizing → Execution
- Positions monitored every 10s for exits
- Fresh market data (quotes, bars) on every cycle

### What ACTUALLY happens:

```
09:30:00  TradingApp.run() called
         install_shutdown_handlers()
         _reconcile_on_startup()           # broker snapshot → reconcile
         last_position_check = T0
         last_scan = T0

09:30:01  [sleep 1s]

09:30:02  [sleep 1s]

... (no monitor or scan until interval elapses)

09:30:10  _monitor_positions() — no open positions, no-op

09:30:20  _monitor_positions() — no-op

09:30:30  _scan_and_process():
          1. scan_finviz_candidates() → list[Candidate] (delayed data, Finviz free = 15-20m stale)
          2. enrichment_fn(c) per candidate (identity lambda by default)
          3. market_data_fn(c) → MarketSnapshot (or None)
             ★ BUG: if market_data_fn returns None, falls back to blank snapshot
                but current code at app.py:333-338 is correctly guarded with
                `if snapshot is None: snapshot = MarketSnapshot(candidate=c)`.
                This is NOT the critical bug line — see "Gap 1" below.
          4. run_pipeline() per candidate:
             a. calculate_data_confidence()
             b. score_attention()
             c. map_soft_warnings()
             d. run_hard_filters()
             e. classify_move_state()
             f. find_entry() — requires bars AND attention>0.25x
             g. entry_sizing()
             h. PaperExecutionGateway.submit_entry() + confirm_fill() + protect_position()
             i. check_exits — skipped (check_exits_for_open=False in scan path)
             j. DecisionLogger.write()
          5. Log cycle summary

09:30:40  _monitor_positions() — still no-op

...pattern repeats every 30s scan, every 10s monitor
```

### Critical missing behavior at 9:30 AM:

1. **Watch-only window NOT enforced.** `hard_filters.py:354` defines `is_watch_only_window(et_time)` for 9:30–9:35, but `TradingApp` never computes ET time and never passes `in_watch_only_window=True` to the pipeline. The bot can enter new positions during the first 5 minutes — directly violating SPEC §7.1.

2. **No ET time awareness anywhere in the app loop.** `entry_cutoff_time` and `flatten_time` are parsed as strings in `__init__` (lines 83-84) but never used in `run()`, `_monitor_positions()`, or `_scan_and_process()`. Time-based gating is completely dead.

3. **Scanner data is 15-20 minutes stale.** Finviz free tier (used by `scan_finviz_candidates()`) provides delayed quotes. The bot evaluates candidates on stale scanner data, and even fresh Alpaca bars can't fix stale discovery.

4. **No attention ranking before processing.** `_scan_and_process()` iterates scanner output sequentially without sorting by attention score. The first candidate processed may be the lowest-attention name.

---

## 3. Function-Level Call Graph

### 3.1 Monitor Path (every ~10s)

```
TradingApp.run() [app.py:207]
  └─ _monitor_positions() [app.py:236]
       ├─ _build_risk_state() [app.py:164]
       │    ├─ PositionStore.all_open()
       │    └─ AccountRiskState(...)  [note: closed losses excluded]
       │
       └─ for each open position:
            ├─ market_data_fn(pos) → MarketSnapshot or None
            │    └─ build_market_snapshot() [market_data.py:52]  (if configured)
            │         ├─ Alpaca StockLatestQuoteRequest
            │         ├─ Alpaca StockBarsRequest
            │         └─ Derived: VWAP, EMA9, day_high, prior_hod, dollar_volume_5m
            │
            ├─ Candidate(symbol=pos.symbol, price=current_price)  [app.py:263]
            │    ★ price may be 0.0 on market-data failure (see Gap 3)
            │
            └─ run_pipeline(candidate, ..., check_exits_for_open=True) [decision_pipeline.py:266]
                 ├─ calculate_data_confidence() [scanner/confidence.py:36]
                 ├─ score_attention() [scanner/attention.py:73]
                 ├─ map_soft_warnings() [scanner/attention.py:440]
                 ├─ run_hard_filters() [hard_filters.py:223]
                 │    ├─ check_market_structure()
                 │    ├─ check_execution_data()
                 │    ├─ check_liquidity_spread()
                 │    ├─ check_risk_definition()
                 │    └─ check_account_risk()
                 │
                 ├─ classify_move_state() [move_classifier.py:30]
                 │    ★ Impoverished feature set passed (see Gap 9)
                 │
                 ├─ find_entry() [entries.py:699]  ★ SKIPPED (check_exits_for_open=True → step 9 only)
                 │    (detectors evaluated in priority order)
                 │    ├─ detect_first_pullback()
                 │    ├─ detect_hod_reclaim()
                 │    ├─ detect_consolidation_breakout()
                 │    ├─ detect_micro_pullback()
                 │    ├─ detect_vwap_reclaim()
                 │    └─ detect_scalp_reclaim()
                 │
                 ├─ entry_sizing() [sizing.py:79]  ★ SKIPPED (same reason)
                 │
                 ├─ Step 9: check_exits [exits.py:404]  ★ THIS IS THE ACTIVE PATH for monitor
                 │    (Priority order: P1→P2→P3→P3b→P4→P5→P6→P7→P8→P9→P10→P11)
                 │    ├─ check_emergency_exit() [exits.py:76]  P1
                 │    ├─ check_loss_caps() [exits.py:124]  P2
                 │    ├─ check_hard_stop() [exits.py:144]  P3
                 │    ├─ check_invalidation() [exits.py:161]  P3b
                 │    ├─ check_missing_protection() [exits.py:211]  P4
                 │    ├─ check_scale_out() [exits.py:222]  P5
                 │    ├─ check_failed_reclaim() [exits.py:277]  P6
                 │    ├─ check_vwap_loss() [exits.py:304]  P7
                 │    ├─ check_spread_expansion() [exits.py:323]  P8
                 │    ├─ check_volume_disappearance() [exits.py:342]  P9
                 │    ├─ check_time_exit() [exits.py:359]  P10  ★ DEAD (no et_time passed)
                 │    └─ check_runner_trail() [exits.py:371]  P11  ★ DEAD (state never RUNNER)
                 │
                 └─ DecisionLogger.write() [journal/decision_logger.py:42]
                      └─ Append to data/decisions.jsonl

            └─ ★ Exit execution [app.py:291-302]
                 ├─ execution_gw.submit_exit(symbol, reason) → **BUG: tuple unpacking** (see Gap 2)
                 └─ execution_gw.confirm_exit_fill(order.order_id) → crashes on tuple
```

### 3.2 Scan Path (every ~30s)

```
TradingApp.run() [app.py:207]
  └─ _scan_and_process() [app.py:307]
       ├─ scanner_fn() → list[Candidate]  [app.py:310]
       │    └─ scan_finviz_candidates() [scanner/scanner.py:23]
       │         └─ scrape_finviz_gainers() [scanner/enrichment.py:55]
       │
       ├─ [enrichment_fn(c) for c in raw]  [app.py:315]
       │    └─ identity lambda by default (no enrichment wired)
       │
       ├─ _build_risk_state() [app.py:164]
       │
       └─ for each candidate:
            ├─ execution_gw.is_symbol_locked(c.symbol)  [app.py:329]
            │
            ├─ market_data_fn(c) → MarketSnapshot or fallback  [app.py:333-338]
            │    ★ Correctly guarded (see Gap 1 caveat)
            │
            └─ run_pipeline(candidate, ..., check_exits_for_open=False) [decision_pipeline.py:341]
                 ├─ calculate_data_confidence()
                 ├─ score_attention()
                 ├─ map_soft_warnings()
                 ├─ run_hard_filters()
                 ├─ classify_move_state()
                 ├─ find_entry() [entries.py:699]  ★ ACTIVE PATH for scan
                 │    └─ Evaluates 6 detectors in priority order
                 ├─ entry_sizing() [sizing.py:79]  ★ ACTIVE PATH
                 └─ Step 8: Order submission  ★ ACTIVE PATH
                      ├─ PaperExecutionGateway.submit_entry(signal) [paper_execution.py:69]
                      ├─ PaperExecutionGateway.confirm_fill(order_id) [paper_execution.py:114]
                      └─ PaperExecutionGateway.protect_position(symbol, stop, qty) [paper_execution.py:155]

            └─ (no exit execution — check_exits_for_open=False)
```

### 3.3 Startup Path

```
TradingApp.run() [app.py:207]
  └─ _reconcile_on_startup() [app.py:138]
       ├─ broker_snapshot_fn() → dict[symbol → (qty, avg_entry)]
       └─ reconcile_positions(broker, local, pending) [paper_execution.py:506]
            ├─ Case 1: broker has, local none → insert OPEN + action "insert_protect"
            │    ★ App only handles "insert_protect" — ignores other 7 cases
            ├─ Case 2: qty matches → action "verify_stop"
            ├─ Case 3: broker qty < local → action "update_qty_reprotect"
            ├─ Case 4: broker qty > local → action "update_qty_reprotect_warning"
            ├─ Case 5: broker none, local open → closes local + "close_local"
            ├─ Case 6: broker none, local pending → cancels + "cancel_stale_order"
            ├─ Case 7: irreconcilable → ERROR state
            └─ (Case 8: broker unreachable — handled by caller returning {})

            ★ Post-reconciliation: app iterates actions and ONLY processes
              "insert_protect" → protect_position() [app.py:153-160]
              All other actions (verify_stop, update_qty, close_local,
              cancel_stale, irreconcilable) are IGNORED.
```

---

## 4. Broken Connections

### GAP 1 (Critical) — MarketSnapshot discard in scan path

**Status:** PARTIALLY FIXED in current code. The conditional at `app.py:333-338` correctly checks `if snapshot is None` before falling back to a blank snapshot. However, the risk remains: if `market_data_fn` itself returns a valid but *data-starved* `MarketSnapshot` (e.g., Alpaca bars fetch fails silently), there is no validation that bars/VWAP/spread are actually present before the pipeline call. Additionally, the `_scan_and_process` does NOT pass the snapshot's full context to `run_pipeline` via separate kwargs — it unwraps `snapshot.candidate`, `snapshot.bars`, `snapshot.vwap`, etc. individually — so any desync between snapshot fields and candidate fields is invisible.

### GAP 2 (Critical) — Exit tuple-unpacking crash

**File:** `app.py:293-297`

`PaperExecutionGateway.submit_exit()` returns `tuple[PendingOrder, PositionStateModel]`, but the code does:
```python
order = self._execution.submit_exit(pos.symbol, reason=result.exit_decision.reason)
self._execution.confirm_exit_fill(order.order_id)
```

`order` is a tuple. `order.order_id` raises `AttributeError`. This means **any exit triggered in the monitor path crashes the bot for that position.** The exception is caught at `app.py:298-302` with `logger.exception`, so the loop continues, but the exit is never executed.

**Impact:** If an exit check triggers (emergency, hard stop, daily loss, etc.), the position is NOT closed. The code logs an error and moves on. The position remains OPEN.

### GAP 3 (Critical) — Crisis context fabrication on data failure

**File:** `app.py:249-263`

When `market_data_fn` raises an exception (or returns None/zero-price):
```python
current_price = 0.0     # app.py:261
quote_age_seconds = 999.0   # decision_pipeline.py:273 default
candidate = Candidate(symbol=pos.symbol, price=0.0)  # app.py:263
```

The exit engine then sees:
- `current_price=0.0` → `check_hard_stop`: triggers if `stop_price >= 0` (always true)
- `quote_age_seconds=999` → `check_emergency_exit`: quote > 60s → emergency flatten

A transient data outage therefore causes a **forced exit of every open position** at zero price, with false P&L calculations.

### GAP 4 (Critical) — Partial exit collapse

**File:** `app.py:293-297`

Exit detectors can return partials (e.g., `check_scale_out` returns `exit_pct=33`, `exit_pct=50`, `exit_pct=25`). But the app never forwards `exit_pct`:
```python
order = self._execution.submit_exit(pos.symbol, reason=result.exit_decision.reason)
#  ^^^ exit_pct not passed
```

`submit_exit` defaults `exit_pct=100` (`paper_execution.py:204`). **Every partial exit becomes full liquidation.**

Additionally, `confirm_exit_fill()` (`paper_execution.py:228-241`) always sets `pos.current_shares = 0` and `pos.state = CLOSED`. There is no partial-fill support in the paper gateway.

### GAP 5 (High) — Phantom protection

**File:** `paper_execution.py:155-171`

`protect_position()` returns early when `pos.stop_price == stop_price`:
```python
if pos.stop_price == stop_price:
    return None  # already protected at this level
```

This only checks local metadata equality, not whether a live stop order exists. The `_has_pending_stop()` helper (`paper_execution.py:173`) exists but is never called here. A position can appear protected while no broker stop is active.

### GAP 6 (High) — P4 missing-protection is unreachable

**File:** `exits.py:211-219`

```python
def check_missing_protection(position, *, position_unprotected=False):
    if position_unprotected and position.state == PositionState.OPEN:
        return _exit_decision(...)
```

But in `decision_pipeline.py:402`:
```python
position_unprotected = pos.state == PositionState.UNPROTECTED
```

When `state == UNPROTECTED`, the condition `state == OPEN` is False. **The missing-protection exit path can never fire.** The intended safety response is structurally dead.

### GAP 7 (High) — `max_open_risk_pct` is dead

**File:** `hard_filters.py:205-207`

```python
if account.total_open_risk > 0:
    # max_open_risk is checked by the caller with a threshold
    pass
```

The function explicitly comments that it does nothing. The caller (`TradingApp`, `run_pipeline`) never enforces it either. The parameter is accepted but functionally inert.

### GAP 8 (High) — Per-symbol loss cap is a stub

- `_build_risk_state()` (`app.py:174-189`) computes `per_symbol_daily_loss` from **open positions only**. Closed losses disappear.
- `per_symbol_loss_capped` is never derived from `per_symbol_daily_loss` in the app.
- The exit engine accepts `per_symbol_loss_capped` as a hook, but it's never set to `True` anywhere in the runtime path.
- Result: a symbol can lose money, get closed, then re-entered the same session with no memory of the prior loss.

### GAP 9 (High) — Classifier starvation

**File:** `decision_pipeline.py:296-299`

`classify_move_state()` is called with only 5 features:
- `price`, `day_high`, `vwap`, `ema9`, `spread_pct`, `rvol`, `appeared_recently`

The classifier interface (`move_classifier.py:30-76`) defines **25+ features** including `halt_count_today`, `lower_highs_count`, `failed_hod_reclaim`, `consecutive_below_vwap`, `vertical_move`, `has_pullback_formed`, etc. None of these are derived from bars and passed in. The classifier operates on a deeply impoverished feature set.

### GAP 10 (High) — Time gates completely dead

- `hard_filters.py:354-372` defines `is_watch_only_window()`, `is_lunch_window()`, `is_past_entry_cutoff()`, `is_flatten_time()`, `check_time_gate()`
- `TradingApp` stores `entry_cutoff_time` and `flatten_time` but **never computes ET time** and **never passes time-gate flags** to the pipeline
- `run_pipeline()` accepts `et_time` but no caller supplies it
- `check_time_exit()` accepts `et_time` and `flatten_time` but receives neither
- Result: no 9:30-9:35 watch-only, no 15:30 entry cutoff, no 15:55 flatten

### GAP 11 (High) — Startup reconciliation only handles 1 of 8 cases

**File:** `app.py:147-160`

`_reconcile_on_startup()` only processes `"insert_protect"` actions. Six other action types (`"close_local"`, `"cancel_stale_order"`, `"verify_stop"`, `"update_qty_reprotect"`, `"update_qty_reprotect_warning"`, `"irreconcilable"`) are silently ignored.

Even for `insert_protect`, the protection placement gate `pos.stop_price is not None` (`app.py:157`) may prevent placing a stop if the broker snapshot doesn't include it — precisely when one is most needed.

### GAP 12 (High) — Stale broker-side sell hazard

**File:** `paper_execution.py:199-226`

`submit_exit()` transitions state to `EXITING` and creates a sell order without cancelling existing stops. `confirm_exit_fill()` (`paper_execution.py:228`) closes the long locally without verifying conflicting stop orders are removed. If a stale protective sell survives at the broker, local and broker states diverge.

---

## 5. Orphaned Safety Code

| Function / Feature | File:Line | Status | Why It's Dead |
|---|---|---|---|
| `run_pipeline_batch()` | `decision_pipeline.py:430` | **Orphaned** | Never imported or called by `TradingApp` |
| `is_watch_only_window()` | `hard_filters.py:354` | **Orphaned** | Never called by app or pipeline |
| `is_lunch_window()` | `hard_filters.py:359` | **Orphaned** | Never called |
| `is_past_entry_cutoff()` | `hard_filters.py:364` | **Orphaned** | Never called |
| `is_flatten_time()` | `hard_filters.py:369` | **Orphaned** | Never called |
| `check_time_gate()` | `hard_filters.py:374` | **Orphaned** | Never called |
| `check_time_exit()` (P10) | `exits.py:359` | **Orphaned** | Never receives `et_time` |
| `check_runner_trail()` (P11) | `exits.py:371` | **Orphaned** | State never transitions to `RUNNER` |
| `check_missing_protection()` (P4) | `exits.py:211` | **Unreachable** | `position_unprotected` flag contradicts state gate (see Gap 6) |
| `is_symbol_locked_for_entries()` | `state_machine.py:101` | **Orphaned** | `TradingApp` uses `execution_gw.is_symbol_locked()` instead |
| `candidate_stage_index()` | `state_machine.py:142` | **Orphaned** | Never called |
| `candidate_has_reached()` | `state_machine.py:150` | **Orphaned** | Never called |
| `Candidate.lifecycle` | `state_machine.py:132` | **Orphaned** | Never used |
| `PositionStore.save_to_disk()` | `state_machine.py:225` | **Orphaned** | Never called by app |
| `PositionStore.load_from_disk()` | `state_machine.py:232` | **Orphaned** | Never called by app |
| `PositionState.ADDING` | `schemas.py:42` | **Dead state** | No code transitions to it |
| `PositionState.SCALING_OUT` | `schemas.py:43` | **Dead state** | No code transitions to it |
| `PositionState.RUNNER` | `schemas.py:44` | **Dead state** | No code transitions to it |
| `score_candidates()` | `scanner/attention.py:646` | **Orphaned** | Never called; app doesn't rank |
| `detect_themes()` | `scanner/attention.py:303` | **Orphaned** | Never called by runtime |
| `is_symbol_in_theme()` | `scanner/attention.py:346` | **Orphaned** | Never called by runtime |
| `calculate_float_rotation()` | `scanner/attention.py:395` | **Orphaned** | Never called by runtime |
| `float_rotation_label()` | `scanner/attention.py:414` | **Orphaned** | Never called by runtime |
| `setup_allowed()` | `move_classifier.py:367` | **Orphaned** | Pipeline uses `get_allowed_setups()` directly |
| `state_mode()` | `move_classifier.py:380` | **Orphaned** | Never called |
| `scan_manual_watchlist()` | `scanner/scanner.py:80` | **Orphaned** | Never called |
| `scrape_yfinance_gainers()` | `scanner/enrichment.py:196` | **Orphaned** | Never called |
| `_finviz_is_stale()` | `scanner/enrichment.py:252` | **Orphaned** | Never called |
| `AlpacaExecutionGateway` | `paper_execution.py:291` | **Dead class** | Never instantiated by `TradingApp` (defaults to `PaperExecutionGateway`) |

---

## 6. Spec Implications

### SPEC §7.1 — Market structure hard blocks
**STATUS: VIOLATED.** Watch-only window (9:30-9:35) not enforced. Entry cutoff (15:30) not enforced. Flatten time (15:55) not enforced.

### SPEC §7.5 — Account risk hard blocks
**STATUS: VIOLATED.** `max_open_risk_pct` accepted as parameter but never checked. `total_open_risk` computed but not gated against any limit.

### SPEC §9.4 — Entry permission matrix
**STATUS: PARTIALLY MET.** `get_allowed_setups()` and `find_entry()` permission gating works correctly per state. However, the classifier feeding the state is feature-starved (Gap 9), so the permission matrix maps from an unreliable state estimate.

### SPEC §10 — Entry detection
**STATUS: CONDITIONALLY MET.** Six detectors exist and evaluate correctly when `bars` are available. But bars may be silently absent when `MarketSnapshot` is blank (Gap 1), causing all entries to evaluate to `attention_too_low` or `no_bars_for_entry`.

### SPEC §11 — Sizing
**STATUS: MET** (pure math — no runtime bugs discovered).

### SPEC §12 — Exit engine
**STATUS: SEVERELY BROKEN.**
- P4 (missing protection) is unreachable (Gap 6)
- P10 (time exit) is inert (no ET time supplied)
- P11 (runner trail) is inert (positions never reach RUNNER)
- Partial exits collapse to full liquidation (Gap 4)
- Exit tuple-unpacking crashes execution (Gap 2)
- Crisis context fabrication forces false exits (Gap 3)

### SPEC §13 — State machine
**STATUS: VIOLATED.** The transition graph defines ADDING, SCALING_OUT, RUNNER states but no runtime code ever produces them. PENDING_ENTRY can strand on failed confirm. EXITING has no recovery path.

### SPEC §14 — Pipeline integration
**STATUS: VIOLATED.** `run_pipeline()` is overloaded for both candidate evaluation and position monitoring. The monitor path fabricates fake `Candidate` objects with zero price and no source metadata.

### SPEC §15 — Execution and reconciliation
**STATUS: SEVERELY BROKEN.**
- Restart only handles 1/8 reconciliation cases (Gap 11)
- `confirm_fill()` and `confirm_exit_fill()` are optimistic: they mark local state filled/closed even when broker verification fails
- Protection truth is local metadata, not live order truth (Gap 5)
- No cancellation of stale protective orders before exit (Gap 12)
- `AlpacaExecutionGateway` does the above with real money consequences

### SPEC §6.1 — Attention-first processing
**STATUS: VIOLATED.** The runtime processes candidates sequentially from the scanner. No top-N attention ranking occurs before pipeline evaluation.

### SPEC §22.15 — Paper mode requirements
**STATUS: PARTIALLY MET.** JSONL logging works. Market snapshot enrichment exists. But the overall runtime behavior does not match the spec's description of a working attention-first execution system.

---

## Summary of Critical Chain

The worst-case runtime path at Monday 9:30 AM:

1. Bot starts → reconciliation (ignores 7/8 cases)
2. Scanner produces stale (15-20m delayed) candidates
3. **No watch-only enforcement** → entries possible in first 5 minutes
4. Candidates processed sequentially (no ranking)
5. Market snapshot fetched but **any feature computation failure causes data loss**
6. Classifier runs on 5 features instead of 25+ → unreliable state estimate
7. Entry detection depends on bars, which may not exist → `"watch"` or `"skip"`
8. If entry succeeds, `protect_position()` may silently skip (phantom protection)
9. 10s monitor loop → market data blip → price=0, age=999 → emergency exit triggered
10. Emergency exit hits **tuple-unpacking crash** → position not closed
11. If exit somehow works, partial scale-out becomes full liquidation
12. All risk state is computed from open positions only → closed losses vanish
13. Same symbol can re-enter same session after a loss
14. At 15:30, no entry cutoff triggered. At 15:55, no flatten triggered.
