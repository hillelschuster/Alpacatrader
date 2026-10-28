# Depth bot runtime — round 3 (method-review blockers closed)

Status: **all F1-F6 fixes landed; parity now holds on the full traded-day population.**
`uv run --no-sync pytest tests/test_depth_bot.py -q` → 36 passed, 167 slow-parity days
skipped; ruff clean. Frozen rule untouched; the four-day/seven-day parity unchanged; shadow
journal == replay journal line for line.

## Files
`factory/scripts/depth_bot.py`, `tests/test_depth_bot.py`.

## P0-a — stream lifecycle (one session per ET day)
`AlpacaSIPStream` now owns a worker thread: the socket `run()`s on that thread, the caller's
thread drives the subscription queue and, at `session_end_us` (15:59 ET), calls `stop()` and
returns so `run_live` can `finish()` (summary, capture, states, `session_end`) and the day can
roll. `supervise()` re-checks the ET date and waits for the next day instead of re-entering the
same session; a crash still restarts immediately. Test: a fake socket asserts `run` ran on a
different thread, `stop()` was called at the deadline, and `stop_reason == "session_end"`.

## P0-b — no subscription from a handler
alpaca-py 0.43.4 `subscribe_quotes` blocks on `run_coroutine_threadsafe(...).result()`, so
handlers now only append names to a list; the caller's thread drains it and calls
`subscribe_quotes`. The test records the calling thread of every `subscribe_quotes` and
asserts it is never the socket's loop thread.

## P0-c — quote-driven admissions are subscribed
`DepthEngine.on_quote` returns the admission diff (as `on_bar` always did) and the stream
forwards both diffs to the subscription queue. Admission tracking no longer copies the
admissions dict per print: `_sync_admissions()` runs once per minute/decision and queues the
new names for the next callback. Test: a snapshot decision computed on a quote advance returns
the newly admitted ticker.

## P1 / F4 — admissions created inside `decision(t)`
`_flush_decisions` now calls `_sync_admissions()` immediately after `features.decision(t)`, so
a name admitted at the snapshot minute is quote-tracked for its `t+1` intent. Proof: 2025-07-31
SNGX is now `t=630` (was `t=635`) and matches the frozen trade exactly; 2026-03-24 and
2026-04-20 also pass in the default suite.

## Pre-paper journaling (PRE-REG-DEPTH-01 C(i)-(iv))
Paper runs a second deterministic `SimBroker` over the same `QuoteCache` and the same clocks.
Every paper trade record now carries, beside the broker fields:
`sim_entry_status, sim_entry_ask, sim_entry_bid, sim_entry_age_s, sim_entry_quote_us,
sim_exit_status, sim_exit_bid, sim_exit_bid_shares, sim_exit_quote_us, sim_fill_status,
sim_would_be_unknown, sim_net_usd_0, sim_net_usd_25`. Attempt→fill, entry vs arrival ask, exit
vs sim bid, the would-be UNKNOWN share and realised vs would-be net are all computable from the
journal alone. Test asserts the fields on a real (fake-broker) paper day.

## Real exit lifecycle (F6)
A canceled/expired/rejected paper exit returns `state="retry"`: the position stays open, the
remainder is re-submitted marketable at the current clock, `exit_retry` is journalled and an
`ALERT exit_retry` is written. Only a fill (or a broker-confirmed flat) closes the book; an
exit still unfilled at the close is journalled `exit_unconfirmed` and raises
`ALERT position_not_flattened`. Tests: canceled→retry→fill (two orders, one alert) and
never-filling (position kept, no `position_closed`, alert present).

## Parity standard (new)
* Default suite: 7 days — 2023-01-19, 2023-01-24, 2025-03-31, 2025-04-01 plus the reviewer's
  2025-07-31, 2026-03-24, 2026-04-20.
* `test_replay_parity_every_traded_day` (marked `slow`, skipped unless `DEPTH_PARITY_SLOW=1`)
  runs **every** day on which `thr300bps|p50` has ≥1 attempt: 167 days (54 validation + 113
  confirmation), attempt-by-attempt over the same field set. `DEPTH_PARITY_SHARD=i/n` splits it
  across processes.
* Result of the full run: **167 / 167 days identical**, 0 diffs (42 + 42 + 42 + 41 attempts-days per shard, 47 min wall each, 4 shards).
* One documented exception to the frozen rule in offline mode only: the `$90` realised-loss
  ops stop is disabled for `replay` (like its absent KILL file), because the research arm has
  none. Three days would otherwise diverge (2025-07-15, 2025-08-18, 2025-11-25) purely because
  the bot's stop refuses a later entry the frozen rule funds; with the stop off they match
  exactly. Live modes keep the stop (its own tests cover it).

## Operational notes
* Replay hot path reworked (preallocated quote buffers, column-wise quote delivery, cheap
  capture from numpy): ~50-70 s per session instead of ~110 s, which is what makes the
  167-day law affordable.
* The universe guard now measures completeness against the declared universe
  (`alive ≥ 50% of declared`), journals `declared`/`ratio`, and keeps the stale/empty gates;
  the earlier fresh-count collapse heuristic was removed because it refused legitimate quiet
  tapes (and would have broken the law).
* Still live-only (cannot be closed offline): real SIP subscription limits for ~5.6k bar
  symbols, `stream.stop()` returning `run()` on the venue's schedule, real bar/quote timestamp
  shapes, real paper IOC/DAY fills and partials, and the supervisor's behaviour after the
  close.

## Verification
```
uv run --no-sync pytest tests/test_depth_bot.py -q                 -> 36 passed, 167 skipped
DEPTH_PARITY_SLOW=1 uv run --no-sync pytest tests/test_depth_bot.py -q -m slow
                                                                  -> 167 passed (4 shards, 47 min)
uv run --no-sync ruff check factory/scripts/depth_bot.py tests/test_depth_bot.py -> clean
```
