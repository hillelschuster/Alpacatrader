# UniverseExpansionScope — widening the scored universe of PRE-REG-DEPTH-01

Read-only scoping. No data downloaded, no protected date read (`allowed()` guard:
`factory/scripts/alpha_open_panel.py:69-70`, mirrored in
`alpha_quote_universe_acquire.py:99`). All numbers below are from files on disk,
counted today.

## 1. What the universe actually is

Definition chain, end to end:

| step | code | semantics |
|---|---|---|
| full-PIT selection table | `sip_universe.py:27-40`, `compact_rth()` `sip_universe.py:96-133` | one compact row per symbol-day for the whole PIT universe (`data/sip/universe/rth/<day>.parquet`): `o570`, `delayed_open`, `hi`, `lo`, `c_last`, `vol`, `n_bars`, and `px_T` / `px_T_et` at 13 frozen clocks `T_LIST` |
| gain metric (B snapshot) | `sip_candidates.py:96-110` | `score = px_T / o570 - 1` — **gain vs the 09:30 open anchor, not vs prev close**; eligible rows require `o570 > 0` and `px_T >= $1` (`basket_anatomy.MIN_PRICE`) |
| **rank cap** | `sip_candidates.py:54` `K_TOP = 10`, used by `top_list()` `sip_candidates.py:82-94` | only the **top 10 by score per snapshot** are ever written as ranked names. `MARGIN = 0.01` (`:55`) adds unranked "boundary" names whose score is within 1 point of the 10th |
| candidate net (bars/trades acquisition driver) | `sip_candidates.py:199-207` | `net = union(top10 of every snapshot population) ∪ margin ∪ winners_open(top10 by hi/o570) ∪ winners_prev` — this set drives raw trades ingestion (`sip_ingest.py:101-112`) |
| panel admission | `alpha_open_panel.py:193-197` | for the 6 decision snapshots only (`SNAPSHOTS = (575,585,600,630,660,720)`, `:27`): `score >= 0.05` **and** `px_T >= 1` **and** `px_T_et >= T-2` (fresh last bar) |
| panel tape | `alpha_open_panel.py:198` | bars loaded only for the admitted tickers: `filter(ticker.is_in(list(admit)))` |
| rank feature | `alpha_open_panel.py:31,139` | `rank_snapshot = ranks[t].get(ticker, 11)` — **11 is the "outside top 10" sentinel**; ranks 1..10 only |
| causal liquidity gate | `alpha_open_sim.py:45-46` | `log_cum_dv >= log1p(1_000_000)` **and** `bars15 >= 12` (DV>= $1M and >=12 bars in the last 15 minutes) |
| decision clock | `alpha_open_panel.py:124` | 5-minute lattice from the first admission; OHLCV strictly `et < t`, last actual bar must be `t-1` (`:119-121`) |
| $ floor | `alpha_open_panel.py:131,196` | `close >= $1` at the decision bar (bars below $1 are skipped), same $1 floor in admission |
| live mirror | `depth_bot_features.py:96-101` | identical: `observations.sort(...)`; `top = observations[:10]`; `score = round(gain,6)`; same `>= 0.05`, `>= 1.0`, `>= minute - 2` gates |

**Where "top ~11" comes from:** `sip_candidates.K_TOP = 10` caps every snapshot at ten
ranked names; the panel's `rank_snapshot` default of **11** (`alpha_open_panel.py:139`)
is the only value a name outside that top ten can ever carry. Measured on two
unprotected days, **all ten ranked names clear the 5% gate at every snapshot**, and the
rank-10 cutoff sits far above 5%:

| day | T=575 cut | T=600 cut | T=660 cut | T=720 cut | names >=5% in top10 |
|---|---|---|---|---|---|
| 2023-05-15 | 7.49% | 12.66% | 14.36% | 16.63% | 10/10 at every B snapshot |
| 2025-06-10 | 7.86% | 14.36% | 16.95% | 19.45% | 10/10 at every B snapshot |

So the 5% gain gate is **never** the binding constraint; the rank-10 cap is. That is the
whole supply ceiling. (Source: `data/sip/candidates/<day>.json`, `.snapshots[].cutoff`.)

