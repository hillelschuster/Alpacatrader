# HARVEST01 — DISCOVERY CONTEXT: what already exists for a state-conditioned V(t) read

Read-only inventory for the next HARVEST01 move. Every number carries a pointer. Nothing here is a
new result; it is the prior that the new design must not repeat.

Roots: worktree `/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1` ·
data root `/home/hillel/projects/Alpacatrader/data` · evidence
`factory/artifacts/basket/phase2/{ATLAS, HARVEST01}/`.

**Population mismatch to keep straight.** ATLAS (CV01/EV01/fall/E1/E3) runs on the **ATLAS panel v2**
(SIP, 1,066 dev days, families `A_pm` / `B600`, **6,160 members**, 1,900,432 state rows,
`panel_sha256 2a021eda…`). HARVEST01 runs on **market-wide PIT-universe top gainers** at 19 clocks,
N=1..4, 4 anchor variants (`raw`/`primary`/`listed`/`compact`). A CV01 result is a *prior on a
different roster*, not a transferable number.

---

## 1. CV01 — the existing continuation-value primitive

Sources: `ATLAS/CV01/report.md`, `ATLAS/CV01/contract.json` (sha `3c599dd36569a56e`),
`ATLAS/CV01/audit.json`, `ATLAS/CV01/A/{counts.json,headline.json,support_by_horizon.csv,…}`.
Bullets: `researches/STATE.md` "UPDATE 2026-09-30 (night)" (CV01 paragraph + EV01 paragraph).

### 1.1 Exact definitions (contract keys, quoted semantics)

| key | value |
|---|---|
| `baseline` | `W_exit = q*P_exit*(1−0.005)`, `P_exit` = next executable open after the completed decision; `q=1` by linear scaling |
| `continuation` | `W_hold = q*P_open(e)*(1−0.005)` at later observed executable minute endpoint `e`; **`CV = W_hold/W_exit − 1`**; the immediate endpoint has `CV=0` exactly |
| `side_friction_bps` / `cash_return` | 50 / 0 → sell fee cancels between the two A arms; `fric_k = (1−s)/(1+s) = 0.990049…` applies only to the B re-entry round trip |
| `existing_entry_cost` | **sunk**, excluded; entry price allowed only as past state / labelled tail attribution |
| `horizons` | every available integer-minute endpoint on the elapsed-ET lattice, reported **separately** from `decision_et` and from `immediate_exit_et`; gaps stay missing, never interpolated |
| `state_time` | panel `et` is a bar-**start** label; completed state is available at `decision_et = et+1` |
| `states` (allowlist) | `family, decision clock, tenure, ret_from_fill, mfe_so_far, dist_from_running_high, mfe_surrendered, bars_since_new_high, reclaim_count, failed_reclaim_count, ret_1/ret_3/ret_5, up_close_streak, volume_vs_own_median, volume_accel, ret_percentile_candidates, peer_ret_median, peer_new_high_5` |
| `analysis` | descriptive curves only; one causal coordinate at a time in coarse declared bins; **no joint high-dimensional grid, no ML, no horizon/stop/size optimisation** |
| `aggregation` | occupancy-weighted **and** equal-member-within-day/family views published **separately**; day/month-clustered uncertainty |
| `tail` | all extremes untrimmed; future labels are **never** states, filters or denominators |

Support (`A/counts.json`): 6,160 members / 273 censored · 1,888,885 clock-eligible decisions ·
**185 lack an immediate liquidation print and stay UNKNOWN** (never 0) · 1,888,700 have one ·
`h0` CV exactly 0 · CV dimensionless (fee cancels).

### 1.2 Headline A (family `ALL`, day×family balanced) — `report.md` §A

| elapsed minutes after next-open liquidation | balanced CV | occupancy CV |
|---|---|---|
| 0 | 0.000% (exact) | 0.000% |
| 60 | **−0.438%** | −0.449% |
| 120 | **−0.560%** | −0.708% |
| 240 | **−0.805%** | −1.138% |

Earliest decision-clock bucket (09:30–10:00) −1.27% @h60 vs −0.25% at 11:00–11:30. Per-minute pointwise
curves: `A/headline.json` → `A_mean_member_by_h`, `A_mean_occ_by_h` (h = 0…388).

### 1.3 Headline B (retain vs sell→re-enter vs cash) — `report.md` §B

