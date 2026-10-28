# Depth bot method review — round 2 (post-fix)

Status: P0/P1 fixes verified. **GO for the first live SHADOW day (no orders), conditional on the
early-return alert; NO-GO for paper** until the sim-comparator divergence cases (F5 residual) are fixed.

## Verification run

* `uv run --no-sync pytest -q tests/test_depth_bot_features.py tests/test_depth_bot.py`
  → **40 passed, 167 skipped** (default; includes the 7-day parity with 2025-07-31,
  2026-03-24, 2026-04-20).
* Slow sample `DEPTH_PARITY_SLOW=1` on 2023-05-02, 2025-08-19, 2026-01-12 → **3 passed**
  (95 s). The author's full 167/167 run was not repeated; the sample plus the 7-day default
  covers the formerly divergent days and both blocks.
* Mutation (actual exit scheduling `HEAD-5` in `_settle_entry`, /tmp copy, 2023-01-19) →
  parity fails `attempts 2 vs 1`. The parity test remains sensitive to the bot's own book/exit
  loop.
* Stream probes: the deadlock is gone (subscription happens on the caller thread; test
  `test_stream_subscribes_off_thread_and_stops_at_session_end`). A normal socket return
  **before** `session_end` yields `stop_reason=None, error=None` and no raise (see R1).
* Sim probes: (a) sim entry `no_match_at_l1` + real fill → the sim exit is still worked and
  overwrites `sim_fill_status` to `conditional_fill` (the sim would not have had a position);
  (b) sim entry fill + real no-fill → no sim exit/net at all; (c) `broker_fill_price` after a
  close is the exit price (entry broker price only in the `fill` event).

## P0/P1 fix status

* F1 stream lifecycle — **fixed**: socket runs on a worker thread; the caller stops it at
  `session_end_us`, then `run_live` calls `finish()`/`write_capture()`; supervisor re-checks the
  ET date (`depth_bot.py:2261-2386`, `2700-2740`). Test passes.
* F2 deadlock — **fixed**: handlers only enqueue; `subscribe_quotes` runs on the caller thread
  (`:2368-2375`). Test asserts the calling thread is never the loop thread.
* F3 quote-driven admissions — **fixed**: `on_quote` returns the admission diff; the stream
  forwards it (`:1215-1220`, `2337-2353`). Test passes.
* F4 admitted lag — **fixed**: `_sync_admissions()` immediately after
  `features.decision(t)` (`:1268-1272`); 2025-07-31 now SNGX t=630; default parity green.
* F6 real exit lifecycle — **fixed**: canceled/expired/rejected returns `retry`, position kept,
  re-submitted, `exit_retry` + ALERT; still-working at the close → `exit_unconfirmed` +
  `position_not_flattened` (`:1934-1975`, `:2220-2240`). Tests pass.
* F8 universe coverage — **fixed**: `alive >= 50%` of the declared universe, ratio journalled
  (`:1331-1360`).

## Criterion verdicts

### 3. Paper-vs-sim comparability — FAIL (pre-paper)

The normal both-legs-fill case now carries `sim_entry_*`, `sim_exit_*`, `sim_fill_status`,
`sim_would_be_unknown`, `sim_net_usd_0/25` and the test asserts them. But the sim position is
tied to the **real** position:
* Sim no-match/unknown entry + real fill: `_maybe_submit_exit` submits a sim exit
  (`:1912-1916`) even though the sim has no position; `_record_sim_exit` overwrites
  `sim_fill_status` to `conditional_fill` (`:1722-1725`), so the journal reports a sim fill for
  an entry the sim refused (the most important C(i) divergence).
* Sim fill + real no-fill: no real position → no sim exit/net, so C(iii)/(iv) are missing on the
  reverse divergence.
* `broker_fill_price` is overwritten by the exit (`:2039`), losing the entry broker price from
  the trade record; a position kept open at the close has no `trade_result`.
Fix: keep a sim `Position` keyed by the same attempt, drive the sim exit from the sim entry's
own fill, and use separate `broker_entry_price` / `broker_exit_price` fields.

### 4. Safety — PASS for shadow (P2 residuals)

P0 safety mechanisms pass (ARMED/--paper, KILL entries-only, loss stop, 15:55 flatten, restart
attempts/cooldown/no-double-entry, universe refusal, secrets). Residuals, paper-only:
* `exit_retry` has no cap/backoff (`:1934-1959`); a rejected order can be re-submitted on every
  quote. Add a retry cap + backoff/alert.
* F7 remains: an adopted position with no journal fill gets `entry_minute=0` (01:00 due) and a
  skeleton record (`:1042-1087`, `:2201-2234`). Mark it `late_exit` + `parity_excluded` or derive
  the broker `filled_at`.

### 5. Operational realism — PASS with one P1 residual

The deadlock, session stop and admission subscription are fixed and tested against alpaca-py
0.43.4 semantics. Residuals:
* **R1 (P1)**: a normal `StockDataStream.run()` return before `session_end` (e.g. the venue's
  `ValueError("insufficient subscription")` path) is silent: `self.error` is None,
  `stop_reason` is None, `run_live` then writes a completed `daily_summary`/`session_end` for an
  empty day and the supervisor waits for the next day. Fix: after the loop, if
  `stop_reason != "session_end"` and the clock is before the session end, raise/ALERT.
* **R2 (P2)**: `pending.clear()` after the comprehension (`:2369-2371`) can drop a batch
  appended by a handler in the window; use a lock/queue.
* Live-only: SIP entitlement and the ~5.6k-symbol bar subscription limit; the real
  `stream.stop()`/reconnect; real timestamps/conditions; paper IOC/DAY fills and partials; the
  static session calendar for future early closes; dedicated paper account.

## Go/no-go

**GO for the first live SHADOW day (no orders)**, conditional on R1's alert/raise (or active
operator monitoring of `decision_tick`/`snapshot_coverage`). **NO-GO for paper** until the F5
divergence cases, R1, and the retry cap are fixed.