Concurrency reality check: `admitted_names` per session is 23-27 (panel day manifests
`2023-05-15` 27, `2023-09-20` 23, `2025-10-15` 25; rows 843 / 741 / 1211), but the
liquidity-qualified state count is far smaller: `signal_supply/results.json` funnel gives
**149,284 qualified state rows over 250 validation sessions (597/day)** and **247,345 over
332 late sessions (745/day)**, from which the frozen arm fires only **107 signals
(0.43/session)** on validation and **244 (0.735/session)** late. 62 / 142 attempts.

## 2. What the stored raw bars cover per day

| layer | coverage | evidence |
|---|---|---|
| `data/sip/universe/rth/<day>.parquet` | **full PIT universe**, compact rows only (no minute bars) | `index_rth.jsonl`: 5,290-5,340 `symbols_with_data`/day, `bars_raw` ~1.19-1.24M, 60-70 s per day (e.g. 2023-05-15 row) |
| `data/sip/net/trades/<day>.parquet` | **candidate net only**, ~50-75 symbols/day | `index_candidates.jsonl` `net_n` 49-74 (2021 samples 49-74); 2023-05-15 = 62, 2025-06-10 = 62 |
| `data/sip/net/bars/<day>.parquet` | derived-from-those-trades minute bars, merged with provider fallback | `sip_netbars.py:20`, `build_day()` `:196-286`; 2023-05-15 manifest `symbols: 62, rows_merged: 14,807, classes {healthy_raw: 58, provider_only: 4, unresolved: 0}` |
| `data/sip/net/quotes/<day>.parquet` (ranked cache) | top-3 per snapshot only | `sip_ingest.py:109-112` |
| `~/alpha-data/open-search-v1/quote_universe_acquire/quotes/<day>.parquet` | admitted **panel** universe; 14.1 names/day validation, 15.0/day late | `acquire_summary.json`: 250 days/3,525 pairs, 332 days/4,984 pairs, 1,003 days total, 974,374,748 merged rows |

Composition of the bar archive on 2023-05-15 (`candidates/2023-05-15.json`, jq):
`net_n = 62`; union of **B-snapshot top-10 across all 13 clocks = 42 distinct names**;
B-margin names = 22; day-high winners = 16; `net ⊇ top10` and
`net − (top10 ∪ margin ∪ winners) = 0`, i.e. the net is exactly that union. 2025-06-10:
`net_n = 62`, B-top10 union 36, margin 23, winners 16, same containment.

**Answer to "are ranks 12-50 present with full minute bars?":** *partially.*
- Ranks **11..~15** are present as the `MARGIN` names (score within 1 point of rank 10) —
  22 names on 2023-05-15, 23 on 2025-06-10 — and they already have raw trades + minute bars
  on disk. They fail **only** the panel's top-10 membership test, not the 5% gate.
- Names that were top-10 at *another* clock are also on disk (42 distinct names on the day),
  so a rank-20 universe at 10:00 largely hits names already in the net from the 09:35/09:45
  snapshots.
- Ranks **16..50 that never entered any snapshot's top ten and were not day-high winners are
  absent**: their bars exist only as the compact single row in the universe table.
- Completeness caveat: on 2023-05-15 only 11 of 62 symbols have >= 380 minute bars; the other
  51 are genuinely thin (e.g. ICCC 7 bars, ENVB 15, REBN 11, APM 65) — `bars15 >= 12`
  excludes most of them from the scored set. Thin bars = illiquid names, not a data gap
  (`unresolved: 0`).

Panel's own row-level evidence that ranks beyond 10 are *not* in the tape:
`build_day` records `missing_admitted_tapes` (0 on the three manifests read) and
`alpha_open_panel.py:201` computes it as `admit − tape tickers` — the tape is co-extensive
with the net, so any widening past the net needs new tape.

## 3. Can the existing h60 head score beyond the panel limit without retraining?

Model: `~/alpha-data/open-search-v1/learned/models/payoff_h60.joblib`, sha
`c5493c6c…`, 26 features (`alpha_open_learned.py:47` `FEATURES_ALL = FEATURES + CONTEXT_FEATURES`),
fit 2021-05..2022-12, never refit (`depth_bot_features.py:18-19`).

Two classes of rank/peer dependence:

