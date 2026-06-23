# Alpacatrader — Consolidated Deep Research Report

**Date:** 2026-06-12
**Sources:** Three independent AI research audits, consolidated.
**Repository:** `https://github.com/hillelschuster/Alpacatrader`
**Source of truth:** `docs/SPEC.md`

---

## EXECUTIVE VERDICT

Strictly judged against the SPEC philosophy — *"top gainer + attention + definable risk = potential trade"* — the current bot is **not yet an execution machine**. It is a partially rebuilt system with a much cleaner philosophy than the old rejection engine, but the runtime has three severe chokepoints:

1. **Discovery is structurally late** — Finviz free-tier delayed data means the bot finds historical snapshots, not current movers.
2. **Move classification is starved** — the pipeline doesn't pass bar-derived features to the classifier, making `active`, `extended`, and `halt_risk` states effectively unreachable.
3. **Exit execution is broken** — the paper-loop exit path mishandles `submit_exit()` return tuples, failing exits and stranding positions.

The rebuild philosophy is correct. The implementation does not yet embody it. The bot can discover candidates, calculate some attention, detect some clean setups — but it cannot reliably classify active momentum, cannot use most entry detectors, cannot place real stop protection, and cannot reliably execute exits.

---

## CRITICAL BUGS — BROKEN RIGHT NOW

### 1. Exit Path Tuple Mismatch (P0)

**`src/app.py:293`** — `_monitor_positions()` does:
```python
order = self._execution.submit_exit(...)
self._execution.confirm_exit_fill(order.order_id)
```

`submit_exit()` returns `tuple[PendingOrder, PositionStateModel]` in both the paper and Alpaca gateways (`src/paper_execution.py`). Assigning a tuple to a single variable makes `order.order_id` an `AttributeError`. The exception is caught and logged, so the failure is noisy in logs but operationally silent — the loop proceeds without completing the exit.

**Impact:** Exits fail silently. Positions stay `EXITING` because `submit_exit()` transitions the state before returning, but `EXITING` is excluded from future exit checks (`decision_pipeline.py` — checks only `OPEN`/`UNPROTECTED`). Symbol permanently locked.

**Fix:** Unpack as `order, pos = self._execution.submit_exit(...)`.

### 2. Partial Exits Ignored — Scale-Outs Become Full Exits (P0/P1)

`exit_decision` carries `exit_pct` (33%, 25%, 50%, etc.). The app call at `src/app.py:293` passes **only** `symbol` and `reason`. `submit_exit()` defaults `exit_pct=100`. So even when the exit engine correctly decides "sell 33% at +1R", the app-level executor says "sell everything."

**Impact:** The bot cannot actually implement partial scaling-out. Every exit decision becomes a full position closure.

**Fix:** Pass `exit_pct=result.exit_decision.exit_pct`, `exit_price=result.exit_decision.exit_price`, `pnl=result.exit_decision.pnl` to `submit_exit()`.

### 3. Stop Protection Is Phantom Protection (P0)

`submit_entry()` creates a local position with `stop_price=signal.stop_price`. After fill confirmation, `protect_position(symbol, stop_price, qty)` is called. But `protect_position()` immediately returns `None` when `pos.stop_price == stop_price` — treating a local model field as proof of real stop protection. It does not verify a pending stop order exists.

**Impact:** The bot can believe a position is protected while no stop order was ever placed at the broker. The `get_unprotected_positions()` function would detect this (it checks for pending stop orders), but it's not called in the monitor loop.

**Evidence:** `src/paper_execution.py:151-167`.

### 4. Fill Confirmation Is Not Real Confirmation (P0/P1)

In the Alpaca gateway (`paper_execution.py:388-392`), `confirm_fill()` checks the broker order status, but if the check fails or the order isn't filled, it logs an exception, waits 0.5 seconds, then transitions the position to `OPEN` anyway. `confirm_exit_fill()` similarly closes locally regardless of broker verification.

**Impact:** Local state can become `OPEN` without broker fill verification. The bot may place stops and exits against phantom positions.

### 5. Exit Context Lost — Setup-Specific Invalidation Goes Blind (P1)

The pipeline passes `entry_setup=result.entry_signal.entry_setup.value` into the exit layer, but `PositionStateModel` does not persist the original `entry_setup`. If the monitoring cycle doesn't generate a fresh entry signal, the exit engine receives `entry_setup=None` and loses setup-specific invalidation logic (HOD reclaim failure, consolidation breakout failure, etc.).

