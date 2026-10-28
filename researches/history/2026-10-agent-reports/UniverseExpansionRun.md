# UniverseExpansionRun — PRE-REG-DEPTH-UNIVERSE-01, executed (corrected 2026-10-27)

Subject: widen the rank cutoff of the frozen PRE-REG-DEPTH-01 depth rule
(`factory/scripts/alpha_adaptive_depth_sizing.py`, arm `thr300bps|p50`) from K=10 to
K∈{20,30} on the same open-anchored ranking (`score = px_T/o570 - 1`) and the same 0.05
admission gate. Producer: `factory/scripts/alpha_depth_universe.py`. Frozen head, threshold,
execution and book are imported, never re-declared.

## 0. Correction (this section supersedes the first publication)

An independent verification (2026-10-27) found a **P1 defect in this study's own quote read
path**, and this report was rebuilt from the raw day objects after fixing it.

* **Defect.** `build_day_cells` passed the acquisition *staging root* to `quote_day_frame` as
  a supplement root. `alpha_quote_universe_acquire.merge_day` writes
  `<root>/quotes/<day>.parquet`, while `supplement_day_path` resolves a root as
  `<root>/<day>.parquet`. The file the run had just written was therefore never read, and
  every intent of a newly acquired pair was recorded `quote_not_acquired` and dropped before
  funding. The readiness check *did* look at the correct deep path, so the day was treated as
  acquired while its quotes were invisible.
* **What it changed.** Only the extra-name funnel, and only on the two sessions where an
  in-run acquisition carried a state at/above the bar (SDA, SNES, INAB, HUDI on validation;
  CNCK late). Validation K=20 additive read `+1.35375 $/session`, 2 funded, 1 known fill,
  mean −$2.445. Corrected: **+1.88291 $/session, 4 funded, 3 known fills, mean +$43.28
  ($129.85 total)**, which turns criterion (a) from FAIL into **PASS**.
* **Fix.** A `quote_roots()` helper returns
  `frozen.SUPPLEMENT_ROOTS + (quotes_root / "quotes",)` and is the only way the merge order is
  built; `ensure_quotes` also counts symbols already present in the staged merged day file so
  a rebuild never re-fetches. `tests/test_alpha_depth_universe.py` pins the invariant: the
  study's root resolves to the file `merge_day` writes, the old bare root provably does not,
  and a freshly acquired quote reaches `intent_facts` as `quoted` with the right ask/bid.
* **Rebuild.** All 582 sessions were rebuilt from the sha-verified `raw/<day>.tar.gz` objects
  on the box (`--no-offload`, local staging deleted after each day, footprint < 0.2 GB). The
  K=10 control is **still exact**: validation `+1.3635270500000087`, late
  `+5.949121298192784`, 53/122 known fills, 107/244 signals — the fix cannot move a top-10
  row, and the rebuilt control proves it.
* **Claims withdrawn.** The earlier "the widening is inert" and "late additive is
  bit-identical to the control" statements were artifacts of the defect and are removed. What
  survives is narrower and is stated in §4b.

## Decision

**K stays 10. Widening is CLOSED for this model.** With the corrected numbers the primary
arm clears the PnL bar but fails the two registration criteria that protect against exactly
this kind of result — too few extra-name fills, and pooled/additive disagreement:

| criterion | K=20 (primary, additive/A) | K=30 (primary, additive/A) |
|---|---|---|
| (a) $/session @25 bps ≥ control +1.363527 | **PASS** +1.88291 (+0.51938) | **PASS** +1.88291 (+0.51938) |
| (b) $/session @50 bps > 0 | PASS +1.5013 | PASS +1.5013 |
| (c) extra-name known fills ≥ 10 and mean net @25 > 0 | **FAIL** 3 fills (mean +43.28) | **FAIL** 3 fills (mean +43.28) |
| (d) POOLED and ADDITIVE agree in sign of the increment | **FAIL** +0.519 vs −2.879 | **FAIL** +0.519 vs −2.615 |
| adopted? | **no** | **no** |

The registered branch applies: **K=10 stands as the frozen rule, no PRE-REG-DEPTH-02 is
opened**, and the pristine 2024 / 2025-01 / 2026-06..08 holdout stays sealed (the code guard
refuses every sealed prefix; no sealed date was read).

