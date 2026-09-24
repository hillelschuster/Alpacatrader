# F10/F14 — descriptive environment, time and trend map

**Evidence:** [RUN] `basket_f10_f14.py` mapped all 1,066 permitted dev days and all 120 completed frozen F1 cells. No classifier, gate, threshold search, strategy variant, or cell selection was run. `maps.json` contains each F1 cell's pooled and dual-block daily-return summaries in every map level; `environment_days.jsonl` is the matching daily feature/label panel; `run_config.json` records definitions, date boundary, inputs, and exclusions.

## Frozen map dimensions

Environment fields are measured from canonical anatomy/bars at 10:00 ET, using only B/600 snapshot members known at that time and bars with `et < 600` (bar 599 is the last completed bar). Each numeric environment label uses the fixed two cutpoints in `run_config.json`:

| Dimension | Measurement | Fixed buckets |
|---|---|---|
| PM leader strength | Maximum A_pm `sel` at 09:30 snapshot | <10%, 10–30%, ≥30% |
| PM top-10 dispersion | Max minus min A_pm `sel` | <10pp, 10–50pp, ≥50pp |
| PM breadth | A_pm names with `sel` ≥10% | 0–1, 2–3, 4+ |
| B/600 cohort trend | Equal-weight mean of canonical B/600 `sel` scores at T=600 (open-anchored return using the last completed bar) | <−1%, −1% to +1%, ≥+1% |
| Prior-session cohort trend | Same B/600 top-10 cohort return from previous represented dev session; unavailable across a >7-calendar-day data gap | <−1%, −1% to +1%, ≥+1% |
| Realized volatility | Median, across B/600 members, of population SD of adjacent 1-minute close returns through 09:59 | <0.5%, 0.5–1.5%, ≥1.5% |
| B/600 breadth | B/600 names with `sel` ≥10% | 0–1, 2–3, 4+ |
| Aggregate dollar volume | Sum(close × volume) for B/600 names through 09:59 | <$1m, $1–10m, ≥$10m |

Time dimensions are calendar year, quarter, month, and weekday. Every dimension reports daily `r_day` summaries for each of the 120 F1 combinations (entry × N × primitive exit × 100/150bps), pooled and separately for block 1 (2021-02..2023-12) and block 2 (2025-02..2026-05). All three fixed levels of each environment bucket are retained even when no development day lands in a level; such rows carry `n_days: 0` and null summaries. Every map level also carries both block rows, including zero-coverage block/level combinations.

## Descriptive read and limits

The fixed categories are highly imbalanced: PM leader strength is 10–30% on 41 days and ≥30% on 1,025; PM ≥10% breadth is 2–3 names on 6 days and 4+ on 1,060; B/600 trend is ≥+1% on all 1,066 days; B/600 ≥10% breadth is 0–1 on 3 days, 2–3 on 53, and 4+ on 1,010; realized volatility is 0.5–1.5% on 254 days and ≥1.5% on 812; selected-name dollar volume is $1–10m on 24 days and ≥$10m on 1,042. Two days lack a prior represented-session feature because of the excluded 2024/2025-01 gap. These counts describe this canonical selected-name sample; unpopulated categories remain visible in the definitions and were not retuned.

The allowed substrate has no SPY/IWM series or PIT market-cap field: B/600 trend is a top-gainer cohort proxy, **not** a broad-market or small-cap index regime. PM and dollar-volume fields likewise describe selected names, not the full market. Environment and time associations are descriptive, conditioned on the already-frozen F1 surface and shared development sample; they establish neither a profitable regime nor a usable gate. **The 10:00 labels are later than the A_pm/A_pm31/A_open entries**, so their association with those families' full-day F1 outcomes is retrospective and cannot justify pre-10:00 entry economics. It may motivate only morning-continuation hypotheses whose action time is declared at/after the measured state and whose outcomes start strictly after that decision using contract-compliant next-bar execution. The F2/F12 remaining-outcome panel is the appropriate source for remaining-tail state associations; neither panel alone is a causal action effect. No winner, profitability, out-of-sample, or live claim is made. Any later gate needs its own declaration and dual-block evidence.

## Provenance / integrity

- Canonical inputs: `factory/artifacts/basket/sip/anatomy/YYYY-MM-DD.jsonl`, `bars/YYYY-MM-DD.parquet`, and `phase2_session_calendar.json`; F1 outcomes are read-only `F1/surface.json` and each of its 120 `daily.parquet` files.
- Exact day set: 2021-02-01..2023-12-29 plus 2025-02-03..2026-05-29, 1,066 unique dates from `basket_sim.dev_days()`; the loader refuses sealed 2024/2025-01 and reserved 2026-06..08 before opening a day.
- No Phase-1 artifact, simulator, contract, strategy, shared state, or live-bot file was modified.
