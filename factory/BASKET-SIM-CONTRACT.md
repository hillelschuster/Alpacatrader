# BASKET-01 Phase-2 — canonical event-driven simulation contract

**Status: FROZEN 2026-09-22** with the PRE-REG-BASKET-02 family contract. Normative for
every Phase-2 strategy run. Any change to this file is a **new degree of freedom**: it must
be dated, declared in the run report, and invalidates comparability with prior runs unless
the affected families are re-run on the new contract.

Companion: `researches/PRE-REG-BASKET-02.md` (families, grids, validation, DOF register).
Engine implementation: `factory/scripts/basket_sim.py` (must follow this file literally).

---

## 1. Why this exists

Ten workers must not invent ten meanings of "return". Every strategy family in Phase 2 runs
on this one engine: identical causal selection, identical fill semantics, identical
accounting, identical friction, identical metrics. Family modules may only compose the
action/rule vocabulary declared here and in PRE-REG-BASKET-02.

## 2. Substrate (inputs; read-only)

Canonical root: `factory/artifacts/basket/sip/` (`BASKET_ART_ROOT` override).

- `anatomy/YYYY-MM-DD.jsonl` — one JSON record per day; 15 snapshots
  (`A_open`, `A_pm`, `A_pm31` at T=570; `B` at T in {575,580,585,590,595,600,615,630,645,660,690,720}).
  Each snapshot: `{pop, T, names:[{ticker, rank, sel, sel_alt, px_decision, open0930,
  prev_close, split_flag, fill:{et,px,gap_min,blocked,alt_px}, pre_high, day_high, close,
  eod_ret, mfe, mae, i_mfe, i_mae, last_et, bars, gap_pre, gap_post, ladders:{up,dn},
  state, ratio_anchor}]}`. Top-10 names per snapshot; ranking tie-break = score desc,
  ticker asc (canonical).
- `bars/YYYY-MM-DD.parquet` — per-candidate 1-minute bars, schema
  `(date, ticker, et, open, high, low, close, volume)`; `et` = ET minute-of-day int;
  RTH range [570, 960).
- Raw SIP layer (`data/sip/`, `/home/hillel/sip/`) is available for verification and
  execution-realism work only; never a substitute for the artifacts above.
- **Session calendar (mandatory, committed):** `factory/artifacts/basket/sip/phase2_session_calendar.json`
  maps every dev day to `session_end` (last *regular-session* minute): 959 normally,
  779 on NYSE early-close days (13:00 ET close). Early closes in dev must be enumerated
  mechanically (volume falloff check: after-hours prints exist past the close) and verified;
  the file records the method and the day list. No Phase-2 run may trade bars with
  `et > session_end`. Half-day candidate list to verify: 2021-11-26, 2022-11-25, 2023-07-03,
  2023-11-24, 2025-07-03, 2025-11-28, 2025-12-24 (build + verify, do not trust the list).

**Evidence boundary (hard):** dev days = the 1,066 canonical days (2021-02..2023-12,
2025-02..2026-05). Sealed 2024 + 2025-01 SIP: never computed on. Reserved 2026-06/07/08:
never fetched, never read. The loader must refuse any day outside the dev set.

## 3. Clock, causality, decisions

- ET integer minutes (`et`). A bar with `et=t` is **completed** at minute `t+1`; every
  decision is made from completed bars only (1-bar lag, frozen doctrine).
- Every decision executes at the **open of the first bar with `et' > t`** for that ticker
  (within the session; see §6 for pending/halts). No same-bar execution anywhere.
- Entry fills are **not recomputed**: they are read from the anatomy snapshot `fill`
  (`et`, `px`, `gap_min`, `blocked`). The engine asserts `fill.px == bars.open[fill.et]`
  (1e-9) and flags any mismatch; anatomy is canonical.
- Ranking and selection for all families come from the anatomy snapshots (top-10 names),
  preserving the Phase-1 selection contract exactly. Family modules choose *within* the
  snapshot (e.g. top-N by the stored rank); they never re-rank on their own prices.

## 4. Entry families (mapping; frozen in PRE-REG-BASKET-02)

| Family id | Snapshot (`pop`,`T`) | Selection known | Fill |
|---|---|---|---|
| `A_pm` | (`A_pm`, 570) | pre-bell premarket print | 09:30 bar open (anatomy fill) |
| `A_pm31` | (`A_pm31`, 570) | same selection | 09:31 bar open |
| `A_open` | (`A_open`, 570) | completed 09:30 bar | 09:31 bar open |
| `B585` | (`B`, 585) | completed 09:44 bar | first open `et>=585` |
| `B600` | (`B`, 600) | completed 09:59 bar | first open `et>=600` |
| `B575`, `B615` | (`B`, 575/615) | neighborhood stability points only | first open `et>=T` |

