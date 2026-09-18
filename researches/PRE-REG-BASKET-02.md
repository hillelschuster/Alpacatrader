# PRE-REG-BASKET-02 — Phase-2 primitive contract (DRAFT — not frozen)

**Status: DRAFT. NOT FROZEN. No Phase-2 P&L may be computed before user sign-off.**
Gate: PRE-REG-BASKET-01 §7 ("Pre-register ONE release rule + ONE permissive survivor rule …
frozen before any Phase-2 P&L"; "User sign-off at each gate").

**SUPERSEDING CONDITION — SIP data upgrade (2026-09-18):** every number in this document is
derived from the legacy OHLCV tape, which the SIP certification panel (20 days, event-level)
showed to be materially wrong in a minority of cases: missing minutes, subsampled
single-print minutes, two confirmed scale/bad-print contaminations, 498 sub-minute
first-passage order flips on healthy-coverage symbols, and 15.4% of snapshots changing the
top-3 set. Certification verdict: **regenerate Phase-1 from SIP-derived bars**
(`factory/artifacts/basket/sip/SIP_REGENERATION_DECISION.md`). Consequently the constants
selected here (T\*=600, L=10, g=50) and all Phase-1 probabilities are **PROVISIONAL** and may
be re-derived once the SIP-regenerated anatomy exists. **This document must not be frozen
against legacy numbers.** Freeze stays PAUSED pending the owner's regeneration decision.

Inputs: `factory/artifacts/basket/agg/` (committed: `T1…T10`, `T4b_frontier.json`,
`T5_paths.json`, `T7_overnight.json`, `T9b_random.json`) + `THESIS-BASKET-01.md`
(§0 state machine, §2 posture, multi-survivor lock). All numbers below are descriptive
anatomy outputs; none is a P&L result.

Naming (fixed): **populations** are `A_open` and `B(T)`. **Arms** are `Arm-0`, `Arm-1`,
`Arm-2`. Arms never use the letters A/B/C anywhere in this document.

---

## 1. Identities (from anatomy; development-formulated choices flagged as such)

| Choice | Value | Justification / honesty note |
|---|---|---|
| Population (primary) | **B, T\* = 600 (10:00 ET)** | F(30,−10) by T: 0.186 (09:35), 0.256 (09:45), 0.255 (10:00), 0.246 (10:15), 0.232 (10:30), 0.199 (12:00) — a plateau 09:45–10:15 after a materially lower 09:35, then decay. Pooled top-3 overlap at 600→615 = **1.75/3** (`T1.json`, day-weighted over 51 months). LULD pauses cluster in the first 15 minutes (external literature, not mechanically verified here), so 10:00 is past the opening band window. **This is a development-formulated narrowing (PRE-REG-01 §7 permits anatomy-justified narrowing), not a pre-registered rule; confirmation can only come from reserved/forward data.** ≈92% of the RTH session remains at 10:00 (not two-thirds). |
| Population (co-primary) | **A_open** (rank at completed 09:30 bar by `open570/prev_close−1`; fill 09:31 bar open) | Coequal conceptually; both populations are always run and reported. Neither is dropped for looking better or worse. |
| N | **3** (unfilled/blocked slots = cash; empty baskets retained; denominators never exclude empty days) | Locked; top-1/3/5/10 remain anatomy-only information. |
| Release barrier L | **−10% from fill** | Pre-declared set {8,10,12,15}; requirement: **shallowest** L with pessimistic tail-preservation **Q_30(L) ≥ 0.80** at T\*=600, days=1065. Measured on the Phase-1 ladder {3,5,8,10,15}: L=8 → Q=0.743; **L=10 → Q=0.807**; L=15 → Q=0.946. **Disclosure: Q is H-dependent and this calibration is B/600-specific — A_open's Q_30(10) = 0.762 does not satisfy the same mapping.** L=10 is a shared B-calibrated rule; A_open's higher `F(30,−10) = 29.20%` shows the two populations must be judged against their *own* benchmarks. L=10 is retained as the owner's choice on that basis. |
| Giveback g (Arm-2) | **50% of post-fill running high** | Pre-declared set {40,50,60}. **The "clips ≤10% of tail members" justification is NOT established and must not be cited.** `T5_paths.json` rows are keyed only by month/set/stratum/stat (no population, no T): the figures −0.459 (MFE≥30) and −0.492 (MFE≥50) are medians of 51 monthly p10s, and 20/51 (resp. 22/51) months have monthly p10 < −0.50. g=50 is therefore frozen as an **owner-chosen permissive rule**, not a mechanically proven bound; no alternative value may be inferred from dev evidence. |
| Slot sizing | equal notional, 1/3 of the population sleeve; static (released capital never re-deployed) | Locked |
| Friction | **100 bps round-trip per filled slot** (entry+exit combined, charged once per slot including carries), **150 bps adversary**; gross reported alongside | Computable. Blocked/unfilled/cash slots pay nothing. `min(open, level)` exits already include adverse gap-through. |
| Assumed fill scale | full fill at touch at small notional; **capacity is not claimed** | Partial fills, participation limits and bar-volume limits are out of scope and must be labeled as such in results. |
| Evidence boundary | dev = 51 months (2021-02…2023-12, 2025-02…2026-05); reserved **2026-06..08 = one-shot, unused** | PRE-REG-01 §5 |

**Universe**: identical to PRE-REG-BASKET-01 §2 (broadest defensible PIT listed common
stock, price ≥ $1 at decision, causal only). Any operational narrowing is not applied here.

## 2. Entry semantics (exact)

* B: decision from the last completed bar with et ≤ 599; entry at the first bar open with et ≥ 600.
* A_open: decision from the completed et=570 bar; entry at the first bar open with et ≥ 571.
* **Gap definition**: `gap = fill_et − target_et` where target = 600 (B) / 571 (A_open).
  `gap ∈ [0,4]` ⇒ filled. `gap ≥ 5`, or no bar at/after target, ⇒ **blocked slot = cash**.
  No backfill, no replacement, no silent exclusion.
* Ranking frame: one row per ticker (dedupe by ticker, keep the latest completed bar ≤ T−1);
  ties broken by score desc then **ticker ascending**. Cross-population duplicate tickers are
  allowed and reported.
* A stale ranking bar (name halted before T) is *not* filtered from the frame; it surfaces
  naturally as a blocked slot when its entry gap ≥ 5.
* Halts: no LULD flags exist in the data (documented); the halt state is only observable as
  missing bars.

## 3. R1 — the ONE release rule (Arm-1 and Arm-2)

With `F` = fill price:

> **Release if any *completed* bar has `low ≤ 0.90 × F`.** Execution at the **next** bar's
> open; if that open is below the level, fill at the open — `min(open, level)`.

`min(open, level)` is a **pre-registered adverse execution-price convention**, not a claim of
realistic next-open execution. No time stop, no breakeven/LOCK, no partial, no trailing, no
volatility/volume condition. R1 is evaluated only on completed bars (1-bar lag); a breach on
the final bar of the session is non-executable and is never close-substituted (it becomes a
pending release, §5).

## 4. S1 — the ONE permissive survivor rule (Arm-2 only)

> **Additionally release if a completed bar's `close ≤ 0.50 × peak_post_fill`**, where
> `peak_post_fill` is the running maximum of *completed* bar highs since fill, initialised at
> the fill price. Order inside each completed bar: (1) evaluate R1; (2) evaluate S1 against
> the **prior** peak; (3) only then update the peak with this bar's high. A bar can never
> trigger from its own new high. Execution at the next bar open, `min(open, level)`.

S1 is an owner-chosen permissive variant. Its tail-preservation cost is **measured in Arm-2,
never assumed** (the earlier low-vs-close "a fortiori" claim is withdrawn; see §11).
No profit target, no +X% exit; there is no state "runner because +30%".

## 5. Forced flat, pending releases, involuntary carry

* **Session definition (mandatory):** the session's last bar is taken from an explicit
  **early-close calendar for the dev span**, built and committed in Phase-2 QA before any P&L.
  Derivation check: on early-close dates the data still contains bars to 16:00 (after-hours
  prints), so a volume/price sanity check per date is required; the timetable must be
  enumerated, not inferred silently. Known dataset artefacts: 2022-09-30 (last candidate bar
  909), 2023-03-10 (919), 2023-03-13 (949) are **truncated sessions**, flagged, not treated as
  half-days. If the calendar is not committed, the Phase-2 run is invalid.
* **Forced flat**: decision at the session's **penultimate** bar; exit at the session's
  **last** bar open. If a rule also triggers on that penultimate bar, the exit price is the
  adverse `min(open, level)`.
* **Pending release through a halt**: if a triggered release has no next bar in the session,
  it remains pending and executes at the **first executable print next session** (price
  `min(print, level)`); it is an **involuntary carry**, counted in capital occupancy and in the
  strategy's risk ledger. Multi-session halts: same rule. No resumption through data end:
  terminal value = last available close, flagged (`no_resumption`).
* The overnight shadow ledger (`T7_overnight.json`) stays diagnostic-only and never repairs
  the intraday result.

## 6. Arms (identical entries; only the rule set differs)

| Arm | Rules | Role |
|---|---|---|
| **Arm-0** | no release; hold to forced flat | mechanism comparator; its role is **attribution** (what the release changed), not a pass/fail benchmark for the thesis |
| **Arm-1** | R1 → survivors hold → forced flat | **primary confirmatory arm** (minimal faithful implementation: we pre-decide only what causes us to stop participating) |
| **Arm-2** | R1 + S1 → forced flat | pre-registered permissive-survivor variant |

All three are always reported together on the same day set. No rule is adopted because one
arm's pooled mean is higher; Arm-2 is adopted only under D2.

## 7. Accounting (locked)

CASH / PRINCIPAL RECOVERED ≠ ECONOMIC RISK REMOVED. Per (day, population, arm), tracked
separately: realized cash; marked (unrealized) exposure; capital released by released peers;
nominal risk; gap-adjusted risk; locked profits.

* Estimand: `B_{d,p,arm} = (1/3) Σ_{slots i=1..3} R_{dpi}(arm)`, computed **per population**
  (A_open and B are separate estimands; no combined portfolio is a verdict input — a
  diagnostic combined sum may be reported, labeled supplementary).
* Filled slot: net return from fill to its exit under the arm's rules (release, forced flat,
  or carried exit), 100 bps charged once per slot round trip.
