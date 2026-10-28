# Bot Method Review — PRE-REG-DEPTH-01 depth bot

Status: **NO-GO for the first live SHADOW day.**
Scope: `factory/scripts/depth_bot.py`, `factory/scripts/depth_bot_features.py`,
`tests/test_depth_bot.py`, `tests/test_depth_bot_features.py`; reference
`factory/scripts/alpha_adaptive_depth_sizing.py` (arm `thr300bps|p50`) and its frozen replay
artifacts. Read-only; no network, no secrets, no orders, no protected dates (2024-*,
2025-01-*, 2026-06..08) were read. Tests ran with the repo venv (`uv run --no-sync`).

## What was run

1. `uv run --no-sync pytest -q tests/test_depth_bot_features.py tests/test_depth_bot.py`
   → **33 passed in 580.50 s**.
2. Mutation A (record exit clock `HEAD-5` in `empty_trade_record`) → parity test fails:
   `('exit_minute', 826, 831)`.
3. Mutation B (the **actual** exit scheduling in `_settle_entry`, `HEAD-5`) → parity test fails:
   `assert 2 == 1` attempts on 2023-01-19 (the earlier exit frees the slot). This proves the
   parity test exercises the bot's own book/exit loop, not only the research functions.
4. Frozen-artifact scan: three real chosen-arm trades have `t == admit_t`
   (2025-07-31 SNGX t=630, 2026-03-24 RBNE t=660, 2026-04-20 FCHL t=630).
5. Replay of 2025-07-31 with the current code: the t=630 intent is `quote_not_acquired`, the
   bot trades the same symbol at t=635; adding that day to the parity test fails with
   `('t', 635, 630)`. With the one-line fix `self.admitted.update(self.features.admissions)`
   after `features.decision(t)`, 2025-07-31 passes and 2023-01-19 / 2025-03-31 stay green.
6. alpaca-py 0.43.4 source inspection (`.venv/.../alpaca/data/live/websocket.py`) plus a
   no-network probe: `subscribe_quotes` called from a stream handler blocks the event loop
   for >3 s (deadlock).
7. Engine probe: a symbol admitted at snapshot 585 by a quote-driven advance is admitted but
   never subscribed to quotes.
8. Paper probe (FakePaperBroker): a filled paper exit journals `exit_bid=None`,
   `net_usd_25=None`, `exit_slippage=None`.

## Criterion verdicts

### 1. Causality — PASS

* `on_bar` buffers a bar for minute `m` and `_close_minutes` flushes it only when
  `minute_us(m+1)+2 s <= now` (`depth_bot.py:1104-1123`, `1133-1141`); the decision `t=m+1`
  therefore consumes bars with `et <= t-1` (`1142-1160`, `depth_bot_features.py:66-145`).
* The snapshot at `t` reads bars `<= t-1` and freshness `et >= t-2`
  (`depth_bot_features.py:78-104`); the h60 score is `pred_60 >= 0.030` (`:130-145`).
* Intent is scheduled at `t+1:00` (`depth_bot.py:1162-1195`) and the arrival is
  `intent + 250 ms` (`alpha_quote_aware_frequency.py:183,477`).
* The intent facts are read only at/before `intent_us` (`research.intent_facts` →
  `latest_raw`); `_state_row` deliberately nulls the research hindsight proxies
  (`depth_bot.py:1370-1382`).
* Replay, shadow and paper all drive the same `DepthEngine`/`IncrementalDepthFeatures`;
  gating, sizing, limit and simulated pricing are imported from the research module
  (`research.arm_eligibility`, `intent_facts`, `view_quantity`, `reserved_usd`,
  `price_entry_ioc`, `priced_leg`). No decision reads a later print.
* Caveat: paper execution itself is broker-side; the simulated comparator is not run in paper
  (criterion 3).

### 2. Parity is real (not tautological) — PASS (mechanism); coverage defect in F4

* The four-day parity test compares the engine's own trades to the frozen research trades
  field-for-field (`tests/test_depth_bot.py:294-318`); the shadow test additionally compares
  the shadow journal line-for-line to the replay journal (`:378-410`).
* Mutation A and B above fail the parity test, so it is sensitive to the bot's own exit/book
  logic. It is **not** a wrapper around `research.replay_day`.
* However, the four-day sample misses a systematic divergence: 2025-07-31 (and two more known
  days) fail when added (F4). "Parity" is therefore not proven for the lane as a whole.

### 3. Paper-vs-sim comparability — FAIL

* C(i) attempt→fill and C(ii) entry vs arrival ask are journaled: paper records carry
  `entry_ask` from the live `QuoteCache` at the arrival clock (`depth_bot.py:1561-1574`), and
  `broker_fill_price` from the broker (`:1576-1585`).
