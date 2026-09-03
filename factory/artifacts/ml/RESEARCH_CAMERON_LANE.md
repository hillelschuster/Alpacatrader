# Research Lane: Discretionary Momentum (Ross Cameron framework) — hypotheses, not rules

Status: RESEARCH DESIGN ONLY. No implementation. No strategy selected. All numbers
below are hypothesis specs to be tested objectively, mostly on 2025 (dev) first.

Source: researcher brief on Cameron's public material (Warrior Trading courses,
YouTube premarket-watch series, "How to Day Trade", interviews), DIRECT/INFERRED
labeled there; cross-checked against framework knowledge. No web fetch was available;
verbatim cutoffs need primary-source confirmation before quoting — but our tests do
not depend on his exact numbers (we grid our own).

## 1. Motivating evidence from our own data (this session)

Two mechanism probes on the 16-month feature panel (fwd60_t1entry, net20, overlapping):

- (a) HINDSIGHT leader regime: minutes belonging to the day's eventual #1 gainer:
  **+29.7bps (2025), +50.9bps (2026)**; mega-leaders (day gain ≥40%): +24.8/+66.5bps.
  Tail minutes (day rank 11+): **−40.6/−43.4bps both years**. Selection regime
  correlates with sign in BOTH years — including 2026, where our stack failed.
- (b) CAUSAL minute-leader (rank==1 at minute t, knowable live): **−26 to −50bps**,
  negative every month both years. Chasing the current leader loses — leadership
  rotates and you buy each rotation top.

