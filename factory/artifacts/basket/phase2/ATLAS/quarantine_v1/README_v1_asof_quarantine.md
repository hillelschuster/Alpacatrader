# ATLAS — minute panel of the held members

`panel.parquet` is the causal minute tape of every A_pm top-3 and B600 top-3 filled member of
every development day, from the fill bar to the session's last bar. It exists so that the window
profile, the fall structure, matched pairs and the dollar ledger can all be computed from one
frozen file instead of re-deriving the tape per analysis.

- Producer: `factory/scripts/basket_atlas_panel.py`
- Contract: `SCHEMA.md` (frozen 2026-09-25) — column names and conventions are not redefined here
- Coverage / census / null rates / ambiguities: `coverage.json`
- Plan: `researches/PLAN-ATLAS-01.md`; doctrine: `researches/INTENT.md` §"THE WINDOW"

## Scope

| | |
|---|---|
| population | A_pm (`A_pm`, T=570) and B600 (`B`, T=600) canonical top-3 by rank, filled and not blocked |
| grain | one row per (member, completed bar) |
| window | fill bar → last member bar with `et <= session_end` |
| days | 1,066 development days (block1 2021-02…2023-12: 734, block2 2025-02…2026-05: 332) |
| build | **1,900,432 rows**, **6,160 members** (A_pm 3,188 + B600 2,972), 51 months |
| smoke | 20 days (10 per block), 119 members, 35,103 rows |

The panel deliberately tracks every member to the close — the fall must be observable — while
defining *no* outcome as "the close". `v_hold_flat` is the labelled EOD-anchored baseline kept for
comparability; the declared continuations (`v_giveback_*`) and the path statistics
(`final_high_flag`, `remaining_run`, `cost_of_waiting`, `bars_to_*`, `tail_class_*`) are what the
window and the fall are read from. Nothing here is chosen, fitted or gated: the panel is the tape.

## Conventions (see `SCHEMA.md`; the full list is in `coverage.json.conventions`)

- **Causality.** State uses only bars with `et <= t` plus the fill. No future bar, no session
  aggregate that includes bars after `t`.
- **Execution.** A decision at completed bar `t` executes at the **open of bar `t+1`**. On the
  member's last tracked bar `next_open`, `next_et` and every outcome column are `null`.
- **Friction.** The panel is friction-free. Analyses apply `friction_bps` per side
  (`bps_total/2`), matching `basket_sim.py`.
- **No fabrication.** A missing input yields `null` — never a carried, filled or zero value.
  The two exceptions are schema-declared event-null columns (`bars_to_next_high`,
  `dd_before_next_high`: null when the member never sets another high).
- **Give-back ties.** A close exactly `g%` below the running high counts as a hit; the trigger
  carries a `1e-9` relative tolerance so a binary-rounding artifact cannot decide the exit.
- **Determinism.** Rows sorted by `(sleeve_day, family, entry_rank, et)`; identical inputs give a
  byte-identical file.
- **Fill bar.** The first bar with `et >= entry_et` (the anatomy fill); `entry_px` is the anatomy
  fill price, which the engine asserts equals that bar's open.

### Producer additions (not part of the frozen column list)

`bar_open`, `bar_high`, `bar_low`, `bar_close`, `prev_close`, `open0930`, `member_last_et`.
These expose the raw tape at `t`, the two anatomy day constants behind
`ret_from_prevclose`/`ret_from_open0930`, and the member's own tape end (`session_end` is the
day-level close, `member_last_et` the last bar the member actually printed).

### Ticket-level constants are look-ahead

`session_peak_et`, `session_peak_ret_from_entry`, `session_peak_bars_from_entry` and
`session_close_ret_from_entry` describe the whole ticket and are repeated on every row. They exist
to group the fall analysis ("which giants died"); they are **not** state and must not be used as
causal features at a row with `et < session_peak_et`.

## Self-tests

`python factory/scripts/basket_atlas_panel.py selftest --panel <panel.parquet>` runs:

| check | meaning |
|---|---|
| (a) | member counts per family equal the C1 anchors (`A_pm_N3_R0_bps100` `n_entries`, `B600_N3_R0_bps100` `n_entries`) |
| (a2) | the member set is *identical* to the C1 engine's `tickets.parquet` on the panel's days (both directions, `entry_px` too) |
| (b) | rows per member equal the number of bars from the fill et to the session end |
| (c) | `bar_index` is `0..n-1` and `et` strictly increasing per member |
| (d) | five pseudo-random rows recompute `ret_from_fill`, `mfe_so_far`, `dist_from_running_high`, `final_high_flag`, `remaining_run`, `cost_of_waiting`, `v_hold_flat` exactly from the raw bars, by an independent naive implementation |
| (e) | no outcome column is populated when `next_open` is null (and the converse for every event-defined outcome column) |
| (f) | the smoke panel built twice is byte-identical (`sha256`) |

## What the tape looks like (measured, `coverage.json.tape_shape`)

| | A_pm | B600 |
|---|---|---|
| members | 3,188 | 2,972 |
| rows / member (median) | 384 | 344 |
| session peak ET (median) | 587 (q25 571, q75 679) | 631 (q25 604, q75 754) |
| peak return from entry (median) | +10.0% (mean +21.3%) | +9.3% (mean +19.6%) |
| close return from entry (median) | −5.3% (mean −2.5%) | −4.4% (mean −2.3%) |

The median peak lands in the first half hour and the median close is below entry — the morning
climax and the afternoon give-back the panel exists to measure. 273 members' tapes end before the
session close (halts); `member_last_et` records where.

## Reproduce