* C(iii) exit vs sim bid and the would-be-UNKNOWN share, and C(iv) realised vs would-be net,
  are **not** journaled. `_settle_exit` computes `net_usd_*` only when
  `record["exit_bid"] is not None` (`:1793-1800`), but `AlpacaPaperBroker.poll_exit` never
  returns an `exit_bid` (`:737-757`). The probe shows a filled paper exit with
  `exit_bid=None`, `net_usd_25=None` and `position_closed.exit_slippage=None`. The paper record
  is only the real broker outcome plus the entry arrival quote; the sim's would-be entry/exit
  status, exit bid and net are absent. The author's report ("in paper the modelled quote sits
  beside broker_fill_price") is true for the entry only.

### 4. Safety — FAIL (one sub-item; the rest passes)

Passing by code and tests: ARMED + `--paper` fail-closed (`depth_bot.py:2256-2259`), KILL
suppresses entries only (`309-314`, `1384-1406`), $90 realised-loss stop (`327-350`), 15:55
flatten scheduled and tested (`996-1023`), restart attempts/cooldown/no-double-entry
(`1025-1087`), stale/empty/collapsed universe refusal (`1201-1236`, tests at
`tests/test_depth_bot.py:481-540`), secrets never echoed (`2088-2104`).

Failing: a real exit that terminates as canceled/expired/rejected is treated as UNKNOWN and
the position is removed (`AlpacaPaperBroker.poll_exit` `:737-757`; `_settle_exit` `:1815-1820`;
`_close_position` `:1829`), so the 15:55 flatten cannot retry it and
`position_not_flattened` (`:1955-1960`) is unreachable. A real position can be left overnight
with no ALERT. This is the paper-path analogue of the research UNKNOWN convention, but here it
is real money.

### 5. Operational realism — FAIL

* **Deadlock**: `AlpacaSIPStream.bar_handler` calls `stream.subscribe_quotes(...)`
  (`depth_bot.py:2042-2046`) from inside the alpaca-py event-loop handler. In alpaca-py
  0.43.4, `_subscribe` does
  `asyncio.run_coroutine_threadsafe(self._send_subscribe_msg(), self._loop).result()` when
  `_running` (`websocket.py:264-282`), and handlers run on that same loop. The probe blocks
  >3 s; the live run freezes at the first admission (around 09:36), before any quote is
  subscribed.
* **Missed admissions**: when a snapshot decision is computed during a quote-driven advance,
  `on_bar`'s `newly` set does not include it and `on_quote` discards the return
  (`depth_bot.py:1125-1130`). The probe: CCC admitted at 585, never subscribed. Even after
  making the subscription non-blocking, these names trade blind.
* **No session end**: `stock.run()` is called and never stopped (`:2063-2064`); there is no
  `.stop()`, timer or wall-clock check. `run_live` calls `engine.finish()` and
  `write_capture()` only after `source.run` returns (`:2335-2342`), which it does not until
  the process is killed. Consequently no `daily_summary`/`session_end`, no
  `bars/quotes/states` capture, no day roll; after the close the same engine keeps a stale
  `now_us` and the next day's bars are dropped (`advance` returns when the clock goes
  backwards). The supervisor only restarts on an exception (`:2368-2410`), so it waits
  forever.
* The API shapes are otherwise plausible: `StockDataStream(..., feed=DataFeed.SIP)`,
  `LimitOrderRequest(IOC)`, `MarketOrderRequest(DAY)`, `StockBarsRequest`/`StockQuotesRequest`
  with `DataFeed.SIP`, `Adjustment.RAW`, `TimeInForce.IOC` all exist in alpaca-py 0.43.4.

## Blocking defects (ordered)

### F1 — P0: live session never stops; no summary, capture or day roll
`AlpacaSIPStream.run` (`depth_bot.py:2024-2064`) blocks in `stream.run()`; `run_live` only
calls `engine.finish()`/`write_capture()` after it (`:2335-2342`). The first shadow day logs
intents/trades but never a `daily_summary`, never `bars/<day>.parquet` or
`quotes/<SYM>/<day>.parquet`, and the process cannot roll to the next day. Minimal fix: run
`stream.run()` in a worker thread and, from the main thread, call `stream.stop()` at
`minute_us(day, session_end)` (or use an asyncio timer), then `finish()`; make the supervisor
start one session per ET day (re-check the date before re-entering `run_live`).

### F2 — P0: dynamic quote subscription deadlocks the alpaca-py loop
`subscribe_quotes` inside `bar_handler` (`:2042-2046`) blocks the loop thread in
`run_coroutine_threadsafe(...).result()` (alpaca-py 0.43.4 `websocket.py:264-282`). Minimal
fix: never call `subscribe_quotes` from a stream handler; enqueue the names and subscribe from
a worker thread, or pre-subscribe the declared universe and filter in `on_quote`.

