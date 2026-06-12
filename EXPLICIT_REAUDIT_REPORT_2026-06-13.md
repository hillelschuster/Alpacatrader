Runtime verification:
- `python3 -m pytest tests/ -q` → `714 passed in 7.25s`
- `python3 main.py --mode mock --once` → 3/3 candidates `watch`, 0 entries
- `python3 main.py --mode paper --once` → 20 candidates, 1 `watch`, 19 `skip`, 0 entries
- `python3 main.py --mode paper --loop` started and after 35s logged `Cycle #30: enter=0 watch=0 skip=6 | positions=0`

## I. Verification of Prior Findings

| Claim | Verdict | Evidence |
|---|---|---|
| C1 exit tuple-unpacking bug | **CONFIRMED** | `src/paper_execution.py:199-207`, `src/paper_execution.py:437-439` return `tuple[PendingOrder, PositionStateModel]`; `src/app.py:293-297` stores tuple in `order` then calls `order.order_id`. |
| C2 partial exits ignored | **CONFIRMED** | `src/exits.py:59-68`, `src/exits.py:253-272` create partial `ExitDecision.exit_pct`; `src/app.py:293-295` never forwards it; `src/paper_execution.py:204`, `src/paper_execution.py:220`, `src/paper_execution.py:437-446` default to `100`. |
| C3 phantom stop protection | **CONFIRMED** | `src/paper_execution.py:93` sets `stop_price` at entry creation; `src/paper_execution.py:169-170` returns early on field match; real pending-stop check exists at `src/paper_execution.py:173-177` but is unused. |
| C4 optimistic fill confirmation | **CONFIRMED** | `src/paper_execution.py:378-395` marks Alpaca entry `OPEN` even when order not filled/lookup fails; `src/paper_execution.py:483-490` closes exits even on lookup failure. |
| C5 classifier starvation | **CONFIRMED** | `src/decision_pipeline.py:296-300` passes only `price/day_high/vwap/ema9/spread_pct/rvol/appeared_recently`; rich classifier inputs live at `src/move_classifier.py:30-76`. |
| C6 backside over-trigger | **CONFIRMED** | `src/move_classifier.py:234-238` backside fires on `spread_pct > 1.0 and not _can_reclaim(price, vwap)`; `_can_reclaim` is only within 2% of VWAP at `src/move_classifier.py:335-340`. |
| C7 spread tiers dead | **CONFIRMED** | `src/hard_filters.py:41-54` classifies 4 tiers; only consumer is `src/hard_filters.py:132-134`, and only `hard_reject` changes behavior. |
| C8 scanner delay | **CONFIRMED** | In-repo: `docs/SPEC.md:270-282`, `docs/SPEC.md:365-380`, `src/scanner/scanner.py:71-72` stamps scrape time, not data age. External: Finviz FAQ says free quotes are delayed 15 min Nasdaq / 20 min NYSE-Amex. |
| C9 IEX distortion / feed unset | **CONFIRMED** | Code: no `feed=` in `src/market_data.py:96`, `src/market_data.py:121-125`, `src/market_data_sim.py:63-68`; no feed config in `config/settings.py`. External Alpaca docs: free/basic is IEX-only, request objects support `feed`. |

## II. New Findings

### CRITICAL

1. **Daily-loss and per-symbol-loss accounting is effectively blind**
   - Evidence: `src/app.py:170-205` builds risk state from `self._positions.all_open()` only; `src/state_machine.py:185-188` excludes `CLOSED`; `src/app.py:185-194` computes realized/unrealized only from non-terminal positions.
   - Impact: once a trade closes, its realized loss disappears from `daily_realized_pnl`, `daily_pnl`, and `per_symbol_daily_loss`. The bot can exceed `max_daily_loss` and still keep trading; losing symbols are never session-banned after closure.
   - Fix direction: keep a session P&L ledger separate from open positions, or include closed positions in day totals.

2. **Paper-mode P&L never updates**
   - Evidence: `src/models/schemas.py:216-217` defines `realized_pnl`/`unrealized_pnl`; `src/app.py:185-189` relies on them; `src/paper_execution.py:228-242` closes paper positions without setting `realized_pnl`; only Alpaca exit path writes realized P&L at `src/paper_execution.py:487`; no production path updates `unrealized_pnl`.
   - Impact: in paper loop, account risk is near-zero fiction. Daily loss cap, per-symbol loss cap, and drawdown control are not trustworthy.
   - Fix direction: mark-to-market open positions every monitor cycle and book realized P&L on every fill/partial.

