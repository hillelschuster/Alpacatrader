# BUILD SPEC — adaptive depth-participation lane on Alpaca PAPER

Status: spec only. No order was placed, no code written. Everything below is grounded in
`factory/scripts/alpha_adaptive_depth_sizing.py` (producer), `alpha_quote_aware_frequency.py`
(quote kernel + legs), `alpha_open_panel.py` (features), `alpha_open_sim.py`
(`causal_liquidity`, `CONTEXT_FEATURES`), `alpha_sparse_daily.day_context`,
`alpha_open_learned.py` (`FEATURES_ALL`, `feature_matrix`), `factory/scripts/flush_bot.py`
(live skeleton), and `/home/hillel/alpha-data/open-search-v1/adaptive_depth_sizing/results.json`.

---

## 0. Verdict and scope

The lane is **mechanically portable** to paper: every one of the 26 model features is a pure
function of the current session's own RTH minute bars (no previous close, no premarket, no PIT
admission flag — `alpha_open_panel.CONTRACT["no_previous_close_features"] == True`). What is
**not** free is the *universe*: admission and `rank_snapshot` are built from a full-PIT-universe
SIP bar sweep at six 5-minute checkpoints, and the four peer features are cross-sectional over
that admitted watchlist. So the bot must reproduce a **ranking pipeline**, not just a feature
vector, and it needs **real-time SIP** (quotes with sizes + consolidated bars). The prior
evidence in-repo already says the free IEX feed is negative for this family and that SIP
(Algo Trader Plus, ~$99/mo) is the only way to run the frozen schedule (`HANDOFF.md:362-364`,
`factory/STATE.md:1316-1318`).

Expected base rate (from `results.json`, chosen cell `thr300bps|p50`): validation **107 signals /
250 sessions**, 62 funded intents, 53 known fills → **0.21 funded intents per session**; late
142 funded intents / 332 sessions → 0.43. The bot will idle most days. Plan the evidence horizon
in years, not weeks (§12).

---

## 1. Frozen strategy contract (do not re-derive)

| Item | Value | Source |
|---|---|---|
| Model | `learned/models/payoff_h60.joblib`, sha256 `c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96`, 26 features, generic booster names | `results.json.model.h60` |
| Feature order | `alpha_open_learned.FEATURES_ALL` (22 path + 4 peer) | producer line 148 |
| Score bar | `pred_60 >= 0.030` | `CONTRACT.model` / `arm_key thr300bps\|p50` |
| Decision clock | grid `t` on the panel's 5-min lattice, **entry intent = `t+1` at 00.000s ET** | `score_day` → `entry_minute = t+1` |
| Intent quote | latest RAW NBBO print at/before the intent clock; valid, un-crossed, R-only, `age <= 2.0s` | `SymbolQuotes.latest_raw`, `MAX_AGE_S` |
| Quantity | `q = min(floor(1000/(limit*1.0075)), floor(0.5 * min(ask_shares, bid_shares)))`, exact rational, fixed before arrival | `view_quantity` |
| Limit | `ceil_to_cent(observed_ask * 1.01)` | `limit_price_of` |
| Spread bar | `spread_bps + 25 <= 10_000 * pred` | `arm_eligibility` |
| Entry order | marketable **LIMIT IOC**, one arrival read at intent+250 ms, never rests | `price_entry_ioc` |
| Fill condition (sim) | `ask <= limit` **and** `arrival ask_shares >= q` | `price_entry_ioc` |
| Exit | market order at `min(entry_minute+60, session_end)`, fresh-quote-gated submission, priced at submission+250 ms, needs `bid_shares >= q` | `submission_clock` + `priced_leg` |
| Book | 3 slots, $1,000 ticket, $3,000 reserved cash, 1 position/ticker, 15-min cooldown after the **actual** exit, 3 attempts/ticker/day, highest-pred-first at a simultaneous clock, no same-clock substitution | `replay_day` |
| Session end | `session_end` = last bar-start minute (959 normal, 779 early close) from `factory/artifacts/basket/sip/phase2_session_calendar.json`; grid `t` stops at `min(895, session_end-1)` | `alpha_open_panel.py:105` |

Reference numbers to compare paper against (chosen cell): validation **+1.36 $/session,
+0.94 $/calendar day** (361-day span) at 25 bps, 53 known fills, 8 UNKNOWN (12.9%), 1 no-match;
late **+5.95 $/session, +4.11 $/calendar day** (481-day span), 122 known of 142 attempts,
20 UNKNOWN; mean qty 160 (val) / 264 (late), mean deployed $707 / $735 per funded intent,
mean hold 60.0 min. Win rate 37.7% (val) / 44.3% (late); worst known fill −$219 / −$265.
Median fill −$9.21 and top-5 fills = 125% of total (lane summary): the PnL is a handful of fills,
which is why §12 accepts distributional parity, not PnL parity.

