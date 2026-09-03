# Forensic Report — why the corrected strategy failed in 2026 (2026-09-03)

Question: implementation/data/parity bug, or real strategy failure? Is any
monetizable top-gainer edge left in the current code/data?

Short answer: **no remaining material bug found after 11 verification checks; the
2026 failure is real. The 2025 result was a weak signal plus a lucky month accepted
without significance checks. No monetizable long edge survives in this family.**

## 1. Implementation / data / parity verdict: CLEAN

| # | check | result |
|---|---|---|
| 1 | Independent hand-math audit (raw bars → 12 features + labels; hand==parquet==engine, 1253 rows) | PASS (prior session) |
| 2 | Sortedness audit of all time-dependent ops | only rvol_attach was unsorted; fixed (prior session) |
| 3 | Bar-level Alpaca-vs-HF validation (2026-03) | 100.00% listed-ticker coverage; 99.45% closes <0.5%; 99.1% vols <1% |
| 4 | Full-stack cross-vendor replication (Alpaca raw→clean→certify→features→eval on 2026-03) | −87.7bps/unit vs HF −74.6bps — same sign/magnitude |
| 5 | eval_frozen faithfulness (2025-08..12 replay) | **+45.0bps EXACTLY** (matches exposure_design) — eval_frozen_2025_replay.log |
| 6 | Vendor-cliff test | Q1-2026 (HF) −62.8 WORSE than Apr–Aug (Alpaca) −41.6 — no April break |
| 7 | checks.json distributions across 2025/2026 boundary | stable (events/day, gain p50/p90, splits, universe) |
| 8 | Coverage JSONs all 16 months | healthy (rvol .93–.97, m5 .99) |
| 9 | Null-features incident (first featurize pass, clean-dir mismatch) | caught by coverage check, re-run fixed BEFORE eval |
| 10 | Multi-day artifacts (symbol-change −99.99% rows, 20x microcap explosions) | identified, excluded from inference |
| 11 | Independent reviewer subagent | **NOT completed** — 2 attempts, both died on provider 429 rate limits; empirical checks 1–10 cover the same questions |

Alpaca SIP data is, if anything, MORE complete than the HF archive (fills HF's bar
gaps; superset of listed bars). Nothing "isn't aligning with Alpaca."

## 2. What actually broke (corrected data, model_v2, theta .00098)

Monthly chronology, event-level net20 unless noted:

| month | base(all) | M3 | comp | E6pool | IC |
|---|---|---|---|---|---|
| 2025-08 | −0.136% | −0.119% | −0.056% | +0.022% | +0.027 |
| 2025-09 | −0.056% | +0.392% | +0.955% | +1.180% | +0.065 |
| 2025-10 | −0.183% | −0.285% | −0.179% | −0.117% | +0.031 |
| 2025-11 | −0.144% | −0.134% | +0.213% | +0.136% | +0.044 |
| 2025-12 | −0.150% | +0.386% | +1.542% | +1.766% | +0.067 |
| 2026-01 | −0.105% | +0.036% | −0.312% | −1.055% | +0.003 |
| 2026-02 | −0.172% | −0.178% | −0.537% | −0.637% | +0.007 |
| 2026-03 | −0.253% | −0.686% | −0.092% | −0.746% | −0.027 |
| 2026-04 | −0.223% | −0.169% | −0.213% | +0.316% | +0.024 |
| 2026-05 | +0.065% | +0.411% | −0.217% | −0.271% | −0.011 |
| 2026-06 | −0.289% | −0.076% | +0.402% | +1.176% | +0.011 |
| 2026-07 | −0.297% | +0.009% | −0.043% | +0.031% | +0.029 |
| 2026-08 | −0.226% | −0.180% | −0.249% | −0.110% | +0.015 |

Entry-level (E6_rvol8, X_60m, 20bps) t-stats: 2025-08 +0.18, 09 +1.07, 10 +0.25,
11 −0.26, 12 **+2.27 (n=41)**; 2025 pooled t=+1.40 (NOT significant); full 13-month
OOS arc: **−10bps, t=−0.53 (n=558)**.