3. **Missing-protection exit path is unreachable**
   - Evidence: `src/decision_pipeline.py:402` sets `position_unprotected = pos.state == PositionState.UNPROTECTED`; `src/exits.py:217-218` only exits when `position_unprotected and position.state == PositionState.OPEN`.
   - Impact: P4 “missing protection” never triggers for `UNPROTECTED` positions, and also misses `OPEN` positions that lack a real stop order.
   - Fix direction: derive protection truth from pending stop/OCO presence, not only local state; make P4 fire for both `OPEN-without-stop` and `UNPROTECTED`.

4. **Startup reconciliation is only partially executed**
   - Evidence: `src/app.py:145-160` handles only `insert_protect`; `src/paper_execution.py:598-629` also emits `verify_stop`, `update_qty_reprotect`, `update_qty_reprotect_warning`; `src/paper_execution.py:589-595` emits `irreconcilable`.
   - Impact: restart recovery ignores most reconciliation actions; broker/local mismatches can persist after startup.
   - Fix direction: handle every returned action explicitly and log each one.

5. **Reconciled broker positions are inserted OPEN but not protectable**
   - Evidence: `src/paper_execution.py:542-549` creates `PositionStateModel(... state=OPEN ...)` with no `stop_price`; `src/app.py:157-160` only calls `protect_position` if `pos.stop_price is not None`.
   - Impact: broker-discovered positions start unprotected after restart.
   - Fix direction: carry required stop information into reconciliation, or mark inserted positions `UNPROTECTED` and escalate immediately.

### HIGH

6. **`main.py --mode paper --once` discards enriched candidate state**
   - Evidence: `main.py:243-261` builds `snapshot`, then calls `run_pipeline(candidate, ...)` instead of `run_pipeline(snapshot.candidate, ...)`; sim mode correctly uses `snapshot.candidate` at `main.py:330-348`.
   - Impact: once-mode mixes delayed Finviz candidate fields with Alpaca bars/spread/quote age.
   - Fix direction: pass `snapshot.candidate`.

7. **Runtime is not actually attention-first**
   - Evidence: `src/app.py:324-365` processes candidates one by one in scanner order; `main.py:240-275` does the same; batch scorer/theme detector `src/scanner/attention.py:303-343,646-689` is unused.
   - Impact: no top-attention ranking before processing, no theme batching, no repeated-scanner bonus, no `top_attention_to_process` cap.
   - Fix direction: batch score first, rank, derive themes, then process top-N.

8. **Time gates and flatten-time are dead in runtime**
   - Evidence: helpers exist in `src/hard_filters.py:354-384`; `run_pipeline` exposes `et_time` at `src/decision_pipeline.py:213`; neither `src/app.py:266-288` nor `src/app.py:341-365` passes time-gate inputs.
   - Impact: no 9:30-9:35 watch-only, no 15:30 new-entry cutoff, no 15:55 flatten.
   - Fix direction: compute ET once per cycle and wire into both entry and exit paths.

9. **Any market-data fetch failure can trigger forced emergency exit context**
   - Evidence: `src/app.py:248-261` falls back to `current_price = 0.0`; `src/app.py:273` sets `quote_age_seconds=999.0`; `src/exits.py:103-105` treats `quote_age_seconds > 60` as full exit.
   - Impact: a transient quote outage can turn into “flatten now” logic with bogus price context.
   - Fix direction: distinguish “no fresh quote” from “current price is zero”; mark `UNPROTECTED`/degraded instead of fabricating price.

10. **State machine declares many states that runtime never uses**
   - Evidence: declared transitions at `src/state_machine.py:34-56`; exit check only evaluates `OPEN`/`UNPROTECTED` at `src/decision_pipeline.py:399-420`; runner trail requires `RUNNER` at `src/exits.py:371-381`.
   - Impact: `ADDING`, `SCALING_OUT`, `RUNNER` are effectively dead; `PENDING_ENTRY` and `EXITING` can strand.
   - Fix direction: either fully wire these states or remove them until implemented.

11. **No scanner fallback in live runtime despite fallback code existing**
   - Evidence: `src/app.py:309-313` logs and returns on scanner failure; fallback functions exist at `src/scanner/scanner.py:80-106` and `src/scanner/enrichment.py:196-249`.
   - Impact: primary scanner failure means no discovery and no `scanner_unavailable` record.
   - Fix direction: wire fallback order and log explicit degraded mode.

### MEDIUM