* Unfilled/blocked slot: return 0 (cash), retained in the denominator.
* Empty days: retained.
* **Carry occupancy (no leverage):** a carried position keeps its slot on subsequent days for
  the same population; new entries only fill unoccupied slots, so total exposure never exceeds
  3 slots per population. Carried P&L is attributed to the originating basket-day until exit.
* Capital occupancy therefore = sum of occupied slot-days; the report includes slot-days,
  carry-days, and released-capital time.

## 8. Pre-registered decision rules (computable)

* **D1 (implementation-consistency diagnostic; not a verdict):** on **all dev days** (never
  only filled days), for each population, the share of days where ≥1 filled member reaches
  +30% (touch) **before its own release** under Arm-1, compared with that population's
  barrier-consistent touch ruler — B: `F(30,−10) = 25.54%`; A_open: 29.20% — and with the
  stricter measure **at-or-above-next-open sale of +30% (B: 18.50%)**, which is the
  realistic-exit version of the same event. The 30.5% barrier-free `exec_k_30 k≥1` is an
  **accessible post-fill excursion ceiling**, not a +30% executable exit. A material shortfall
  is investigated as an execution-layer issue first; if root-cause analysis shows the
  shortfall is unavoidable (inaccessible/marks-only tail), that **is** a thesis-level finding
  (THESIS §9), not an excuse.