`A retain` beats `B sell→reenter` at every reported endpoint (B−A = −0.74 / −1.04 / −1.00 / −0.81 /
−0.73 / −0.66 pp at h = 1 / 5 / 15 / 30 / 60 / 120) and **both arms fall below cash from ~15
minutes on** (A−cash +0.17 / +0.05 / −0.33 / −0.94 / −1.35 / −1.53 pp at the same h). Complete-tape
sensitivity keeps the ordering (h30 A −1.06pp / B −1.80pp / B−A −0.74pp). B census (`B/manifest.json`):
5,165 sales, 4,793 re-entries, 1 unresolved, 991 never triggered. Explicit caveat in the report:
*holding better than this rule ≠ hold-always has positive EV*, and these means establish no stop policy.

### 1.4 State conditioning already run — declared bins (`A/counts.json → pins`)

`clock_bucket_minutes 30` (anchor et 570) · `tenure_bucket_bars 15` · `quantile_subsample_rate 16` ·
`quantile_levels [0.01,0.05,0.1,0.25,0.5,0.75,0.9,0.95,0.99]` · `ruler_note`: single-coordinate
descriptive rulers frozen before any CV was computed, missing = explicit `UNKNOWN(missing)` bucket.

| coordinate | edges | labels | artifact |
|---|---|---|---|
| **`dist_from_running_high`** (the declared `state_coordinate`) | `−∞, −0.2, −0.1, −0.05, −0.02, 0.02, ∞` | `<=-20%`, `-20..-10%`, `-10..-5%`, `-5..-2%`, `-2..2%`, `>=+2%` | `A/curve_state_distrh_h.csv` |
| **`volume_accel`** | `−∞, −0.5, 0, 1, ∞` | `<=-0.5`, `-0.5..0`, `0..1`, `>=1`, `UNKNOWN(missing)` | `A/curve_state_volaccel_h.csv` |
| `ret_from_fill` | `−∞, 0, 0.1, 0.3, 1.0, ∞` | `<=0`, `0..10%`, `10..30%`, `30..100%`, `>=100%`, `UNKNOWN(missing)` | `A/curve_state_retfill_h.csv` |
| `ret_percentile_candidates` (native range 0..1) | `0, 0.25, 0.5, 0.75, 1` | `q1_0..25`, `q2_25..50`, `q3_50..75`, `q4_75..100`, `UNKNOWN(missing)` | `A/curve_state_retpct_h.csv` |

CSV columns: `<bin>,label,h,n_pairs,n_member_states,mean_occ,mean_member,pos_sum,neg_sum,n_pos,n_neg`
(+ `cv_min/cv_max/cv_p01…cv_p99/cv_n` on `distrh`). `mean_member` = the day×family-balanced view,
`mean_occ` = the occupancy-weighted view.

### 1.5 What those bins actually say (mean_member %, h60 / h120 / h240)

| `dist_from_running_high` | mean_member | mean_occ |
|---|---|---|
| `<=-20%` | **+1.51 / +1.75 / +0.42** | −0.95 / −1.98 / −4.23 |
| `-20..-10%` | −2.50 / −2.85 / −2.41 | −0.45 / −0.60 / −1.00 |
| `-10..-5%` | −4.12 / −5.16 / −4.67 | −0.17 / −0.12 / −0.15 |
| `-5..-2%` | −4.84 / −5.89 / −5.46 | −0.02 / +0.13 / +0.27 |
| `-2..2%` | −5.24 / −6.43 / −6.30 | −0.02 / +0.17 / +0.28 |

| `ret_from_fill` | mean_member @h60 | @h240 | member-states @h60 |
|---|---|---|---|
| `<=0` | **+3.02** | +3.14 | 2,129 |
| `0..10%` | −2.59 | −2.78 | 2,115 |
| `10..30%` | −3.87 | −4.21 | 1,622 |
| `30..100%` | −6.26 | −7.09 | 751 |
| `>=100%` | −8.73 | −11.33 | 142 |

| `volume_accel` | mean_member @h60 | @h240 |
|---|---|---|
| `<=-0.5` | +0.10 | −0.29 |
| `-0.5..0` | −0.48 | −0.88 |
| `0..1` | — | −0.97 |
| `>=1` | — | −0.93 |
| `UNKNOWN(missing)` | — | −1.73 |