- A blocked fill (`blocked:true` or no bar) = **cash slot**, retained in the denominator,
  reported (`n_blocked_slots`). No backfill, no replacement, no close substitution.
- Fewer than N qualifiers = fewer slots; empty days are retained in all denominators.
- Slot i takes an equal budget (see §5); one position per ticker; duplicates across
  snapshots are irrelevant (a run uses one entry family at a time).

## 5. Position & action model

Ticket state: `{ticker, entry(fill et,px), shares, unit_notional, cash_in, realized, peak,
mfe, mae, actions[]}`. Fractional shares are allowed in the simulator (notional accounting);
rounding for live execution is an execution-realism question (family F11), not a core concern.

| Action | Size vocabulary | Trigger (completed bar t) | Execution |
|---|---|---|---|
| `ENTER` | full slot budget | entry family | anatomy fill |
| `ADD` | +25% / +50% / +100% of **original unit notional** | strategy-declared strengthening module | next bar open |
| `REDUCE` | 25% / 33% / 50% of **current shares** | strategy-declared deterioration module | next bar open |
| `EXIT` | 100% of current shares | release module fired / forced flat | next bar open |
| `HOLD` | — | default | — |

- **No leverage, ever.** Engine invariant: `cash >= 0` at every event; total deployed
  notional ≤ C0; an ADD that cannot be fully funded from available cash is **skipped**
  (flagged), never partially filled.
- **Add cap:** total ADD notional per ticket ≤ 2.0× unit notional (i.e. ≤ +100% total adds).
  Exceeding the cap = strategy bug; engine flags and skips.
- An add may only be justified by the candidate's **own state** (thesis lock); the engine
  cannot enforce semantics, but family reports must cite the causal justification.

## 6. Session end, forced flat, carries

- `session_end(day)` from the committed calendar. Session bars = `et <= session_end`.
- **Forced flat:** decision at the last completed session bar (`et = session_end - 1`);
  exit at the `open` of the last session bar (`et = session_end`). If a release also
  fires that bar, the exit price is the adverse convention: `min(open, level)` for
  level-type exits (§7).
- **Pending through halt:** a triggered exit with no later session bar stays pending and
  executes at the **first available bar in a later session** (price `min(px, level)` for
  level-type exits); the position is an involuntary carry, keeps its slot (no leverage),
  and is flagged (`n_carries`). If the ticker has no bars in the next session(s) under the
  artifacts, the engine carries at the last known close, flagged `no_resumption`, until a
  bar appears or the data ends (terminal mark at last close, flagged).
- Carried P&L is attributed to the originating basket-day in ticket rows, but the
  basket-day return is only recognized when realized (exit); marked value is reported
  separately (`realized` vs `marked`). Daily return uses realized P&L of the day plus the
  day's marked change for open tickets (mark-to-market), so carry days are not
  zero-return days.

## 7. Deterministic event order (engine must implement exactly)

Per completed bar `t`, per ticket, in this order:

1. **Forced-flat check** (`t == session_end - 1`): emit `EXIT`; skip rules below.
2. **Release modules** in the strategy's declared order (e.g. `R1` then `R3`): the first
   firing module emits its action (`EXIT`, or `REDUCE` for scale-out variants). One rule
   firing consumes the bar for that ticket (no double action from two release modules).
3. **Scale-in modules** (only if no release/reduce fired this bar): may emit `ADD`.
4. **State updates:** update running `peak` with this bar's high; update `mfe/mae`;
   a bar's own high can never trigger a peak-relative rule on the same bar.
5. Ambiguity inside a bar (both +H and −L touched) is resolved by the module's declared
   completed-bar definition (close/low/high fields); the engine never invents intra-bar
   ordering. Same-bar `min(open, level)` is the only adverse price convention.

Cross-ticket: all tickets evaluate the same bar independently; executions are applied in
order `(execution et, ticker asc, action priority ENTER < ADD < REDUCE < EXIT)`; cash
deductions happen in that order. Any ties in selection are already settled by the canonical
ranking (score desc, ticker asc).

## 8. Accounting & friction

- Primary convention: **one independent sleeve per basket-day**, capital `C0 = 1.0` unit.
  Slot budget = `C0 / N` (times reserve fraction on entry, see capital family). Returns are
  basket-day returns `r_day = P&L_day / C0`. Secondary (supplementary) convention:
  compounded equity across days (`E_{d+1} = E_d (1 + r_day)`), reported with max drawdown.
