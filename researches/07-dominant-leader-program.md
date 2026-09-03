# 07 — Dominant-Leader Research Program (fresh, 2026-09-03)

Status: RUNNING. 6 parallel subagent lanes (same model, web-heavy) + local empirical replication.

## Core finding (to explain, not to trade directly)
- Minutes of the eventual day-#1 gainer: positive in BOTH 2025 and 2026 (hindsight population).
- Tail top-gainers: negative both years. Mega-leaders particularly strong.
- Buying current rank-#1 each minute: negative (rotation tops).
- Prior commit probe: leader +51bps 2026 hindsight vs minute-R1 negative.

## Thesis
Not all top gainers are one population. The #1 dominant attention stock (extreme move +
participation + liquidity + float rotation + catalyst + focal-point status) has a different
bar-generating process. SELECT THE POPULATION FIRST (causally), THEN evaluate patterns.
No hindsight: eligible-at-10:17 means nothing before 10:17 counts.

## Local replication (main thread, 2026-09-03)
- Sample: `data/clean_ohlcv_2025-03.parquet`, first 2 sessions (Mar 3-4 2025), regular session
  (14:30-21:00 UTC), candidates = tickers with >=100 bars & first open $1-50 (n=5,328 tickers).
- Eventual #1 by session open->close % gain: BTAI +55.4% (Mar 3), RAPP +49.0% (Mar 4).
- Next-minute mean: eventual-#1 minutes **+16.6 bps** (n=663) vs tail **-0.48 bps** (n=1,375,571).
  Medians 0. Direction matches the probe. Edge lives in right tail, not median minute.
- Method note: full-month groupby-rank too heavy (timed out at 7M rows); keep samples to
  <=2 days + candidate filter, vectorized merges, no row-wise apply.

## Open empirical questions
1. Early-dominance proxies (causal): premarket/early rank persistence, time in top-1/3/5 by
   14:45/15:30 UTC, gain separation vs #2, early dollar-volume share, volume acceleration.
   Do persistent early leaders behave like eventual #1?
2. Attention definition: % gain alone vs RVOL / dollar volume / float turnover / rank persistence.
   Hypothesis: +35% rotating float 2x with huge volume > thin +80%.
3. Scarcity: 1-2 exceptional names vs 15 similar movers.
4. Price bands ($2-10 vs $10-20 vs other), time-of-day splits.
5. Catalysts: causal timestamped only (Alpaca hist news verified present); test whether
   price/volume already contain the info.
6. Patterns CONDITIONED on attention: first pullback / ABCD / bull flag / micro-pullback /
   flat-top / HOD break / ORB / VWAP reclaim / halt-resume — leader-pattern vs tail-same-pattern.
7. Skeptic kills: hindsight/survivorship, bid-ask bounce, close-auction, outlier days,
   rotation arithmetic — each needs an exact killing test.

## Lanes (async workflow 379cdfd6, 6 lanes)
- cameron: Warrior Trading selection -> causal quantitative proxies.
- literature: attention/momentum empirical + practitioner evidence, testable hypotheses.
- catalyst: catalyst taxonomy + causal-vs-hindsight data assessment + scanner formulas.
- microstructure: L2/tape -> SIP proxies + 1m-scanner-to-SIP architecture feasibility.
- empirical: local 1m leader-formation + early-dominance + price-band splits.
- patterns-skeptic: objective pattern definitions + conditional-edge test design + 5 alt explanations.

