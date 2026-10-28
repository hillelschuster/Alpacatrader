# Depth bot method review — round 3 (final paper gate)

## Verification run

* Default: `tests/test_depth_bot_features.py` + `tests/test_depth_bot.py` → **51 passed,
  167 skipped** (8:04). The 7-day parity includes the formerly divergent days.
* Slow sample with the amended $300 stop: 2025-07-15, 2025-08-18, 2025-11-25 (the $90-blocked
  days) → **3 passed** (2:10). The author's full 167/167 run was not repeated.
* Mutation (actual exit clock `HEAD-5`, /tmp copy) → parity fails `attempts 2 vs 1`. The parity
  test is still sensitive to the bot's own exit/book loop.
* Probes: early socket return → 3 sockets, 3 `stream_ended_early` + `stream_reconnect_exhausted`
  alerts, `StreamEndedEarlyError`; sim no-match + real fill → `sim_fill_status` stays
  `no_match_at_l1_unfilled_cash`, no sim exit/net; sim fill + real no-fill → sim exit/net
  present; `broker_fill_price` stays the entry price.

## Round-2 findings

| finding | status |
|---|---|
| R1 early stream return silent | **closed** — reconnect loop + alert + `StreamEndedEarlyError`; `run_live` writes `session_incomplete`, no `daily_summary`/`session_end`; tests `test_socket_return_before_session_end_alerts_and_reconnects`, `..._exhaustion_raises`, `test_run_live_never_writes_a_completed_day_after_an_early_end`. |
| F5 sim tied to real position | **closed** — `sim_attempts` ledger, own entry/exit clocks, no exit if the sim entry did not fill; tests for both divergence directions; probes confirm. |
| `broker_fill_price` overwritten by exit | **closed in the trade record** (`broker_entry_price`/`broker_exit_price`); P2 remains in the `position_closed` event (below). |
| open-at-close position no `trade_result` | **closed** — `unresolved_open` + `position_unresolved`; test `test_position_open_at_the_close_still_gets_a_trade_result`. |
| exit retry unbounded | **closed** — `EXIT_RETRY_MAX=5`, exponential backoff, `exit_retry_exhausted` alert; test. |
| F7 adopted positions | **mostly closed** — broker `filled_at`/`filled_avg_price` path, `entry_clock_known`; bootstrap flags unknown clocks. P2 residuals below. |
| pending.clear race | **closed** — `threading.Lock` around enqueue/drain; test 60 admissions. |
| $90 stop | **changed to $300**, shared by replay+live; default test; the 3 formerly divergent days pass; no in-sample trigger at $300. |

## Residual findings (P2/P3, not order-safety blockers)

1. **P2 `position_closed` uses the entry price for the exit** (`depth_bot.py:2169-2174`):
   `broker_exit_price`/`exit_slippage` read `record["broker_fill_price"]` (entry). Use
   `record["broker_exit_price"]`; the paper slippage comparator is `sim_exit_bid`, not the
   (absent) real `exit_bid`. The trade_result itself has the correct fields; the event can
   mislead a report reader.
2. **P2 `client.get_orders()` default is OPEN** (`:2844-2863`): the filled-order adoption map is
   empty in the real client, so an overnight position falls back to the journal (or, on a fresh
   day with no journal fill, `entry_minute=0`/flagged and it exits at the open). Use
   `GetOrdersRequest(status=QueryOrderStatus.CLOSED)` (or ALL) and filter filled. Also flag any
   adopted position whose due clock is already past at the first advance, independent of the
   clock source.
3. **P3** the pre-reg appendix says $300 but line 33 still says $90; mark line 33 superseded.
4. **P3** `sim_would_be_unknown` is True for an unknown *entry* (there is no exit); C(iii)
   should key off `sim_exit_status`, which is None for an entry-only unknown.
5. **P2 live-only** the supervisor restarts immediately on `reconnect_exhausted`; add a
   day/time guard or a max to avoid a tight restart loop.

## Verdicts

* **(a) Live SHADOW day: GO.** No orders; the P0/P1 blockers (stream lifecycle, deadlock,
  admission lag, early-return alert) are closed and tested.
* **(b) PAPER orders (ARMED): GO for a flat-start first day.** The sim comparator, retry cap,
  stream and stop are ready; fix the two P2s (position_closed slippage fields, filled-order
  adoption query) before the first overnight/restart paper day.

## First shadow-day journal checklist

1. `session_start` and a final `session_end` + `daily_summary` + `live_session_complete`; no
   `session_incomplete`, `stream_ended_early`, `reconnect_exhausted`, `crash`.
2. `snapshot_coverage` at 575/585/600/630/660/720 with `declared` (~PIT count) and
   `ratio >= 0.5`; no `universe_refused`/`backfill_failed` cluster.
3. `decision_tick` at each 5-min lattice 575..895 (or until session end); signal counts sane;
   `intent` skip reasons dominated by the research vocabulary, no systematic
   `quote_not_acquired`/`intent_not_firm_fresh`.
4. `bars/<day>.parquet`, `quotes/<SYM>/<day>.parquet`, `states/<day>.parquet` exist; states
   columns equal `research.STATE_COLUMNS`; quote captures cover the admitted set.
5. Shadow-vs-replay on the captured day: features/pred bit-equal, intent facts equal, entry/exit
   fill classes; no unexplained divergence; the $300 stop fire count 0.
6. ALERT file empty; no paper orders/positions; ARMED absent; supervisor waits for the next day.

## Live-only

SIP entitlement and the ~5.6k-symbol bar limit; real reconnect/venue close; real
timestamp/conditions shapes; paper IOC/DAY fills and partials; the static session calendar for
future early closes; dedicated paper account; real fill-order retrieval (`QueryOrderStatus`).
