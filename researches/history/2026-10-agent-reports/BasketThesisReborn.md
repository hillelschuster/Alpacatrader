# Basket thesis reborn — evidence and implementation report

## Verdict

**Verdict: basket structure adds no value over the single-name comparator in this grid.** All 18 validation N=2/3 cells lose at 25 bps and are worse than same-time/checkpoint N=1; all 18 late cells are likewise negative and worse. The sole positive-validation comparator region is N=1 at 10:00 state / 5-minute cut (not a robust plateau: median fill is negative, only 4/12 months positive, and 25-bps P&L is highly concentrated). Reject basket promotion; late is retrospective transparency, not confirmation.

## Why the old basket failed (five evidence-based points)

1. **The primitive entry/breadth surface lost money throughout.** The worktree F1 report says all 120 registered cells were negative: across both blocks the mean spans −4.60% to −1.99% at 100 bps and −5.07% to −2.44% at 150 bps. This covered A_pm/A_pm31/A_open/B585/B600, N=2/3/4, hold and R1 exits. (Evidence: [RUN], `factory/artifacts/basket/phase2/F1/README.md`.)
2. **Rule-based release improved the comparator but did not create positive economics.** F3's nine R2/R3 release settings had positive deltas versus R0, yet every pooled cell remained negative: −4.12% to −2.99% at 100 bps and −4.59% to −3.47% at 150 bps. Thus cheaper failed tickets alone did not pay for the basket. (Evidence: [RUN], `factory/artifacts/basket/phase2/F3/README.md`.)
3. **Simple concentration/weighting was not a robust cure.** F9 found all computed means negative in both blocks; rank-linear improved 6/120 full-period cells and mild score-gap 8/120, while their average full-period deltas were −0.280 and −0.063 percentage points. Sparse favorable cells did not establish a repeatable weighting benefit. (Evidence: [RUN], `factory/artifacts/basket/phase2/F9/README.md`.)
4. **The old registered basket economics and the desired deployment object differ.** The canonical basket contract uses anatomy fills / next-bar opens and the standard 100/150-bps base/stress ladder, while the requested mechanism depends on observed NBBO touch, displayed-depth-limited whole shares, and forecast re-evaluation during the position. The former results do not certify this distinct quote-executed formulation. (Sources: `factory/BASKET-SIM-CONTRACT.md`; `researches/PRE-REG-BASKET-02.md` §§2, 3, 4.)
5. **There was a real but insufficiently harvested joint tail.** Current-checkout F8 says A_pm/N=3 had ≥2 profitable close outcomes on 30.0% of days and ≥2 +30% touches on 9.8%, but every all-hold family×N mean was negative; its A_pm ord2/ord3 EOD medians were −5.1%/−17.9%, and 21.5% of days had a +30 toucher with at most one green EOD name (“spike then bleed”). MFE/touch opportunity did not translate to basket-level realized value. (Evidence: [RUN], `factory/artifacts/basket/phase2/F8_joint/README.md` §§2–4.)

The current checkout's F8 joint report corroborates tail/fade mechanics; current checkout F3_release reports frequent loss breaches and conditional deterioration patterns, but is an anatomy study, not a quote-priced survivor P&L. The required worktree `factory/STATE.md` tail ends with the Oct. 6 EXP-95/H025 closure and adds no basket results. Worktree F1/F3/F9 outcome summaries were read directly.

## Reborn grid status

The new producer declares decision times 10:00/10:30/11:00 ET, sizes 2/3, score-cut checkpoints 5/10/15 minutes, h60 score cutoff 0.030, 50% of smaller displayed L1 size for quantity, and a $1,000/name, $3,000 gross cap. It imports the existing depth lane's quote readers and uses ask/bid touch pricing, the existing immutable learned h60 head, and cost rungs 0/25/50/100 bps. A top-single comparator is included as N=1.

The complete 250-session 2023 validation and 332-session late replay finished. Entries/exits used historical L1 ask/bid touch with integer quantities capped by 50% of the smaller displayed side and $1,000 per name. Entry/exit modeled fills are conditional depth touches, not guaranteed exchange/broker fills. The 0/25/50/100-bps residual round-trip ladder is applied beyond spread already paid via ask/bid.
Clock convention: table labels are the completed-panel-bar ET minute (`t=600/630/660`); the decision/quote intent follows at `t+1`, consistent with the source panel's one-bar lag.

### Full surface: validation 2023

`$25`/session is known net contribution at 25 bps; Δ is paired cell dollars/session vs N=1 with same time/cut. All dollar rates divide known contribution by all 250 panel sessions; $/calendar day divides by the fixed 361-day span. Fill counts are known quote fills; unknowns are separate. Best within each three-cell neighborhood is bolded in the complete artifact.