12. **Remaining silent/broad exception problems**
   - Silent:
     - `src/paper_execution.py:488-489` → `except Exception: pass`
     - `src/scanner/enrichment.py:245-246` → `except Exception: continue`
   - Broad-but-logged hot-loop catches:
     - `src/app.py:253-304,311-312,380-381`
     - `src/exits.py:494-496`
     - `src/entries.py:777`
     - `src/market_data.py:189-195`
   - Impact: some failures still vanish or only weakly surface.
   - Fix direction: fail loud with symbol/op context; do not swallow fill-state failures.

13. **Hard-block reasons are not fully machine-consistent**
   - Evidence: dynamic strings at `src/hard_filters.py:134,141,180`; `kill_switch_reason` passthrough at `src/hard_filters.py:195-200`; `AccountRiskState.is_kill_switch_on` conflates kill switch and daily loss at `src/models/schemas.py:257-258`.
   - Impact: logs are harder to parse and can mislabel `daily_loss_breached` as `kill_switch_active`.
   - Fix direction: use structured reason keys + separate metadata fields.

14. **Dead/unwired production APIs remain**
   - Notable zero-caller definitions:
     - `src/scanner/attention.py:646-689` `score_candidates`
     - `src/scanner/attention.py:303-343` theme helpers (production-dead)
     - `src/hard_filters.py:374-384` `check_time_gate`
     - `src/state_machine.py:225-238` persistence hooks
     - `src/paper_execution.py:271-283` `get_unprotected_positions`
     - `src/scanner/scanner.py:80-106` `scan_manual_watchlist`
   - Impact: architecture claims more capability than runtime actually uses.
   - Fix direction: wire or remove.

## III. Architecture Assessment

### Module decomposition
- **Mostly correct.** `scanner`, `attention`, `hard_filters`, `move_classifier`, `entries`, `sizing`, `execution`, `exits`, `state_machine` are cleanly separated.
- **Main architectural flaw:** `run_pipeline()` is doing too much for two different jobs:
  - candidate entry evaluation
  - open-position exit monitoring
- Evidence: one function handles attention, hard filters, entry detection, execution, exit detection, and logging at `src/decision_pipeline.py:179-427`.
- Result: monitor path rebuilds a fake `Candidate` (`src/app.py:263`) and loses original entry context.

### What should split / merge
- **Split** entry evaluation from position monitoring.
  - Candidate flow should own attention/confidence/hard filters/classifier/entry/sizing.
  - Position flow should own broker truth, protection truth, exit context, and P&L.
- **Add one derived-feature stage** between `MarketSnapshot` and classifier.
  - Current snapshot only carries raw bars/VWAP/EMA9/day_high at `src/decision_pipeline.py:53-72`.
  - Classifier expects derived bar features that are never computed.
- **Persist execution context on position state.**
  - `PositionStateModel` at `src/models/schemas.py:203-220` lacks entry setup, entry spread, pullback low, stop order id, initial shares, etc.

### Data flow quality
Main degradation points:
1. **Scanner age is semantically false**: `src/scanner/scanner.py:71-72` stamps scrape time, not Finviz snapshot time.
2. **Enrichment mutates candidate price but once-mode ignores it**: `src/market_data.py:117-118`, `main.py:245-261`.
3. **Hard filters fabricate bid/ask**: `src/decision_pipeline.py:273-275`.
4. **Classifier receives almost none of the features it was designed for**: `src/decision_pipeline.py:296-300`.
5. **Exit flow loses original entry setup**: `src/decision_pipeline.py:410`, `src/models/schemas.py:203-220`.

### State machine completeness
- Declared state graph is richer than runtime usage.
- Dead/unused states: `ADDING`, `SCALING_OUT`, `RUNNER`.
- Zombie scenarios:
  - `PENDING_ENTRY` on fill failure
  - `EXITING` on confirm failure
  - `UNPROTECTED` with no recovery
- Direct state mutation bypasses `transition_position()` in `src/paper_execution.py:234,490,563,589`.

### Pipeline ordering
- **Correct:** monitor runs before scan in `src/app.py:221-229`; exit priority order in `src/exits.py:440-487` matches SPEC.
- **Incorrect/incomplete:** attention is not ranked before candidate processing; time gating is not wired; emergency exit inputs are sometimes fabricated (`current_price=0.0`).

## IV. Risk and Safety Assessment

- **Can the bot lose more than `max_daily_loss`?** **Yes.**
  - `src/app.py:170-205` only counts non-terminal positions.
  - Closed realized losses vanish.
  - Paper-mode P&L is not updated at all.