| `ret_percentile_candidates` | mean_member @h60 | @h240 | mean_occ @h60 |
|---|---|---|---|
| `q1_0..25` | **+3.39** | +3.16 | −0.82 |
| `q2_25..50` | +1.04 | +0.76 | −0.31 |
| `q3_50..75` | −0.38 | −0.47 | −0.30 |
| `q4_75..100` | −3.73 | −4.15 | −0.42 |

### 1.6 Positives / negatives already banked (do not re-run as novelty)

**Positive (conditional, member-balanced only).**
- Deep damage (`dist_from_running_high <= −20%`) is the only positive state bin (+1.5% @h60) — and it
  is **negative on the occupancy view** (−0.95%), i.e. the same states are worth holding per member but
  cost money per minute-opportunity. Same pattern for `ret_from_fill <=0` and `retpct q1`.
- **Monotone negative in the move-so-far**: `ret_from_fill` falls +3.0% → −11.3% across bins;
  `ret_percentile_candidates` falls +3.4% → −4.2% across quartiles. The further the name has already
  run, the worse its continuation, in both weightings.

**Negative / closed.**
- `volume_accel` is a **flat-to-weak** separator (all bins within ~1 pp of each other, monotone only
  through the `UNKNOWN` bucket). The single coordinate the CV01 contract explicitly offers as the
  "observable renewed activity" hypothesis shows no separation here.
- Overall continuation is negative at every marked horizon (h60 −0.438%, h240 −0.805%).

**Guards already written (verbatim, `report.md` §Guardrails).**
- The A marginal split (return ≤ 0: +3.02% per day-member vs negative: −0.449% occupancy) is a
  **weight reversal, not a buy-the-dip edge**; the "a few member-days dominate" explanation is void
  because the readout is day-balanced.
- Time-in-bin / path-duration and clock composition are an **inference, not a measured separator**;
  **no causal separator** between "tail worth holding" and "bounce into temporary failure" is
  identified. The only candidate next question is *observable renewed activity or peer strength*
  under a fixed-clock/tenure/damage-matched first-GB→reclaim design — posed as a question, never fitted.

**Tail attribution (audit-only, never a state / filter / denominator)** — same section:
`entry_mfe_ge_300pct` (n≈8–13) A +32pp @h30 / +189pp @h240; `entry_mfe_100_300pct` (n≈133) A +16.6pp
@h30; `entry_mfe_lt_50pct` (n≈3,917) below cash in both arms. CV denominator is **current next-open
liquidation proceeds**, never the original entry price.

---

## 2. HARVEST01 lanes — what already carries per-member minute-ish state

Data root `data/harvest01/`, 1,066 days (2021-02-01..2026-05-29; 2024 + 2025-01 sealed; 2026-06..08
reserved). Producers under `factory/scripts/basket_harvest_*.py`.

### 2.1 `sim/fills/<day>.parquet` — per **member-day-clock-rank** (one row per selection)

Built by `basket_harvest_sim.py` (`member_eval`, rows assembled at lines 128–160).

Present, usable as-is: `day, variant, clock, rank, ticker, status` (`filled`/`blocked`), `fill_et`,
`fill_px`, `entry_gap_min`, `decision_px`, `decision_gain`, `slip_vs_decision`, `pit_listed`,
`age_min`, **`mfe_adj`, `mae_adj`, `mfe_et`, `mae_et`**, and `r{E}_100` / `r{E}_150` for every exit
clock `E` (cash ⇒ 0.0, missing ⇒ null).

**Not present:** any per-minute trajectory — no `ret_from_fill(t)`, no `mfe_so_far(t)`,
no `dist_from_running_high(t)`, no running-high/new-high counters, no volume state, no per-minute
mark. `mfe_adj/mae_adj` are **whole-session** extremes from fill to the last available bar, and
`mfe_et/mae_et` are their timestamps; they are *future* labels about the member.

⇒ **Everything minute-conditioned must be recomputed** from `base/bars/<day>.parquet`
(`ticker, et, open, high, low, close, volume, trade_count`, from 04:00 ET to `session_end`,
**only** for the tickers that appear in `base/selected` plus the rank-1 rows of `base/leaders` —
`basket_harvest_bars.py:140-156`). That restriction is the binding limit on market-wide path work.

