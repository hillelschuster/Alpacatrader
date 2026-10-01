# HARVEST01 — causality audit: basket_harvest_sim / mgmt / select

Audit date: 2026-10-02 (03:00 +03:00). Worktree: `basket-phase2-f1`. No producer code was edited.

Audited revisions (sha256 at read time):
- `factory/scripts/basket_harvest_sim.py` — `328b9c827b76f121ed424b7375080d68600d64382127b35a67f31afc63eb0328`
- `factory/scripts/basket_harvest_mgmt.py` — `b3ffe028d01732d4c1e98be4b3a9c1b56b2a98d39bbc3015c73c3ada563057b0`
- `factory/scripts/basket_harvest_select.py` — `56053f5e1dddd7a44627a4fe4b31a1217eb301f1eb7244836a6acefd2418bd3d`

Conventions compared against `factory/BASKET-SIM-CONTRACT.md` (FROZEN 2026-09-22 + C1) and the
lane brief in `factory/artifacts/basket/phase2/HARVEST01/contract.json` (ET minute labels; decision on
completed bars only; entry = first bar open with `et>=clock`, `gap>=5min` blocked = cash; exit = first bar
open with `et>=max(exit_clock, fill_et+1)`; EOD `exit_clock=session_end` requires a bar printed at/after
`session_end` else UNKNOWN; 100 bps round trip = 0.005/side).

## Method and evidence base

On-disk artifacts under `data/harvest01` **predate the audited revisions** (e.g. `mgmt/2026-05-29.parquet`
mtime 00:35 vs code 00:42; the sim `EXITS` list has since gained 615/705/735 and mgmt's `DELTA_ENDS_OFFSET`
gained 690). All numeric claims below were therefore re-derived by **running the current code in scratch
data roots** (`/tmp/auditroot*`, symlinked/copied read-only inputs) and by independent recomputation:

- Full independent recomputation of every cell of the current sim output for 2026-05-29: **6,384 cells /
  12,768 comparisons (ret_100+ret_150 incl. fill/block/unknown counts), 0 mismatches**. Engine math and
  the entry/exit/indexing conventions reproduce exactly.
- Independent recomputation of mgmt's chained deltas (`gb10`, `gb10_half`, `reentry_gb10`, `replace_gb10`)
  over all members × endpoints: **5,472 comparisons, 0 mismatches**.
- sim↔mgmt hold-endpoint cross-check (incl. EOD): **1,959 comparisons on 3 days, 0 mismatches**.
- Synthetic early-close test on the real 2021-11-26 calendar label (`session_end=779`) with controlled bars.
- Real `select` run for the early-close day 2021-11-26 in a scratch root (panel `session_end=779`).
- Determinism: double runs of select/sim/mgmt produced identical frames.
- Panel as-of semantics verified in the producer: `basket_tape_atlas_observation.py:3618`
  (`np.searchsorted(a["et"], ts - 1, side="right") - 1` ⇒ px at row t is the close of the last bar with
  `et <= t-1`), row population pre-filtered to the PIT set (`:3557`).
- PM decision price verified in `basket_pm_snapshots.py:133` (`bisect_right(ets, C-1)-1`, bars only from the
  `[04:00, 09:30)` window, `et<=569`).

## Findings

### F1 — `replace_gb10` reports `fired=True` but null execution metadata (P2)

`factory/scripts/basket_harvest_mgmt.py:341-352` (contrast `:335`).

The rule→base-rule mapping is written twice. The first ternary at `:335` maps
`replace_gb10 → gb10` (used for the `fired` flag); the second ternary at `:341` omits `"replace_gb10"`, so
the metadata block at `:343-352` looks up `exec_of["replace_gb10"]` (never populated — `eval_rules` only
fills the nine base rules) and nulls `fire_et`, `exec_et`, `exec_px`, `mfe_after`, `monster_after`. The delta
block at `:373` does use `value_replace`, which reads `exec_of["gb10"]`, so the deltas are computed while the
row says the rule never executed.

