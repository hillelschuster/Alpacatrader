# ATLAS — minute panel of the held members (v2)

`panel.parquet` is the causal minute tape of every A_pm top-3 and B600 top-3 filled member of every
development day, from the fill bar to the session's last bar. It exists so that the window profile,
the fall structure, matched pairs and the dollar ledger all compute from one frozen file instead of
re-deriving the tape per analysis.

- Producer: `factory/scripts/basket_atlas_panel.py`
- Contract: `SCHEMA.md` — **frozen 2026-09-25, amended 2026-09-25 (v2)**; read the amendment log first
- Independent verifier: `factory/scripts/basket_atlas_verify.py` → `verify_report.json`
- Coverage / census / null rates / ambiguities / registries: `coverage.json`
- Plan: `researches/PLAN-ATLAS-01.md`; doctrine: `researches/INTENT.md` §"THE WINDOW"

> **v1 is invalid and quarantined.** The v1 panel mis-valued terminal outcomes for halt-ended tapes,
> executed a last-bar give-back trigger at the same close, and used a close instead of the engine's
> forced-flat price. Nothing of it is used; the small v1 sidecars and the defect list are in
> `quarantine_v1/INVALID.md`.

## Scope

| | |
|---|---|
| population | A_pm (`A_pm`, T=570) and B600 (`B`, T=600) canonical top-3 by rank, filled and not blocked |
| grain | one row per (member, completed bar) |
| window | fill bar → last member bar with `et <= session_end` |
| days | 1,066 development days (block1 2021-02…2023-12: 734, block2 2025-02…2026-05: 332) |
| build | **1,900,432 rows / 6,160 members** (A_pm 3,188 + B600 2,972, both equal to the C1 anchors), 88 columns |
| censored | 273 members / 33,845 rows (1.78%) have state but no terminal outcomes (`coverage.censor_census`) |
| condition-after-forced-flat | rows whose first give-back condition appears at `et >= session_end - 1`: valued at `v_forced_flat`, `fired = false`, never null (counts in `coverage.censor_census.condition_after_forced_flat_rows`) |
| smoke | 20 days (10 per block), 119 members — built and verified before every full build |

The panel tracks every member to the close — the fall must be observable — while defining *no*
outcome as "the close". `v_hold_flat` is a labelled close baseline kept for comparability;
`v_forced_flat` is the executable hold-to-flat continuation; `v_giveback_*` are the declared
continuations; the path statistics (`final_high_flag`, `remaining_run`, `cost_of_waiting`,
`bars_to_*`, `tail_class_*`) explain them. Nothing here is chosen, fitted or gated.

## Conventions (full list in `coverage.json.conventions`)

- **Causality.** State uses only bars with `et <= t` plus the fill. No future bar, no session
  aggregate that includes bars after `t`.
- **Execution.** A decision at completed bar `t` executes at the **open of bar `t+1`**. On the
  member's last tracked bar `next_open`, `next_et` and every outcome column are `null`.
- **Terminal censoring.** A member with no print at the session close cannot be valued there:
  `terminal_censored = true`, and every terminal-dependent outcome is `null` on all its rows —
  `v_hold_flat`, `v_forced_flat`, `v_giveback_*`, `giveback_fired_*`,
  `giveback_condition_after_forced_flat_*`, `final_high_flag`, `remaining_run`, `cost_of_waiting`,
  `bars_to_peak`, `peak_et_after_t`, `tail_class_*`, the four session constants and
  `future_forced_flat_px`. State columns are unaffected.
- **No same-bar execution; the forced flat has precedence by clock ET.** A give-back exit is the
  open of the bar *after* the trigger bar. The engine's forced-flat branch runs at
  `t == session_end - 1` **before** release evaluation and skips the ticket, so no release rule is
  evaluated on a bar with `et >= session_end - 1`: a first condition there is non-firing
  (`giveback_fired_g = false`, `v_giveback_g = v_forced_flat`, diagnostic
  `giveback_condition_after_forced_flat_g = true`). A first condition at `et <= session_end - 2` is
  executable; its exit is the open of the next printed bar (which may be the `session_end` bar when
  the tape gaps — still a genuine firing). Give-back values are `null` only for censored tapes and on
  the member's own last tracked row.