### 2.2 `base/champs/<day>.parquet` — per **ticker-day**, market-wide (from `race.minute_full`)

`basket_harvest_select.py:279-297`. Columns: `ticker, px_max, px_max_et, px_last, px_last_et,
n_known_min, first_known_et, session_end, prev_close_raw, prev_close_adj, split_factor,
split_events, flag_discrepancy, flag_nonpos, pit_listed, gain_max_adj, gain_last_adj, gain_max_raw,
day`.

Gives **day-level** outcome maxima for every ranked name (containment / champion analysis), plus the
split-normalised denominator. **No intraday path** — no MFE clock, no per-minute marks, no volume.
`gain_max_adj` is retrospective by construction.

### 2.3 `window/<day>.parquet` and `window/members/<day>.parquet`

`basket_harvest_window.py`. Basket level: `day, clock, N, peak_et, peak_ret, r{E}, cap{E}` for
`E ∈ WINDOW_EXITS = [600,615,630,645,660,675,690,705,720,750,780]`. Member level:
`day, clock, rank, ticker, m_peak_et, m_peak_ret, m_close_ret`.

These are **marks at 11 coarse grid clocks**, computed as "each member's last known close"
(last-observation-carried-forward), not executable fills, and `cap{E} = r{E}/peak_ret`. Per-minute
resolution is available only as `peak_et` (an integer minute). **Not reusable as a V(t) ladder** —
no per-minute mark series is stored. Numbers: `readouts/window.md` (median peak 09:43–09:50 for PM
entries, 12:07 for 11:00 entries, 13:02 for 12:00; mean mark at noon ≈ −4.5%; `cap@720` −83% for
09:20 entries at N=3).

### 2.4 Adjacent lanes worth knowing

- `base/selected/<day>.parquet` — the decision row: `day, clock, source` (`pm_snapshot` /
  `minute_full`), `variant`, `rank`, `ticker`, `decision_px`, `decision_et`, `age_min`, `gain_adj`,
  `gain_raw`, `prev_close_raw`, `prev_close_adj`, `prev_used_src`, `split_factor`, `split_event`,
  `flag_discrepancy`, `n_eligible`, `rank_known`, `rank_fresh`, `rank_unfiltered`, `known_by_t`,
  `pit_listed` (`basket_harvest_select.py:161-176`).
- `report/subminute_probe.parquet` — the **only direct execution measurement**: `day, ticker, clock,
  rank, mfe_adj, fill_et, fill_px, n_prints, first_print_et, first_print_px, entry_px_diff,
  first60_n, first60_sz, first60_range, exit_print_et, exit_first_print_px, max_print_gap_min,
  status`. Sample = 120 rows / 79 days (`readouts/subminute_probe.md`). **`exit_px_diff` is a dead
  branch** (`… if False else None`, `basket_harvest_subminute.py:117`) — the exit-side
  print-vs-bar-open difference is *not* computed. Only the entry side is measured.

---

## 3. `race.minute_full` — usable as LIVE state per ticker per minute

Path: `data/atlas/observation/v0/race.minute_full/month=YYYY-MM/YYYY-MM-DD.parquet`.
Shape: **1,066 days, 2,352,687,570 rows**, `t` takes exactly **390 values, 570…959**
(`TAPE/OBSERVATION/v0/full/summary.json`; anchor `representation_matrix_v2.json`); ≈2.06–2.37 M rows
per day file. Keys `(day, t, ticker)`. Field semantics are frozen in
`TAPE/OBSERVATION/v0/schema.json → race_minute_full.fields` (every field carries
`observable_asof_rule` / `prospective_allowed` / `retrospective_only`).

**Live / prospective (`retrospective_only: false`) — usable as state:**