Reading it honestly: the extra supply is **rare but positively selected** — 4 candidate
states in 250 sessions, all four funded, three known fills averaging **+$43.28** (dominated by
HUDI 2023-04-28, +$127.89), i.e. when the head does fire on a rank-11 name it fires well. It
is simply too rare to be a lever: the registration demands ≥10 known fills before a widened
cutoff may be adopted, and 3 is a single lucky tail rather than a measurable edge. Criterion
(d) then fails as well, because recomputing the peer context over the widened watchlist moves
the *existing* signals and costs 1.9-2.9 $/session — the interaction the registration was
designed to detect. Two independent reasons to keep K=10.

## 1. Harness — the control reproduced the frozen rule exactly

| check | scope | result |
|---|---|---|
| stage-0 rank lists vs `data/sip/candidates/*.json` (6 clocks × B populations) | 3,492/3,492 clock lists, 582 days | 0 mismatches |
| stage-0 admission set vs the panel's admission rule | 582 days | 0 mismatches |
| rebuilt K=10 panel (tape + `day_context` + frozen head) vs the stored frozen states | 582/582 days, 396,629 states | bit-identical (max abs pred diff 0.0, 0 mismatches) |
| replayed control, validation | 250 sessions | **+1.3635270500000087 $/session @25**, 107/62/53, 8 UNKNOWN, 54 traded days |
| replayed control, late | 332 sessions | **+5.949121298192784 $/session @25**, 244/142/122, 20 UNKNOWN, 113 traded days |

The control figure was re-derived twice: once from the frozen producer's own stored replay
artifacts, once from this study's rebuilt states after the quote-path fix. Both agree to the
last bit.

The 39 days where the admitted set differs from the panel *ticker* set are sparse-tape names
(admitted by the rule, present in the tape, but failing the exact `et[i]==t-1` + 15-bar-history
lattice at every clock → zero states in the frozen panel too).

## 2. Stage 0 — free re-rank of the stored full-market RTH table (no network)

Source: `data/sip/universe/rth/<day>.parquet` (5,405-5,975 symbols/day, mean 5,578), ranks
1..30 at the six decision snapshots, admission = `score>=0.05 & px_T>=$1 & px_T_et>=T-2`.

| K | extra (day,ticker) pairs val / late / all | pairs/day val / late | admitted names/session | 5%+fresh pass rate on widened ranks |
|---|---|---|---|---|
| 20 | 5,169 / 7,371 / **12,540** | 20.68 / 22.20 | 25.0 → 45.7 | 62.1% / 74.2% |
| 30 | 9,416 / 14,195 / **23,611** | 37.66 / 42.76 | 25.0 → 62.7 | 62.2% / 76.2% |

The K=30 extras decompose into 5,169 (ranks 11-20) + 4,247 (ranks 21-30) on validation and
7,371 + 6,824 on the late block. Two measured facts that shaped the run: the 0.05 gate is
**no longer inert** in the widened tail (only 62-76% of names that appear in top-K pass it,
versus 10/10 at K=10), and **19.6-30.1%** of already-admitted names would get an EARLIER
first admission under the wider cutoff (the additive arm keeps their rows bit-identical; the
pooled arm trades those extra states).

## 3. Acquisition — stages 1-2, off-host, resumable

Per day: provider SIP minute bars for the extra names absent from the net tape (one batched
request), and NBBO quotes acquired through `alpha_quote_universe_acquire`'s own
`fetch_symbol_day`/`merge_day` (imported, never copied; per-pair resume, full pagination,
429-aware GET, 0.35 s pacing). Each day's acquisition is pushed to the box as **one verified
object** `raw/<day>.tar.gz` (streamed `tar|gzip -1|rclone rcat`, sha256 checked against
`ssh storagebox sha256sum`), then the local staging is deleted; 16 pre-existing loose folders
were converted the same way.

**Quote volume is bounded by what the replay can consume.** The head's 26 features are
bar/peer derived, so every state is scored *before* any quote exists; the frozen replay only
reads an intent's quote for states at/above its threshold. Only those (day,ticker) pairs get a
stream. Under the corrected read path this is verifiable end-to-end: the four validation
candidates now actually fund, and the control still reproduces exactly.

## 4. Cells (all ten, validation = decision block)

