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
   decaying +10/+9/+5.5 later buckets (n=6 days Mar 2025, 1,661 leader-minutes). Edge is
   early. EOD-reversal literature says exit before ~15:00 ET. All 6 eventual #1s opened $2-10.
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

### Next experiments (bounded, <=2-day samples, vectorized)
1. Expand time-of-day + gap-bucket table to 20+ days across 2025 + 2026 (needs prior-close
   join for true gaps; premarket 08:00-14:30 UTC bars present in files).
2. leader_share/separation vs top-20 (fix denominator bug: share vs all-candidates ~0%).
3. Rank-persistence curve: P(eventual #1 | rank<=k at time t) for t=14:45..16:00.
4. PM-high distance at 14:30 vs rest-of-day return.
Full lane outputs: subagent-artifacts/outputs/3116ad61.../{cameron,science,desk}-deep.md +
379cdfd6.../{cameron,catalyst,literature,microstructure}.md.
