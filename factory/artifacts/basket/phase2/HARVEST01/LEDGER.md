# HARVEST01 — running evidence ledger (night of 2026-10-01 → 10-02)

Thesis under test: causal top-gainer baskets (top 1–4 by split-normalized gain vs the
previous session close at fixed PM/RTH clocks), equal initial dollars, next executable
open; then how to HANDLE the members (release / partial / re-entry / rotation /
redistribute / scale-in) across the golden window where these names do their work.

Dev data only: 1,066 days (2021-02-01..2026-05-29, 2024 + 2025-01 sealed, 2026-06..08
reserved). Conventions: decision on completed bars; entry/exit at first bar open
et>=clock; gap>=5min => blocked slot = cash; 50bps/side friction (100bps round trip,
150bps stress); EOD requires a print at/after session_end else UNKNOWN (never zero).

## Substrate (built and validated)

- Broad causal PM snapshots (new lane `basket_pm_snapshots.py`): full PIT universe,
  04:00–09:30, decision px at 08:30/09:00/09:10/09:15/09:20/09:25/09:29 + first-open
  fills; canary reconciled EXACTLY against the legacy premarket compact (8,818/8,818
  rows). Full 1,066-day acquisition running (gated chain follows it).
- RTH substrate: local full-market race panel (`race.minute_full`), decision row t=C
  (px = close of last bar et<=C-1); no acquisition needed.
- Split normalization (3,630 reverse splits in-span): without it, a sampled reverse
  split showed a fake +468% vs the true ~-5%.
- Selection lane: 19 clocks x N=1..4 x variants raw/primary/listed; day champions.
- Bars lane: minute bars for selected + leader tickers; PM fill cross-check 0 mismatches.
- Unmanaged sim: independently verified EXACT (45 cell fields + 12 fill rows + 81-cell
  sweep, max delta 2.2e-16; `data/_scratch/canary_sim_verify.py`).
- Basket capital engine baseline (`base_hold`) reproduces sim ret_100 exactly
  (max diff 4.4e-16 over 1,140 cell-days).

## Phase-1 recap (why "30–40%" is remembered)

`PHASE1-RECAP.md` (recovered, cross-checked): the memory maps to the JOINT touch +30%
k>=1 share at N=3: A_pm 44.1%, A_pm31 41.5%, A_open 38.9%, B/600 39.6% — i.e. on ~40%
of days the 3-name basket contained >=1 member whose path touched +30% after its fill.
NOT net EV. Containment of the day's #1 gainer is a different object (A_pm/570/N=3
8.7%; B/600 35.4%).

## Canary texture (3–5 days; explicitly NOT conclusions)

- Unmanaged: e.g. 2026-05-29 09:20 N=3 to 10:00 = +14.4% net-100 (exact hand-check).
- Member rules (12): gb10 fires ~67%; later-window deltas positive on the sample while
  the decay/time-stop families sell far fewer future monsters; re-entry and rotation
  variants UNDERPERFORM plain sell (consistent with the CV01 lesson).
- Policy grid (36 policies): on the sample, "sell into strength then trail" and gb10
  variants lead at 11:00–12:00; rotate_leader/reclaim_same are the worst.
- Basket capital: release->cash beats base_hold on the sample; redistributing into
  survivors or the current market leader is worse than cash so far; holding back 1/3
  reserve ("de-leveraging") also helps on the sample — the classic exposure effect.
- Window anatomy: basket peaks clustered ~10:00–11:00 on the sample days.

## In flight (gated chain)

select_final -> leaders -> bars -> sim -> readouts -> containment -> mgmt -> policies
-> basket -> window (+ reports). Then: independent audits of the full-run numbers,
sub-minute execution probe (sample), and the consolidated morning report.

## 2026-10-02 (~04:40) — PM acquisition CLOSED, causality audit applied

- Broad PM snapshots complete: 1,066/1,066 days, index built (2,657 s wall, 3 workers,
  zero errors on the final day log). Lane-completeness audit (PMAudit): PASS on all 5
  items — 1,066/1,066 manifests ok, 0 sha256 mismatches, index exact, 160/160 provider
  spot-checks exact (8 days incl. an early close), 0 sanity violations over 2.68M rows.
  Coverage context: PM prints cover ~36–40% of the PIT universe in 2021–23 vs ~54–57%
  in 2025–26 (early-year PM liquidity is thinner).
- Independent causality review (AUDIT-CAUSALITY.md) verified the core conventions by
  full recomputation — 12,768 sim-cell comparisons and 5,472 mgmt chained-delta
  comparisons matched exactly, selection/PM reads causal, split normalization correct
  once, EOD/UNKNOWN rules exact — and found 6 defects; all fixed in commit 1de982f:
  (1) replace_gb10 exec metadata nulled by a duplicated base-rule mapping;
  (2) prev_used_src provenance label always "panel";
  (3) sim emitted degenerate exit<=entry cells (now skipped per contract valid_cells;
      same filter added to mgmt/policies/basket endpoints and the readout);
  (4) mgmt never froze its rule config (now writes mgmt/_config.json);
  (5) select crashed on a day without a panel file (now degrades with full schema);
  (6) mgmt readout silently dropped replace_gb10 (now included).
- All stale canary-lane outputs were deleted so the gated chain regenerates every lane
  uniformly with the fixed code (1,066-day single-pass).

## Next serious confirmation (to be filled once the full grid lands)

Candidates will be ranked only if they are positive on the FULL dev span, in BOTH
blocks, at 150bps stress, with the giant tail preserved — and only then pre-registered
against reserved months.