| field | meaning |
|---|---|
| `t` | decision minute; `px` = close of the last completed bar with `et <= t−1` (null if none) |
| `px`, `px_et` | that close and its `et`; `age_min = t − px_et` (staleness in minutes) |
| `known_by_t` | a completed filled bar with `et <= t−1` exists |
| `fresh_2m` | `age_min <= 2` |
| `prev_close` | close of the last RTH bar of the prior stored session |
| `gain` | `px / prev_close − 1` |
| `rank_known` | competition rank over the **known** population (null when not eligible) |
| `rank_fresh_2m` | competition rank over the **fresh_2m** population |
| `rank_unfiltered` | rank over every name with a known price and positive denominator, **all quality filters ignored — reference only** |
| `rank_eligible` | the mask: `known_by_t` ∧ positive px & prev_close ∧ gain outside the suspect split/bad-print band ∧ **not** a confirmed >10% prev-close discrepancy ∧ a *qualified* prev-close reference (not stale, not floor-qualified). Whole-day envelope never filters a rank |
| `order_slot` | deterministic ordinal from `(gain desc, ticker asc)` inside the known population |
| `n_known`, `n_fresh_2m`, `n_eligible`, `n_unfiltered` | published population sizes at `(day,t)`; **`n_eligible` is the rank-eligible count, NOT the fresh count** |
| `population_def` | which rank families this row belongs to |
| `pit_listed`, `roster_state` | roster membership in the latest PIT vintage ≤ day; `observed_in_raw` / `pit_unobserved` |
| `quality_flags` | `nonpositive_px` / `extreme_gain` |
| `flag_prevclose_discrepancy` | >10% mismatch vs the provider's prior close — **known before the open, so a causal rank filter** |
| `flag_nonpositive_px`, `flag_extreme_gain` | causal |
| `prev_close_day`, `prev_close_et`, `prev_close_age_sessions`, `prev_close_gap_days`, `prev_close_source` (`raw_prior_session` / `seed_2021-01-29` / `block_gap_last_stored` / `none`), `prev_close_acquired`, `prev_close_lane_kind`, `prev_close_lane_sha256`, `acquisition_used`, `acquisition_manifest_sha256`, `alias_map_sha256`, `source`, `source_sha256`, `feed_era`, `qualified_floor_source`, `block`, `month` | provenance / audit coordinates |
| `session_end` | calendar endpoint for the day |

**Retrospective-only (`prospective_allowed: false`) — forbidden as state, filter or denominator:**
`day_high_raw`, `sip_high_day`, `day_high_vs_sip_high_ratio`, `day_envelope_warning`,
`prevclose_warning`, `prevclose_vs_sip_clast_ratio`, `prev_close_stale` (`gap_days > 4`),
`prev_close_sealed_prior`, `prev_close_floor_qualified`, `in_universe_file`.

**Caveats that bind a new design.**
1. **Staleness is a first-class nuisance**: `age_min`/`px_et` are the only freshness measure, and
   `fresh_2m` is a hard 2-minute cut. Two names in one basket can carry different ages at the same `t`.
2. **Quality filter is part of the rank, not an extra step**: `rank_known` is null outside
   `rank_eligible`, and `rank_unfiltered` is explicitly reference-only (it still carries the broken
   reference on stale/floor-qualified days — e.g. 2025-02-03 references 2023-12-29).
3. **The px series is closes, not bars**: `race.minute_full` has **no high/low/open/volume/trade_count**.
   MFE, MAE, new-high counts, volume ratios and gap counts are *not* derivable from it; they need
   `base/bars` (selected + rank-1 leaders only) or the SIP bar tape.
4. `gain` is vs the **previous session close**, not vs the open and not vs the fill — it is the ranking
   coordinate, not a continuation coordinate.

---

## 4. Prior state→continuation work — exact AUCs and the "no dollar conversion" verdict

Closure doc: `researches/ATLAS-PHASE2-CLOSURE.md` (2026-09-28). Numbers:
`researches/ATLAS-PHASE1-SYNTHESIS.md` §2 (audited three-label table). Bullets:
`researches/STATE.md` "UPDATE 2026-09-27". Artifacts: `ATLAS/fall.json`,
`ATLAS/window_profile{,_index}.json`, `ATLAS/matched_pairs.json`, `ATLAS/E1/`, `ATLAS/E3/`,
`ATLAS/EV01/`.

**Fall v2 (state → forward labels, out-of-block within-clock AUC, identical rows):**