Concrete scenario (current code, scratch run, 2026-05-29, variant `listed`, clock 510, rank 1, ticker
`PRFX`): the `gb10` row is `fire_et=586, exec_et=587, exec_px=4.695, mfe_after=+9.5%`; the `replace_gb10` row
of the same member is `fired=True` with `fire_et=exec_et=exec_px=mfe_after=monster_after=None`, even though
`cand_ticker=HUBC, cand_fill_et=590, cand_fill_px=0.4015` and `delta100_690=+0.194`. Day totals: 228/228
fired `replace_gb10` rows have null `exec_et`; 198 have a populated candidate. Independent recomputation of
all those deltas matched, proving the execution happened while the metadata says it did not.

Impact: any consumer of `exec_et`/`exec_px`/`mfe_after`/`monster_after` for the replacement family (mean
execution delay, "monster sold after fire", release timing) silently drops every replacement release;
`fired=True ∧ exec_px=None` is internally contradictory.

Minimal fix: include `"replace_gb10"` in the tuple on line 341.

### F2 — `prev_used_src` is always "panel"; `n_prev_panel` manifest stat is meaningless (P2)

`factory/scripts/basket_harvest_select.py:118-121` and `:233`.

At `:118` `with_columns(pl.col("prev_close_panel").fill_null(pl.col("prev_close_compact")))` overwrites the
`prev_close_panel` column in place. The source label at `:119-121` is then computed from the *filled*
column, so it is `"panel"` for every row that has an anchor; the compact fallback can never be labelled.
The manifest stat `n_prev_panel` (`:233`) therefore equals `n_universe` always.

Concrete scenario: 2021-02-01 — 25 of the 5,305 universe tickers have no row in that day's
`race.minute_full` and take the fallback (e.g. `CCC: prev_close_raw=13.47` from
`sip/universe/rth/2021-01-29.parquet`), yet all 5,305 rows report `prev_used_src="panel"` and the manifest
records `n_prev_panel=5305` (`n_universe=5305`). The same holds for 2022-05-09 (6044/6044) and 2026-05-29
(5494/5494). The values used are coalesce-correct; only the recorded provenance is wrong.

Impact: the anchor-provenance field the contract asks for (and the fallback-usage statistic) is wrong in
every day row/manifest; an audit can never identify which denominators came from the compact.

Minimal fix: compute the label before the fill, e.g.
`pl.when(pl.col("prev_close_panel").is_not_null()).then("panel").otherwise("compact")` applied to the
pre-fill column (or `when(prev_close_compact.is_not_null() & prev_close_panel.is_null()).then("compact")`),
and keep `prev_close_raw` as the coalesced value.

### F3 — Lower-triangle cells (`exit <= clock`) are emitted and consumed as valid (P2)

`factory/scripts/basket_harvest_sim.py:159-193` (clamp at `:83-92`); consumed unfiltered by
`factory/scripts/basket_harvest_report.py:53,104-105`; `HARVEST01/contract.json:14` defines
`valid_cells` as `exit_et > entry_et and exit_et <= calendar session_end`.

For exit clocks at or before the entry clock, `target = max(E, fill_et+1)` degenerates to the first bar
after the fill, and the engine emits the cell as if it were valid. The clamped exit is causal and documented,
but nothing marks the cell invalid.

Concrete scenario: 2021-02-01, variant `listed`, clock=630, N=3, exit=555 — stored `ret_100=-0.01366408`,
independently reproduced as a one-bar hold (sell at the first bar after the fill); the "exit clock" precedes
the entry clock. In the current-code run for 2026-05-29, 1,812 of 6,384 cells (28.4%) have `exit<=clock`.
`basket_harvest_report.py` groups all cells (`:53`) and the "Best cells by mean net-100" table (`:104-105`)
ranks the top-25 over them, so degenerate one-bar holds can drive the exploratory cell ranking; the pivot
money map is also filled in the whole lower triangle.