* **D2 (Arm-2 adoption):** Arm-2 replaces Arm-1 only if, on the identical pooled dev day set:
  (a) mean `B_d` ≥ Arm-1's mean; (b) 10th-percentile `B_d` ≥ Arm-1's; (c) the top-5% of days'
  share of total positive P&L ≥ Arm-1's. Otherwise Arm-1 stands and Arm-2 is reported as a
  rejected variant. No tolerance constants; no per-arm worst-day cherry-picking.
* **D3 (thesis-level conditions; separate verdict roles):** dev (51 months) is the primary
  estimate (month-blocked, day-clustered bootstrap CI, fixed seed); the reserved months
  (2026-06..08) are a **separate one-shot** estimate; a combined estimate is supplemental
  only. Thesis-level negative if **both** populations show, in Arm-1, at 100 bps:
  (i) dev mean ≤ 0 **and** the day-clustered 90% CI upper bound < 0; **or**
  (ii) a mark-only tail — mean > 0 under touch-price accounting but ≤ 0 under executable
  accounting, with no arm variant passing; **or**
  (iii) runner-day majority negative — conditional on days with ≥1 +30% touch, mean ≤ 0 in
  > 50% of those days, with co-member loss preventing the 1/3 stake from paying.
  Reserved months alone never falsify; **dev-positive + reserved-negative = "not confirmed"**
  → no further claims and no re-point.
