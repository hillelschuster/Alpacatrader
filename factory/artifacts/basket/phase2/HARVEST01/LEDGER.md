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

## 2026-10-02 (~02:20) — anchor-provenance finding and the compact variant

Raw PM top-4 lists are heavily polluted by prev-close mismatches: of 81,016 raw
selection rows, 13,501 (16.7%) carry a quality flag — 12,990 discrepancy-flagged
(12,729 of them at PM clocks, ≈43% of PM raw rows), 1,199 gain>10x, 305 sub-nickel.
The `primary` variant removes these; `raw` is retained for sensitivity only.
Because the legacy A_pm lane anchored on the rth-compact c_last (not the panel's
prev_close), a fourth variant `compact` (same lane anchor, split-normalized,
gain<=10x, px>=$0.05) was added to selection; pass 1 (running) covers
raw/primary/listed, and the auto-gated pass 2 regenerates every lane forced so the
anchor comparison is uniform. Independent audits: PM lane PASS (5/5); causality
review applied (6 fixes); selection audit running (incl. an anchor-sensitivity
section).

## 2026-10-02 (~02:15) — first full-grid read: the money structure (dev, 1,066 days)

Unmanaged grid (primary variant; 6,324 cells, net-100): **every cell negative**. Best
cell −0.08% (09:29→09:31 scalp, t≈−0.3); PM entries held to the close −7…−9%;
10:00→noon exits −1…−5%. N=1 beats N=3 almost everywhere; rank1 is the WORST member
at 09:20 (mean −3.4% at 10:00 vs −2.1% for rank4) but the best at 11:00 — the
morning chases the most extended name and pays for it.
Member-level (78,349 filled members): mean post-fill MFE +24.5% vs mean realized
−2.7% (10:00) / −4.6% (noon) — the excursion exists, the hold gives it back.
The tail is enormous and real: 2,883 members (3.7%) with MFE ≥ +100% average +173%
MFE and realize +64.9% at noon; their summed contribution (+1,872 per-slot dollars)
dwarfs the all-member total (−2,747). Half the tickets (50.4%) never make +10% and
average −11.6% at noon — the bleed is the duds.
Release-tradeoff (whole-day damage association): P(MFE≥100% | touched −10%) = 2.0%
vs 6.9% if never damaged; damaged members average −9.9% at noon vs +9.4% for the
never-damaged. Damage separates winners from losers, but ≈1,045 members that touch
−10% still become giants — the release rule's core tradeoff.
Joint tail after the actual fill (primary, N=3): PM clocks ~53–55% of days have ≥1
member touching +30%; ~13% have ≥1 touching +100%. (Phase-1's 39–44% was on the
older candidate universes; the market-wide PM selection is stronger.)

Candidates will be ranked only if they are positive on the FULL dev span, in BOTH
blocks, at 150bps stress, with the giant tail preserved — and only then pre-registered
against reserved months.

## 2026-10-02 (~03:10) — pass 1 closed; two engine bugs fixed by hand-verification

Pass 1 (15 stages) completed exit 0 and REPORT.md assembled. Hand-verification of the
basket capital lane caught two defects that had inflated the scale/release results:
(1) endpoint valuation priced trades that executed AFTER the endpoint at later bars
(post-endpoint look-ahead), and (2) the event replay debited share COUNTS instead of
dollars on buys (free money: a day verified at +110% collapsed to +27.5% and then
matched an independent recomputation exactly). Both fixed; the basket lane was re-run
for all 1,066 days. Corrected best absolute in the basket lane: −0.53% (reserve
scale-in 2/3, 11:00→12:00, N=1); release→cash / survivors / leader deltas stay
positive (+1…3 pp, block-stable) but never reach zero. Across all four engines the
best absolute cells are: unmanaged −0.08% (09:29→09:31 scalp), member rules −0.74%
(dmg_wait 11:00→12:00 N=1), policies −0.80% (same family), basket capital −0.53%.
Final lane numbers and the bottom line are in SYNTHESIS.md §4–6. Pass 2 (compact
anchor variant, every lane forced) is running; push up to date.

## 2026-10-02 (~06:00) — discovery phase: state-conditioned V(t) at minute grain

Built: minute panel for every actual basket member (causal state each minute; executable
V_h labels h=5..120; forward tail flags) across all 1,066 dev days (59M+ member-minutes);
discovery readout (11 coordinates, declared bins, three weightings, B1/B2, tail ledger);
transition-anatomy tool with cross-member matched controls; measured-friction pass
(entry/exit print-vs-bar-open, participation, auction attribution) — all committed.

Results so far:
- **No state bin is positive in both blocks on the occupancy (member-minute) view at
  h=30 or h=120** with n ≥ 300. The closest occupancy cells hover at ±0.1%.
- A handful of member-balanced positives in both blocks, all growing with horizon and all
  containing the continuing giants: `basket_ret ≤ −5%` (whole basket flushed together)
  +0.24→+1.33% across h5→h120 (mean V120 among in-state +100% members: +88%);
  `drank5 ≥ +10` (rank collapses 10+ places in 5 min) +0.49→+1.51%; `rank_known ≥ 101`
  +0.49→+1.01%. Occupancy is negative for all three — the wedge again, so the decisive
  question is the ENTRY transition (first minute in state) vs matched controls, which is
  what the transition tool measures. Guardrails are diagnostics, not kill switches: a
  formulation that fails matched controls gets refined (sub-bins, conjunctions, timing),
  not discarded; a state is parked only when no refinement keeps it positive in both
  blocks net of friction with its tail intact.