**Impact:** The strategy exits based on whatever can be re-inferred that cycle, not the original entry conditions.

**Fix:** Persist `entry_setup`, `entry_spread`, `pullback_low`, `consolidation_low`, `prior_hod`, `original_risk_per_share`, `initial_shares`, `stop_order_id` on the position model.

### 6. Daily Loss Cap Half-Implemented (P1)

The daily loss cap successfully blocks new entries when breached. But it does NOT market-exit existing positions or cancel pending orders. The exit check `check_loss_caps()` depends on `daily_loss_breached=True`, which is not reliably passed from the app to `run_pipeline()` in all call paths.

**Evidence:** `src/exits.py:124-141`, `src/app.py:256-277`.

---

## ARCHITECTURAL FLAWS — DESIGNED WRONG

### 1. The Classifier Starvation Problem (Highest impact structural flaw)

`src/decision_pipeline.py:296-300` — the production call to `classify_move_state()` passes only: `price`, `day_high`, `vwap`, `ema9`, `spread_pct`, `rvol`, `appeared_recently`.

But the classifier (`src/move_classifier.py`) is explicitly designed to consume a much richer feature set:
- `lower_highs_count`, `failed_hod_reclaim`, `consecutive_below_vwap`, `failed_vwap_reclaim`
- `vertical_move`, `vertical_without_pullback`, `has_pullback_formed`, `pullback_low`
- `nearest_stop_distance_pct`, `hod_behavior_repeated`, `higher_low_structure`, `pullbacks_bought`
- `strong_volume`, `candle_range_gt_2x_avg`, `price_moved_pct_5m`, `halt_count_today`

The file's docstring says bar-data parsing is the caller's responsibility. The caller simply doesn't do it.

**Consequences:**
- **`active` state is unreachable** — requires ≥3 of 5 core signals. Only `rvol≥2.0` and `spread_pct≤3.0` can fire from passed data. At most 2 signals.
- **`extended` state is unreachable** — depends on `nearest_stop_distance_pct`, `pullback_low`, `candle_range_gt_2x_avg`, `vertical_without_pullback`. None passed.
- **`halt_risk` is unreachable** — `halt_count_today` not passed, vertical move features not passed.
- **Production states collapse to `early` or `backside`** — with heavily restricted entry setup permissions.

### 2. BACKSIDE Over-Triggered by Spread+VWAP Distance

`move_classifier.py:234` — `_is_backside()` marks "backside" when `spread_pct > 1.0` AND `not _can_reclaim(price, vwap)`. `_can_reclaim()` returns false if VWAP missing or price >2% away from VWAP.

Top gainers often trade far above VWAP with spread 1-5%. Any spread above 1% pushes them into `BACKSIDE`, which allows only `vwap_reclaim`. VWAP reclaim requires proximity to VWAP, which a top gainer far above VWAP cannot satisfy. The stock is permanently stuck.

**This is backwards for the philosophy.** A top gainer far above VWAP with 1-3% spread may be risky, but it is not automatically "backside/fading."

### 3. Production States → Crippled Entry Permissions

| State | Allowed Setups | Reachable in production? |
|-------|---------------|------------------------|
| `early` | first_pullback, vwap_reclaim | **Yes (when spread ≤1% or near VWAP)** |
| `active` | first_pullback, micro_pullback, hod_reclaim, consolidation_breakout, vwap_reclaim | **NO — starved of features** |
| `extended` | first_pullback, hod_reclaim, vwap_reclaim, scalp_reclaim | **NO — starved of features** |
| `backside` | vwap_reclaim only | **Yes (spread>1% + far from VWAP)** |
| `halt_risk` | scalp_reclaim only | **NO — starved of features** |

**Bottom line:** The bot has 6 detectors but in practice can use only 2 (first_pullback, vwap_reclaim), and in backside state often only 1 (vwap_reclaim, which itself fails on extended runners).

### 4. The Six Detectors — Production Reality

