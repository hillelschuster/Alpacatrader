# Depth bot runtime — replay / shadow / paper

Status: **all three contract steps green.** 17 tests pass (`uv run --no-sync pytest
tests/test_depth_bot.py -q`, 384 s), ruff clean on both files at line-length 100. Nothing was
armed, no network was touched, `data/forward/bot/LIVE` was never read, `.env` was never written
or printed.

## Files

* `factory/scripts/depth_bot.py` (~1800 lines) — the runtime.
* `tests/test_depth_bot.py` (~400 lines) — parity + safety suite.
* `factory/scripts/depth_bot_features.py` — untouched (already parity-tested, 4 passed).
* Research producers, `flush_bot.py`, `.env` — untouched.

## Architecture (one loop, three modes)

```
stream (injected) --on_bar/on_quote--> DepthEngine
    IncrementalDepthFeatures (causal bars, snapshots, panel rows, h60 predict)
    clock-ordered task heap   release | intent | flatten   (priorities release<intent<flatten)
    book rules                cash=BOOK, active slots, attempts, cooldown, kill, loss stop
    broker boundary           SimBroker | AlpacaPaperBroker (IOC limit buy / MARKET DAY sell)
    journal                   data/forward/depth_bot/journal/<day>.jsonl (fsync per line)
```

* Streams: `ReplayStreamSource` (stored parquet, clock-ordered merge), `AlpacaSIPStream`
  (alpaca-py, `DataFeed.SIP`, all-symbol bars + quotes for names the engine admits). Tests
  inject a third, independent fake stream that delivers one bar/print per callback.
* `QuoteCache` keeps the prints actually observed and rebuilds the exact `SymbolQuotes` arrays
  (`regular`/`valid` masks, 100x size multiplier before 2025-11-03), so every research read is
  the same function of the same prefix of the tape.
* `SimBroker` settles only at clocks the engine has already reached and only calls
  `research.price_entry_ioc` / `research.priced_leg`; the exit submission clock is derived with
  the same fresh-quote gate as `research.submission_clock` (`submit_at_due_intent_firm_fresh`,
  `submit_at_first_regular_after_due_intent`).
* Book rules are research-verbatim: 3 slots, $3,000, `qty = min(capital, floor(0.5*min(ask,bid)))`,
  one position per ticker, 15-min cooldown anchored at the RELEASE clock, 3 attempts, highest
  pred first at a simultaneous clock, all funding before any arrival outcome.
* Ops additions (new, logged separately): `data/KILL` blocks entries only, `ARMED` + `--paper`
  gate every order, $90 realised-loss stop (`daily_loss_stop`), 15:55 hard flatten
  (`flatten_forced`), `qty*limit > $1,100` guard (unreachable by the frozen sizing).

## Step 1 — replay parity vs the frozen research replay (`thr300bps|p50`)

`replay --day D` drives the same engine with `ReplayStreamSource`; `kill_path=None` (an offline
replay is a what-if, never an order path).

| day | block | attempts | outcome |
|---|---|---|---|
| 2023-01-19 | validation | 1 | GNS t=770 qty 300, ask 1.52, `unknown_exit_execution` (`unknown_stale_but_valid_at_arrival`) |
| 2023-01-24 | validation | 1 | BIAF t=760 qty 250, 3.58 → 3.37, `conditional_fill`, net@25 −54.671875 |
| 2025-03-31 | confirmation | 2 | ICCT t=665 qty 820, 1.19 → 1.15, net@25 −35.1985; ICCT t=790 qty 902, entry 1.08, exit bid 0.9901, `unknown_exit_partial_depth` |
| 2025-04-01 | confirmation | 2 | RSLS t=635 qty 894, 1.09 → 1.12, net@25 +24.3503; CSAI t=710 qty 89, 10.99 → 9.28, net@25 −154.4450 |