Minimal fix: skip `E <= clock` in the cell loop (contract's `valid_cells`), or set `ret_*`/counts plus a
`cell_valid=False` marker and filter it in the report.

### F4 — Rule parameters are never frozen or recorded (P2)

`factory/scripts/basket_harvest_mgmt.py:10` (docstring), manifest write `:388`, `--config` parsing
`:406-413`; `HARVEST01/contract.json` management `freeze_numerical_specs_before_policy_pnl=true`.

The docstring promises the defaults are "frozen per run in `mgmt_config.json` before any policy PnL is
viewed". No writer exists — a repo-wide and data-root search finds no `mgmt_config.json`, and the per-day
manifest contains only `{day, status, rows, elapsed_s}`. A run with `--config custom.json` records nothing
about the config; a later edit of `DEFAULTS` is likewise invisible.

Impact: policy PnL is not tied to a frozen spec; days processed under different configurations merge
silently in `harvest01/mgmt/*.parquet`, and resume (`status=="ok"`) cannot detect a config change, so a
mixed-config dataset is indistinguishable from a frozen one.

Minimal fix: write the resolved `cfg` plus a sha256 to `harvest01/mgmt/mgmt_config.json` (or every day
manifest) before processing, and refuse resume/force-mix when the hash differs.

### F5 — `build_universe` crashes if the day has no panel file (P3, latent)

`factory/scripts/basket_harvest_select.py:114-121`.

When `panel_universe` returns `None` (no `race.minute_full` file) the fallback frame has only `ticker`
(`:114-115`), but `:118`/`:119-121` reference `prev_close_panel`, which does not exist. `process_day`
explicitly anticipates the missing-panel case (`:277` sets `stats["n_panel_rows"]=None`), so PM-only
days are meant to work; instead the day aborts with `ColumnNotFoundError`.

Reproduction: `build_universe("1999-01-04", "1999-01-01", splits, <empty-root>)` →
`ColumnNotFoundError: unable to find column "prev_close_panel"; valid columns: ["ticker"]`.

Impact today: none on the frozen corpus (0 of 1,066 dev days lack a panel), but a PM-only or an
acquisition-gap day crashes the whole select stage for that day rather than degrading.

Minimal fix: build the fallback frame with the full expected schema (or guard the fill/label expressions
behind `"prev_close_panel" in u.columns`).

### F6 — Cross-boundary: `replace_gb10` is silently dropped from the management readout (P3)

`factory/scripts/basket_harvest_mgmt_report.py:26-30` (consumer dispatcher).

`basket_harvest_mgmt.py:57-59` emits 13 rules including `replace_gb10`; the report's `RULES` list at
`:27-30` contains 12 and omits `replace_gb10`, so `mgmt_summary.parquet`/`mgmt.md` never report the
replacement family even though its deltas exist and are non-trivial (e.g. `PRFX` 2026-05-29 listed/510:
`delta100_690=+0.194`). `ENDS = [600, 630, 660, 720, 959, 779]` at `:26` is now stale (no 690) and unused —
the report derives endpoints from the delta columns.

Minimal fix: add `"replace_gb10"` to `RULES`; drop or refresh `ENDS`.

## Suspected issues that checked out fine (explicit)

1. **Selection leakage / `t==C` vs `t=C-1`**: `select_rth` reads panel row `t == C`
   (`basket_harvest_select.py:202`), and the panel's px at row t is the close of the last bar with
   `et <= t-1` (`basket_tape_atlas_observation.py:3618`), guarded by `known_by_t`. This is the correct
   causal read (decision info completes at C). Evidence: real run 2021-11-26, clock 720 → `decision_et=719`.
   **Not a leak.** (The module docstring at `:13` says "Decision row t = clock-1", contradicting
   `select_rth`'s docstring and the code — see notes; the code is the correct side.)
2. **PM lane**: `px_{C}` = close of the last completed bar with `et <= C-1` and `fo_{C}` = first bar
   `et >= C` (`basket_pm_snapshots.py:133-137`); the snapshot window/filter cannot contain RTH bars
   (`et <= 569`), so a PM decision cannot see the open print it trades.
3. **Entry semantics**: first bar with `et >= clock`; `gap = fill_et-clock`; `gap >= 5` or no bar → cash
   slot `1/N`, never backfilled/substituted. Verified by the full independent cell recomputation
   (12,768 comparisons, 0 mismatches).
4. **Exit semantics**: first bar with `et >= max(E, fill_et+1)`, searched from `fill_idx+1` so the fill bar
   can never price its own exit; for `E == session_end` a bar at/after `session_end` is required, otherwise
   UNKNOWN (`None`), never the last close and never cash. Synthetic early-close test on the real 2021-11-26
   calendar label (se=779): the EOD exit prices at the 779 bar open; a tape ending at 778 yields
   `n_unknown=1, ret_100=None` (N=2 cell), not 0.0. Full recomputation includes EOD and all 24/28 exit
   clocks.
5. **mgmt exit convention matches the sim exactly** (same `bisect_left(..., max(E, ets[fi]+1), fi+1)` and
   same EOD `>= se` rule): 1,959 cross-checks on 2021-02-01/2022-05-09/2026-05-29, 0 mismatches.
6. **mgmt rule timing**: every rule fires on a completed bar `i` and executes at `o[i+1]`
   (`exec_of`, `basket_harvest_mgmt.py:235-240`); re-entry signals on close `j` and buys at `j+1`; the
   replacement candidate is the rank-1 leader at the first 5-minute clock `L >= sell exec` and is bought at
   the first candidate bar with `et >= L` (info ≤ L-1). No same-bar execution, no post-decision prices.
   Verified by independent recomputation of all chained deltas (5,472 comparisons, 0 mismatches).
7. **Split normalization is correct, neither missing nor double-applied**: MBRX, 2021-02-01 (reverse split
   6→1, ex_date that day): the panel/compact prior close `0.8268` on 2021-01-29 is genuinely pre-split
   (that day's compact `o570=0.91, c_last=0.8285`), and 2021-02-01 opened at 4.63 (~6x). `factor = old/new = 6`
   makes the comparison basis 4.96 and the day's adjusted gain ≈ -7%, correctly keeping the raw +495% ratio
   out of the ranking. Nothing observed gets its prior close multiplied twice.
8. **Determinism**: ranking tie-break is `gain desc, ticker asc` (`_rows_for`), loops over
   variant/clock/rank/N/exit are sorted, dict/column orders are insertion-stable, `normalize` fixes the
   column order, and per-day outputs are independent of worker count (`imap_unordered` affects print order
   only). Double runs of select/sim/mgmt in the scratch root produced byte-identical frames.
9. **Wealth chain**: blocked slot = cash `1/N`; unknown member → whole cell `None` (excluded from means,
   counted); friction `px*(1-0.005)` / `px*(1+0.005)`; half = 50% at exec + 50% held; re-entry and
   replacement compound the sale proceeds through a second buy with both-side friction. All verified
   numerically against independent implementations.
10. **No leverage / no cross-cell compounding / no rebalancing inside the sim**; duplicate-ticker and
    rank-gap cases are handled deterministically (`evals` keyed by rank; missing ranks counted as blocked).

## Deviations and notes (not causality defects)

- **gb10 peak includes the current bar's high** (`basket_harvest_mgmt.py:93-100`, deliberately documented
  in the code comment). Using a completed bar's own high+close is causal because execution is at the next
  bar's open, but it differs from the frozen engine ordering in `BASKET-SIM-CONTRACT.md` §7.4 ("a bar's own
  high can never trigger a peak-relative rule on the same bar"). If this rule is meant to reproduce the
  contract's `peak_giveback_10pct`, it is stricter/earlier than the engine.
- **`listed` variant is currently a no-op vs `primary` for RTH**: the panel's `pit_listed` is a constant
  `True` above a PIT-filtered row set (`basket_tape_atlas_observation.py:3557,3778`); for the PM lane it
  de-facto means "appears in today's RTH board" (compact-only names get `pit_listed=False`). No top-4
  differed between `primary` and `listed` on the four days sampled (2021-02-01, 2021-11-26, 2022-05-09,
  2026-05-29). Fine to keep, but the variant adds no information as recorded.
- **Doc/code drift**: `basket_harvest_select.py:13` says "Decision row t = clock-1" while the code uses
  `t == C` (correct, see item 1). Fix the docstring so a future maintainer does not "correct" the code by
  one bar. `basket_harvest_mgmt.py:20` advertises an `mfe_member` column that is never emitted.
- **Lane contract drift**: `HARVEST01/contract.json` management says `no_reentry_or_replacement: true` and
  lists `entry_damage_15pct` as a benchmark; `basket_harvest_mgmt.py` implements re-entry/replacement and no
  15% damage variant. Given the current brief explicitly covers partial/re-entry/replacement, this looks
  like a stale contract rather than a code bug — but the contract should be amended or the rules dropped so
  the frozen DOF register matches what is computed.
- **Stale on-disk artifacts**: `harvest01/sim/*` and `harvest01/mgmt/*` written before the audited
  revisions (sim `EXITS` gained 615/705/735; mgmt `DELTA_ENDS_OFFSET` gained 690) must be regenerated
  before any aggregate is trusted; they were not used for any numeric claim above.