| # | Detector | Min Bars | Core Requirements | Production Reality |
|---|----------|----------|-------------------|-------------------|
| 1 | **first_pullback** | 5 | Strong surge (3-20 bars) → 2-8 bar pullback (≥20% retrace) → green reclaim with vol>1.2× pb avg → controlled selling → near logical level | **Reachable in early state.** Structured, rigid. Misses ugly-but-tradeable momentum tapes. |
| 2 | **hod_reclaim** | 5 | Prior HOD → pulled below → closes back above → volume gate | **Blocked.** Needs active/extended states (unreachable). Also relies on `prior_hod` from only 20 bars. |
| 3 | **consolidation_breakout** | 6 | 5-20 bar range ≤2% of price → within 3% of HOD → vol≥50% prior → breakout candle | **Dead.** Needs active state (unreachable). 2% range impossible for volatile runners. Checks exactly 5 bars (`bars[-6:-1]`). |
| 4 | **micro_pullback** | 6 | 1.5× avg_range advance over 3-5 bars → 1-3 red dips → green reclaim above peak → vol≥1.5× dip | **Dead until active fixed.** Also misses sub-minute structures (no tick data). |
| 5 | **vwap_reclaim** | 3 | Prior bars near VWAP → current bar closes above → spread≤5% → vol≥50% recent | **Reachable in early/backside.** But top gainers trade far above VWAP — "near VWAP" rarely satisfied. When it pulls back to VWAP, upward momentum has likely failed. |
| 6 | **scalp_reclaim** | 4 | 1-2 red dips → green reclaim. Spread ≤3%, quote ≤5s, stop ≤ min(1% price, avg_range) | **Dead.** Needs extended/halt_risk (unreachable). Spread ≤3% blocks 90%+ of top gainers. Quote ≤5s hard with polling. |

### 5. Scanner/Data Reality — Garbage In?

- **Finviz free screener:** 15-20 minute delayed HTML scrape. The bot finds historical snapshots, not current movers. For a strategy whose edge is reaction speed, this is a direct contradiction.
- **Alpaca IEX free tier:** Single exchange. IEX accounts for ~2-3% of US equity volume. Spreads on IEX are systematically wider than NBBO (National Best Bid and Offer). A stock that looks like 5.5% spread on IEX might be 2.8% on the consolidated market. The bot rejects on artificially inflated data.
- **20 bars only:** `market_data.py` fetches only 20 one-minute bars, then calculates VWAP over that window, `day_high` as max of those bars, `prior_hod` as second-highest in that window. These are not true session VWAP, not true HOD. Entry logic treats them as actual market structure.
- **$1 focus_price_min:** `TradingApp.__init__` defaults invite ultra-low-priced names where a few cents of spread becomes several percent.

### 6. Spread Tiers — Classified but Never Consumed

`spread_tier()` (`hard_filters.py:41-54`) classifies four tiers: `normal` (≤1%), `caution` (1-3%), `tiny_scalp` (3-5%), `hard_reject` (>5%). But:
- Only `hard_reject` has any effect (blocks entry).
- `caution` and `tiny_scalp` are computed then discarded. Nothing reads them.
- `sizing.py` `entry_sizing()` has no spread tier parameter.
- Soft warnings for spread map to **1.0 multiplier** (`attention.py:615-616`) — zero penalty.

**The SPEC says spread should mean "smaller, faster, stricter exit" — not simply "reject above 5%."**

### 7. Account Risk — Uneven Enforcement

The hard-filter layer has a comment: `"max_open_risk is checked by the caller"` with a `pass`. Per-symbol daily loss caps exist in the model but are not wired into the app monitor call. Theme concentration logic exists in `attention.py` but the app processes candidates one-by-one without batch theme detection.

### 8. Synthetic Bid/Ask in Production

Before hard filters run, `decision_pipeline.py` estimates bid and ask as `price * 0.999` and `price * 1.001` when actual bid/ask are not passed. The execution-data gate is partially evaluating fabricated quotes. This is intentional for mock mode but shouldn't be the production path.

### 9. No Persistent State

`PositionStore` uses in-memory dicts (`state_machine.py`). `save_to_disk()` exists but is never called. On crash/restart, all position history is lost. Reconciliation depends entirely on broker availability. If broker is unreachable at startup, there is no local state to fall back on.

---

## OVER-ENGINEERED VS UNDER-WIRED

### Over-Engineered (interfaces promise more than the production path delivers)

