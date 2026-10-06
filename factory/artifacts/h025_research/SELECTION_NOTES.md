# H025 improvement program — selection notes (written BEFORE reading full curves)

Started 2026-10-06 ~13:48 UTC, while sweeps run (core baseline, qualification native/curves,
entry, exit-main, execution). Purpose: keep candidate formation honest when 1046-day curves land.

## Data and blocks
- 1046 legitimate days = 533 original development (2021-02-01..2023-03-14) + 513 already-seen
  larger block (2023-03-15..2023-12-29, 2025-03-01..2026-05-29). All already H025-seen:
  development/temporal replication, NOT fresh OOS.
- Excluded from any new selection: 2024 + Jan/Feb 2025 (spent original OOS, 19 Feb-2025 days
  excluded), 2026-06..08 (cross-strategy reserved). Original frozen pass unchanged.
- Source caveat: rank inputs are historical substrate; no new-rank-quality claim.

## Baselines
- (a) Frozen legacy all-orders lifecycle (published comparator; unchanged semantics).
- (b) Causal pf2-preorder policy (prior_flush_min=2 filters BEFORE placement; deployable form).
- (c) Published post-hoc pf2 subset (+1.14%/trade was on 381 spent-OOS fills) — historical
  reference only; never used for selection here.
- (d) Corrected counters: carried / fresh-only variants as separate formulations.

## Primary read (when curves land)
1. Core baseline: legacy all / prepf2 legacy / pess / opt / posthoc-pf2 across all three blocks
   incl. published-parity checks on matching dates only.
2. Qualification curves (98 specs): per-family response surfaces, by year, with fills/day,
   net fill, total net/day, ranges and retention vs causal pf2 cohort.
3. Entry: discount x expiry/refresh/rearm curves (pf0 vs pf2), $250/$500/$1000, 100/150/200bps.
4. Exit: recovery / stop / hold main effects + joint neighborhoods.
5. Execution: chronology pessimistic/optimistic spans, stop-gap contribution, capacity.

## Selection rules (pre-declared)
- Objective: total net dollars per trading day over ALL days (zero-filled), $500 integer
  order, 150bps primary; also reported at 100/200bps and $250/$1000.
- Prefer causal pre-order policies (deployable). Legacy post-hoc comparisons are diagnostic.
- Mechanistic neighborhoods/plateaus, not isolated best cells. A cell must not flip sign
  across: chronology (legacy vs pessimistic), cost (100 vs 200bps), or the two blocks
  (original533 vs replication) and not be driven by one year.
- Every examined family/cell disclosed in the final read (including negatives).
- No selection using 2024/Feb2025/Jun-Aug2026 outcomes.
- Combination stage: bounded joint grid over qualifying name-condition x discount x exit
  family chosen from the above; no full-Cartesian fishing.

## Leverage / capital layer (user constraint: 2-5x possible)
- Report per $1 of peak committed capital: dollars/day, worst day, worst fill, stop-gap
  dollars; peak pending+position distribution from producer outputs.
- Scale at 2x/3x/5x: max drawdown of the cumulative dollar curve, worst week, ruin-distance
  at integer sizing; capital-binding only if peak commitment exceeds buying power.
- Funded replay only if binding (deterministic priority; disclosed approximation).

## Sub-minute layer (user constraint: Hetzner 900GB available)
- Targeted acquisition AFTER candidate freeze, not now: buy-touch service, sell-at-target
  service, stop gap-through realism for candidate windows on allowed dates.
- Existing Alpaca SIP pipeline (lb18_subminute-style) + storage on Hetzner once wired
  (rclone currently only has the broken gdrive remote). Local disk holds candidate windows
  meanwhile (single-digit GB scale).

## Freeze and forward
- Freeze = exact Policy JSON (core fields + any state conditions) + effective timestamp +
  SHA256; document in researches/ (pre-reg style) with the full evidence read.
- Forward shadow via factory/scripts/h025_research_forward.py on live IEX: decision journal,
  read-only, non-trading; SIP cross-check later. Forward record = observational, NOT
  execution parity (IEX feed, no queue model). Bot/live flags untouched unless user directs.
- If no formulation beats baseline net/day robustly under these rules: report exactly that
  (no forced freeze); improvements must be material and mechanism-coherent.