(a) is hindsight (day rank needs full-day max gain) — NOT tradable as-is. (b) is
tradable and loses. The gap between them IS the lane: **early identification of the
eventual dominant name** (Cameron's premarket routine) **plus pattern entries within
that name** (his structures). Our failed family did the opposite: model-score
admission spread across all ranks, no dominance concept, fixed 60-min holds with no
invalidation.

## 2. Cameron principles → quantitative translations

### Selection (BEFORE entries — his order, and ours now)

| Principle (brief #) | Quantitative variable | In our data? |
|---|---|---|
| Price $2–$20, sweet $2–$10 | `price_tier`: 2–5 / 5–10 / 10–20 / >20 (session open basis) | YES (log_close) |
| Premarket/inc-session % gain, gap magnitude | `gap_pct` (have: open_gap); premarket high/low/vol | PARTIAL — premarket bars EXIST in raw files (backfill 00–24h UTC window; HF raw ~8% extended-hours). Build `pm_gain`, `pm_volume`, `pm_high` from raw, not clean |
| Relative volume elevated | `rvol` (have) + premarket RVOL vs trailing premarket means | YES (session); premarket baseline needs raw-file pass |
| Low float; FLOAT ROTATION (volume/float, INFERRED multiple — grid 1x/2x/3x/5x, do not assert) | `float_shares` (yfinance floatShares — VERIFIED, ~0.7s/ticker + short% bonus); `rotation_day` = day_volume/float; `rotation_0945` = 9:30–9:45 volume/float | YES w/ PIT caveat (current snapshot; cache monthly, note) |
| Fresh news = fuel; no-news parabolics suspect | `news_24h`, `news_premarket` counts via Alpaca NewsClient (VERIFIED historical). TIMING RULE: catalyst window must END at/before 9:30 ET — articles published mid-move ("movers roundups") are coincident, not causal. Test raw count first, then source-filtered | YES |
| ONE primary stock; avoid diffuse tapes | `dominance` (day-level): leader_gain / runner-up_gain; leader day-dv share of top-20 dv; `inplay_count` = # top-20 names with gain>15% (grid 10/15/20). Leader-selection model (see §4) | YES (certification panel) |
| Short interest / squeeze fuel (our addition, Cameron-adjacent) | `short_pct_float` (yfinance — VERIFIED on sample) | YES w/ PIT caveat |

### Patterns (ONLY within selected names — the interaction hypothesis)

| Structure (brief #) | Event definition on 1-min grid (implementable) |
|---|---|
| ABCD / first pullback | swing high = max high since 9:30; pullback = ≥2 consecutive down closes OR ≥25% retrace of spike range, holding above VWAP AND low > prev_close (red-to-green defense); trigger = close above pullback-leg high on up-volume; stop = pullback low |
| Bull flag (5-min) | impulse +X% in ≤15 min (grid 3/5%); flag 5–15 min, range ≤40% of impulse, vol/min <70% of impulse; trigger = close above flag high; stop under flag low; TIME-STOP: flag >15 min without break = dead (his "loses momentum" rule) |
| Flat-top breakout | same 5-min high tested ≥3× in 30 min with rising lows; trigger = print above level + break volume >1.5× trailing mean; invalidation = close back below level within 5 min |
| Micro-pullback | uptrend (close>VWAP, higher highs): 1–3 red 1-min bars holding above VWAP; trigger = break of micro-high; stop under micro-low (tight); invalidation = >3 red bars or VWAP loss |
| HOD break | high > session max high (n_hod_breaks EXISTS — reuse, add volume-expansion condition) |
| Halt/resume | SKIP v1 (LULD halts not reliably detectable in 1-min bars; proxy = ≥5-min no-trade gap + resume volume spike — mark proxy, defer) |
| Absorption (his tape read → bar proxy) | high volume + small range at HOD/whole-dollar (volume z high, range z low) = buyer absorption, bullish context; inverse at support = bearish. Needs per-ticker rolling z infrastructure (new, small) |

### L2/tape inferences → SIP-approximable proxies (no L2 in system, per brief)

- Bid support holding/stepping → consolidation-low holds across tests + absorption signature (above).
- Ask thinning into level → break-volume expansion ratio.
- Tape speed/acceleration → **trade intensity = trades/min** — NOT in our data (trade_count dropped in backfill; absent in HF). DESIGN CHANGE R1: retain `trade_count` (+vwap) in all future backfills; intensity features for live + forward research.
- Spread state → SIP quotes backfill possible but heavy; DEFER to live measurement.
- Spoofing/stuffing detection → out of scope.

## 3. Research design changes (material)

- **D1. Day-regime conditioning first.** Every pattern test is reported WITHIN
  dominance/in-play/price/rotation/news cells, never pooled-only. The hypothesis is
  the interaction; pooled tests already failed (forensic report).
- **D2. Two-stage program.** Stage A: early-leader prediction (can 9:30–10:00 +
  premarket features identify the eventual dominant name? classification, not
  trading). Stage B: pattern entries WITHIN predicted-leader days only, with
  invalidation exits. Stage B without Stage A repeats the old mistake.
- **D3. Morning primary window** (entries tod<120; his 9:30–11:00 core), midday as
  explicit negative control (his chop avoidance — testable, one parameter).
- **D4. Invalidation exits re-tested on NEW pattern events** (pattern-low stop,
  VWAP-loss exit, flag time-stop). Old X_death verdict was on the old stream +
  polluted data — does not transfer; re-test is legitimate new science.
- **D5. Model score demoted to covariate**, not gate (IC ~0.02, top tail inverted).
- **D6. Float + news attached at ticker-day level** (yfinance monthly cache;
  Alpaca news backfill over the window with the pre-open timing rule).
- **D7. Premarket variables from RAW files** (backfill ohlcv + HF raw extended
  hours — verify HF premarket coverage before use).
- **D8. Catalyst necessity tested as A/B** (news vs no-news within same
  attention regime), never assumed.

## 4. Hypothesis list (test specs; 2025 = dev, 2026 = dev, forward paper = validation)

- **H-C1 (leader prediction):** 9:30–10:00 features (gap, pm gain/vol, rotation_0945,
  news_premarket, first-15-min range/vol, float tier) rank the eventual top-3
  dominant names above median. Metric: top-3 hit rate vs base rate. N ≈ 330 days.
- **H-C2 (regime existence):** predicted-leader days' pattern minutes have positive
  expectancy; non-leader days' don't — same patterns, opposite regimes.
- **H-C3 (first pullback):** first-pullback triggers on predicted leaders, entries
  tod<120, pattern-low stop: expectancy > 0 net20 over 2025+2026 dev.
- **H-C4 (midday negative control):** identical triggers tod 150–300 underperform
  morning triggers (expect ≤ 0).
- **H-C5 (rotation filter):** rotation_day ≥2x filter adds expectancy within leaders
  (grid 1/2/3/5x on dev only).
- **H-C6 (catalyst A/B):** news_premarket>0 vs =0 within same dominance cell —
  measure, do not assume.
- **H-C7 (absorption):** absorption-proxy bars at HOD precede continuation (conditional
  probability test, not a standalone entry).

Power honesty: ~330 leader-days → pattern events likely 500–1500 total. Directional
reads only; significance at 5% needs ~±15bps se — report t-stats, do not ship on
t<2. Genuine validation = forward paper Sep–Dec 2026 (nothing historical is fresh).

## 5. What materially changes vs the old program

Old: score admission across all ranks → fixed 60-min holds → pooled evaluation.
New: dominance selection → pattern triggers → invalidation exits → regime-conditioned
evaluation. Float, rotation, premarket, news, intensity are new state. The 60-min
fixed hold and the score gate are retired hypotheses, kept as covariates/benchmarks.
