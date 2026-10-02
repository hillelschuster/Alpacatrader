# HARVEST01 — DISCOVERY PLAN: state-conditioned V(t) on the actual baskets

Status: DESIGN (written 2026-10-02, after HARVEST01 pass 1/2 closed and the owner redirected
from "another rule grid" to discovery). Scope: dev days only (1,066); reserved months untouched.

## The question

For a member of an actual top-gainer basket at minute t (causal state only), is the forward
continuation value versus CASH positive after friction — and which observable states separate
**continued convexity** (the tail that pays) from **dead capital** (the dud bleed)?
This is CV01's primitive (`CV = W_hold/W_exit − 1`, entry cost sunk, cash numeraire) moved onto
the market-wide HARVEST01 roster and conditioned on coordinates that have never been used:
market-wide rank/race state, rank trajectory, participation breadth, and peer/basket states.

## Why not another rule grid (evidence)

- Rule grids convert AUC into nothing: E1 exhaustion (AUC 0.82–0.84) never beat giveback:10 in
  dollars; fall v2 predicts dispersion, not direction (+0.07/+0.04 AUC over clock). 
- HARVEST01 pass 1/2: every handling family cut losses (best pooled +2.18 pp) but no absolute cell
  crossed zero; the release rules fire on 99.9% of giants. The missing piece is not a threshold —
  it is a *state* that separates convex continuation from dead capital in dollars.
- The one prior that DID separate per-member (CV01: deep damage ≤−20%: +1.51% @h60 member-balanced)
  is negative per-occupancy — the wedge is unexplained and is itself an object here.

## Data plan (no new acquisition)

Per member-day (fills lane, status=filled; variants `primary` + `compact`), a **minute panel** for
t = fill_et+1 .. session_end (completed bars only; state at t uses bars with et ≤ t−1 and the
race-panel row t):

- **Own path** (from `base/bars/<day>.parquet`; the member tickers are already fetched):
  ret_from_fill, dist_from_running_high, mae_so_far, bars_since_new_high,
  new_high_count_{5,15,30}, up_close_streak, ret_{1,3,5}, volume_accel (5/20),
  vol_vs_day_median_so_far, price, age (staleness of the last bar).
- **Market/race state** (from `race.minute_full` row t; live/prospective fields only):
  rank_known, rank_fresh_2m, order_slot, age_min, fresh_2m, gain, n_known, n_fresh_2m, n_eligible,
  and trajectories: Δrank_known over 5/15 minutes (join same ticker at t−5 / t−15).
- **Peer / basket state** (siblings from the same panel at t): each sibling's ret_from_fill and
  dist_from_running_high; count above fill; count with a new high in the last 5 minutes; basket
  mean ret; basket drawdown from basket peak.
- **Context**: time-of-day (t), entry clock, N, tenure (t − fill_et), price band, entry gap.

**Labels (future, executable, never states):** for h ∈ {5, 15, 30, 60, 120}:
`V_h(t) = (open(first bar ≥ t+h) / open(first bar ≥ t+1)) − 1` (sell now vs hold h minutes; both
ends next-open executable), plus forward tail flags: fwd MFE, fwd MAE over (t, t+h], and
fwd_MFE ≥ 30/50/100% indicators. UNKNOWN (no executable print) stays null, never 0.

## Friction (measured, not assumed)

- Sell-side cost at t from the local quotes archive where covered: quoted spread at the minute;
  plus the entry-side print-vs-bar-open already measured (`subminute_probe`), extended to the
  exit side (the dead `exit_px_diff` branch gets fixed).
- Every table is published twice: **ruler** (50 bps/side) and **measured-where-covered** (else
  ruler, flagged). Breakeven-friction per cell is reported so the near-zero scalp region can be
  judged against real cost rather than the constant.

## Analysis (descriptive-first; no fitting, no ML, no joint grids)

1. **Univariate state tables** — one coordinate at a time, in pre-declared coarse bins
   (mirroring CV01's discipline): bin → mean V_h and median V_h, n, B1/B2 split, and the
   **tail ledger co-primary**: P(fwd_MFE ≥ 30/50/100%), mean V_h | fwd_MFE ≥ 100%, top-1%
   contribution. Published in **three weightings**: occupancy (member-minute), member-balanced
   (day×clock×rank), basket-day (equal slots).
2. **The never-used coordinates**: rank level and Δrank_5/15; market breadth (n_fresh_2m,
   fresh share of eligible); participation (top-decile count); peer/basket states.
3. **The wedge**: for each bin, member-balanced minus occupancy — characterized, not averaged away.
4. **Time-of-day profile** of V_h within state bins (the golden-window question at minute grain).
5. **Two-sided separation**: P(dead | state) and P(convex | state) side by side; a state is only
   interesting if BOTH the dollar expectation is positive in both blocks and the tail columns
   do not collapse.

## Deliverables

- `factory/scripts/basket_harvest_minute.py` — minute-panel builder (per-day, atomic, resumable).
- `data/harvest01/minute/<day>.parquet` + aggregates; `readouts/minute.md`; committed readouts.
- Any candidate that survives the full-dev read goes to a **pre-registered confirmation** on the
  reserved months before it is believed (the 0-for-4 collision history stands).

## Falsifiers (pre-declared)

- A coordinate is dead if its V_h means are within ±0.1 pp across all bins in both blocks, or if
  its positive cells are tail-column collapses (P(fwd_MFE≥100%) ≈ 0).
- The whole discovery is dead if no bin yields positive V_h in both blocks at any h with n ≥ 300
  and non-collapsed tails — that would say: on this roster, minute-conditioned continuation is
  uniformly ≤ cash, and the money question moves elsewhere (execution/order-level).

## Advisor guardrails (binding, added 2026-10-02)

1. **Do not promote the early "deep-damage positive per member" wedge.** It can easily be a
   duration/selection-weighting artifact rather than a tradable separator: member-minutes in a
   bin are not independent, and the members passing through deep damage are a selected set. Any
   wedge-shaped finding must survive matched comparisons (same clock, tenure, and prior-state)
   and a duration decomposition before it is even called a candidate.
2. **h = 5/15/30/60/120 are rulers, not truths.** If a state looks valuable, the next step is its
   **path/transition anatomy** — what actually happens minute-by-minute after a member enters the
   state (P(new high), forward return profile, drawdown, death), and what the exit looks like —
   before any rule is derived from it. A state becomes a rule only after its transition anatomy
   is understood and its dollars are positive in both blocks.