**(a) `rank_snapshot` — direct, bounded.** It is feature #3 (`alpha_open_panel.py:31`), gain
importance **6.91** (19th of 26; `learned/report.json` `.model_report["60"].gain_importance`).
Training support is `{1..10}` plus the sentinel `11`. GBDT splits are threshold based, so
values 12..30 do not crash anything, but they are **extrapolation**: every split below 11
sends `rank>=11` down the same branch, so ranks 12-30 are scored as "the worst tail", not as
"rank 20". Encoding choice therefore matters more than model fit:
- **primary**: `rank_snapshot = min(rank, 11)` — extra names get the exact value 11 that every
  non-top-10 admission already used → no new input value, no extrapolation;
- **secondary/diagnostic**: `rank_snapshot = min(rank, K+1)` — new values 12..31, honest rank
  semantics, but out of distribution.

**(b) peer context — indirect, hits *existing* rows too.** `CONTEXT_FEATURES`
(`alpha_open_sim.py:19`) are computed over the observed watchlist states at each
`(day, t)`: `peer_positive3` (count), `peer_gain_mean`, `peer_breadth10` (count of
`gain_open >= 10%`), `peer_heat` (median `range5`) — `alpha_open_sim.py:28-33`, recomputed
per day by `alpha_sparse_daily.day_context()` `:408-431`. Importances: **peer_heat 14.09
(9th), peer_gain_mean 12.72 (11th), peer_breadth10 6.98 (17th), peer_positive3 5.79 (22nd)**.
Widening the universe mechanically *dilutes* `peer_gain_mean` and *raises* the counts for
**every** row, including the current top-10 names. So an expansion is **not a superset** of
the frozen run: the same early signals can flip on/off. Any honest test needs an
"additive" arm that recomputes peer context on the top-10 subset (old rows bit-identical)
next to the "pooled" arm (peer context over the wide watchlist).

**How many extra states/day a top-20 / top-30 would add** (projection; the exact count is
measurable for free in stage 0, see §4):

| basis | value | source |
|---|---|---|
| qualified states/day, validation | 597 | 149,284 / 250 |
| qualified states/day, late | 745 | 247,345 / 332 |
| signal conversion | 0.072% (val), 0.099% (late) | 107 / 244 signals |
| rows per admitted name per day | ~27-31 | 843 rows / 27 names; 1211 / 25 |
| lattice clocks per name | up to 78 | `range(first[0], ..., 5)`, `alpha_open_panel.py:124` |

Rank 11-20 adds up to 10 names per snapshot; over 6 snapshots with turnover the union is
typically 20-35 new names, of which the 5% gate and the liquidity gate keep an estimated
one-third to one-half (~8-17 qualified names/day). At ~27-40 states per name that is
**+250-700 states/day (+40-115%)** and, at the measured conversion, **+0.2-0.7 extra
signals/day** (≈ +50-175 signals over the 250-session validation block vs the 107 observed).
Top-30 roughly doubles that again. **[INFERENCE]** — the names, not the states, are what
stage 0 measures exactly.

## 4. Acquisition plan for the gap (validation 2023 + late 2025-02..2026-05 = 582 sessions)

**Stage 0 — re-rank the full universe, zero network, zero new bytes.**
`data/sip/universe/rth/<day>.parquet` already holds `o570` and `px_T/px_T_et` for ~5,300
symbols/day, and `sip_candidates.snapshot()` needs nothing else for the B populations. A
~60-line producer (`sip_candidates.snapshot` with `K_TOP` as a parameter, writing to a NEW
root, never overwriting `data/sip/candidates/`) yields, per day, the exact rank list
1..30 at each of the 6 decision snapshots, plus the set difference against the current net.
Output: `manifest/<day>.json` (candidate list frozen before any fetch, same contract as
`alpha_quote_universe_acquire.py:20-40`) and a `plan_summary.json` with the true pair count.
Run time: minutes (local parquet reads). **Do this first — it converts every number below
from an estimate into a measurement.**