1. **Move classifier** — rich feature interface, but the pipeline passes almost nothing. Creates false confidence that the classifier is doing real work.
2. **Entry permission matrix** — complex state→setup mapping, but states are mostly unreachable. The matrix gates on conditions that never occur in production.
3. **Spread tiers** — four tiers, only one consumed. The tier infrastructure promises nuance that doesn't exist.
4. **Soft warnings** — elaborate mapping of 30+ conditions to multipliers, but many map to 1.0 (no penalty). Zero-penalty warnings are dead code.
5. **Partial exit logic** — detector emits percentages, but executor ignores them. The scale-out architecture is present but the runtime flattens everything.
6. **Time gate helpers** — `is_watch_only_window`, `is_lunch_window`, `is_past_entry_cutoff` defined in `hard_filters.py`, but the scan pipeline doesn't use actual ET time gates.

### Under-Wired (code exists but isn't connected)

1. **Classifier input features** → no extraction from bars into classifier arguments.
2. **HOD/ROC attention scoring** → function supports it, pipeline doesn't pass the data.
3. **Theme detection** → module exists, app doesn't batch-detect themes before pipeline.
4. **Spread tiers** → classification exists, sizing/mode don't consume them.
5. **Exit percentages** → exit detector emits them, app ignores them.
6. **Entry setup context** → exit invalidation needs setup context; position state doesn't store it.
7. **Broker truth reconciliation** → SPEC says broker truth wins, but paper/local state dominates execution.
8. **`broker_snapshot_fn` in paper loop** → `main.py` creates `TradingApp` without passing it; reconciliation is dead code in the main CLI path.

---

## THE IEX DATA PROBLEM (Detailed)

Alpaca's free Basic plan provides IEX data only. IEX = a single exchange handling ~2-3% of US equity volume. The NBBO (National Best Bid and Offer) aggregates all exchanges (NYSE, NASDAQ, ARCA, BATS, etc.).

**What this means in practice:**
- A liquid momentum stock with actual NBBO spread of 0.8% can show 2-4% spread on IEX due to missing market makers on that specific venue.
- A stock rejected at 5.5% on IEX might be 2.8% on the full market — a false positive rejection.
- VWAP and EMA9 computed from IEX volume represent a tiny, non-random sample. These indicators are mathematically distorted.

**No `feed=` parameter is passed** to Alpaca requests — the effective feed is whatever the account's default entitlement provides. If free tier → IEX.

---

## ENTRY DETECTOR THRESHOLD ANALYSIS

| Detector | Parameter | Current | Could Loosen To | Rationale |
|----------|-----------|---------|----------------|-----------|
| first_pullback | min surge % | 5.0% | 3.5% | Many top gainers show 3-4% micro-surges within 20 bars |
| first_pullback | retrace min | max(20%, 1×ar) | max(15%, 0.75×ar) | Shallower pullbacks common in momentum; risk: slightly wider stops |
| first_pullback | controlled selling vol | ≤70% surge | ≤85% | More lenient on pullback selling pressure |
| consolidation_breakout | max range | 2.0% of price | 3.0% | 2% range unrealistically tight for volatile runners |
| micro_pullback | min advance | 1.5× ar | 1.2× ar | Would catch more micro-pauses during uptrends |
| vwap_reclaim | "near VWAP" tolerance | 1% | 3% | More stocks qualify as "near enough" |

---

## RECOMMENDATIONS — RANKED BY IMPACT

### Tier 1: Must Fix Before Paper Evaluation (P0)

1. **Fix the exit path tuple unpacking.** `src/app.py:293` — unpack as `(order, pos)` and pass `exit_pct`, `exit_price`, `pnl` from `ExitDecision`. Add an end-to-end test proving partial exits stay partial and full exits close positions.

2. **Fix fill confirmation.** Don't transition to `OPEN` unless broker confirms fill. Add retry loop with timeout. Don't mark exits closed without verification.

3. **Fix stop protection.** `protect_position()` must check for actual pending stop orders, not only the model field. Place a real stop order and verify it exists.

### Tier 2: Unlock the Entry Engine (P1)

4. **Build a bar-feature extraction layer.** Before calling `classify_move_state()`, compute from the 20 bars (preferably more):
   - `lower_highs_count`, `failed_hod_reclaim`, `consecutive_below_vwap`, `failed_vwap_reclaim`
   - `vertical_move`, `vertical_without_pullback`, `has_pullback_formed`, `pullback_low`
   - `nearest_stop_distance_pct`, `hod_behavior_repeated`, `higher_low_structure`, `pullbacks_bought`
   - `candle_range_gt_2x_avg`, `price_moved_pct_5m`

   Then pass them to `classify_move_state()`. This single change unlocks `active`, `extended`, and `halt_risk` — and therefore unlocks 4 of 6 detectors.