---

## 2. Modules

New package, do not edit other lanes' producers. Single runnable entry so it matches the
`flush_bot.py` operational model (supervisor + KILL + LIVE flags).

```
factory/scripts/depth_paper_bot.py        # entry point: --live / --dry / --shadow / --once
factory/scripts/depthbot/__init__.py
factory/scripts/depthbot/universe.py      # full-universe bar sweep, B(T) snapshots, admission+ranks
factory/scripts/depthbot/bars.py          # per-symbol RTH minute-bar store (o570 anchor, contiguity)
factory/scripts/depthbot/features.py      # port of alpha_open_panel.symbol_rows + day_context + liquidity
factory/scripts/depthbot/model.py         # load + sha/order verify payoff_h60.joblib, predict
factory/scripts/depthbot/quotes.py        # running NBBO print cache; latest_raw/age/R-only
factory/scripts/depthbot/rules.py         # intent gate, qty, limit, spread bar, exit clock, book
factory/scripts/depthbot/broker.py        # Alpaca paper wrapper (IOC limit buy, market sell, ids)
factory/scripts/depthbot/journal.py       # atomic JSONL + per-day dirs + state.json
factory/scripts/depthbot/loop.py          # minute-boundary scheduler, day roll, restart, EOD
factory/scripts/depth_paper_parity.py     # offline: replay captured day, diff vs journal
factory/scripts/depth_paper_supervisor.sh # clone of flush_bot_supervisor.sh
tests/test_depthbot_rules.py              # gate/qty/limit/limit-match/exit-clock invariants
tests/test_depthbot_features.py           # bit-parity vs two frozen research panel days
tests/test_depthbot_journal.py            # atomic append, restart rehydrate, attempt/cooldown persistence
```

`features.py` must be a **hand port with a parity test**, not a re-implementation from prose:
import `FEATURES_ALL`, `feature_matrix`, `causal_liquidity`, `day_context` from the research
modules and reproduce `symbol_rows` numerically (the whole of `alpha_open_panel.py:67-183` is
~60 lines of numpy; port it literally, `searchsorted` calls included).

---

## 3. Data flow (one session, ET clocks)

```
pre-open
  Alpaca clock/calendar -> is_open, close_minute (session_end), early-close flag
  PIT universe = data/pit/pit_symbols.parquet, latest vintage <= today (same source flush_bot uses)
09:31     freeze o570 per symbol (the ticker's 09:30 bar open). No 09:30 bar -> the name can never
          be traded today (research drops et[0] != 570)
snapshots T in {575,585,600,630,660,720}: universe minute-bar sweep
          px_T = close of the last completed bar with et <= T-1 (fresh: et >= T-2)
          score = px_T/o570 - 1, floor px_T >= $1, rank desc (tie-break symbol asc), top-10
          admission: first T with score >= 0.05 -> admit_t, admit_gain ; ranks[T] = {sym: rank}
          subscribe quotes for every admitted symbol (union grows monotonically)
grid t in {575,580,...,895} step 5:
  t+0.05..t+0.50  per admitted symbol: bar at t-1 must exist; assemble the 22 path features
                  from bars <= t-1; drop rows failing the panel gates (see §5)
  t+0.50          peer context over the surviving rows at the same t (all four CONTEXT features)
  t+0.55          causal_liquidity filter (log_cum_dv >= log1p(1e6) and bars15 >= 12)
  t+0.60          LightGBM predict (Float64, nulls -> 0.0, frozen order); keep pred >= 0.03
  t+0.70          book admission: overlap / cooldown / attempts / slots / cash, order by pred
t+1:00.000        INTENT: read latest raw quote at the boundary from the local print cache
                  -> intent_facts -> arm_eligibility -> q, limit; write-ahead journal
t+1:00.02         submit LIMIT IOC (q, limit) client_order_id depthbot-b-<sym>-<intent_us>
t+1:00.25         ARRIVAL snapshot (parity field only; the order is already out)
t+1:00.25..       poll order by id until terminal; ack/fill timestamps recorded
entry_min+60:00   EXIT: fresh-gated submission (if not fresh, first eligible regular print at/after)
                  market sell q ; price read at submit+250ms ; eod safety at session_end
```