- **Can a position exist without stop protection?** **Yes.**
  - C3 phantom protection: `src/paper_execution.py:169-170`
  - Restart insert-protect gap: `src/paper_execution.py:542-549`, `src/app.py:157-160`
  - Missing-protection exit dead: `src/decision_pipeline.py:402`, `src/exits.py:217-218`

- **Can the bot accidentally go short?** **Broker-level oversell risk exists.**
  - `submit_exit()` never cancels conflicting stop orders before sell: `src/paper_execution.py:199-226`, `src/paper_execution.py:437-475`
  - `confirm_exit_fill()` closes locally without resolving live stop state: `src/paper_execution.py:228-242`, `src/paper_execution.py:477-497`
  - Local model has no short state, but broker-side stale sell orders can outlive the local long.

- **Can reconciliation fail silently?** **Yes.**
  - Loop path does not pass `broker_snapshot_fn` from `main.py:399-408`.
  - Startup handler ignores most action types: `src/app.py:153-160`.
  - No decision log of reconciliation actions.

- **Can broker truth diverge from local state indefinitely?** **Yes.**
  - Optimistic fill/exit confirmation: `src/paper_execution.py:378-395`, `src/paper_execution.py:483-490`
  - No ongoing broker polling in loop
  - No persistence recovery beyond startup
  - No protection truth check in hot path

## V. Entry and Exit Viability

### When it can enter now
A current entry requires all of:
- hard filters pass: `src/decision_pipeline.py:306`, `src/hard_filters.py:223-339`
- attention multiplier `> 0.25` → effectively attention score `>= 50`: `src/decision_pipeline.py:307-310`, `src/sizing.py:29-48`
- bars exist: `src/decision_pipeline.py:310`
- current move state allows the setup: `src/decision_pipeline.py:311-318`, `src/move_classifier.py:348-377`
- detector fires
- sizing returns `shares > 0`: `src/decision_pipeline.py:327-338`

### Practical current entry reality
- Production classifier mostly yields `EARLY` or misfires to `BACKSIDE`.
- Practical setups are mostly limited to:
  - `first_pullback`
  - `vwap_reclaim`
- `micro_pullback`, `consolidation_breakout`, `scalp_reclaim`, and effective `active`/`extended` behavior are dead or nearly dead.

### When it can exit now
Exit detection runs only for `OPEN` and `UNPROTECTED` at `src/decision_pipeline.py:399-420`.
Available detectors:
- emergency
- loss caps
- hard stop
- invalidation
- scale-out
- failed reclaim
- VWAP loss
- spread expansion
- volume disappearance
- time exit
- runner trail

But actual viability is much worse:
- execution path is broken by C1/C2/C4
- missing-protection P4 is dead
- runner trail is dead in practice
- exit setup context is usually missing

### Current entry rate
Only directly verified numbers:
- mock once: **0 / 3** entries
- paper once: **0 / 20** entries in the live sample run
- paper loop sample: **0 entries** in observed 35s run

There is no trustworthy repo telemetry for a session-wide percentage, but current architecture strongly suggests **very low to near-zero actual entry conversion**.

### Biggest bottlenecks
1. Scanner delay hidden by fake `source_timestamp`
2. IEX-only quote/bar distortion with no explicit feed control
3. Fabricated bid/ask in hard filters
4. Classifier starvation
5. No batch attention ranking/themes
6. Entry detectors too constrained for the data actually provided
7. P&L/risk state not trustworthy once in a trade

## VI. Recommendations

### If only 3 things get fixed

1. **Fix execution/protection correctness first**
   - Files: `src/app.py`, `src/paper_execution.py`, `src/decision_pipeline.py`, `src/exits.py`
   - Includes: C1, C2, C3, C4, dead missing-protection path, conflicting-order cancellation
   - Complexity: **M**
   - Regression risk: **High**
   - Why first: without this, entries/exits/protection are not trustworthy.

2. **Fix risk-state truth and restart safety**
   - Files: `src/app.py`, `src/models/schemas.py`, `src/paper_execution.py`, `src/hard_filters.py`, `main.py`
   - Includes: real P&L ledger, closed-trade inclusion, `max_open_risk_pct`, per-symbol cap, reconciliation action handling, `broker_snapshot_fn`
   - Complexity: **H**
   - Regression risk: **High**
   - Why second: this is the real guardrail against catastrophic behavior.

3. **Fix candidate/feature flow**
   - Files: `src/market_data.py`, `src/decision_pipeline.py`, `src/move_classifier.py`, `src/scanner/attention.py`, `src/app.py`, `main.py`
   - Includes: derived bar features, attention ranking before processing, theme wiring, `snapshot.candidate` fix, real quote inputs
   - Complexity: **H**
   - Regression risk: **Medium**
   - Why third: this is what turns the bot into an execution machine instead of a watcher.