5. **Persist entry context on the position.** Store: `entry_setup`, `entry_spread`, `pullback_low`, `consolidation_low`, `prior_hod`, `original_risk_per_share`, `initial_shares`, `stop_order_id`. This lets exit invalidation work correctly.

6. **Fix the backside over-trigger.** Raise `spread > 1.0` gate to `spread > 5.0` OR add a volume-fading requirement. A stock up 80% with 2% spread should not be labeled "backside."

### Tier 3: Replace Rejection with Sizing (P1)

7. **Replace binary spread rejection with a sizing/down ladder:**
   ```
   0-2% → 1.00× normal starter
   2-4% → 0.75× caution starter
   4-7% → 0.50× tiny/scalp only
   7-10% → 0.25× scalp only, must have fresh quote, exit at +0.5R
   >10% → reject
   ```
   Wire the tier into: allowed setups, entry sizing, stop-width minimums, emergency exit thresholds, partial speed.

8. **Add a "momentum micro-starter" fallback entry.** If a stock is at/near HOD, has fresh quote, acceptable spread tier, high attention, and a nearby structural stop — enter with 25-50% normal size even if no textbook pattern fires. This aligns with "find a way to participate."

### Tier 4: Improve Scanner and Data (P2)

9. **Raise `focus_price_min`** from $1.00 to $3.00 or $5.00. Sub-$1 stocks have spreads that make systematic trading impossible.

10. **Add a second scanner source.** The TradingView public scanner API (`scanner.tradingview.com`) provides real-time data with RVOL, sector, pre/post-market gainers — no API key, no auth, structured JSON. This would provide fresher, richer candidate discovery than Finviz alone. **Note:** This is the public scanner endpoint used by TradingView's own UI — not an "official vendor API."

11. **Compute true session VWAP/HOD when possible.** Don't call a 20-bar high "day_high."

12. **Pass `feed=DataFeed.IEX` explicitly** to Alpaca requests so the data source is documented and switchable to SIP when subscribing.

### Tier 5: Paper Loop and Testing (P2)

13. **Wire the paper loop fully.** `_run_paper_loop()` in `main.py` must pass `broker_snapshot_fn` so startup reconciliation works.

14. **Add integration tests for critical paths:**
    - Monitor detects exit → app submits correctly → gateway confirms → position transitions → partial exits remain partial
    - Entry → confirm fill → protect stop → assert pending stop order exists
    - Top-gainer with adequate features → reaches `active` state through full pipeline
    - Emergency exit outranks hard stop in monitor path
    - `submit_exit()` integration test that would have caught the tuple bug

15. **Implement database-backed state.** Replace volatile in-memory dict with SQLite or file-based JSON. On startup, execute reconciliation sweep against Alpaca API.

---

## ENTRY BOTTOM LINE

**Can the bot enter top gainers?** Yes, but much less often than intended, and often for the wrong subset of setups. The current production path enters only when: hard filters pass, attention multiplier > 0.25, bars exist, classifier state permits setup, detector fires, sizing produces ≥1 share. Because states collapse to `early`/`backside`, most detectors never get a chance. The bot watches stocks it wants to trade but has no tool to enter them.

## EXIT BOTTOM LINE

**Can the bot exit positions?** Exit detection is more mature than entry detection. The emergency exit framework, hard stops, scale-outs, invalidation, and time exits are well-structured. But exit execution is broken — the tuple mismatch means exits fail silently, partial exits become full closes, and setup-specific invalidation disappears without stored context. **Exit detection: partly good. Exit execution: unreliable.** The system cannot be trusted even in paper logs until fixed.

---

## FINAL ASSESSMENT

The rebuild philosophy is correct: top gainer + definable risk = potential trade. The bot should enter small, add only when right, scale out fast, and exit before damage becomes uncontrolled.

The current implementation does not yet embody this philosophy. It can discover candidates and detect some setups, but it cannot reliably classify active momentum, cannot use most of its entry detectors, cannot place real stop protection, cannot reliably execute exits, and cannot implement scale-outs as designed.

The fix sequence: **execution state correctness first → classifier feature wiring second → spread-as-sizing third.** After these three changes, the bot becomes worth paper-trading seriously.