Notes that are easy to get wrong:
* The grid is `t ∈ 5·Z` starting at the first snapshot and is common to all names (all six
  snapshots are ≡0 mod 5), so there is exactly one decision batch per 5 minutes, and the six
  snapshot minutes are themselves grid points.
* Rows exist only where the symbol has a **real** bar at `t-1`; gaps are row-drops, never
  forward-fills (the panel is grid-free — unlike `src/live/features.py`, which is a different,
  ffill-based lane and cannot be reused).
* `session_end` caps both the grid and the exit minute; there is no entry after `t+1 = 896`.

---

## 4. Data inputs and entitlements

| Need | Source | Notes |
|---|---|---|
| Top-gainer universe / admission | full-PIT universe SIP 1-min bars, batched REST at each of the 6 snapshots | ~10k symbols, 6 sweeps/day. Alpaca multi-symbol bar requests; stay under the plan's req/min (the repo measures ~171 req/min safe, `alpha_delayed_sip_iex.py:347`) |
| Path features | same per-symbol RTH bars (09:30..t-1) | keep the whole session per admitted symbol; also persist to `bars/<day>.parquet` for parity |
| NBBO with sizes | SIP quote stream (websocket) for the admitted watchlist, plus REST `latest quote` as a repair path | `bid_price, ask_price, bid_size, ask_size, bid_exchange, ask_exchange, conditions, tape, ts` |
| R-only / valid | local derivation: `conditions` non-empty and all `R`; `bid>0, ask>0, bid<=ask` | exactly `alpha_sparse_quote_service.regular_mask` |
| Session clock / half days | Alpaca `get_clock` + `get_calendar`; cross-check `phase2_session_calendar.json` | flush_bot already has the 500-tolerant clock fallback (reuse) |
| PIT symbol list | `data/pit/pit_symbols.parquet`, latest vintage ≤ today | same call flush_bot uses (`pit_symbols()`) |
| Model | `/home/hillel/alpha-data/open-search-v1/learned/models/payoff_h60.joblib` | verify sha256 + `feature_order == FEATURES_ALL` + booster name check at load; refuse to start otherwise |

**Not needed** (and must not be introduced): previous-session close, premarket prints, PIT
admission flags, any model refit, any IEX-only substitute, TradingView. The
`data/sip/candidates/*.json` snapshots are a research artifact of the same sweep — the live bot
recomputes them, it does not read them (they are produced post-close).

Size display caveat: the historical corpus multiplies raw quote sizes by 100 before
`UNIT_EPOCH = 2025-11-03` (round lots). Live data is post-epoch → multiplier 1. Port the constant
and assert `day >= UNIT_EPOCH` at startup so the mistake cannot silently halve quantities.

---

## 5. Feature parity table (all 26 features)

Gate applied before features (all must hold, else the row does not exist —
`alpha_open_panel.py:104-121`): bars for the symbol exist and `et[0] == 570`; `c[t-1] >= 1.0`;
a real bar at exactly `t-1`; at least one bar at/before `t-6` and at/before `t-16`.

