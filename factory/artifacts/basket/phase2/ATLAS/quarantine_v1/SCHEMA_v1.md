# ATLAS panel schema — FROZEN 2026-09-25

Contract for `factory/artifacts/basket/phase2/ATLAS/panel.parquet`, produced by
`factory/scripts/basket_atlas_panel.py`. Every downstream analysis (window profile, fall structure,
matched pairs, dollar ledger, rule extraction) reads this file and **must not redefine its columns**.

## The thesis this panel exists to measure

The top-gainer phenomenon is an **early-session explosive-attention event**: the climb and the
climax are concentrated in the morning-to-midday hours; the afternoon is the relaxation phase, where
these names die or return to base camp. **The session close is the contradiction of the thesis, not
the value horizon.** The panel therefore tracks every member **to the close** (so the fall is
observable) but defines *no* outcome as "the close": outcomes are executable values of staying
exposed from the next open onward, under declared continuations, plus the path statistics that
explain them.

Never collapse this into "return to EOD". Never let a current implementation constraint become part
of the economic thesis.

## Grain and keys

One row per (member, completed bar):

| column | type | definition |
|---|---|---|
| `sleeve_day` | str | session date, `YYYY-MM-DD` |
| `ticker` | str | member |
| `family` | str | `A_pm` (fill at ET 571) or `B600` (fill at ET 601) |
| `entry_rank` | int | canonical snapshot rank at entry, 1..3 |
| `entry_et`, `entry_px` | int, float | anatomy fill (canonical bar open) |
| `et` | int | ET minute-of-day of this completed bar |
| `bar_index` | int | 0 at the fill bar, +1 per completed bar |
| `bars_since_entry` | int | `bar_index` (alias kept for readability) |
| `session_end` | int | ET of the last bar used for this day |
| `month`, `block` | str | `YYYY-MM`; `block1` (2021-02..2023-12) or `block2` (2025-02..2026-05) |

Population: A_pm top-3 and B600 top-3 filled members, all 1,066 development days. Blocked fills are
excluded (and counted). Coverage must equal the known filled-member counts for both families; the
producer fails loudly on any deviation.

## Conventions (non-negotiable)

- **Causality:** every state column uses only bars with `et <= t` plus the fill. No future bar, no
  future snapshot, no session-level aggregate that includes bars after t.
- **Execution:** a decision at completed bar `t` executes at the **open of the next bar** (`t+1`).
  If `t` is the session's last bar, `next_open` and all outcome columns are `null` (never fabricated).
- **Friction:** the panel is friction-free. Analyses apply `friction_bps` per side on the executed
  action (`bps_total/2`, matching `basket_sim.py`).
- **No fabrication:** a missing input (no bar at t, no next bar, no episode) yields `null`.
- Determinism: identical inputs → identical file (sorted by `sleeve_day, family, entry_rank, et`).

## State columns (all causal)

**Own path:** `ret_from_fill` (close/entry_px − 1), `ret_from_prevclose`, `ret_from_open0930`,
`mfe_so_far` (max high since entry / entry_px − 1), `mae_so_far` (min low / entry_px − 1),
`running_high`, `dist_from_running_high` (close/running_high − 1),
`mfe_surrendered` ((mfe_so_far − ret_from_fill)/mfe_so_far, 0 if mfe_so_far ≤ 0).

**Episode state:** `bars_below_entry_episode` (consecutive completed closes < entry_px ending at t; 0
if the close is ≥ entry), `episode_low` and `bars_since_episode_low` (low of the most recent
below-entry episode and bars since it; null before any such episode), `reclaim_count` (transitions
close < entry → close ≥ entry so far), `failed_reclaim_count` (reclaims already followed by another
close < entry), `bars_since_new_high` (bars since the bar that set the current running high),
`new_high_count_5/15/30` (bars in the last 5/15/30 minutes that set a new running high).

**Dynamics:** `ret_1`, `ret_3`, `ret_5` (close/close(t−k) − 1), `accel_1_5` (ret_1 − ret_5/5),
`up_close_streak`, `range_expansion` (bar range / mean range of the prior 5 bars − 1),
`bar_range_pct` ((high−low)/close).

**Attention:** `volume`, `dollar_volume`, `volume_vs_own_median` (bar volume / median volume since
entry, null if < 5 prior bars), `volume_accel` (mean volume of the last 5 bars / mean of the prior 5
− 1), `gap_count_so_far` (bars where `et` jumps by more than 1 minute), `bars_since_gap`.

**Cross-section (live, causal):** `candidate_count_t` (names in the day's `A_pm` snapshot with a bar
at t), `ret_percentile_candidates` (percentile of this member's `ret_from_open0930` among those
names), `peer_ret_median` (median `ret_from_open0930` of the *other* members of the same family/day at
t), `peer_new_high_5` (how many of those others set a new high in the last 5 minutes).

## Outcome columns (strictly after t; from `next_open`)

**Level:** `next_open` (open of bar t+1), `next_et`.

**Declared continuations — value of staying exposed, measured from `next_open`:**

| column | definition |
|---|---|
| `v_hold_flat` | close(session_end)/next_open − 1 (the labeled EOD-anchored baseline, kept only for comparability) |
| `v_giveback_5/10/15/20` | return from `next_open` to the exit price of "exit at the next open after the first bar whose close is ≥ g% below the running high *as of that bar*" — the running high is re-evaluated causally forward; null if it never triggers (then the position runs to the forced flat, and the column holds that value with `giveback_fired_* = false`) |
| `giveback_fired_5/10/15/20` | bool, whether that rule fired before the close |
| `v_sell` | 0 by construction (cash is the numeraire); `next_open/entry_px − 1` is available as `level_ret` for P&L accounting |

**Path statistics (the explanation of why `v_*` move):**

| column | definition |
|---|---|
| `final_high_flag` | true iff `max(high[t+1..end]) <= running_high(t)` — the running high at t was the session's last high (`p_climb_over` is estimated by grouping this) |
| `remaining_run` | `max(high[t+1..end])/next_open − 1` |
| `cost_of_waiting` | `min(low[t+1..end])/next_open − 1` |
| `bars_to_next_high` | bars from t+1 until the first bar whose high > `running_high(t)`; null if none |
| `dd_before_next_high` | `min(low[t+1..next_high_bar])/next_open − 1`; null if no next high |
| `bars_to_peak`, `peak_et_after_t` | bars/ET of the session's max high after t |
| `tail_class_50/100/300` | bool: `max(high[t+1..end])/next_open − 1` ≥ 0.5 / 1.0 / 3.0 |

**Ticket-level constants (repeated per row, for the fall analysis):** `session_peak_et`,
`session_peak_ret_from_entry`, `session_peak_bars_from_entry`, `session_close_ret_from_entry`.

## What the panel deliberately does NOT contain

Policy bookkeeping (exposure, cash, actions taken) — that belongs to the policy replay layer, not to
market state. Thresholds, triggers, gates, composites, fitted scores — nothing is chosen here. The
panel is the tape, described causally and exhaustively; every rule comes later and is judged by the
dollar ledger.