**Stage 1 — minute bars for the incremental names.**
Producer: extend `sip_universe.fetch_bars` (`sip_universe.py:136-186`, `BATCH = 500`,
`StockBarsRequest(TimeFrame.Minute, feed=DataFeed.SIP, adjustment=Adjustment.RAW)`) with a
`--symbols-from <stage0 manifest>` mode writing a bars archive instead of compact rows.
One request per (day, batch of <=500 symbols) → **582-1,200 requests, minutes of wall
clock**; ~40 names × 390 bars = ~16k rows/day, tens of MB total.
Alternative (recommended): extend the existing net/trades route — `sip_ingest.py:101-112`
reads `rec["net"]`; a wider net file feeds the same `--net` ingestion and
`sip_netbars.build_day()` (`:196-286`) reproduces the identical condition-table bars.
Cost: `/v2/stocks/trades`, `limit=10000`, paginated (`sip_ingest.iter_pages` `:254-283`);
extreme days are 1.6M trades for 62 names (2023-05-15 manifest), incremental names are
thinner → ~10-40 pages/day → **6k-23k requests, 1-2 h, ~10 GB** [INFERENCE].

**Stage 2 — NBBO quotes for the incremental names (the real cost).**
Producer: **`alpha_quote_universe_acquire.py --universe` — add a third mode** (currently
`UNIVERSES = ("qualified","panel")`, `:110-119`; the day's ticker list comes from
`day_plan()` `:246-295`). New mode e.g. `wide`: candidate list = stage-0 wide universe minus
symbols already present in the ranked cache/supplements (`existing_symbols()` `:218-232`).
Everything downstream is unchanged and already proven: full-RTH window from 30 s before the
earliest intent to the close, `feed=sip`, `asof=<day>`, `limit=10000`, full pagination,
per-(day,ticker) resume (`fetch_symbol_day()` `:341-500`), PRIMARY_KEEPFIRST merge
(`merge_day()` `:503-556`), 429-aware GET and 0.35 s pacing
(`alpha_sparse_execution_frontier.py:105-107,242-251`).

Measured unit costs (validation shard, `acquire_summary.json`):
- 3,525 (day,ticker) pairs → 46,121 pages ⇒ **3.08 requests/pair** (a top-gainer's RTH SIP
  quote stream pages several times);
- 70,689,203 new quote rows ⇒ **20.1k rows/pair**;
- last shard 4,031 s / 4,984 pairs ⇒ **0.81 s/pair end-to-end** (incl. 0.35 s sleep + merge);
- merged quotes ≈ 974M rows for 1,003 days ≈ 5 GB on disk (day files 2-16 MB, typically
  5-7 MB).

| arm | extra names/day | extra pairs (582 d) | requests | wall clock | new rows | disk |
|---|---|---|---|---|---|---|
| top-20 | ~15-20 | 8,700-11,600 | 27k-36k | **2.0-2.6 h** | 175-233M | 0.9-1.2 GB |
| top-30 | ~30-40 | 17,500-23,300 | 54k-72k | **3.9-5.2 h** | 350-467M | 1.8-2.4 GB |

Plus the raw page JSON (`raw/`, 46k files for 15k pairs) — budget 2-3× the parquet size.

**Totals for the honest test:** ~4-8 h wall clock and **~12-15 GB (top-20) / ~25 GB (top-30)**
of new disk, against ~880 GB free local and ~900 GB cloud. Every GB is re-fetchable and
resumable; the 2024 / 2026-06..08 guard is enforced in code (`guard_days()` `:157-168`,
called before any read `:641`).

**Feasibility on Alpaca historical endpoints:** proven, not speculative — 14,991/14,993
pairs returned `complete` across 1,003 sessions of SIP quotes, and `sip_universe` fetched
5,290 symbols × ~1.2M bars/day for 1,339 days. Constraints to respect: historical Basic
plan 200 req/min (pacing already 171/min), `BATCH=500` for bars, `limit=10000` +
`page_token` for both trades and quotes, and the `^`/`/` symbol-syntax filter
(`sip_universe.py:145-148`). Note the live path will need the same wider universe in
`depth_bot_features._snapshot` (`top = observations[:10]` → `[:K]`) — a one-line change with
a parity test against the widened panel, and no extra subscription (SIP streaming is one
firehose).

## 5. Pre-registration outline (PRE-REG-DEPTH-02)

1. **Frozen parts (may not change):** h60 head (same sha256, no refit), 26 feature
   definitions except the declared rank encoding, 5-minute lattice, intent at t+1, marketable
   limit IOC at the research limit rule, arrival quote at intent+250 ms, freshness <=2 s,
   qty rule (0.5 × min displayed size, $1,000 cap), 60-minute marketable exit, 3 slots,
   $3,000 reserve, 15-minute cooldown, 3 attempts/ticker/day, one position/ticker,
   25/50 bps primary rungs (0/100 reported alongside).