### Validation 2023 (250 sessions, DECISION block)

| cell | sessions | signals | funded | known | UNK | $/s@0 | $/s@25 | $/s@50 | $/s@100 | $/calday@25 | median@25 | top5 | months+ | extra fills / mean@25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| k10_control | 250 | 107 | 62 | 53 | 12.9% | +1.7291 | +1.3635 | +0.9980 | +0.2669 | +0.9443 | -5.49 | 465% | 4/12 | n/a |
| k10_pooled_rebuild | 250 | 107 | 62 | 53 | 12.9% | +1.7291 | +1.3635 | +0.9980 | +0.2669 | +0.9443 | -5.49 | 465% | 4/12 | n/a |
| k20_add_A | 250 | 111 | 66 | 56 | 13.6% | +2.2645 | +1.8829 | +1.5013 | +0.7381 | +1.3040 | -4.39 | 337% | 4/12 | 3 / +43.28 |
| k20_add_B | 250 | 111 | 66 | 56 | 13.6% | +2.2645 | +1.8829 | +1.5013 | +0.7381 | +1.3040 | -4.39 | 337% | 4/12 | 3 / +43.28 |
| k20_pool_A | 250 | 93 | 54 | 47 | 13.0% | -1.2286 | -1.5155 | -1.8023 | -2.3760 | -1.0495 | -6.26 | -212% | 3/12 | 0 / n/a |
| k20_pool_B | 250 | 93 | 54 | 47 | 13.0% | -1.2286 | -1.5155 | -1.8023 | -2.3760 | -1.0495 | -6.26 | -212% | 3/12 | 0 / n/a |
| k30_add_A | 250 | 111 | 66 | 56 | 13.6% | +2.2645 | +1.8829 | +1.5013 | +0.7381 | +1.3040 | -4.39 | 337% | 4/12 | 3 / +43.28 |
| k30_add_B | 250 | 111 | 66 | 56 | 13.6% | +2.2645 | +1.8829 | +1.5013 | +0.7381 | +1.3040 | -4.39 | 337% | 4/12 | 3 / +43.28 |
| k30_pool_A | 250 | 106 | 62 | 54 | 11.3% | -0.8926 | -1.2510 | -1.6094 | -2.3263 | -0.8663 | -3.97 | -385% | 5/12 | 1 / -2.44 |
| k30_pool_B | 250 | 106 | 62 | 54 | 11.3% | -0.8926 | -1.2510 | -1.6094 | -2.3263 | -0.8663 | -3.97 | -385% | 5/12 | 1 / -2.44 |

### Late 2025-02..2026-05 (332 sessions, descriptive)

| cell | sessions | signals | funded | known | UNK | $/s@0 | $/s@25 | $/s@50 | $/s@100 | $/calday@25 | median@25 | top5 | months+ | extra fills / mean@25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| k10_control | 332 | 244 | 142 | 122 | 14.1% | +6.6019 | +5.9491 | +5.2964 | +3.9909 | +4.1063 | -9.21 | 125% | 10/16 | n/a |
| k10_pooled_rebuild | 332 | 244 | 142 | 122 | 14.1% | +6.6019 | +5.9491 | +5.2964 | +3.9909 | +4.1063 | -9.21 | 125% | 10/16 | n/a |
| k20_add_A | 332 | 244 | 142 | 122 | 14.1% | +6.6019 | +5.9491 | +5.2964 | +3.9909 | +4.1063 | -9.21 | 125% | 10/16 | 0 / n/a |
| k20_add_B | 332 | 244 | 142 | 122 | 14.1% | +6.6019 | +5.9491 | +5.2964 | +3.9909 | +4.1063 | -9.21 | 125% | 10/16 | 0 / n/a |
| k20_pool_A | 332 | 282 | 156 | 135 | 13.5% | -1.1608 | -1.8604 | -2.5600 | -3.9592 | -1.2841 | -12.92 | -278% | 7/16 | 0 / n/a |
| k20_pool_B | 332 | 282 | 156 | 135 | 13.5% | -1.1608 | -1.8604 | -2.5600 | -3.9592 | -1.2841 | -12.92 | -278% | 7/16 | 0 / n/a |
| k30_add_A | 332 | 245 | 143 | 123 | 14.0% | +6.7434 | +6.0877 | +5.4319 | +4.1204 | +4.2019 | -9.14 | 122% | 10/16 | 1 / +46.00 |
| k30_add_B | 332 | 245 | 143 | 123 | 14.0% | +6.7434 | +6.0877 | +5.4319 | +4.1204 | +4.2019 | -9.14 | 122% | 10/16 | 1 / +46.00 |
| k30_pool_A | 332 | 309 | 160 | 136 | 14.4% | +0.7903 | +0.0777 | -0.6349 | -2.0602 | +0.0536 | -18.01 | 8732% | 5/16 | 1 / +46.00 |
| k30_pool_B | 332 | 309 | 160 | 136 | 14.4% | +0.7903 | +0.0777 | -0.6349 | -2.0602 | +0.0536 | -18.01 | 8732% | 5/16 | 1 / +46.00 |

