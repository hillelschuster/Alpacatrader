# F4 — per-ticket partial scale-out surface

**Status: RUN, development-only economic experiment; not a strategy promotion.** F4 measures
whether a registered reduction of deteriorating exposure lowers failed-ticket cost without
giving up too much exceptional-survivor value. Avoided losses alone do not establish success:
the primary read is net C0 basket EV against both same-trigger full exits and the matched
full-hold control, with captured raw-MFE tail and the economics of each sold tranche beside it.

## Frozen comparison

- **Anchor:** A_pm/T570, canonical stored anatomy fills, top 2 by canonical rank, two equal
  slots, C0=1, no leverage. It is fixed to match F3 and avoid selecting a favorable F3 cell.
- **Triggers:** all six registered R2(L,w) combinations (`L={10,15}`, `w={3,5,10}`) and
  all three R3(g) values (`g={40,50,60}`). R2 uses the unchanged grace-window state machine;
  R3 checks the prior running peak. Each event uses completed-bar information only.
- **Partial patterns:** `25→25→rest`, `33→33→rest`, `50→rest`. Each percentage reduces the
  then-current shares (not original notional); `rest` is an EXIT on a later trigger. A pending
  action blocks a second trigger until it executes. R2 level reductions retain the simulator's
  adverse `min(next_open, level)` convention; R3 reductions fill at the next eligible open.
- **Controls:** a same-trigger full exit for each of nine triggers and each friction (18
  controls), plus full hold at each friction (2 controls). Total: 54 partial + 18 full-exit +
  2 hold = **74 cells**, all retained in `surface.json`.
- **Friction/data:** total round-trip 100 bps and 150 bps (charged per side by the canonical
  simulator). Only the canonical 1,066-day development set is used: block 1 = 734 days / 35
  months, block 2 = 332 days / 16 months. Sealed 2024/2025-01 and reserved 2026-06..08 are
  excluded. The F3 map supplies vocabulary only; no F3 outcome selects an F4 cell.

## Engine, timing, and accounting

`factory/scripts/basket_f4_scaleout.py` runs and audits the family; `basket_f4_rules.py` supplies
family-local `ReleaseRule` objects, `basket_f4_paths.py` measures raw paths and reconciles executed
action dates, and `basket_f4_metrics.py`/`basket_f4_blocks.py` build economic reports.
`factory/scripts/basket_sim.py` remains unchanged. Trigger state uses completed bars, execution is
next eligible bar open, and the simulator owns release priority, cash/C0 invariants, session
calendar, forced flat, involuntary carry, friction, realized/marked accounting, and deterministic
cross-ticket order. Scale-out chronology is retained in each partial/full-exit cell's
`action_chronology.json`, including actual execution ET/price, shares sold, cost basis removed,
remaining shares, flags, and first-touch chronology. Raw post-fill path MFE/MAE and +30/+50/+100
touches are independently measured from the complete canonical entry-session path; this avoids
the engine's position MFE ceasing when a full-exit cell closes early. Raw path outcomes do not
enter decisions. A reduction executed at the first-touch bar's open is included in shares retained
at that touch; `reduced_early_rate` remains strict to reductions before the touch bar, matching
the contract's “before first H-touch bar” definition.

Every cell reports pooled and block C0 EV, deltas against matched hold and same-trigger full-exit,
failed-ticket loss, early reductions/releases before raw-MFE touches, raw-tail shares retained at
touch, net tranche contribution by stage and raw-MFE≥50/100 cohorts, realized versus marked P&L,
drawdown, turnover, estimated friction, monthly/year/quarter stability, and joint-sleeve
multi-survivor measures. `surface.json` preserves all 74 cells, including negative/null values;
no best cell is selected. Month-keyed daily/ticket parts are written by the unchanged simulator
for resumability; `action_chronology.json` is written atomically after each completed cell.
Parity check: both 1,066-row F4 hold controls and representative R2/R3 full-exit daily-return
series reproduce the corresponding F3 R0/module series exactly (`max_abs_diff=0`).

## Economic results [RUN]

All 74 cells completed on the full 1,066-day calendar at both registered frictions. At 100 bps,
partial-cell mean net C0 EV spans **−4.01% to −3.05%/basket-day**; at 150 bps it spans
**−4.48% to −3.53%**. The matched hold means are −4.12% and −4.59%; every one of the 27 partial
cells at each friction improves pooled EV versus hold by 11–107 bps (100 bps) / 11–106 bps
(150 bps). That does **not** establish an extraction edge: matched same-trigger full-exit means
span −4.00%…−2.99% (100 bps) and −4.47%…−3.47% (150 bps), and only 12/27 partial cells beat
their matched full exit on pooled EV at either friction. Only **one of 27** beats its matched
full exit in both blocks at each friction (`R2(L=15,w=5)`, `50→rest`); it remains one reported
cell, not a selected strategy. Across the full surface, partial EV remains negative in both
blocks: −4.03%…−2.67% (block 1) and −4.23%…−3.28% (block 2) at 100 bps; −4.50%…−3.15% and
−4.70%…−3.76% at 150 bps. Against full exit, pooled-positive deltas occur in 20/27 block-1 cells
but only 4/27 block-2 cells.