| target | A_pm (block1/block2) | B600 (block1/block2) |
|---|---|---|
| forward **dispersion** (range-30 / mabs-30) | **0.714 / 0.699** | **0.736 / 0.739** |
| **exhaustion** (this new high is the session's last) | 0.676 / 0.661 | 0.699 / 0.701 |
| **direction** (sign of executable forward return) | **+0.07 / +0.04 over the clock alone** | same |

Within forward-dispersion deciles the hazard keeps ≈ full discrimination (within-decile medians
0.6748/0.6926 and 0.6714/0.6987 against like-for-like baselines 0.6758/0.6613 and 0.6986/0.7011), so
the hazard is **not** a volatility restatement. Range-30 leads hazard by only +0.038/+0.044 (A_pm) and
+0.037/+0.009 (B600). Per-decile coefficient allocation is **UNIDENTIFIED and withdrawn as mediation**
(~393 parameters on as few as 592 rows).

**E1 — management by exhaustion score: CLOSED (no increment).** Every-bar score AUC **0.82–0.84**
within clock; releases profitably vs holding in both folds, but **does not beat the trivial
`giveback:10` ruler on dollars in either fold** (primary and all three clock-quantile rulers, sleeve and
dedup views); the event-hazard ablation family is killed in fold B by the pre-registered giant-tail
clause. The apparent "top predicted-dispersion tercile" niche was a **look-ahead artifact** — with a
decision-time statistic the niche is negative in **16/16 arm × fold cells**. Nothing promoted, no
rescue slicing.

**E3 — sub-minute information: CLOSED (null).** 20,937 windows / 1,066 days / five case sets; no
feature family positive out-of-block in both blocks for CS-1..CS-4; CS-5 survives only the weak kill
rule and every delta ≤ 0.0168 (CS-5 ≤ 0.0055), three orders inside the pre-registered **0.054** null
band. L-EV (latency cost) is **blocked by precondition — there is no signal whose latency could
matter** (`E3/latency.json` frozen).

**EV01 — retrospective anatomy, not causal alpha.** `researches/STATE.md`: it reads 30/60/120 objects
**later than** the +1/+5/+30 decisions plus the whole future-N universe, so its PASS is *statistical
separation, not actionability*. `ATLAS/EV01/report.md`: unit = member (6,160; resolved n=5,887 pooled;
censored 4.43% / blocks 3.91% / 5.56%). Outcome `Delta_member = k·(gross_rule − gross_hold)` vs
`hold_flat`, `bps_total=100`, `k=0.99005`, horizons `N ∈ {1,5,30}`.
- (a) balanced-density: **PASS** at N=1/5/30 — S1 −0.2336 / −0.2483 / −0.2248, S2 −8.91% / −9.74% /
  −8.98% (i.e. **high density ⇒ worse** continuation), Bonferroni CIs exclude 0 everywhere.
- (b) rare-flag: **DISMISS** (K1,K2,K4) at N=1/5, (K4) at N=30.
- (c) learned-NN: **DISMISS** (K3) at N=1, PASS at N=5, **DISMISS** (K4) at N=30.
- Stated limitation, verbatim: *"Separation only: no entry/exit/sizing rule is declared."*

**The governing conclusion (closure §"What actually stands", items 2–3).**
2. The strongest management policy tested inside ATLAS is the trivial peak-relative ruler
   (`giveback:10`) — positive vs holding, and **nothing learned beats it**.
3. **"Predictability without monetization": state predicts forward dispersion (AUC 0.67–0.74) and
   exhaustion (0.82–0.84) out-of-block, yet neither converts into dollars beyond the trivial ruler.**

**Window-value prior (directly on-topic for V(t))** — `ATLAS/window_profile.json` /
`window_profile_index.json`, keys `reference_v_hold_flat_median` (−0.0504…−0.0369 by window),
`reference_v_forced_flat_median`, `reference_v_giveback_10_median`, `reference_executable_oracle_median`.
`ATLAS-PHASE1-SYNTHESIS.md` §2: *"The executable continuation value hovers ≈0 in mean for most of the
session (A_pm block1 crossing at 922, block2 643; B600 block2 940; B600 block1 never positive) while
the median dies far earlier — the average is carried by the right tail."* Also: after a matched
down-moment ~51.6% / 51.4% **never close back at the exit price** for the rest of the session
(`ATLAS/matched_pairs.json`), but recovery when it happens is fast (median 1 bar, median cost −0.05%)
while 25.7% / 44.0% make a **new low within 30 bars**.

---

## 5. Peer / cross-section features — what exists and what is rebuildable

**Existing (ATLAS panel v2, `factory/scripts/basket_atlas_panel.py:855-880`, schema lines 146-150,
namespace `state_cross`, both `retrospective_only: false`):**

| column | exact construction |
|---|---|
| `candidate_count_t` | size of the day's **candidate universe** with a price at `t` |
| `ret_percentile_candidates` | fraction of that universe at or below the member's own return-from-open-09:30 — **percentile within the day's candidate set (the A_pm snapshot universe, for *both* families)**, *not* a market-wide rank (`basket_atlas_panel.py:427-431` records this resolution explicitly) |
| `peer_ret_median` | nan-median of the **other members of the member's own family on the same day** — sleeve peers, not the market (`peer_matrices(members, bars, session_end)`, `basket_atlas_panel.py:1125`) |
| `peer_new_high_5` | count of **distinct own-family peers** with ≥1 new-high bar in `(t−5, t]` |

`HARVEST01/contract.json` states the caveat in its own words: *"Existing Atlas peer percentile is not
market-wide rank."* And CV01's contract excluded them from the first diagnostic while keeping them on
the state allowlist.

**Rebuildable for arbitrary market-wide baskets from `race.minute_full`** (same `(day, t, ticker)`
grid, no new acquisition — `researches/STATE.md` 2026-10-02: *"RTH decisions reuse the local
full-market race panel … no new acquisition"*):
- market-wide **cross-sectional rank of gain at every minute**: `rank_known`, `rank_fresh_2m`,
  `rank_unfiltered`, `order_slot`, and the population sizes `n_known` / `n_fresh_2m` / `n_eligible` /
  `n_unfiltered` — a true market-wide analogue of `ret_percentile_candidates`, available at all 390
  minutes of the session;
- **rank trajectory / persistence** for a held name (rank at `t` vs rank at `t−k`), and roster
  breadth (`pit_listed`, `roster_state`);
- **participation breadth** proxies: how many names are in the top decile / how many are `fresh_2m` at
  `t` — a market-wide replacement for `peer_new_high_5`'s *intent* (sector-wide move) computed from
  `gain` alone;
- a market-wide **"renewed activity"** proxy in the same spirit as `volume_accel`, e.g. share of
  eligible names with `fresh_2m` rising, or cross-sectional dispersion of `gain` at `t` (which is the
  `fall` dispersion channel re-expressed on the market-wide population).

**Not rebuildable from `race.minute_full`** (no high/low/open/volume columns): minute-to-minute
returns, intraday MFE/MAE, new-high counters, `volume_accel` / `volume_vs_own_median`,
`up_close_streak`, `bars_since_new_high`, tape-gap counts. Those require `base/bars` — which covers
**only** `base/selected` tickers and rank-1 leaders — or a fresh SIP bar read for the market.

---

## 6. Genuinely unexplored (5 lines)

1. **V(t) conditioned on live market-wide participation state** — `n_known` / `n_fresh_2m` /
   `rank_known` / `rank_fresh_2m` breadth at the decision minute on the HARVEST01 market-wide roster:
   CV01 binned only *own-name* coordinates (`volume_accel`, `dist_from_running_high`,
   `ret_from_fill`, `ret_percentile_candidates`); no market-breadth coordinate has ever been a state.
2. **Owned-name rank trajectory as a renewal signal** — `rank_known(t) − rank_known(t−k)` on
   `race.minute_full` is available for every held name at every minute and has never been used as a
   continuation state (CV01's `ret_percentile_candidates` is a static per-minute percentile within the
   Atlas candidate set, not a trajectory).
3. **The member-balanced vs occupancy wedge itself as the object** — CV01 shows the *same* states
   positive per-member and negative per-minute-opportunity (`dist_from_running_high <= −20%`:
   +1.51% vs −0.95% @h60) but explicitly declined to interpret it; nobody has characterised the wedge
   as a function of observable state.
4. **Exit-side executable friction measured, not assumed** — `subminute_probe.parquet` has a dead
   `exit_px_diff` branch, so 100 bps has only ever been applied as a constant; the print-vs-bar-open
   cost of *selling* a top-gainer minute-by-minute has never been measured on this population.
5. **V(t) on the HARVEST01 roster at all** — every published CV01/EV01 number is on the ATLAS
   A_pm/B600 sleeve; the market-wide top-gainer basket (`base/selected`, 19 clocks, N=1..4) has
   only fixed-clock exit returns (`r{E}_100`/`r{E}_150`, `r{E}`/`cap{E}`), never an
   observable-state-conditioned continuation curve against a cash numeraire.