- **Terminal price.** The engine schedules `FORCED_FLAT` on the completed bar `session_end-1` and
  executes it at the **open of the `session_end` bar**; `future_forced_flat_px` is that price and
  `v_forced_flat = future_forced_flat_px / next_open - 1`. `v_hold_flat` is the *close* baseline and
  is **not** an execution price. With no give-back trigger the continuation runs to the forced flat.
- **Fill clock.** `entry_et`/`entry_px`/`entry_rank` are the anatomy fill and rank — A_pm commonly
  ET570, B600 commonly ET600 — asserted equal to the C1 engine's tickets; the 571/601 in the schema
  text is nominal (amendment v2.1).
- **No fabrication.** A missing input yields `null`. Documented null groups are: event-defined
  fields such as `bars_to_next_high` / `dd_before_next_high` when the event never occurs; every
  outcome on a member's own last tracked row, where `next_open` is unavailable; and every
  terminal-dependent outcome of a terminal-censored tape. On complete tapes, a give-back condition
  first seen at `et >= session_end - 1` is preempted by forced-flat logic and the continuation is
  `v_forced_flat`, not `null`.
- **Future-only columns** (`coverage.json.future_only_columns`): the `future_` family
  (`future_member_last_et`, `future_forced_flat_px`), the four session-wide constants, and the two
  censor flags. No causal feature may use any of them; `causal_feature_columns()` in the producer is
  the selector that enforces it, and check (i) proves a generic selector admits none of them.
- **Determinism.** Rows sorted by `(sleeve_day, family, entry_rank, et)`; identical inputs give a
  byte-identical file.

### Producer additions (not part of the frozen column list)

`bar_open`, `bar_high`, `bar_low`, `bar_close`, `prev_close`, `open0930`,
`path_complete_to_session_end`, `terminal_censored`, `future_member_last_et`,
`future_forced_flat_px`. Types and families: `column_registry.json` (the verifier validates the
physical parquet against it).

## Self-tests and verification

```bash
python factory/scripts/basket_atlas_panel.py selftest --panel factory/artifacts/basket/phase2/ATLAS/panel.parquet
python factory/scripts/basket_atlas_panel.py corruption --panel factory/artifacts/basket/phase2/ATLAS/panel.parquet
python factory/scripts/basket_atlas_verify.py --panel factory/artifacts/basket/phase2/ATLAS/panel.parquet \
        --report factory/artifacts/basket/phase2/ATLAS/verify_report.json
```

| check | meaning |
|---|---|
| (a) | member counts per family equal the C1 anchors (`n_entries` of the N3/R0 cells) |
| (a2) | **exact** equality with the C1 engine: member keys both directions, and `entry_px`, `entry_et`, `entry_rank` and the engine's forced-flat exit price (`exit_px`/`exit_et` of every in-session `FORCED_FLAT` ticket); a missing reference fails |
| (a3) | synthetic boundary cases (trigger at `session_end-2` executable, at `session_end-1` preempted, condition at `session_end` preempted, gap `session_end-2 → session_end` executable, no trigger) + reconciliation of **every** fill row, fired rows included, against the engine's realized exit and the tape-derived exit price |
| (b) | rows per member equal the number of bars from the fill et to the session end |
| (c) | `bar_index` is `0..n-1` and `et` strictly increasing per member |
| (d) | sampled rows (random + censored/last-bar/unexecutable/early-close/missing-reference/threshold-tie strata) recompute exactly from raw bars by an independent naive implementation |
| (e) | no outcome column is populated when `next_open` is null; on complete tapes every event-defined outcome is populated (exceptions listed) |
| (f) | censoring: flags never null, complementary, equal to `future_member_last_et < session_end`, and every terminal-dependent outcome is null on censored tapes |
| (g) | forced-flat precedence: any give-back condition first seen at `et >= session_end - 1` yields `fired = false` and `v_giveback_g == v_forced_flat` exactly (per-threshold counts reported) |
| (h) | every `future_*` column is registered future-only, and no future-only column is causal state |
| (i) | source-level registry audit: every column the producer's reports reference exists in the column registry; a generic causal selector admits no future-only/outcome column and still admits the state columns |
| verifier | independent recomputation (state + outcomes + censoring + forced flat) over deterministic boundary strata, plus physical parquet schema/type/sort/key-uniqueness checks and a prefix-invariance test (state must not change when bars after `t` are replaced by garbage) |
| corruption | deliberately corrupting a member key, `entry_px`, `entry_et`, `entry_rank` or `future_forced_flat_px` must each fail the C1 assertion (`corruption_check.json`) |
| verifier negative checks | the verifier proves its own checks can fail: permuted column order, missing column, reversed rows, duplicate key, corrupted value (all reported in `verify_report.json.negative_checks`) |
| (f) determinism | the smoke panel built twice is byte-identical (`sha256`) |