- **Transition anatomy (deep-damage entry) does not support the wedge as stated**: the
  matched control path is better at every horizon (V60 −1.72% vs −0.47%; V120 −3.42% vs
  −1.03%) — the duration/selection confound the advisor warned about. Full-span anatomies
  for basket5 / rank101 / rankworse10 / stall10 / nohigh15 running; those decide whether
  the three member-balanced candidates survive as entry states.
- The never-used market-state coordinates (rank level, rank trajectory, breadth,
  peer/basket) did not produce a positive occupancy separator at this univariate level.

## 2026-10-02 (~07:00) — FIRST MECHANISM CANDIDATE: the flush bounce (deep20 entry)

Transition anatomy, full dev span, cross-member matched controls (same clock, tenure ±5,
not in state): **first touch of −20% from the running high (n=84,435 episodes)**.

| horizon | event mean | B1 | B2 | control | gap |
|---|---|---|---|---|---|
| h5 | **+0.43%** | +0.46 | +0.38 | −0.25% | +0.67 |
| h15 | **+0.23%** | +0.32 | +0.10 | −0.48% | +0.70 |
| h30 | −0.16% | −0.11 | −0.24 | −0.84% | +0.68 |
| h60 | −0.37% | −0.33 | −0.43 | −1.12% | +0.75 |
| h120 | −1.18% | −0.98 | −1.43 | −1.61% | +0.43 |

- Positive absolute value at h5/h15 in BOTH blocks; gaps positive at every horizon.
- Splits: strongest when flushed from the high but still above fill (h5/h15 = +0.63/+0.66,
  n=20,476) and in all time-of-day buckets (<10:00 +0.50/+0.06; 10–11 +0.43/+0.39;
  after 11:00 +0.38/+0.20); below-fill-by-20% names bounce +0.52/+0.29 (n=9,241).
- Fades negative by h30–h120 — the bounce is a WINDOW, not a hold.
- This is a HOLD-TIMING edge (sell-now vs hold 5–15 min: zero incremental friction) and
  it inverts the tested release rules, which sold exactly at the flush minute.
- `rank101` (fell out of top-100): positive only in B1 (B2 negative) — parked, not
  both-block. `basket_ret ≤ −5%` / `drank5` / stall / nohigh anatomies: finishing.
- NEXT: the flush-timing rule test — defer a release after a fresh −20% flush; sell into
  the bounce (e.g., +X% off the flush low or after 15 min) vs the standing rules, basket-day
  dollars at 720/close, both blocks, friction unchanged (one sale either way).



Pass 2 (every lane forced, all four selection variants) finished exit 0; the assembled
REPORT.md and all readouts are committed under
`factory/artifacts/basket/phase2/HARVEST01/readouts/`. Anchor robustness: the
compact-anchored selection is a little less bad at several cells (e.g. 11:00→12:00
N=3: −1.06% vs primary −1.66%; 10:00→11:00: −1.63% vs −2.68%) and never positive —
the conclusion does not depend on the anchor. Final cadence probe: stall10 is the
best pooled delta of the night (+2.18 pp vs gb10 +1.71 pp) but fires on 99.9% of
giants and its best absolute cell is −0.46% (nohigh10d0, 11:00→12:00, N=1). Best
absolute across every engine: −0.46% (policies), −0.53% (basket capital), −0.74%
(member rules), −0.08% (unmanaged 09:29→09:31 scalp). Nothing crosses zero at 100
bps; nothing at all at 150 bps. Commits: 10997c8 (readouts), a3c4eb8 (cadence grid),
51ee2b9/ee525d3 (basket engine fixes), 1de982f (audit fixes), 8dff0ed..21ce548
(synthesis/ledger). All pushed to origin/basket-phase2-f1.

## 2026-10-02 (~07:20) — flush-timing test: sell into the bounce, not at the flush

Paired executable exits from the same 84,435 flush episodes (every variant sells once;
fees identical across variants → friction-free timing differences vs selling at the
flush open):

| exit choice | mean | B1 | B2 | median |
|---|---|---|---|---|
| hold 5 min then sell | +0.43% | +0.46 | +0.38 | +0.39 |
| hold 15 min then sell | +0.23% | +0.32 | +0.10 | +0.16 |
| hold 30 min then sell | −0.16% | −0.11 | −0.24 | −0.32 |
| sell on +1% bounce off the panic low | +0.28% | +0.32 | +0.24 | +0.38 |
| sell on +2% bounce | +0.39% | +0.44 | +0.33 | +0.65 |
| **sell on +3% bounce** | **+0.51%** | +0.54 | +0.49 | +0.95 |
| **sell on +5% bounce** | **+0.71%** | +0.65 | +0.79 | +1.35 |

All positive in both blocks; +0.5–0.7% mean / +0.95–1.35% median advantage per flushed
member over selling at the flush minute. The rule shape to test at basket level:
**defer any release that would fire at the flush; sell into the first +3–5% bounce off
the panic low (cap 30 min).** Open question for the rule test: interaction with the
standing −10% release rules (which fire earlier than −20%) and with the giants (which
flush rarely and recover strongly).