| # | Feature | Causal definition (research) | Live source | Live reproducible? |
|---|---|---|---|---|
| 1 | `minute` | `float(t - 570)` | bot clock | **Yes, exact** |
| 2 | `admission_age` | `t - admit_t`, `admit_t` = first B-snapshot with `score >= 0.05` | snapshot sweep | **Yes, iff sweep matches** |
| 3 | `rank_snapshot` | rank in that snapshot's top-10, **11 if absent** (`ranks[checkpoint<=t]`) | snapshot sweep | **Yes, high risk** — full-universe rank, $1 floor, `score desc, symbol asc` tie-break |
| 4 | `admit_gain` | `score` at `admit_t` = `px_T/o570 - 1` | snapshot sweep | **Yes, iff sweep matches** |
| 5 | `gain_open` | `c[t-1]/o570 - 1` | o570 anchor + bars | Yes (needs the 09:30 bar) |
| 6 | `log_price` | `log(c[t-1])` | bars | Yes |
| 7 | `ret1` | `c[t-1]/c[j] - 1`, `j` = last real bar with `et <= t-2` | bars | Yes — index over **real bars**, not the clock grid |
| 8 | `ret3` | same, `et <= t-4` | bars | Yes |
| 9 | `ret5` | same, `et <= t-6` | bars | Yes |
| 10 | `ret15` | same, `et <= t-16` | bars | Yes |
| 11 | `dd_high15` | `c[t-1]/max(h in [t-15,t-1]) - 1` | bars | Yes |
| 12 | `dd_day_high` | `c[t-1]/running max(h <= t-1) - 1` | bars | Yes |
| 13 | `rebound_low5` | `c[t-1]/min(low in [t-5,t-1]) - 1` | bars | Yes |
| 14 | `range5` | `(max h - min l in [t-5,t-1]) / c[t-1]` | bars | Yes |
| 15 | `range15` | same over `[t-15,t-1]` | bars | Yes |
| 16 | `log_cum_dv` | `log1p(sum c*v over bars <= t-1)` | bars (consolidated volume) | Yes — needs SIP consolidated volume |
| 17 | `log_dv5` | `log1p(sum c*v in [t-5,t-1])` | bars | Yes |
| 18 | `dv_accel` | `dv5 / max(sum c*v in [t-10,t-6], 1.0)` | bars | Yes |
| 19 | `efficiency15` | `(c[t-1]-c[first bar >= t-15]) / sum abs(dc)`; 0.0 if travel 0 | bars | Yes |
| 20 | `bars15` | count of real bars in `[t-15,t-1]` | bars | Yes |
| 21 | `since_high` | `t - et[last index of the running-max high] - 1` | bars | Yes |
| 22 | `vwap_dist` | `c[t-1]/(cum_dv/cum_vol) - 1` | bars | Yes |
| 23 | `peer_positive3` | count of rows at the same `(day,t)` with `ret3 > 0`, **self included** | cross-section of admitted rows | Yes — needs the full qualifying set at `t` |
| 24 | `peer_gain_mean` | mean `gain_open` over those rows | same | Yes |
| 25 | `peer_breadth10` | count of those rows with `gain_open >= 0.10` | same | Yes |
| 26 | `peer_heat` | median `range5` over those rows | same | Yes |

**Research-only dependencies: none among the 26.** The three residual hazards are structural,
not per-feature: (a) `rank_snapshot` + `admission_age` + `admit_gain` are downstream of the
*ranking sweep*; (b) `peer_*` are downstream of the *qualifying set* (any bar-gate difference
changes a cross-sectional mean for every name at that second); (c) `log_cum_dv`/`log_dv5` are
downstream of *consolidated volume*, so an IEX-only or exchange-partial feed silently changes the
$1M liquidity gate and `dv_accel`.

State filter applied after the features: `causal_liquidity` = `log_cum_dv >= log1p(1_000_000)`
**and** `bars15 >= 12`. Keep it a filter on the row set, never a feature.

---

## 6. Latency budget

Anchor: the intent clock is `t+1:00.000`. The sim's own budget is **0 ms** of decision work
(its features are precomputed) and **250 ms** from intent to arrival read.

| Stage | Budget | Notes |
|---|---|---|
| bar `t-1` available | ≤ 200 ms after minute close (SIP stream); ≤ 600 ms via REST fallback | the pipeline must not start before it exists |
| 22 path features, ≤ 30 live symbols | ≤ 15 ms (vectorized polars/numpy) | the port uses per-symbol `searchsorted`: O(bars) |
| peer context (4 aggregates) | ≤ 5 ms | one group-by over ≤ 30 rows |
| LightGBM predict (≤ 30 rows) | ≤ 3 ms | 200 trees, 15 leaves |
| book admission + journal write-ahead | ≤ 10 ms | fsync the state file before any submit |
| **decision ready** | **t + 0.6 s** ⇒ ~40 s of slack before the boundary | compute at `t + a few hundred ms`, not at the boundary |
| intent quote read | at the boundary, from the local cache (no network) | `age <= 2 s` is the gate, so cache staleness is measurable |
| order submit → broker ack | measure; expected 30–120 ms REST | log `submit_us`, `ack_us`, `fill_us` |
| parity arrival read | boundary + 250 ms | **record only**; never gate the order on it |