## 4b. What the late block shows (descriptive, never selected)

The late block repeats the pattern rather than changing it. The additive arm at K=20 adds
nothing (244/142/122, identical to the control), and at K=30 it adds exactly one candidate —
CNCK 2025-12-01, funded and known at **+$46.00**, taking the block to 245/143/123 and
+6.0877 $/session. The pooled arm is again the loser: −1.86 $/session at K=20 and +0.078 at
K=30, with 282-309 signals instead of 244 — re-ranking the peer context perturbs the existing
signals far more than the extra names add. Supply is the binding constraint, not selection.

The mechanism behind the rarity is a property of the frozen head, not of the encoding: inside
the control's own K=10 panel, the rows that carry `rank_snapshot = 11` (72,239 of them) clear
the 0.030 bar only **6** times, versus 67 times for rank 1 (10,850 rows). The extras are 99.9%
rank-11 rows (53,263 of 53,299 qualified extra rows), so their expected candidate count is
tiny by construction — which is exactly what "the head cannot use the extra names" means here.

## 5. Registered rule, applied mechanically

```
for K in (20, 30):
    prim = cells[f"k{K}_add_A"]["validation"]   # PRIMARY = additive arm, encoding A
    pool = cells[f"k{K}_pool_A"]["validation"]  # diagnostic arm
    (a) prim.usd_per_session["25"] >= control    -> True  (1.88291 >= 1.36353)
    (b) prim.usd_per_session["50"] > 0           -> True  (1.5013)
    (c) prim.extra_names.known_fills >= 10
        and mean_net_usd_25 > 0                  -> False (3 fills, +43.28)
    (d) sign(prim increment) == sign(pool incr)  -> False (+0.519 vs -2.879)
adopted = [K for K in (20,30) if all(a..d)] = []   -> K stays 10, widening CLOSED
```

Multiplicity is disclosed in the same artifact: 3 cutoffs × 2 arms × 2 encodings, every cell
published above with its funnel beside its PnL. Encoding B is identical to A in the additive
arm (the encoding only re-caps `rank_snapshot`, and every extra candidate already sits at 11).

## 6. Files

| path | content |
|---|---|
| `factory/scripts/alpha_depth_universe.py` | producer: stage0 re-rank, bars, quote acquisition (corrected read path), widened states, replay, decision |
| `tests/test_alpha_depth_universe.py` | regression tests: quote-root alignment, the old wiring provably misses the file, a fresh quote reaches the intent, sealed-date guard |
| `~/alpha-data/open-search-v1/depth_universe/plan/<day>.json` | per-day ranks 1..30, admissions, extra pairs (582 days) |
| `.../plan_summary.json`, `.../stage0_verify.json` | exact pair counts, per-day table, rank/admission audit |
| `.../derived/<day>.daily.parquet`, `<day>.trades.parquet`, `<day>.cov.json` | the cell outputs per session (rebuilt after the fix) |
| `.../results.json` | every cell + the mechanically applied decision |
| `.../harness_control.json` | control replay vs the frozen reference (both blocks) |
| `.../STATUS.json` | state, decision, counts |
| `storagebox:.../depth_universe/raw/<day>.tar.gz` | one verified object per session (bars + quotes + manifest) |

Nothing in `data/sip/`, `~/alpha-data/open-search-v1/days`, `adaptive_depth_sizing` or
`quote_universe_acquire` was written or modified. No protected date was read.