Failed-ticket mean loss improves against hold in every partial cell (delta +0.15…+4.36
percentage points at 100 bps; +0.15…+4.23 points at 150 bps), but against matched full exit
the delta is −0.83…+0.007 points. The reduced tranches themselves are economically negative
across this surface: mean net P&L per executed tranche is −0.089…−0.017 C0 units for stage 1 and
−0.041…−0.014 for stage 2 at 100 bps; stage-1/2 ranges are −0.090…−0.018 / −0.041…−0.014 at
150 bps. Stage contributions by block and by raw-MFE≥50/100 tickets are in each cell's metrics.

Tail preservation is measurable but does not reverse that trade-off. At the first raw +50% touch,
partial cells retain 84.6–100% of original shares; 0–15.9% of +50% raw-MFE tickets have a
reduction before the touch bar, and 0–14.3% for +100%. Captured net/raw-MFE among raw-MFE≥50
tickets is 0.256–0.374 at 100 bps and 0.249–0.366 at 150 bps. These are distinct: retaining
shares at a touch does not make the sold tranche profitable or prove that the tail was captured.
Turnover is 1.93–1.95 C0/day. Estimated total ticket friction across the 1,066 days is
10.33–10.40 C0 units at 100 bps and 15.45–15.55 at 150 bps. The compounded secondary max
drawdown reaches −100% across these cells. Daily worst returns can be below −100% because the
canonical daily row aggregates mark changes from independent originating basket-day sleeves and
involuntary carries; it is not a single-sleeve loss bound. Per-sleeve cash≥0 and deployed≤C0
invariants are enforced by the simulator.

Joint-sleeve outcomes retain more than the one-winner narrative: at least two members touch raw
+30% MFE on 3.41% of block-1 days and 5.12% of block-2 days. At least two tickets end net-positive
(realized or marked) on 8.6–10.8% / 8.7–14.5% of block-1 / block-2 days at 100 bps, and
7.9–9.8% / 7.8–13.0% at 150 bps. The raw-MFE multi-survivor rate is an opportunity measure, not
executable profit. Monthly, annual, quarterly, realized/marked, drawdown, and all 74 cell results
remain in `surface.json` and the cell-level simulator/report files.

**Economic interpretation:** per-ticket scale-outs consistently soften failed-ticket losses
relative to holding, but usually do not beat releasing the entire ticket at the same registered
deterioration event. The rare matched-full-exit exception is not stable across the two blocks as a
family. Tail shares remaining high at +50% cannot be substituted for tail EV: both tranche stages
have negative mean realized contribution at realistic friction. No pattern is called successful
on loss avoidance alone; no strategy, threshold, or live/OOS claim is selected or promoted.

## Basket-level de-risking: not delivered in this lane

This delivery is the complete registered **per-ticket** lane. It does not approximate the
separately registered basket-level `K∈{2,3}, x∈{25%,50%}` mode. `basket_sim.py` evaluates release
rules independently inside each ticket loop and `sim.run()` constructs the base `Strategy`; there
is no synchronized batch callback that can count simultaneous conditions across a common
completed bar and schedule reductions for all affected tickets. A local alternate simulator
would diverge from contract event ordering and cash accounting, so it was not introduced.

**Exact shared-engine extension required for the later dynamic-capital owner:** add an optional
batch deterioration hook called once per completed ET after all eligible ticket bars are known
and before scale-in/state-update; it must accept the common completed ET, active tickets and their
bars, return per-ticket REDUCE/EXIT intents, then let the existing scheduler execute those intents
at each ticker's next eligible open in `(execution_et,ticker,priority)` order. It must define and
test K against occupied N slots, count only simultaneously firing registered R2/R3 modules, use
current-share x fractions, defer repeat firing until pending executions complete, preserve forced
flat precedence and halt carry, and assert cash≥0 / deployed≤C0. Add contract tests for K=2/3,
both x values, ties and missing ticker bars, overlapping release actions, carry, and cash ordering
before running that grid. Do not amend the core while another family owns it.

## Reading results and limits

The authoritative cell table, matched deltas, input provenance, and run status are in
`surface.json`, `provenance.json`, and each `F4/<run_id>/metrics.json`; the complete action-level
record is cell-local. `config.json`, incremental parts, merged daily/ticket tables and simulator
summaries are the canonical-run outputs. `canary/canary.json` records a deterministic two-run
hash check for hold, full-exit and partial examples; a fresh post-refactor check will be retained at
`canary_refactor/canary/canary.json`.

An economic improvement must be judged jointly: lower failed-ticket losses are insufficient if
net C0 EV falls against full exit/hold or if raw tail retention and tail-cohort tranche P&L
collapse. Cross-cell comparisons share days/names and are not independent replications. These are
development results only—not live, OOS, or profitability evidence. MFE/touch is an opportunity
measure, not an executable fill. Marked open tickets are not realized profits; friction estimates
and realized/marked columns must be kept distinct. No threshold outside the frozen F3 R2/R3
vocabulary and no outcome-fitted gate is tested.