```bash
cd /home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1
python factory/scripts/basket_atlas_panel.py all
```

`all` = 20-day smoke panel → self-tests (a)-(e) + determinism (f) → the full development-day build
(month parts, resumable) → merge into `panel.parquet` → self-tests on the full panel → `coverage.json`.
Individual steps: `smoke`, `build --all`, `merge`, `coverage`, `selftest --panel <path>`.
Long builds write one parquet part per month under `parts/` and record each finished month in
`_progress.json`; re-running resumes instead of restarting (delete `parts/` and `_progress.json`
for a cold rebuild).

Artifacts of a run: `panel.parquet`, `coverage.json`, `_progress.json` (per-month hashes + census),
`selftest.json` (the last self-test report), `parts/month=YYYY-MM.parquet`, `smoke/` (smoke panel
and its report), `differential_check.json` (the differential verification summary below).

## Differential verification

Beyond the in-script checks, every non-trivial column was recomputed for a pseudo-random row sample
by a *separate*, deliberately naive implementation (plain Python loops over the raw bars parquet and
the anatomy JSON, no producer code path): 902 rows of the full panel → 49,604 column comparisons,
0 mismatches (plus 402 rows of the smoke panel → 22,110 comparisons, 0 mismatches). Summary and the
list of bugs it caught during development: `differential_check.json`.

## Known limitations

- **Top-3 only.** N2/N4/A_open/A_pm31 sleeves are not in this panel; the producer is parameterised by
  `FAMILIES` when they are needed.
- **A halt-free tape is not assumed.** Bars are sparse and gapped; `gap_count_so_far`,
  `bars_since_gap` and the bar-vs-clock conventions make the sparsity visible, but a halted minute
  simply does not exist as a row. 273 members' tapes stop before the close.
- **`v_giveback_*` uses bars.** The trigger is a completed-bar close, the exit is the next bar's
  open, so intra-bar paths inside a gap are not modelled.
- **Cross-section is the A_pm snapshot for both families** — the literal schema text (see
  `coverage.json.ambiguities`), not the B snapshot.
- **The universe has 10 names.** `candidate_count_t` / `ret_percentile_candidates` are therefore
  statistics of the day's candidate snapshot, not of the whole market.
- **Anatomy day constants can be missing** (21 members lack `open0930`, 37 lack `prev_close`):
  `ret_from_open0930`, `ret_percentile_candidates` and `ret_from_prevclose` are then `null`, never 0.
- **Dollar volume is `close * volume`** (bars carry no VWAP).
- **The panel ends at the close.** Everything about the fall is inside the session; overnight and
  next-day behaviour is out of scope.
- **Fill ETs are anatomy values**, not the nominal 571/601 of the schema text: A_pm fills at 570 on
  3,167 of 3,188 members, B600 at 600 on 2,581 of 2,972 (see `tape_shape.entry_et_histogram`).

## Interpretation discipline and corrections (parent, 2026-09-25)

Rules that apply to every ATLAS reading, learned the hard way in this cycle:

1. **Briefs carry labelled priors and measurement design — never conclusions.** The coarse prior in
   `DIAGNOSTICS_20260925/clock_cohort.json` splits members by their *eventual* session peak, i.e. a
   hindsight cohort. Its timing statements ("the broad continuation closes around 11:00-12:00") are a
   PRIOR, not a law, and must never be restated to an analysis as an instruction. Doing so once
   already produced an over-learned task brief.
2. **Dependence.** The 1.9M panel rows are repeated minutes from 6,160 tickets: the effective sample
   is ~1,066 days, not 1.9M rows. All uncertainty must be clustered at the day level (conservative),
   ticket-clustered as a secondary. Minute rows are never independent observations.
3. **Clock is not tenure.** At et=600, A_pm members have a median 30 bars of ownership while B600
   members have 0; at 30 bars of ownership A_pm sits at et~600 and B600 at et~630. Never pool the two
   families in a minute profile; report `et` and `bars_since_entry` as separate coordinates.
4. **Duplicate paths.** 255 (sleeve_day, ticker) pairs appear in both families — 510 of 6,160 members
   (8.3%) are the same market path held twice. Exclude or explicitly flag them in any pooled statistic.
5. **Print gaps are not halts.** The sub-minute probe measured >=5-minute no-print intervals
   (804 of 1,066 days have at least one); illiquidity produces them too and no halt status is joined.
   Call them print gaps / halt-like gaps.

### Ledger reading (corrected)

`giveback:10` (exit when 10% below the running high), friction on both legs, per-member attribution:

| | block1 | block2 |
|---|---|---|
| avoided − non-giant destroyed = **gross benefit** | +156.8 | +99.2 |
| − giant dollars destroyed (>=+100% forward from exit) | −78.7 | −66.3 |
| = net dollar ledger | **+78.2** | **+32.9** |
| giants' share of the gross benefit | **50%** | **67%** |
| non-giant exits that later traded >=+10% above the exit price | **41.6%** | **51.2%** |

The giants' destroyed dollars are already *inside* `dollars_destroyed`; they must not be subtracted a
second time from the net. The correct reading: the rule earns a genuine gross benefit on ordinary
failures, and the tail it cuts consumes half to two-thirds of it. The open question is whether causal
state can keep the gross benefit while sacrificing less future upside.

The "70/70 cut giants were re-admissible" figure is tautological (a member with >=+100% forward MFE
from the exit necessarily traded >=+10% above it) and must not be cited. Likewise, "the rule is not
detecting death" overclaims: what is measured is that it frequently exits *before* subsequent
recovery. Whether that recovery is timely, executable and worth re-entering is an open question, and
it is now a required analysis in the matched-pair work.