Do **not** add an artificial 250 ms delay before submitting: the sim's +250 ms read is a *model* of
arrival, and sending immediately can only make the live fill condition stricter (a later book is
not the sim's book either way). Instead record the delta between the sim's modelled arrival and
the broker's actual ack/fill clocks; that delta is a first-class parity metric (§12).

Worst case that must degrade safely: stream disconnect → no bar at `t-1` → no row at `t` → no
intent. That is exactly the sim's absent-row behaviour, so it is *not* a divergence. Missing quote
prints for an admitted symbol → `intent_not_firm_fresh` skip, which the sim also produces
(11/107 val, 17/244 late).

---

## 7. Order types

**Entry — marketable LIMIT IOC.** `side=buy`, `qty=q`, `limit_price=ceil_to_cent(ask*1.01)`,
`time_in_force=IOC`, `client_order_id="depthbot-b-<SYM>-<intent_us>"`. One submit per funded
intent; no repricing, no re-submit on a partial or a miss (the attempt is already counted).
Alpaca supports `TimeInForce.IOC` on paper. Outcomes:
* full fill at ≤ limit → position; exit clock = entry fill minute + 60.
* partial fill → **live-only state** (the sim has no partial-entry path). Book the filled part as
  the position, let the IOC remainder cancel; log `partial_entry`, exclude the trade from parity
  PnL, keep the attempt consumed.
* rejected / no fill → unfilled cash, released at the arrival clock; cooldown is **not** started
  (matches `no_match_at_l1`).

**Exit — MARKET sell** of exactly `q` (never re-sized), at `min(entry_minute+60, session_end)`,
`time_in_force=DAY`, `client_order_id="depthbot-s-<SYM>-<exit_us>"`. No stop, no target, no OCO:
the sim has neither, and adding one is a new strategy that also invalidates the comparison.
If unfilled at `session_end`, keep the DAY order working and raise `ALERT` — the position is real
even though the sim would call it UNKNOWN.

**Never**: shorts, margin, bracket/OCO, `gtc`, options, after-hours.

---

## 8. Risk caps

| Cap | Value | Origin |
|---|---|---|
| Concurrent positions | 3 (`MAX_SLOTS`) | research |
| Ticket | $1,000 (`ORDER_BUDGET`), q from the two caps | research |
| Reserved cash | $3,000; `reserved = q*limit*1.0075`; released at the **actual** exit clock | research |
| Per ticker | 1 open position, 15-min cooldown after the actual exit, max 3 attempts/session | research |
| Simultaneous clock | fund by descending `pred_60`; no substitution after seeing an outcome | research |
| Entry window | grid `t <= min(895, session_end-1)`; no entries after 14:56 ET (t=895) | research |
| Single-order sanity | reject any submit with `qty*limit > $1,100` or `qty < 1` | ops guard (new) |
| **Daily loss stop** | halt **new entries** when day realized + mark-to-market ≤ −$90 (−3% of book); exits and open positions unaffected | **new, not in the research contract** |
| Feed guard | no new entries unless the quote feed is SIP-entitled and the boundary print age ≤ 2 s; refuse to start on `Feed.IEX` | ops guard |
| Kill | `data/KILL` → stop new entries immediately; exits keep running; process exits | ops (flush_bot pattern) |
| Arming | `data/forward/depthbot/LIVE` must exist, else dry-run (intents logged, no orders) | ops (flush_bot pattern) |

The daily loss stop is a deliberate, documented deviation: log it as `risk_stop` and tag every
subsequent skip `skip_reason="daily_loss_stop"`, so the parity report can show "days the sim
traded and the bot refused" as a separate bucket. Never let it leak into parity PnL.

---

## 9. State and restart

Per ET day, `data/forward/depthbot/<day>/`:
```
state.json        # durable: attempt counts, cooldown_until[per ticker], open reservations,
                  # pending intents (client_order_id, qty, limit, entry_minute, exit_due_us),
                  # day PnL (realized + last mark), risk_stop flag, kill flags
intents.jsonl     # every evaluated candidate state (see §10)
trades.jsonl      # every funded attempt, one line per lifecycle transition
quotes/<SYM>.parquet  # raw NBBO prints captured today for tracked symbols
bars/<day>.parquet    # consolidated RTH minute bars used (all tracked symbols)
states/<day>.parquet  # the exact 26-feature rows + pred + intent facts that drove decisions
snapshots.json    # the 6 B(T) snapshots, admission set, ranks, o570 per symbol
```
Write-ahead rule: `state.json` is written (atomic replace + fsync) *before* the submit call, and
again before the fill is booked. A crash between submit and ack must leave a record that lets
reconciliation find the order by `client_order_id`.

Startup reconcile (broker truth; clone of `flush_bot.startup_reconcile`):
1. cancel nothing that is an IOC (it cannot rest) — only look for stray `depthbot-` DAY exits;
2. adopt every position with a `depthbot-b-`/`depthbot-s-` id; re-derive `entry_minute` from the
   broker's `filled_at`, so the exit clock is the original one, not the restart time;
3. if the exit due clock already passed → submit the market sell **immediately**, tag
   `late_exit=true`, keep it in real PnL but out of parity PnL;
4. rebuild `attempts`/`cooldown_until` from `trades.jsonl` (**do not reset**, unlike flush_bot,
   which is stateless across restarts);
5. rebind the quote cache: after a mid-day restart, backfill the day's prints for tracked symbols
   from REST history before enabling entries (a cold cache can only produce
   `intent_not_firm_fresh`, i.e. silent no-trades).

Day roll: at the first poll after the ET date changes, flatten any remaining position (there
should be none: exits are ≤ 16:00), archive the day dir, drop the quote/bar caches, re-arm.

---

## 10. Logging schema

`intents.jsonl` — one line per (day, ticker, t) that survived to scoring, traded or not:
```json
{"ts":"<et iso>","event":"intent","day":"2026-11-02","ticker":"XYZ","t":605,"entry_minute":606,
 "pred_60":0.0412,"features":{...26 named floats, null->0.0...},"feature_null_mask":[0,0,1,...],
 "intent_us":1698,"intent_status":"quoted",
 "intent_ask":10.02,"intent_bid":10.00,"intent_ask_shares":412.0,"intent_bid_shares":300.0,
 "intent_age_s":0.62,"intent_spread_bps":19.96,"limit_price":10.13,"qty_capital":98,
 "qty":150,"reserved_usd":1060.5,"admit_t":585,"rank_snapshot":3,"admission_age":20,
 "skip_reason":null,
 "quote_source":"sip_stream"
}
```
`skip_reason` vocabulary must be the research one, verbatim:
`intent_not_firm_fresh`, `no_order_zero_depth`, `no_order_min_capital`, `intent_depth_unsupported`,
`spread_above_prediction`, `quote_not_acquired`, `overlap_skips`, `cooldown_skips`,
`max_attempt_skips`, `cash_or_slot_skips`, plus the two new ops-only tags `daily_loss_stop` and
`quotes_unavailable`.

`trades.jsonl` — mirrors `resolve_trade`'s record 1:1 so a diff is mechanical, plus live fields:
```
day, ticker, t, entry_minute, intent_us, score, limit_price, qty, qty_capital,
deployed_reserved_usd, intent_ask/bid/shares/age_s/spread_bps,
entry_status, entry_ask, entry_bid, entry_age_s, entry_quote_us, entry_arrival_us,
exit_minute, exit_submit_us, exit_submit_status, exit_arrival_us, exit_bid, exit_bid_shares,
exit_age_s, exit_quote_us, exit_status, actual_exit_us, fill_status,
attempt_index, positions_at_entry,
--- live-only: broker_order_id, submit_us, ack_us, broker_fill_us, broker_fill_price,
    broker_filled_qty, partial_entry(bool), late_exit(bool), mark_usd, parity_excluded(bool),
    parity_excluded_reason
```
`daily.json` — per-day counts in the research vocabulary (`signals`, `intents_funded`,
`attempts`, `fills`, `known_fills`, `unknown_fills`, `no_match_fills`, `traded_days`,
`skip_reasons{}`, `positions_peak`, `deployed_reserved_usd`, `peak_reserved_usd`) so the paper
block can be read by the same code that reads `results.json`.

Non-negotiable: persist `quotes/<SYM>.parquet` + `bars/<day>.parquet` + `states/<day>.parquet`.
Without them no divergence can ever be attributed, and the repo already records the principle:
preserve SIP around paper fills; paper is a software test (`HYPOTHESES.md:930-932`).

---

## 11. Reuse vs rewrite

| Component | Decision |
|---|---|
| `alpha_open_learned.FEATURES_ALL`, `feature_matrix`, `sha256_file` | **import** (order/nulls contract) |
| `alpha_open_sim.causal_liquidity`, `CONTEXT_FEATURES` | **import** (one-line expressions; keeps the gate identical) |
| `alpha_sparse_daily.day_context` | **import** for the parity harness; live computes the same four aggregates in `features.py` (a one-row-per-symbol frame cannot be grouped) |
| `alpha_quote_aware_frequency`: `limit_price_of`, `minute_us`, `arrival_us`, `MAX_AGE_S`, `MAX_RUNG_COST`, `ENTRY_OVERHEAD_BPS`, `entry_price_leg`, `priced_leg`, `submission_clock`, `SymbolQuotes` | **import the pure parts** — `SymbolQuotes` is a numpy dataclass over arrays, construct it from the captured prints rather than from a parquet day file |
| `alpha_adaptive_depth_sizing`: `capital_quantity`, `participation_quantity`, `view_quantity`, `reserved_usd`, `intent_gate`, `arm_eligibility`, and the constants (HEAD/THRESHOLDS/ORDER_BUDGET/MAX_SLOTS/COOLDOWN_MIN/MAX_ATTEMPTS/FUNDING_SIDE_FACTOR) | **import** — this is the frozen rule; never copy a second version |
| `alpha_adaptive_depth_sizing.replay_day` / `resolve_trade` | **import in the parity harness only** (they take pre-built stream objects); the live path re-implements the same steps against live objects, and its records must be diffable against these |
| `flush_bot.py`: `Broker.clock` retry/fallback, `owned()` client_order_id ownership, `jlog` JSONL convention, day-roll, `kill_cleanup`, EOD flatten, `ALERT` escalation, supervisor + KILL + LIVE flags | **reuse by clone** (~120 lines, adapted) |
| `flush_bot.py`: 60 s poll loop, TV/Alpaca-movers scan, IEX bars, `state_minutes`, OCO brackets, 120-min expiry, partial-fill cancel logic | **do not reuse** (wrong clocks, wrong lane, no analogue) |
| `src/live/features.py` | **do not reuse** — it ports the older `build_features.py` 30-feature ffill lane; different semantics entirely |
| `src/app.py`, `paper_execution.py`, `entries.py`, `exits.py`, `state_machine.py`, `scanner/`, `decision_pipeline.py`, `config/` | **do not reuse** — the earlier attention/30-feature workstream; a second convention would be created. Only `src/_atomic.py` (atomic file write) is worth lifting |
| `factory/scripts/sip_candidates.py` | **reference only** for the snapshot definition (do not call it live: it reads a post-close SIP table) |
| `factory/scripts/flush_bot_supervisor.sh` | **clone** the 17-line pattern for `depth_paper_supervisor.sh` |

---

## 12. Parity harness and acceptance criteria

`depth_paper_parity.py <day>`:
1. read `bars/<day>.parquet`, `quotes/*.parquet`, `snapshots.json` from the live capture;
2. build the panel frame with the **research** code path (`day_context` + `causal_liquidity` +
   `feature_matrix` + the frozen model) and assert **bit-equality** with `states/<day>.parquet`
   (feature matrix max abs diff == 0, pred max abs diff < 1e-9);
3. build `SymbolQuotes` from the captured prints and run `replay_day` for the chosen view against
   the same book state replayed from `intents.jsonl`;
4. diff per attempt on `entry_status`, `entry_ask`, `qty`, `exit_status`, `exit_bid`,
   `actual_exit_us`, `net_usd_25`, plus the live-only deltas
   `broker_fill_price − entry_ask` (entry slippage vs the modelled ask) and
   `broker_exit_price − exit_bid` (exit slippage);
5. emit `parity.json`: counts per outcome class, distributions of the two slippage series, and the
   attempts whose class differs.

Acceptance for "the bot implements the lane" (before any PnL claim):
* feature/pred bit-equality on ≥ 10 captured days (step 2);
* intent-fact equality: `intent_ask/bid/shares/age` equal to the same-clock parity read for every
  non-skipped intent (tolerance 0; a mismatch means the quote cache is not the SIP NBBO);
* skip-reason multiset in the research shape — no systematic `intent_not_firm_fresh` inflation
  (that would mean the stream is starved);
* entry fill class agreement ≥ 90% of funded intents, and **median entry slippage ≤ 0 bps** (live
  must not be systematically worse than the modelled ask);
* exit fill class agreement ≥ 90%, with the median exit slippage reported and a CI.

Acceptance for the *edge* is separate and slow: at 0.2–0.4 funded intents/session, 25–50 known
fills need 150–250 sessions, and with top-5 fills = 125% of total the CI straddles zero for
~1–2 years. Report `$/session`, `$/calendar day`, median fill, top-5 share, months positive and
UNKNOWN share — never a single total.

---

## 13. Top live-vs-sim divergence risks

1. **Fill modelling is not the same object.** The sim asks "was the displayed ask ≤ limit and the
   displayed ask size ≥ q at the arrival print" and then books the fill *at the ask*. Paper books
   whatever the broker's own fill model says (partials, price improvement, no fill at all, its own
   latency rules). With top-5 fills = 125% of PnL, one fill-class difference moves the result more
   than everything else combined. *Detect*: §12 class agreement + entry-slippage distribution.
   *Mitigate*: never tune the rule to match paper; treat paper as a mechanics test
   (`HYPOTHESES.md:930`).