The 60-minute continuation lift (model rank + rvol>4 + vwap>0.03 over a −15bps
base) fired in 2 of 5 OOS months 2025 (Sep, Dec) and ~0 of 8 months 2026. The daily
long-short score-quintile spread is negative in 11/13 OOS months — the model's top
tail (what gets traded) underperforms its bottom tail.

## 3. Was 2025 luck, overfit, or regime-aligned?

All three partially, in this order of importance:
1. **Small-sample luck (dominant)**: the pooled +45bps leans on Dec-2025 (+177bps,
   n=41) plus micro-cohorts — cycle-3 n=20 at +222bps, late-episode n=31 at +200bps,
   vwap 0.06–0.12 n=39 at +190bps. The repeatable core (cycle-1) was +30bps on n=160
   (t≈1.2). Nov-2025, the other validation month, was NEGATIVE (−17.5bps). The pooled
   validation t=+1.40 never reached significance; per-month t-stats were never
   checked before acceptance.
2. **Mild selection-adjacency**: gates picked on Aug–Oct (pooled +28bps, t=+0.82 —
   also insignificant), "validated" on adjacent Nov–Dec. Never a clean estimate.
3. **Regime-alignment unsupported**: market proxies built from our own clean data
   (liquid-universe drift, dispersion, up-day share) do NOT separate winning from
   losing months — Sep/Dec-2025 are unremarkable months; the strongest market month
   (Apr-2026, +28.6 bpd drift) was a losing strategy month. 2026 is somewhat choppier
   (dispersion 330–388 vs 301–369) but that doesn't explain month-level outcomes.

## 4. Execution is exonerated (and cannot fix this)

- Entry bar t+0 vs t+1: no difference in either year.
- Hold 15/30/60m: ALL negative in 2026 (−30…−42bps); 60m was best in 2025. The loss
  accrues across the whole hour — no mistimed edge to re-time.
- Costs: 2026 gross is negative (−22bps fresh, −43bps Q1). Costs are not the driver.
- Within-episode timing (cycle 1/2/3, minutes-since-first-qual, tod buckets):
  negative everywhere in 2026.
- 12+ simple logical variants on 2026 fresh (pullback, dip-below-VWAP, fresh highs,
  rvol windows, morning-only, score-only, rank-only): ALL negative at event level.
- Conclusion: the opportunity pool itself is negative. No entry/exit rule rescues a
  negative pool. Execution was never the problem.

## 5. Is the underlying mechanism still real? NO (long side, current data)

- 60-min continuation: unmeasurable in 2026 under every conditioning tested.
- Multi-day continuation: NEVER existed — gainer days fade over 1–3d in BOTH years
  (cc1 trimmed t-stats negative 14/16 months; cc3 worse). The 60-min 2025 edge was a
  fragile intraday-only phenomenon.
- Residual model rank IC (+0.02) exists but the tradable top tail underperforms —
  unusable.
- Short side: ≈ breakeven at 20bps RT (+0.2bps fresh, +23bps Q1), with borrow/locate
  friction concentrated on exactly these names. Not a strategy.

## 6. Strongest candidate: NONE in this family

The only same-sign-across-periods cell is rvol 8–12 picks (+54bps 2025 n=75, +24bps
2026-fresh n=79; pooled t≈1.0, not significant). It is a post-hoc subset of a failed
pre-registration and FAILS the "not obviously curve-fit" bar — explicitly NOT a
recommendation, at most an observation-mode filter for the paper bot's measurement
stream.

## 7. What to test next

1. **Forward paper measurement (Sep–Dec 2026) of the M3 stream, zero capital** —
   the only honest validation path left. src/live/features.py is parity-proven; the
   SIP backfill pipeline works. Measure, don't trade.
2. **Genuinely new hypothesis classes** (different signal/universe/horizon) with
   their own pre-registration. All 2025-2026 data is now dev/burned — no more
   "fresh" historical validation exists in this repo.
3. Explicitly NOT: re-selecting gates on 2026, promoting rvol 8–12, building the
   short side on 2026 evidence, deploying the frozen stack.

## 8. Continue toward paper deployment? NO for this stack

Do not deploy the frozen composite/E6 stack. The surviving assets are infrastructure:
parity-proven live feature engine, validated SIP data pipeline, frozen eval harness.
Keep building ONLY the zero-capital measurement path; redirect strategy research to
new hypotheses.