2. **Only variable:** the rank cutoff `K ∈ {10, 20, 30}` at the 6 decision snapshots, with
   admission (`score>=0.05`, `px>=1`, fresh bar) and liquidity (`DV>=$1M`, `bars15>=12`)
   gates **unchanged**. Rationale is measured: at K=10 the gate never binds (§1).
3. **Two declared encodings, both registered:**
   - *A (primary, in-distribution):* `rank_snapshot = min(rank, 11)`.
   - *B (diagnostic, rank-true):* `rank_snapshot = min(rank, K+1)`.
4. **Two declared bookkeeping arms, both registered:**
   - *pooled* — all qualified states compete for the 3 slots exactly as the frozen replay
     does (`score` desc, minute asc); peer context recomputed over the wide watchlist.
   - *additive* — the top-10 signal set is replayed bit-identically (peer context on the
     top-10 subset) and wide-only signals may only take otherwise-idle slots. This isolates
     *supply* from *re-ranking of existing states*; the difference between pooled and
     additive is the interaction term and is reported as such.
5. **Selection — validation 2023 only, 250 sessions.** Choose (K, encoding, bookkeeping) by
   `$/session @25 bps` subject to: >=100 known fills, >=50 traded sessions, `$/session @50 bps`
   > 0, and >=0.5 of the frozen rule's per-session variance explained by the same days (paired
   comparison). No hyperparameter beyond K is tuned.
6. **Reported, not selected on:** the late block 2025-02..2026-05 (332 sessions) — previously
   explored, explicitly *not* pristine; 2024 and 2026-06..08 stay sealed for the one-shot
   holdout of PRE-REG-DEPTH-01 and are not read by any stage of this work.
7. **Multiplicity:** 3 cutoffs × 2 encodings × 2 arms = 12 cells, all published in the
   summary with the funnel (states → signals → eligible → funded → known) per cell. The
   frozen rule's own numbers are re-derived under the same replay before comparison.
8. **PASS:** expansion beats the frozen rule on validation at **both** 25 and 50 bps with
   >=100 known fills, and does not turn negative on the late block at 25 bps.
   **FAIL:** no cell beats the frozen rule at 25 bps on validation, or the extra signals'
   median known fill is worse than the panel's -9.21 median (toxic supply), or the pooled and
   additive arms disagree in sign (instability of the peer-context change).
   **Either outcome is published;** a FAIL closes the supply lever for this rule class.
9. **Data honesty:** symbol-days without acquired quotes stay UNKNOWN (never cash, never
   dropped) — the same coding the frozen replay uses. Any `unresolved`/`unknown_http_*`
   acquisition outcome is reported per day, and an arm is only comparable if the coverage
   funnel is reported beside its PnL.

## 6. GO / NO-GO

**GO — staged, cheapest-first, and validation-only.**

- The premise is measured, not assumed: the 5% gate never binds at the top-10 cutoff on the
  days checked; the rank-10 cap (`sip_candidates.py:54` + `alpha_open_panel.py:139`) is the
  entire supply ceiling, and the frozen rule's capacity controls never bind
  (`signal_supply/results.json`: 0 slot-full, 0 attempt-cap, 0 cash skips).
- Stage 0 (exact wide-universe rank lists + true pair counts) costs no network, no money,
  minutes of CPU, and it re-ranks the same 5,300-symbol compact tables the panel already
  trusts. It must run before anything is bought, because every cost number in §4 is an
  estimate until it does.
- Stage 2's price tag is ~2-5 h of paced requests and 1-2.5 GB — two orders of magnitude
  below the ~$99/month SIP subscription the rule already needs, and well inside local disk.
- The one genuine risk is **not** data or cost, it is the frozen head's calibration under
  (a) unseen `rank_snapshot` values and (b) mechanically shifted peer-context features.
  Both are handled by registering encoding A and the additive arm, so the test can separate
  "more supply" from "the model's inputs moved". If the additive arm is flat and the pooled
  arm is negative, the answer is "the head cannot use the extra names", which is a clean,
  publishable negative — not an ambiguous one.

**NO-GO on any variant that** refits the head, changes tickets/slots/latency, reads 2024 or
2026-06..08, or widens by dropping the liquidity gate (the extra names would then be exactly
the thin, unfillable tails the depth rule loses money on).