| state T (ET) | N | cut min | $25/session | Δ vs N1/session |
|---|---:|---:|---:|---:|
| 10:00 | 1 | 5 | +0.96 | — |
| 10:00 | 1 | 10 | −0.41 | — |
| 10:00 | 1 | 15 | +0.56 | — |
| 10:00 | 2 | 5 | **−0.83** | −1.79 |
| 10:00 | 2 | 10 | −2.83 | −2.42 |
| 10:00 | 2 | 15 | −1.56 | −2.11 |
| 10:00 | 3 | 5 | −3.61 | −4.57 |
| 10:00 | 3 | 10 | −4.03 | −3.62 |
| 10:00 | 3 | 15 | −3.34 | −3.89 |
| 10:30 | 1 | 5 | −2.99 | — |
| 10:30 | 1 | 10 | −1.78 | — |
| 10:30 | 1 | 15 | −2.66 | — |
| 10:30 | 2 | 5 | −6.97 | −3.98 |
| 10:30 | 2 | 10 | −5.87 | −4.09 |
| 10:30 | 2 | 15 | −6.42 | −3.76 |
| 10:30 | 3 | 5 | −5.50 | −2.50 |
| 10:30 | 3 | 10 | −4.39 | −2.60 |
| 10:30 | 3 | 15 | −5.47 | −2.82 |
| 11:00 | 1 | 5 | −3.77 | — |
| 11:00 | 1 | 10 | −3.31 | — |
| 11:00 | 1 | 15 | −3.03 | — |
| 11:00 | 2 | 5 | −5.52 | −1.75 |
| 11:00 | 2 | 10 | −4.74 | −1.43 |
| 11:00 | 2 | 15 | −4.33 | −1.30 |
| 11:00 | 3 | 5 | −6.89 | −3.12 |
| 11:00 | 3 | 10 | −5.77 | −2.46 |
| 11:00 | 3 | 15 | −5.37 | −2.33 |

Across N=2/3: every validation cell is negative at 25 bps (range −$0.83…−$6.97/session; −$0.58…−$4.83 per calendar day) and every Δ vs N=1 is negative (−$1.30…−$4.57/session). The best basket cell, N=2 / 10:00 / 5m, has 250 fills, 38 unknowns, median fill −$3.01, 4 positive months, and total known P&L −$207.86 at 25 bps. Its single-name comparator makes +$0.96/session at 25 bps, but has median fill −$2.11, 130 fills, 19 unknowns, only 4 positive months and top-five fill share 227%—a spike, not a plateau.

Primary-cost plateau check (range over all 18 basket cells, known dollars/session): **0 bps −$5.63…+$0.96; 25 bps −$6.97…−$0.83; 50 bps −$8.81…−$2.62; 100 bps −$12.65…−$6.19.** At 50 bps even the 10:00/5m N=1 comparator is just +$0.03/session, then turns negative at 100 bps. No N=2/3 cell is positive at any rung. Basket-cell fill counts span 188–393, unknown counts 24–53, median fill −$6.97…−$2.45; positive months span 0–5 of 12. Top-five share is undefined for all losing basket totals (not evidence of diversified profits).

### Late transparency: 2025-02..2026-05

All N=2/3 cells are negative at every cost rung. At 25 bps the range is −$3.99…−$11.77/session (−$2.75…−$8.13/calendar day), and every basket cell underperforms its same-time/cut N=1 comparator by −$0.80…−$7.82/session. Fill count range: 274–495; unknown: 30–62; median fill: −$5.53…−$3.71; positive months: 0–5 of 16. For 0/25/50/100 bps, all-cell basket $/session ranges are respectively −$9.68…−$2.28, −$11.77…−$3.99, −$13.86…−$5.53, and −$18.77…−$8.53. This previously explored late span is not pristine holdout evidence.

### Bottom line

Validation-only selection favors the strongest N=1 comparator cell, not a basket; its 0/25/50/100-bps known totals are $471.64/$239.52/$7.39/−$456.86 across 250 sessions. Chosen N=2 basket at same time/cut: $238.92/−$207.86/−$654.65/−$1,548.21; no basket structure uplift. No non-overfit plateau, no basket value-add verdict in favor, and no strategy recommendation. This is not the same design as the production adaptive-depth lane's qualified, threshold-gated $1,000 ticket; comparison here is explicitly the requested top-scored single-name basket control, not a claim against the lane's full portfolio economics.

## Action

1. No next basket work is recommended. The evidence says retain the simpler single-name mechanism and do not promote this basket formulation.
2. If the learned-score idea is revisited, register a genuinely distinct hypothesis before any further selection; do not tune the current grid against the same 2023 validation.
3. No paper orders or bot changes were made.

## Paths

- `factory/scripts/alpha_basket_reborn.py` (producer)
- `~/alpha-data/open-search-v1/basket_reborn/contract.json`, `results.json`, `daily/*.json` (complete replay outputs)