Field identity (tolerance 0 for strings/ints, ≤1e-6 for floats) on all attempts:
`ticker, t, entry_minute, intent_us, limit_price, qty, qty_capital, entry_status, entry_ask,
entry_bid, entry_age_s, entry_quote_us, entry_arrival_us, exit_minute, exit_bid,
exit_bid_shares, exit_status, actual_exit_us, fill_status, net_usd_25, attempt_index,
positions_at_entry`; `net_usd_0/net_usd_150` also matched when spot-checked.

Deliberate non-reproduction: `proxy_gross` / `proxy_entry_status` are research hindsight
observability proxies (`gross_60` needs the open of the entry minute's own bar, which has not
closed at the decision instant). They are now journalled as `None` / `"unknown_pending"` instead
of a fabricated value, and no rule reads them.

## Step 2 — shadow

`run_live("shadow")` = the same engine + `SimBroker` + injected `AlpacaSIPStream`. The test
replays 2023-01-19 through `FakeSIPStream` (bars one row per callback) and asserts the shadow
journal is **equal line for line** to the replay journal (only the trailing
`live_session_complete` marker is dropped). Shadow never constructs `AlpacaPaperBroker`; the test
patches `AlpacaSIPStream` to fail, so a network read would fail the suite.

## Step 3 — paper

`run_live("paper")` raises before credentials/broker when `--paper` is missing **or**
`data/forward/depth_bot/ARMED` is absent (test: broker untouched). With ARMED present:
`AlpacaPaperBroker` submits `LimitOrderRequest(qty, IOC, ceil(ask*1.01))` with
`client_order_id=depthbot-b-<sym>-<intent_us>` and a `MarketOrderRequest(DAY)` exit
`depthbot-s-<sym>-<submit_us>`; `reconcile_positions` adopts broker positions, re-derives
`entry_minute/exit_minute` from the journal, and the engine blocks re-entry (`overlap_skips`)
and keeps the cooldown (`cooldown_skips`) across restarts. Tests (fake broker, no network):
KILL blocks the entry and never the exit; loss stop blocks new entries only; 15:55 forces the
exit that could not price; restart rebuilds attempts/cooldown and does not double-enter an
adopted position; paper run submits exactly one IOC buy per attempt.

Journal per day: `decision_tick` (lattice summary), `signal`, `intent` (intent quote),
`order_submitted` (entry/exit), `entry_result` (arrival quote + partial flag), `fill`,
`position_closed` (realised P&L, exit slippage), `risk_stop`, `trade_result` (the full research
record = the SIM's would-be outcome in replay/shadow; in paper the modelled quote sits beside
`broker_fill_price` so the slippage diff is mechanical), `daily_summary`. Bars and admitted-name
prints are persisted under `data/forward/depth_bot/{bars,quotes}/<day>`.

## Remaining gaps before arming

1. Universe sweep: `AlpacaSIPStream` subscribes SIP minute bars for the whole PIT list; no
   batched REST fallback, no subscription-limit/rate handling, and no refusal path when a
   snapshot sweep is incomplete (research §12 hazard 2).
2. Mid-day restart has no REST quote backfill: a cold `QuoteCache` can only produce
   `intent_not_firm_fresh` until prints arrive (silent no-trade, never a wrong trade).
3. Paper fill mechanics: partial entries are booked at the filled qty and tagged, but there is
   no partial-exit policy, no `parity_excluded` tagging, and no re-submit/escalation beyond the
   `ALERT` journal line (no `ALERT` file, no supervisor script, no day-roll/archive).
4. The loss stop is realised-only (no mark-to-market) and is a documented deviation. It fired on
   2025-04-01 (−$125 after the last attempt) without changing attempts; on another day it can
   legitimately refuse an intent the research replay funds.
5. The 15:55 flatten can pre-empt the research exit clock for an entry at 14:56 (research exit
   15:56); not exercised on the four parity days.
6. `states/<day>.parquet` (the exact 26-feature rows) is not persisted; only the journal,
   bars and quote prints are. Feature/pred bit-equality is covered indirectly by parity.

## Verification run

```
uv run --no-sync pytest tests/test_depth_bot.py -q     -> 17 passed (384 s)
uv run --no-sync ruff check factory/scripts/depth_bot.py tests/test_depth_bot.py -> clean
```