- Friction: configurable total round-trip bps, applied **per side** as
  `side = bps_total / 2 / 10000`. ENTER/ADD: `shares = budget / (px * (1 + side))`;
  EXIT/REDUCE proceeds: `shares * px * (1 - side)`. Minimum base = 100 bps (adversary
  150); gross (0) and the sensitivity ladder {0, 50, 100, 150, 200} are reportable runs.
  Blocked/unfilled/cash slots pay nothing.
- Realized vs marked always separated; released capital ≠ economic risk removed; report
  capital released by released peers and locked profits where the family uses them.

## 9. Standard metrics (every run emits all of these)

`days_n`, `filled_days`, `mean_basket_day`, `median_basket_day`, `std_basket_day`,
month-blocked **day-clustered bootstrap CI** (resample months with replacement, 10,000
draws, seed 20260922), `compounded_growth` + `compounded_max_dd` (secondary convention),
`worst_day`, `worst_week` (calendar-week sums), `worst_month`, `positive_day_share`,
daily percentiles `p1/p5/p10/p25/p50/p75/p90/p95/p99`, `avg_deployed_capital`
(time-weighted deployed notional / C0), `turnover_per_day` (executed notional / C0) and
`turnover_annualized` (×252), `n_entries/n_adds/n_reduces/n_exits/n_blocked_slots/
n_pending/n_carries`, `avg_failed_ticket_cost` (mean net return of tickets with net < 0),
`avg_survivor_contribution` (mean net return of tickets with net > 0 and their share of
positive P&L), `top1/top5/top10_day_share` of total net P&L (NA if total ≤ 0),
`path_contrib`: net P&L share of tickets whose raw post-fill MFE ≥ {50,100,200} (touch),
`false_release_rate[H]` for H ∈ {30, 50, 100}: among tickets with raw MFE ≥ H, the share
fully flat before the first H-touch bar; `half_release_rate[H]`: share reduced ≥50% before
it; `tail_retained`: mean/median of `captured_net / MFE_raw` among MFE ≥ 50 tickets
(`captured_net` = total net P&L / unit notional); per-year and per-quarter versions of the
headline block. All money numbers are unit-normalized (C0 = 1).

Gross and net@100/net@150 are mandatory columns; every family must also report the full
parameter surface it explored — never only the best cell.

## 10. Outputs (per run) & hygiene

`factory/artifacts/basket/phase2/<family_id>/<run_id>/`:
`config.json` (family, cell params, friction, days, seed, git HEAD, sim contract hash),
`daily.parquet` (date, r_day, deployed, actions, flags), `tickets.parquet` (ticker, entry,
exits, net, mfe_raw, flags), `metrics.json` (section 9), `surface.json` (family-level grid
summary), plus a worker-authored `README.md` (mechanism read + stability). Incremental
month-keyed part writes; `--merge-only` to finalize; runs must be resumable and never leave
`.tmp` artifacts. Workers do not commit; the orchestrator commits.

## 11. Mandatory canaries (before any family result is trusted)

1. `fill.px == bars.open[fill.et]` for every filled snapshot name; count mismatches (must be 0).
2. Recomputed MFE/MAE from bars == stored `mfe/mae` (1e-9) for all names in a ≥30-day sample.
3. Recomputed first-touch et for the up/dn ladders == stored `ladders` for a ≥30-day sample.
4. Engine `R1(-10)` exit et/px reproduced by an independent scan on ≥20 hand-checked tickets,
   including at least one gap-through (`min(open, level)`) and one pending/halt case.
5. Forced-flat plumbing: engine all-hold exit price == `bars.open[session_end]` for every held
   ticket, and the engine's mean of `open[session_end]/fill - 1` over all filled `A_pm` names
   equals an independent direct scan of the same quantity from bars+anatomy (exact). The
   anatomy `eod_ret` (close-based) is reported beside it as a documented, different convention.
6. Determinism: two identical runs → identical `daily.parquet` content hashes.
7. Cash/no-leverage invariant assertions across a full 1,066-day baseline run.

## 12. Prohibited

Leverage; trading past `session_end`; recomputing fills from bars (except canary checks);
using sealed 2024/2025-01 or reserved 2026-06..08 in any path; modifying Phase-1 artifacts,
the canonical packet, or other lanes' files; live bot edits; commit by workers; strategy
runs before the PRE-REG-BASKET-02 freeze commit.