### If 10 things get fixed

| Rank | Change | Files | Complexity | Regression risk |
|---|---|---|---|---|
| 1 | Fix exit tuple + partial exit plumbing | `src/app.py`, `src/paper_execution.py` | S | High |
| 2 | Fix optimistic fill/exit confirmation | `src/paper_execution.py` | M | High |
| 3 | Fix protection truth + P4 missing-protection exit | `src/paper_execution.py`, `src/decision_pipeline.py`, `src/exits.py` | M | High |
| 4 | Implement real session P&L ledger and mark-to-market | `src/app.py`, `src/models/schemas.py`, `src/paper_execution.py` | M | High |
| 5 | Enforce `max_open_risk_pct` and per-symbol loss cap | `src/hard_filters.py`, `src/app.py`, `src/decision_pipeline.py` | M | Medium |
| 6 | Handle all reconciliation actions and wire broker snapshots in loop | `src/app.py`, `main.py`, `src/paper_execution.py` | M | Medium |
| 7 | Use `snapshot.candidate` in paper once path | `main.py` | S | Low |
| 8 | Batch-rank attention/themes before processing | `src/app.py`, `main.py`, `src/scanner/attention.py` | M | Medium |
| 9 | Build classifier feature extraction layer | `src/market_data.py` or new module, `src/decision_pipeline.py` | H | Medium |
| 10 | Wire time gates/flatten and scanner fallback | `src/app.py`, `main.py`, `src/hard_filters.py`, `src/scanner/scanner.py` | M | Low |

## VII. Test Gaps

1. **App-level exit execution regression**
   - File: `tests/test_phase10_app.py`
   - Prove: `run_pipeline` exit decision → `submit_exit` unpacking → `confirm_exit_fill`
   - Would have caught C1 and C2.

2. **Risk-state ledger tests**
   - File: `tests/test_phase10_app.py` or new `tests/test_phase10_risk_runtime.py`
   - Prove:
     - closed trade realized loss remains in daily totals
     - open mark-to-market updates `daily_unrealized_pnl`
     - per-symbol loss cap persists after close

3. **Protection-truth tests**
   - Files: `tests/test_phase7_execution.py`, `tests/test_phase8_exits.py`, `tests/test_phase10_app.py`
   - Prove:
     - `submit_entry → confirm_fill → protect_position` creates a real pending stop
     - `UNPROTECTED` or `OPEN-without-stop` triggers correct emergency behavior

4. **Startup reconciliation app-level full matrix**
   - File: `tests/test_phase10_app.py`
   - Missing app-context cases: qty match, broker qty more, stale pending orders, irreconcilable, broker unreachable
   - Prove: every returned reconciliation action is acted on.

5. **Time-gate integration**
   - File: `tests/test_phase10_app.py`
   - Prove:
     - 9:30-9:35 watch-only
     - 15:30 cutoff blocks new entries
     - 15:55 forces flatten

6. **Paper-once candidate/snapshot regression**
   - File: `tests/test_cli_rebuild.py`
   - Prove: paper once path passes `snapshot.candidate`, not raw scanner candidate.

7. **Batch attention/theme ranking integration**
   - File: `tests/test_phase10_app.py`
   - Prove: top candidates are ranked by attention before processing, theme flags are populated, top-N limiting works.

8. **Scanner fallback / `scanner_unavailable` record**
   - File: `tests/test_phase10_app.py`
   - Prove: primary scanner failure uses fallback or logs degraded mode explicitly.

9. **Replace broad detector assertions**
   - File: `tests/test_phase5_entries.py`
   - Current weak examples: `tests/test_phase5_entries.py:217-223`, `:269-276`, `:305-312`, `:342-347`, `:388-393`, `:432-436`
   - Prove exact `entry_setup`, `entry_price`, `stop_price`, `risk_per_share`.

10. **Replace broad pipeline assertions**
    - File: `tests/test_phase9_pipeline.py`
    - Current weak examples: `tests/test_phase9_pipeline.py:305-317`, `:378`, `:552-553`, `:573`
    - Prove exact decision and exact entry/exit fields, not `decision in ("watch", "enter")`.

Bottom line:
- Philosophy: **good**
- Module split: **mostly good**
- Runtime wiring: **still not trustworthy**
- Deepest systemic weaknesses: **execution truth, risk truth, feature/data truth**
