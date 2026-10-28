# Depth bot runtime — replay / shadow / paper (round 2: pre-arming gaps closed)

Status: **all items closed, prior tests still green.** `uv run --no-sync pytest tests/test_depth_bot.py -q` → 29 passed; ruff clean on both files (line length 100). No
network in tests, no secrets read or printed, no protected dates touched, the frozen rule and
the four-day replay parity are unchanged (verified field-for-field again this round).

## Files

* `factory/scripts/depth_bot.py` — runtime (+3 gaps, +supervisor subcommand).
* `tests/test_depth_bot.py` — parity + safety + one test per new item.
* Nothing else. The supervisor is a `supervise` subcommand of the same file (no new file).

## 1. Universe sweep: REST repair + refusal

* `HistorySource` protocol (`bars`, `quotes`) with `AlpacaHistory` — batched SIP REST
  (`REST_SYMBOLS_PER_REQUEST=500`) behind `TokenBucket(per_minute=150)`; the clock and sleep
  are injectable, so the rate-limit test never waits.
* Every snapshot minute `T ∈ {575,585,600,630,660,720}` has a scheduled `snapshot` task. The
  guard computes `alive / fresh (et ≥ T-2) / newest` over the prints actually observed and
  refuses the session when the sweep is `universe_empty`, `universe_stale` (nothing newer than
  `T-2`) or `universe_incomplete` (<50% of the last accepted fresh set).
* On a failed verdict the engine first tries the REST repair (`REPAIR_LOOKBACK_MIN=6`,
  universe-wide, rate limited); if the repair fails or does not fix coverage it journals
  `universe_refused`, writes an `ALERT` line, and refuses **new entries for the rest of the
  session** (`skip_reason="universe_refused"`); exits keep running.
* Guards need a declared universe (`DepthEngine(universe=...)`); `replay_day`/`run_live` pass
  the stored tickers / the PIT list. Replay of the four parity days never refuses (verified).

## 2. Mid-day restart: exact state rebuild

`DepthEngine.bootstrap(resume_us=...)`:

1. `merge_bars(history.bars(session_start, resume))` — monotonic-only merge (real tapes have
   gaps; never ffill, never duplicate);
2. replay every snapshot `≤ resume` through `feature_frame` so admissions/ranks/`admit_t` are
   exactly the uninterrupted ones;
3. `history.quotes(...)` for the admitted set into `QuoteCache` (a REST failure is an `ALERT`
   `backfill_failed`, never a crash);
4. `now_us = resume_us`, journal `session_resumed` (bars/quotes/admitted counts);
5. decide only the tick whose bar window just closed (`minute_us(t) ≤ resume < minute_us(t+1)`)
   so that tick's intent is not lost and no future tick is decided early.

`on_bar` drops bars at/below the rebuilt state and duplicates per ticker. `run_live` derives
`resume_us` with `restart_clock()` (a `session_start` without `session_end` inside RTH).

Test: restart on 2023-01-19 at 11:00 (one bar call + one quote call) then stream the remainder:
every post-11:00 `decision_tick`, `signal`, `intent` and `trade_result` is **identical** to the
uninterrupted replay journal (the 12:50 GNS attempt included).

## 3. Partial exits

`Position` now carries `filled_qty / exit_notional / realized_usd / partials`. A closed exit
outcome that fills less than the position: journals `exit_partial` (tranche, remaining, price,
tranche P&L, index), tags the record `parity_excluded="partial_exit"`, re-arms the remainder at
the current clock and works a **fresh marketable order** until it fills or 15:55 (then the
flatten path forces it); an unresolved remainder at the session end writes
`ALERT kind=partial_exit_unfilled`. Test: 40 of 97 shares then the rest → two sell orders,
`exit_partial` present, close reports the full 97 and the summed realised P&L.

## 4. Alerting + supervisor

`data/forward/depth_bot/ALERT` — one JSON line per alert (`ts`, `kind`, `day`, `mode`, fields)
via `write_alert`; the same alert is journalled as `ALERT` (deterministic, so replay == shadow).
Kinds: `universe_refused`, `backfill_failed`, `loss_stop`, `broker_unexpected` (paper),
`partial_exit_unfilled`, `position_not_flattened`, `previous_session_incomplete`, `crash`,
`supervisor_giving_up`, `kill_stop`.

`supervise(run_session, kill_path, alert_path, backoff_s, max_restarts, max_sessions)`
(+ `depth_bot supervise --live-mode shadow|paper`): restarts on any exception with a backoff,
alerts each crash, gives up after `max_restarts` (exit 2), and stops the moment `data/KILL`
appears (alert `kill_stop`). Tests: two crashes then success; give-up; KILL stops.

## 5. Day roll + states parquet

* `session_start` is journalled on construction (after a `fresh` truncate or a live recovery);
  `finish()` writes `daily_summary`, then `session_end` (mode, trade count, realised P&L,
  refusal, parity-excluded count). A crashed session (start without end) is closed on the next
  start with `rolled_over=true` plus an `ALERT previous_session_incomplete`.
* `states/<day>.parquet` — the exact `research.STATE_COLUMNS` rows that drove each intent
  (day, ticker, t, admit_t, entry_*, pred_60, intent_us, intent facts, limit, qty_capital).
  Path: `<journal_root>/../states/<day>.parquet`. Test asserts schema and coverage of the
  journalled signals. `proxy_gross_60` stays `None` (it needs the entry minute's own bar).

## 6. Parity tagging

Trade records carry `parity_excluded` + `parity_excluded_reason` (comma-joined when several
apply). Reasons today: `mode_paper` (a real broker fill is not the simulated one),
`partial_entry`, `partial_exit`, `flatten_forced`, `late_exit` (adopted position whose exit
clock already passed). `daily_summary["parity_excluded"]` counts them. Sim records on the
parity days stay `parity_excluded=false`.

## Prior guarantees (re-verified)

* Replay parity unchanged: 2023-01-19 (1), 2023-01-24 (1), 2025-03-31 (2), 2025-04-01 (2) —
  identical attempts and identical `ticker, t, entry_minute, intent_us, limit_price, qty,
  qty_capital, entry_*, exit_*, fill_status, net_usd_25, attempt_index, positions_at_entry`.
* Shadow journal == replay journal line for line (now including `session_start`,
  `snapshot_coverage`, `session_end`); `AlpacaSIPStream` is patched to fail in the test.
* Safety: ARMED/`--paper` fail closed, KILL blocks entries only, loss stop blocks entries only,
  15:55 flatten, restart cooldown/attempt recovery, no double entry on an adopted position,
  partial exits, broker rejection alert.

## Remaining, still not arming-ready

* `AlpacaHistory`/`AlpacaSIPStream` are the only untested-by-construction pieces (no network in
  tests): the REST request/response shapes and the subscription limits still need one live
  paper-shadow session to validate.
* `parity_excluded` heavy days are not yet summarised into a rate (it is a count in the daily
  summary); the parity report generator that consumes these tags is out of scope here.
* The supervisor has no time-of-day gating: it restarts immediately, so a crash at 16:30 starts
  a new (empty) session rather than waiting for the next open — harmless but noisy.
* Day-roll archives nothing: the per-day JSONL/states/bars/quotes are left in place.

## Verification

```
uv run --no-sync pytest tests/test_depth_bot.py -q   -> 29 passed (337 s)
uv run --no-sync ruff check factory/scripts/depth_bot.py tests/test_depth_bot.py -> clean
```