### F3 — P0: admissions discovered by a quote-driven advance are never subscribed
`on_bar` returns the admission diff (`:1123`) but `on_quote` returns `None` (`:1125-1130`).
Once F2 is fixed, snapshot decisions computed during a quote advance still leave the new
symbols without a quote subscription. Minimal fix: return the admission diff from `on_quote`
too and subscribe it (via the F2 worker thread), or pre-subscribe the universe.

### F4 — P1: newly admitted symbols miss their t+1 intent
`_flush_minute` updates `self.admitted` before `_decide_tick` (`:1151`), but the snapshot
admission is created inside `features.decision(t)` (`depth_bot_features.py:78-104`), so
`self.admitted` lags one minute and `on_quote` drops all prints of the new symbol until the
next flush. Proven on the frozen rule: 2025-07-31 SNGX t=630 is `quote_not_acquired` and the
bot trades t=635; the parity test fails `t 635 vs 630`. The same pattern exists on
2026-03-24 (RBNE t=660) and 2026-04-20 (FCHL t=630). Minimal fix: update
`self.admitted` immediately after `scored = self.features.decision(t)` in `_flush_decisions`
(`:1163`); with that one line 2025-07-31 passes and the existing parity days stay green.

### F5 — P1 (pre-paper): paper attempts do not journal the sim's would-be outcome
As in criterion 3: add `sim_entry_status` (evaluate `research.price_entry_ioc` on the arrival
quote), `sim_exit_status`, `sim_exit_bid`, `sim_net_usd_*` (evaluate
`research.submission_clock`/`priced_leg` on the same clocks) and `sim_would_be_unknown` to the
paper trade record, keeping the real broker fields separate. Alternatively run a shadow
`SimBroker` engine over the same events. Without this, PRE-REG C(iii)/(iv) cannot be judged.

### F6 — P1 (pre-paper): a non-filling real exit is silently dropped
`AlpacaPaperBroker.poll_exit` returns UNKNOWN for any terminal status other than `filled`
(`:737-757`); `_settle_exit` then closes the position (`:1815-1829`), so the 15:55 flatten
cannot retry and `position_not_flattened` is dead code. Minimal fix: in paper mode keep the
position and alert/retry while the broker position still exists; only close the book when a
fill or a flat reconciliation is confirmed.

## Lower-priority findings

### F7 — P2: restart adoption has no broker fill clock and a skeleton record
`reconcile_positions` fills only `qty`/`entry_price` (`:2201-2234`); `_adopt_position`
defaults `entry_minute` to 0 (`:1042-1045`, due at 01:00) and builds a record without
`intent_us`, `limit_price`, `net_usd_*` and without `parity_excluded` (`:1057-1087`). A crash
between the broker fill and the `fill` journal line, or an overnight position, therefore
sells at the first eligible print with a malformed parity record. Fix: derive the fill clock
from the broker order's `filled_at` (or the journal) and, when unknown, mark the position
`late_exit` + `parity_excluded` instead of using minute 0.

### F8 — P2: the universe guard does not check initial coverage
`_sweep_stats`/`_sweep_verdict` (`:1196-1236`) compare freshness and the *relative* collapse
against the last accepted count, but never against the declared `universe`; a partially
subscribed feed (e.g. an alpaca-py subscription cap on the ~5,633-symbol PIT list) is accepted
as the baseline and poisons ranks/peer features silently. Fix: at the first accepted snapshot
require `alive` (or the count of names with a 09:30 bar) to be a documented fraction of the
declared universe, and journal the ratio.

## Live-only validation list (cannot be closed offline)

1. SIP entitlement and the real subscription limits/throughput for ~5,633 bar symbols plus
   dynamic quote subscriptions; reconnect behaviour.
2. That `stream.stop()` at 16:00 returns `run()` and `finish()` writes the day capture; a
   full first-day lifecycle with no journal, and a restart at 11:00.
3. Real bar/quote timestamp shapes (`timestamp`, `conditions`) and the 09:30 bar for the PIT
   universe; the live `session_end_for_day` (the static calendar has no future early closes;
   an unexpected half-day defaults to 959).
4. Real paper IOC fills/partials/rejects and DAY exit fills between 15:56 and 16:00; the
   ARMED/KILL filesystem paths and the supervisor's behaviour after the close.
5. The 3 known divergence days after F4 (2025-07-31, 2026-03-24, 2026-04-20) and the
   shadow-vs-replay parity on ≥10 sessions (PRE-REG B) — impossible until F1 is fixed.
6. Dedicated paper account (flush_bot adoption conflict), and the `.env` credential names.

## Go/no-go

**NO-GO for the first live shadow day.** F1-F4 are blocking: F1/F2 freeze or truncate the
session; F3 makes the live journal diverge; F4 proves the bot does not implement the frozen
rule on known days. After F1-F4 (F4's fix is one line), re-run the full test file plus the
five-day parity (add 2025-07-31, 2026-03-24, 2026-04-20) and a no-order live stream smoke test.
F5/F6 are required before any armed paper day.
