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

## Next (after lanes return)
Synthesize against local data, keep only multi-definition-robust relationships, design
causal candidate-selection framework, then pattern tests. No production bot yet.