2. **Universe/admission reconstruction.** `rank_snapshot`, `admission_age`, `admit_gain` and the
   subject's own contribution to every cross-sectional mean come from a full-universe sweep with a
   $1 floor, a `px_et >= T-2` freshness rule and a `symbol asc` tie-break. A missing or short bar,
   a different PIT vintage, or a late 09:30 bar changes who is admitted — and therefore changes the
   peer features of names that *did* qualify. *Detect*: compare `snapshots.json` with the research
   `data/sip/candidates/<day>.json` (post-close, offline only) on the admission set and the top-10
   lists. *Mitigate*: same feed (SIP), same vintage, explicit tie-break, and refuse to trade a day
   whose snapshot sweep errored.
3. **Bar/volume parity.** `log_cum_dv`, `log_dv5`, `dv_accel`, `bars15` and the $1M liquidity gate
   all ride on consolidated volume and bar presence. Alpaca's intraday SIP bar for an illiquid
   microcap can differ from the post-close consolidated print (late prints, odd lots, corrections
   arriving after we already decided). This shifts the score silently — no error, just a different
   model input. *Detect*: nightly re-fetch of the day's bars and a diff against
   `bars/<day>.parquet` (the only way to measure revision size). *Mitigate*: accept that the bot
   trades the unrevised tape, and quantify the difference.
