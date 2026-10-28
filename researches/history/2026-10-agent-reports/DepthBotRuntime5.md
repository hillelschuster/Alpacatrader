# Depth bot runtime — rounds 4-5 (round-2 residuals, review-3 P2s)

Status: all six items fixed; default suite green; full-coverage parity re-run with the amended
$300 stop.

## Files
`factory/scripts/depth_bot.py`, `tests/test_depth_bot.py`.

## 1. SHADOW blocker — a socket return before the session end is never silent (R1)
`AlpacaSIPStream.run` now loops over socket attempts:
* a worker-thread return before `session_end_us` sets `stop_reason="socket_returned_early"` and
  `error`, writes `ALERT stream_ended_early` (reason, error, attempt, resume clock) and
  re-creates the socket with an exponential backoff, re-subscribing the universe and every
  name already subscribed;
* exhausting `max_reconnects` sets `stop_reason="reconnect_exhausted"`, writes
  `ALERT stream_reconnect_exhausted` and raises `StreamEndedEarlyError`;
* `run_live` checks `stop_reason`: anything other than `session_end` writes
  `ALERT session_incomplete` + a `session_incomplete` journal line, captures the states, and
  raises — it never writes `daily_summary`/`session_end` for an incomplete day, so the
  supervisor restarts and `roll_session` closes it as rolled over.
Tests: early return → alert + second socket + `session_end`; repeated deaths → 3 sockets,
3 alerts, `reconnect_exhausted` raise; dead source in `run_live` → raises, journal has
`session_incomplete` and no `session_end`/`daily_summary`.

## 2. Loss stop $90 → $300, same stop in every mode
`LOSS_LIMIT_USD = 300.0` (PRE-REG amendment). The replay-only `risk_stop=False` switch is gone:
the offline replay now runs the same stop as live. Checked against the research arm (no day has
a running realised loss ≤ −$300 before a later attempt) and re-run over all 167 days: 167/167
identical, 0 diffs.

## 3. F5 — the sim verdict is independent of the real position
Paper runs a second deterministic `SimBroker` with its own attempt ledger
(`sim_attempts`): entry at the intent clock/limit, its own fill decision, its own exit due
clock (`entry_minute+60`, fresh-gated submission through `_exit_submit_clock`), its own exit
poll — none of it depends on the real order or the real position.
* sim no-match + real fill → `sim_fill_status="no_match_at_l1_unfilled_cash"`, no sim exit/net,
  never overwritten;
* sim fill + real no-fill → the sim still gets its exit and `sim_net_usd_*`;
* `broker_fill_price` stays the ENTRY fill price; `broker_entry_price`/`broker_exit_price` are
  separate fields;
* a position still open at the close gets `trade_result` with `fill_status="unresolved_open"`,
  `parity_excluded="unresolved_open"` and a `position_unresolved` event;
* paper trade results are now journalled at the close (sorted by intent), so a late sim
  verdict cannot be lost.
Tests for each of the three divergences.

## 4. Exit retry cap + backoff
`EXIT_RETRY_MAX = 5`, `EXIT_RETRY_BACKOFF_US = 2 s` doubling. A retried exit waits for
`next_retry_us`, each retry is journalled with its index and next clock, and exceeding the cap
sets `retry_blocked`, journals `exit_retry_exhausted` and writes `ALERT exit_retry_exhausted`.
Test: 90 clock steps on a printing tape → at most `cap+1` orders, position blocked, alert and
journal line present.

## 5. Adopted overnight positions
`reconcile_positions` takes the broker's filled buy orders and adopts `entry_minute` from
`filled_at` and the price from `filled_avg_price` (journal first when it has today's fill). If
the broker has no fill clock, `bootstrap` adopts the position at the restart minute, flags
`late_exit` + `parity_excluded="entry_clock_unknown"` and records `broker_fill_price` — no
`entry_minute=0`/01:00 skeleton. Net P&L is not fabricated when the entry price is unknown.
Tests: broker fill clock/price adopted; unknown clock → restart minute, flagged.

## 6. `pending.clear()` race
The subscription queue is drained under a `threading.Lock` (enqueue and drain are atomic), so a
name appended by a handler during a drain cannot be dropped. Test: 60 admissions dispatched
from the loop thread while the caller drains → all 60 subscribed.

## Review-3 items (round 5)

1. **F7 filled-order retrieval** — `client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL))`
   (the default is OPEN, so overnight fills were invisible), filtered to filled BUYs whose
   `filled_at` ET day is today. Independently of the clock source, the engine flags any adopted
   position whose due clock is already past at the first advance: `late_exit` journal + ALERT,
   `parity_excluded`, and the exit is submitted marketable at once. Tests: past-due adoption
   sells immediately and alerts; `reconcile_positions` uses the broker clock/price.
2. **`position_closed` prices** — `broker_exit_price` now comes from the exit fill (not the
   entry price); `exit_slippage = broker_exit_price − sim_exit_bid` (the paper comparator) and
   `entry_slippage = broker_fill_price − entry_ask` are separate fields. Test asserts both.
3. **Supervisor** — exponential backoff (`backoff_s·2^(n-1)`, capped at `max_backoff_s=300`),
   default `max_restarts=5`, reset on a completed session, `ALERT supervisor_giving_up` then
   exit 2. Test: delays `[5, 10, 20]` for three crashes.
4. **`checklist --day D`** — evaluates the reviewer's first-shadow-day checklist from that
   day's artefacts and prints PASS/FAIL per item: lifecycle completeness, snapshot coverage
   (ratio ≥ 0.5 at every snapshot, no refusal/backfill cluster), decision lattice + skip
   vocabulary, persisted bars/states/quotes (schema = `research.STATE_COLUMNS`), shadow == a
   replay of the captured tape (ticks/signals/intents/trades, $300 stop fires 0), and
   no-alert/no-paper-orders/ARMED-absent/single-session. Tests: a clean shadow day passes all
   six; a journal with `session_incomplete` fails item 1.

## This lane's own switches (round 5)
`KILL_PATH` is `data/forward/depth_bot/KILL` (never the retired flush bot's `data/KILL`, which
stays in place), overridable with `--kill-file` on `shadow`/`paper`/`supervise`. The kill flag
reaches the engine through an explicit argument, so tests can point it anywhere. Test: the
depth bot keeps trading with `data/KILL` present and only stops on its own file.

## PIT subscription filter (live preflight)
`load_pit_universe()` keeps only tickers matching `^[A-Z]+$`: the SIP bar websocket rejects the
WHOLE subscription with "invalid syntax (400)" when any symbol is non-alphabetic, and the PIT
list carries `BRK/A`, `BAC^E`, `BRK-B` forms. Test writes a PIT parquet with those forms and
asserts only `AAPL`/`ZZZ` survive. Checked against the research artefacts: across all 167 traded
days no non-alphabetic ticker appears in the producer states or in the arm's trades, and the
replay path never calls `load_pit_universe`, so the 167/167 parity is unaffected.

## Verification
```
uv run --no-sync pytest tests/test_depth_bot.py -q                 -> 56 passed, 167 skipped (11m25s)
DEPTH_PARITY_SLOW=1 uv run --no-sync pytest tests/test_depth_bot.py -q -m slow  -> 167 passed (4 shards: 42+42+42+41, ~39 min each)
uv run --no-sync ruff check factory/scripts/depth_bot.py tests/test_depth_bot.py -> clean
```

## Still live-only
SIP entitlement and the ~5.6k-symbol bar subscription limit; real reconnect behaviour against
the venue; real timestamps/conditions; paper IOC/DAY fills and partials; the static session
calendar for future early closes; dedicated paper account.