* **D4 (parameterization-level):** a different L, g, T\*, N, or split-day baskets is a **new**
  pre-registration requiring fresh unseen months. Dev results may not confirm a re-point.
* **D5 (kill vs refine):** "Arm-0 and Arm-1 both negative in dev" is *not* a kill by itself;
  audit D1 and the execution layer, then run the reserved months once under the frozen rule.
  Only D3 falsifies the thesis.

## 9. Degrees-of-freedom register

Frozen on sign-off: populations, T\* (600) + A_open, N=3, R1 (−10%), S1 (50%), entry/gap
semantics, event-order and carry rules, friction ladder, universe, arms. **Owner-decided
items** (not mechanics): T\*=600, L=10, g=50 — sign-off means accepting them as
development-formulated choices whose confirmation is reserved/forward only. Anatomy-only
information (top-1/5/10, T ladder, L ladder, H rulers) is never a Phase-2 knob. Grid search:
none.

## 10. Explicitly NOT in this document

Adds / scaling-in / re-entry / dynamic replacement / LAST-SURVIVOR event study (blocked until
this document is frozen and Phase-2 runs) / partials / overnight strategy / any target price.
The static-survivor comparator is mandatory for any later sizing work.

## 11. Change log

* 2026-09-17: drafted from the committed Phase-1 anatomy by the mechanical mappings
  (L: shallowest with Q≥0.80 → 10; g: clips ≤10% of tail → 50). Not frozen.
* 2026-09-17 (adversarial audit, pre-freeze): independent review found 12 fatal / 7 material /
  3 minor issues. Corrections applied: arms renamed (Arm-0/1/2) so they cannot be confused
  with populations; one exact per-population estimand with carry-occupancy (no leverage);
  complete event-order precedence table incl. R1/S1 collisions and forced flat; forced flat
  redefined relative to the session's actual last bar with a mandatory committed early-close
  calendar (half-day/truncation handling, incl. 2022-09-30, 2023-03-10, 2023-03-13 flags);
  pending-release-through-halt and no-resumption terminal values defined; friction unit,
  charge point and capacity posture made explicit; D1–D5 rewritten as computable rules with
  exact denominators, like-for-like per-population benchmarks, separate dev/reserved verdict
  roles and no tolerable-ambiguity wording; S1's unsupported ≤10% clip claim withdrawn and
  g=50 relabelled owner-chosen; A_open's Q_30(10)=0.762 and F=29.20% disclosed; the
  at-or-above-next-open +30% rate (18.50%) added; the "≈2/3 of session" and "overlap 2.1/3"
  claims replaced with measured values (≈92%, 1.75/3); the LULD statement marked external,
  not mechanically verified. Parameters unchanged (T\*=600, L=10, g=50) and are now
  explicitly owner-decisions. No Phase-2 P&L computed.
* 2026-09-18 (SIP data upgrade — freeze condition added): SIP certification over the 20-day
  panel showed the legacy tape is materially wrong in a minority of cases (missing minutes,
  subsampled single-print minutes, confirmed scale/bad-print contaminations, 498 sub-minute
  first-passage order flips, 15.4% of snapshots changing the top-3 set); certification verdict
  = regenerate Phase-1 from SIP-derived bars (SIP_REGENERATION_DECISION.md). All constants and
  probabilities in this document are marked PROVISIONAL at the top and must not be frozen
  against legacy numbers. Freeze remains PAUSED pending the owner's regeneration decision.
  No Phase-2 P&L computed.