4. **Clock and arrival alignment.** The sim reads the intent quote at exactly `t+1:00.000` and the
   arrival at `+250 ms`. Live, the boundary read comes from a stream whose last print may already
   be up to 1.9 s old (legal under `age<=2s`) and the order reaches the broker at an unknown
   offset. Both changes hit the two binding conditions (`ask<=limit`, `ask_size>=q`) and the
   depth-participated size `q` itself, which is computed *from* the intent sizes. *Detect*: log
   `intent_age_s`, `submit_us − intent_us`, `ack_us − intent_us`, and the parity-read diff.
   *Mitigate*: compute early (built-in slack), snapshot quotes locally, never re-read a later
   print to "fix" a size.
5. **No-exit and gap-through in the tail (the sim's UNKNOWN is real money).** The sim's UNKNOWN
   paths (stale book, absent stream, insufficient exit-bid depth) are bookkeeping conventions that
   reserve cash to the session end and *never* create a loss. Live, an unexited position after a
   halt, an LULD pause or a price gap is a real holding that can be −50% while the sim records
   "unknown". 20/142 late attempts (14%) are UNKNOWN and 11 are exit-depth shortfalls, so this
   bucket is not hypothetical. *Detect*: `late_exit`, `parity_excluded`, and any position alive
   after `session_end`. *Mitigate*: DAY order + ALERT + documented next-open exit, and report
   real-PnL and parity-PnL as two separate series forever.

Honourable mention (6): **shared paper account.** `flush_bot` assumes the paper account is
dedicated and adopts any position in it. Two bots in one account will fight over positions and
the ledger. Use separate paper keys for `depthbot`, or run exactly one at a time.

---

## 14. Rollout plan

1. **Shadow (no orders), 2–4 weeks.** `--shadow` logs intents, skips and the full capture set; the
   pipeline runs including sizing and the limit price, then discards the order. Gate: §12 steps
   1–3 on ≥ 10 days, signals/day in the 0.2–0.4 range, no `quote_not_acquired` spikes.
2. **Parity harness green** on those days (bit-equal features/pred, intent facts equal).
3. **Armed paper** with `LIVE` present, KILL armed, supervisor running. Daily checks: journal
   tail, account flat or exactly the expected positions, `ALERT` empty, error count 0.
4. Review at 25 known fills: class agreement, slippage, UNKNOWN share, and whether the days the bot
   refused (feed gaps, risk stop) correlate with the good days. Any rule change needs a new
   pre-reg — this lane's choice is frozen.
5. Only if paper mechanics agree *and* ≥ 25 known fills land positive at 25/50 bps does sizing (or
   real money) become a legitimate question.

---

## 15. Open decisions for the owner (blocking, not derivable)

* **SIP entitlement** (Algo Trader Plus, ~$99/mo): without it neither the quote sizes nor the
  consolidated bars exist, and IEX is measured negative for this family.
* **Dedicated paper account** (or accept that `flush_bot` and `depthbot` cannot run together).
* **Daily loss stop**: confirm −$90 (−3% of the $3,000 book) with "halt entries only", or accept no
  stop at all (pure sim parity). Either way it is logged as a separate bucket.
* **Exit tail handling**: confirm "DAY market order, escalate, exit next open" as the standing
  policy for a position that cannot be priced before the close.