The verification commands and the sha256 of the producer and verifier are recorded in
`coverage.json.verification` and in `selftest.json.verification`.

## Reproduce

```bash
cd /home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1
python factory/scripts/basket_atlas_panel.py all
```

`all` = smoke panel + self-tests (a)-(i) + verifier + determinism → full development-day build
(month parts, resumable) → merge → full-panel self-tests + verifier → C1 corruption tests →
`coverage.json`. Individual steps: `smoke`, `build --all`, `merge`, `coverage`, `selftest`,
`corruption`. Long builds write one parquet part per month under `parts/` and record each finished
month in `_progress.json`; re-running resumes (delete `parts/` and `_progress.json` for a cold
rebuild).

Artifacts of a run: `panel.parquet`, `column_registry.json`, `coverage.json`, `selftest.json`,
`verify_report.json`, `corruption_check.json`, `_progress.json`, `parts/month=YYYY-MM.parquet`,
`smoke/` (smoke panel + its reports), `quarantine_v1/` (invalid v1 record).

## Known limitations

- **Top-3 only.** N2/N4/A_open/A_pm31 sleeves are not in this panel; the producer is parameterised by
  `FAMILIES` when they are needed.
- **Censored members carry state but no outcomes.** 273 members stop printing before the close
  (`coverage.censor_census` lists every one, the hour of their last print, and the nulled cell
  count); their rows are usable as state, never as an outcome.
- **Bars, not ticks.** The give-back trigger is a completed-bar close and the exit is the next bar's
  open, so intra-bar and intra-gap paths are not modelled.
- **Cross-section is the A_pm snapshot for both families** — the literal schema text (see
  `coverage.json.ambiguities`), not the B snapshot; the universe is 10 names.
- **Anatomy day constants can be missing** (21 members lack `open0930`, 37 lack `prev_close`):
  `ret_from_open0930`, `ret_percentile_candidates` and `ret_from_prevclose` are then `null`, never 0.
- **Dollar volume is `close * volume`** (bars carry no VWAP).
- **Ticket constants are look-ahead.** `session_peak_*` / `session_close_ret_from_entry`,
  `future_member_last_et` and `future_forced_flat_px` describe the whole ticket; they are cohort
  labels, not state.
- **The panel ends at the close.** Overnight and next-day behaviour is out of scope.

## Required consumer migration (from v1)

| v1 usage | v2 replacement |
|---|---|
| `member_last_et` | `future_member_last_et` (future-only; not a causal key) |
| `v_hold_flat` read as the executable hold-to-flat | `v_forced_flat` (executable); `v_hold_flat` is the close baseline only |
| `v_giveback_*` read on members with no session-end print | now `null`: use `terminal_censored` to separate cohorts |
| `v_giveback_*` read as "null when the rule could not execute" | no longer true for complete tickets: the forced flat takes precedence and the value is `v_forced_flat` |
| `giveback_fired_*` "true means an executed exit" | still true; a condition first seen on a bar with `et >= session_end - 1` is not an exit — `giveback_condition_after_forced_flat_*` flags it and `v_giveback_*` equals `v_forced_flat` |
| any panel row as a causal feature | use `causal_feature_columns()` / `coverage.json.causal_excluded_families` — outcome, ticket-constant, `future_*` and censor columns are excluded |