## Causal conversion (2026-09-04, main thread, 10 days Mar 2025+Mar 2026)
Top-3 by gain-so-far at 15:00 UTC, ONLY bars<=15:00, forward-to-close (bps, n=30 names):
mean fwd +347 (2025) / +339 (2026) — near-identical across years; median ≈ +305/+437;
P(fwd>20bps costs) = 60%; P(+100bps touched before -100bps) = 63%;
mean MFE +3359/+2272 vs mean MAE -868/-1142. Read: a SIMPLE causal top-3 already
isolates large opportunity in both years — but paths are violent (deep MAEs common),
and intra-watchlist rank does not order outcomes (e.g. BTAI #1 faded -1926 while RAPP
#3 ran +3258 same day). Population selection works; EXTRACTION (entry/invalidation)
is the next problem. 7-lane consolidation (target-curve, dominance-rep, state-archetype,
premarket-backfill, concentration-turnover, target-design, exhaustion-pros) running.

## Synthesis (2026-09-03, all 8 lanes + local tests)

### Cross-source consensus (practitioner x science x local)
1. Selection = catalyst x gap% x volume/RVOL x float, NEVER rank-#1-%-mover alone.
   Every desk (Cameron, SMB, IU, Humbled, BBT, Madaz, Sykes/Grittani, vendors) ranks by the
   combination — this is exactly why blind rank-chasing rotates into tops.
2. Causal premarket funnel numbers: gap >=4% (momentum >=10%), PM vol >=100k shares,
   RVOL >=2x (Cameron 5x ideal; Humbled backtest PM RVOL>1.5, n=280, PF 1.59),
   price $2-20 (sweet $5-10), float <20M (prefer <10M), 3-5 stock watchlist,
   trade 9:30-11:30 only, dead 11:30-14:00, flat by close.
3. Science supports regime split: attention -> fast intraday continuation then days-scale
   reversal (Barber-Odean, Da-Engelberg-Gao, Barber-Huang-Odean-Schwarz); unconditional
   intraday spikes reverse (tail negative = the statistical home); only the overnight-gap +
   news-fresh + imbalance-confirmed subset persists (Heston same-half-hour flow,
   Gao-Han-Li-Zhou first->last half-hour, Lou-Polk overnight-vs-intraday).
4. Local replication: leader minutes +95bps mean (+45 median) in FIRST hour 14:30-15:30 UTC,
   decaying +10/+9/+5.5 later buckets (n=6 days Mar 2025, 1,661 leader-minutes). 2026 CONFIRMS:
   +51bps mean (+25 median) first hour, +12/+28/+6 later (n=6 days Mar 2026, 1,748 leader-minutes;
   incl. GSIW +215% mega-leader day). Gradient holds in the year the old stack failed. Edge is
   early. EOD-reversal literature says exit before ~15:00 ET. All 12 eventual #1s opened $2-10.
   Premarket bars VERIFIED present in backfill files (~4M bars 08:00-14:30 UTC in 2026-03).
5. 15:30-UTC rank is weak alone: 15:30-#1 == eventual #1 only 3/6; 15:30-top5 fwd avg -220bps
   vs rest -32bps (n=6, outlier-driven, needs expansion). Rank needs persistence +
   separation + dollar-share confirmation, not level.
6. Architecture feasible: 1m scanner -> 1-5 names -> deep SIP trades/quotes ($99/mo Algo Trader
   Plus), seconds/event logic OK; SIP = NBBO-only, no L2; sign via lagged-quote Lee-Ready +
   count-based OI; halt/auction prints must be segmented.

### Causal candidate-selection framework (v0 draft, all inputs timestamped <= decision t)
- Universe: price $2-20, prior-day $-vol >= $500k, gap >= +4% (band-pass; 40%+ = watch-only).
- Score A (z): 0.30*gap% + 0.25*log(PM $-vol) + 0.20*PM RVOL-vs-20d + 0.15*catalyst_prior
  (earnings+guidance/FDA-approval/M&A/contract high; analyst/sympathy/none = veto) + 0.10*PM range expansion.
- Score B (separation): leader_share = own PM $-vol / top-gapper total (need >=20%?);
  separation = own gap*xRVOL / rank-2 (need >=1.5x?); skip if >=6 competing >=10% gappers.
- Vetoes: no causal news by 09:25, buyout-target, preclinical/Phase-1-only, gap>50%+PM>5M sh
  (71.5% fade cohort), trailing-5-min move near Tier-2 +-10% LULD band, sub-$2/OTC.
- Confirmation (09:45-10:30): hold above VWAP + break 09:30-09:45 high on rising CVD +
  EMA9>EMA20; entries only on objective structures (bull-flag/flat-top/micro-pullback/HOD-break
  with volume expansion + R:R>=2).
- Data gaps: no local float table (yfinance last-published, document staleness); no local news
  table (Alpaca created_at<=t, calibrate delay vs IR/EDGAR); borrow/FTD daily-lagged only.

### Strongest surviving ideas (ranked)
1. Gap 4-40% + PM RVOL>=2 + catalyst tier + float<20M funnel (unanimous).
2. Morning-only edge: leader drift concentrates 9:30-10:30; EOD exit discipline.
3. PM-high hold + 09:45-10:30 VWAP/CVD confirmation (Gap-and-Go formalized).
4. Separation/share over rank level (leader_share, rank-2 distance, competitor count).
5. Float rotation >=1x early as crowd-leader mark (needs float plumbing).
6. Halt-survival: re-break pause-high <15min on >=150% vol = continuation; else fade.
7. CVD-leader vs rank-leader disagreement -> follow CVD (retail-alignment critique).
8. Clean-daily/blue-sky distance (no resistance within 5%).

### Kill-tests queued (skeptic lane + C1-C6)
- CVD-confirmed/news-fresh/halt-surviving subset still reverses intraday -> thesis dead.
- Fresh vs stale news split zero -> lottery discount dominates.
- Pure 1-min slope works without gap+flow -> contradicts overnight-carry story.
- H1-H8 zero post-2020 in $2-20 illiquid sample -> regime killed it.
- Everything fails net of quoted spread + 1-min lag -> untradable.
- P&L left-tail on high-VIX/market-down days -> add no-trade regime.

### Pass 2 results (2026-09-03/04; 4 web lanes High + 2 empirical, 21-22 sessions)

EMPIRICAL (own panel, causal, bounded day-by-day):
- True gap buckets (prior-close join, open->close mean): 10-20% positive BOTH years
  (+2.97% 2025 n=116; +1.67% 2026 n=201); 4-10% weak/mixed; 20-40% strong 2026 (+6.4%)
  small-n 2025; 40%+ unstable (2025 +7.3% n=19 vs 2026 -8.6% n=15). NOT tradable stats
  (include full-day leaders; unfiltered entry = 55-69% fade base rate) — use as priors.
- Time-of-day leader next-min: 2025: 81/37/18/14 bps; 2026: 46/24/18/5 bps. Front-loaded
  both years (n~2.8k leader-min). Tail ~0 all day.
- Remaining opportunity: eventual #1 in top-3 by 15:00 UTC -> 15:00->close mean +48-50%,
  median +30-33%, beats 20bps costs 9/10 days (n=10, small — expand).
- Rank persistence (22 days): snapshot #1 holds 27-41% (14:45->16:00); top-5 contains
  50-64%. Rank alone never enough.
- SEPARATION (gain1/gain2) >> share: 15:00 high-sep converts 85.7% vs 12.5% low (n=22).
  Leader $-share vs top-20 NON-monotonic (mid tercile 57% beats high 43% every snapshot).
- DEMOTED in-sample: premarket-high distance corr -0.007 (~0); competing-movers count
  corr -0.022 (~0, non-monotonic buckets). Drop both from framework; keep separation.
- Coverage note: 2025 clean files start 14:30 UTC (NO premarket); 2026 backfill files HAVE
  premarket (~71% ticker-days). PM tests only where covered.

WEB rechecks (fresh sources): gap-fade CONFIRMED (FILTRIX n=66,906 monotonic 55.8->69.1%);
RVOL 2x/5x UNCERTAIN as causal cutoff (convention only; SmallCapLab: higher PM vol -> HARDER
fade); 100k PM floor = execution constraint, not signal; float tiers directional only ->
rotation-multiple-by-10:00 is the real variable; $2-20 = tradability compromise (INCLUDES
$5-10 hardest-fade tier per SmallCapLab); morning-only STRONGLY CONFIRMED; EOD reversal
CONFIRMED (winners flatten/losers bounce; 73.9% close below VWAP; 71% fill prior close in 1d).
Tradeoff curve: first 15 min holds most GROSS edge but is unchaseable (46.6% HODs there);
NET-extractable window = 09:45-11:00 continuation; midday/EOD = headwinds. Rent the hold.
Patterns: unconditional edges ~0 (52-57% win); conditioning is everything; leader-vs-tail test
exists NOWHERE publicly — our panel is the only place. New numbers: first pullback 63.2%/
PF 2.25 (n=11k); ORB 190k trades +0.004-0.028R; Concretum RVOL-filter 1637% vs 29%;
HOD-extension curve 93% fade (weak lifts) -> 11% (doublers); squeezed median gap 316% vs
faded 102%; halt = reversal default (SEC 83-87% revert); Cameron 20c stop ceiling;
Dux box (0.5R/trade, 0.25-0.35 ATR stops); grade-by-size A+/A/B/C; day-2/MDR overlay;
pyramid-from-cushion only.
ML formulation: profit-weighted LEARNING-TO-RANK (forward net opportunity labels at decision
minutes 09:30-11:00) + calibrated meta-gate (trade/abstain + size) in 3-stage funnel
(filters -> ranker -> pattern trigger). LightGBM LambdaRank, gain = forward net bps.
Month-blocked walk-forward with purge/embargo, DSR, top-1 net P&L as metric. Binary
classification of leader identity explicitly rejected (loses magnitude, collapses 1-vs-100s).

FRAMEWORK v1 revisions: promote separation@15:00 + 10-20% gap pocket + rotation-pace-by-10:00
+ 09:45 ORB/RVOL stack as earliest gate; demote PM-high distance + competitor-count +
static float tiers; keep vetoes + time-stop ~11:00-11:30 + EOD flatten; exits =
pattern-low / VWAP-loss / 10-15min dead-flag / clock.
Full lane outputs: subagent-artifacts/outputs/04d0cfa8.../{selection-timing,practitioner-v2,
patterns-evidence,ml-formulation}.md (empirical lanes returned inline).

### Next experiments (Stage A order)
1. P(day-#1 | threshold) on own panel for 100k PM-vol / RVOL 2x / gap bands (replace lore).
2. Rotation-by-10:00 ranking vs gap% ranking (needs yfinance float cache <=60 tickers first).
3. Echo-ratio (PM range x direction) replication on small-cap panel.
4. Small-cap-conditional ORB expectancy (filtered vs unfiltered) + time-stop attribution.
5. Leader-vs-tail pattern tests (first-pullback-hold, VWAP-reclaim, pre-10:30 HOD-break).
6. News created_at<=t pipeline + delay calibration vs IR/EDGAR; catalyst-tier split.
7. Slippage measurement for $2-20 at 09:30-10:00 (largest unmodeled cost).
