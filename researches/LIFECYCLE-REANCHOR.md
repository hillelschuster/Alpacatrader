# LIFECYCLE-REANCHOR — the research statement (2026-10-02, owner-directed reset)

This replaces the framing that produced the first LIFECYCLE-01 policy pipeline. The
measurements stay; the mental model, the objective and the architecture are restated here
and everything downstream is rebuilt from this text.

**Interpretation correction (2026-10-03 takeover).** The later Stage F/G conclusions
“entry is the binding problem,” “the pop completes before 08:30,” and “selection
variable is the problem” are **unsupported interpretations**, not measured facts.
Their negative fixed-endpoint cashflows falsify those implementations only. The
same rosters' large forward excursions leave harvesting unresolved; a high touch
also does not prove executable profit. Early-ramp selection remains a parallel
hypothesis, not a replacement for top-gainer optionality. Preserve ranks 1–5.
The window boundary remains a ruler: the monster anatomy contains later legs.
`personality_routes_*` uses last-close `captured`, whereas the executable roster
anatomy uses next-open outcomes; those quantities must not be pooled or relabelled.


## 1. The actual inefficiency

At an early causal moment, **the actual top gainers contain a wildly disproportionate share
of the day's future extreme winners.** On the corrected discovery half (533 days, clocks
09:00/09:20/09:29/09:31, fixed top-5, same gain anchor): 42–47 members per clock reach
MFE ≥ +100% and their **mean executable captured return is +109% to +119%** (day-block CI
+95%..+141%). That is the asset: an early, cheap claim on a fat tail that exists on roughly
8–9% of days. Nothing about the average member is the inefficiency — the option on the tail is.

## 2. Why the early basket exists

Because at T we **cannot** tell which horse wins. Measured: monsters appear at ranks 1–5 —
rank-4 members on the discovery half include AUVI (+152% captured), NRSN (+208%), KALA (+91%),
VGFC (+70%), GRNV (+37%). The top-5 is therefore not "five picks"; it is an **optionality
portfolio of competing claims**, and the uncertainty is exactly what gives the basket value.
Corollary: any ownership architecture (how many names may hold capital simultaneously, with
what weights, with what rotation) is a **discovery target to be proven economically**, not a
constraint inherited from a simulator default. The earlier `max_holdings=3` and "top-3
primary" are hypotheses, not truths.

## 3. Why unconditional hold is nearly antithetical to the strategy

The phenomenon is a **finite window of strength**: catalyst → climb → climax → relaxation.
Measured on the discovery half, the continuation ruler is negative beyond ~3 minutes at every
clock (−0.3% at 30 min, −0.5% at 60 min, −0.8/−0.9% at 120 min; occupancy and member-balanced
agree), and the median member closes −6% to −7% from the entry. The afternoon fade is the
**death of the phenomenon**, not evidence about the morning. Therefore:

* "Hold" is a **ruler / counterfactual**, never the strategy. It exists to price the window
  and to isolate what a release/re-entry action adds.
* "Is continuation positive for 60 minutes?" is a **local measurement**, not the objective.
* A fixed evaluation endpoint (noon, 13:00, close) must never silently define the economics.
  Exit timing is part of the policy, and the window is the natural value horizon.

## 4. What "personality / lifecycle" means

Each member has a **latent character expressed through many changing observables**, not a
single flag. The vocabulary we measure: gain level and acceleration; rank and rank velocity;
relative strength versus peers and versus the emerging field; new-high cadence and failed
pushes; recovery elasticity after damage; drawdown geometry; time spent above entry / below
entry / away from the high; volume and dollar-volume acceleration; print activity, intensity
and inter-arrival structure; liquidity and spread where the tape is trustworthy; breadth;
sibling behavior; leadership changes; premarket personality; opening reaction; and the
**state transitions** between all of these. "Exhaustion" is not `no new high in 10 min`;
"healthy" is not `drawdown < X`. These are latent personalities, and the **route** — how the
character evolves minute by minute — is the object of study. That is where sequence models,
trajectory retrieval and latent-state clustering may earn their place; not as winner-pickers.

## 5. How capital should dynamically follow evidence

The decision at every minute is: **where should the next dollar of capital be?** Candidates
are: this leader; another basket member; an emerging outsider; a partially realized position;
full cash; a resurrecting former leader. The answer may change minute by minute. Some days
justify 0 names, some 1, some 2, some 3, possibly more; a monster may deserve increasing
capital; a deteriorating rank-1 may deserve zero. The objective is the **highest realistic
executable EV of capital through the top-gainer window**, with two explicit obligations:
**preserve the actual right tail**, and **measure the dud tax** — every action is judged by
what it does to both. Classification accuracy, "winners identified", and benchmark deltas are
not objectives.

## 6. What the existing evidence genuinely teaches us (discovery half, corrected substrate)

* **Tail**: 42–47 monsters per clock over 533 days; E[captured | monster] +109..+119%;
  duds are 50–55% of members at −11..−13%. The tail is the only thing that can pay for the tax.
* **Route**: the median monster is already +3.4% at 09:31 and +24.6% at 10:01, plateaus
  +40..+66% through 10:20–11:20, takes a second leg into midday, peaks ~14:40 and closes +92%;
  its drawdown from the high stays −10..−18% throughout. The median runner: +0.8% at 09:31,
  +10.4% at 10:01, peak +35% around 11:31. The median dud: −10.8% captured, −19% max drawdown.
* **Separation**: a future +30% leg is separated by a *combination* — recovery-from-low
  (AUC 0.865), race gain (0.839), rank inverse (0.220), recent return (0.752), peak gain
  (0.753), print intensity (0.70) — while single-threshold flags have failed repeatedly in
  this project. The information exists; the naive rule shapes do not capture it.
* **State-conditioned value** (first-visit states, 533 days): the only positive personalities
  are **deep drawdown still actively repairing on expanding flow** (+0.44%/+0.57% at 30/60 min,
  112 independent days, P(+30% within 60 min) = 22.5%) and **shallow pullback with a fresh
  high** (+0.18–0.20% at 30 min, 500+ days). Everything else is negative. At a −15% drawdown
  event, the level relative to entry separates healthy from terminal *immediately* (+2.2% vs
  −3.2% at the event) and the gap widens to +17.8% vs −5.9% thirty minutes later.
* **Chronological ML**: rank-IC 0.043 (price-only) / 0.050 (race+volume+peers) / **0.078**
  (tape with spread and quote microstructure, on matched coverage) — weak, real, and the tape
  adds the most.
* **Friction**: measured spreads on the roster at entry are ~50–80 bps (rank 1–3) with round
  trips near 100 bps; the conditional edges above are the same order of magnitude. Therefore
  the policy must be brutally selective, and the tail must be preserved — harvesting the tail
  early is how every previous formulation destroyed its own economics.

## 7. Machinery that serves this objective (keep)

* **Corrected causal substrate** (`lifecycle/v2`): fixed top-5 at 09:00/09:20/09:29/09:31,
  minute state strictly completed-bar, executable next-open labels, UNKNOWN semantics,
  8.49M member-minutes, split locked 533/533 — the measurement foundation.
* **Tape layer**: 1-minute and genuine 5/10-second activity with explicit availability times,
  round-lot units resolved, coverage stated per symbol-day — the microstructure ground truth.
* **Behavior anatomy**: route by descriptor, separation timeline, pullback health,
  event-aligned healthy/terminal curves, strongest-path name/day evidence.
* **Replay engine**: cash/share conservation, delayed fills, partials, missing = UNKNOWN,
  explicit fees — re-purposed as a general capital-allocation simulator.
* **Leg/continuation maps**: state → continuation and tail probabilities — rulers.
* **Audits**: money-conservation and causal-leakage reviews — the invariant set.

## 8. Artifacts of the old framing (discard or redesign)

* **"Hold EV" as the central question** → replaced by "next-dollar allocation across the full
  action set", with hold retained only as a counterfactual.
* **Mechanical `max_holdings=3` and "top-3 primary"** → ownership architecture (count,
  identity, weights, rotation, re-entry) becomes a discovery dimension to be *proven*.
* **Fixed endpoints as evaluation horizons** → exit timing is part of the policy; the window is
  the value horizon and the fade is modeled explicitly as the phenomenon's death.
* **The state-table + LightGBM EV pipeline as the driver** → demoted to *instruments* that
  estimate the evolving personality; the strategy is the allocation policy over personalities.
* **"One winner per day" / a frozen roster as the strategy** → the roster is the entry set; the
  evolving opportunity set may include emerging outsiders and resurrected former leaders.
* **Any policy run, freeze, or second-half evaluation built on the old framing** → cancelled;
  the second half is spent once, on a mature thesis only.

## The redesigned discovery program (first 533 days only)

1. **Personality trajectories (A)**: for every member, build the minute-by-minute personality
   vector plus route history (trailing 5/15/30-minute trajectory features), grouped by eventual
   route type — monster, sustained runner, second-leg runner, transient spike, recoverable
   flush, fake recovery, exhausted leader, slow death, immediate dud, resurrection. Deliverable:
   for each route type, the **time-resolved separability curve** (when does it become
   distinguishable from causal state, and by which combination), not a single threshold.
2. **Next-dollar map (B)**: at each minute and each personality state, the marginal value of a
   dollar in each candidate destination — this name, a sibling, an emerging outsider, cash —
   including re-entry after release. Cross-name comparison (relative strength, leadership
   rotation) is first-class.
3. **Ownership architecture search (C)**: slot count, weights, release triggers, rotation,
   re-entry, and window exit as search dimensions, evaluated with the replay engine at measured
   friction; every candidate judged on executable EV, tail preservation, and dud tax together.
4. **Freeze and test once (D)**: only after A–C produce an architecture worth the clean half.

## Status of the superseded pipeline (cancelled, not deleted)

* `lifecycle_policy.py` discovery selection, `lifecycle_freeze.py` freeze, and the
  second-half validation path are **cancelled under the old framing**. The files stay on disk
  as evidence and will be redesigned (the replay/accounting parts survive; the "hold-EV
  driver" and the fixed 3-slot architecture do not).
* No freeze exists, no second-half outcome has been read, and no policy number is a result.
* Kept as rulers: the behavior anatomy, the leg/continuation maps, the chronological model
  rank-ICs, the measured friction, the strongest-path name/day evidence, and the audits.

## Discovery log — first pass results and the honest negatives (2026-10-02/03)

1. **Route census (569, 533 days)**: monster 2.4% (+77% captured median), second-leg runner
   3.8% (+40%), sustained 2.0% (+25%), recoverable flush 0.7% (+18%), resurrection 0.8%;
   **fake_recovery 34.2% at −17.9%** and immediate_dud 21.2% — the tax. ~9.6% of members
   carry the money.
2. **Route separability is high early but largely tautological**: monster AUC 0.80 at +10 m
   yet the best SINGLE observable is 0.84 — the labels are forward-defined rulers, so this
   stays descriptive and no classifier is deployed.
3. **Next-dollar ranking (chronological OOF)**: real cross-sectional skill (mean daily
   rank-IC +0.12 at 09:00, +0.10 at 09:20) but the ABSOLUTE level of fresh deployment at the
   PM clocks is negative (field −0.7% to −1.3% over 120 m). Net of 100 bps the top-1 is
   −0.63% (09:00) / −0.77% (09:20) over 383 OOF days. **No net edge.**
4. **RETRACTED — leakage**: an earlier fold definition trained and tested on the same days
   (days 300:533), manufacturing "+20.8% top-1%", "+1.5% top-1 net" and a "+1.16%/day
   architecture". With corrected two-segment OOF those numbers are void. The architecture
   results built on them are void.
5. **Architecture search (leak-free structural rules, 533 days, 100 bps)**: hysteresis is
   decisive (no-hysteresis thrash: −17.6%/day, 127 orders/day); with dwell 30 m + a 2% cost
   margin the *model* architecture was +0.8..+1.2%/day on the leaked scores — void. The
   **structural** anatomy rules on clean data: deep-drawdown-repairing **+0.007% (540) /
   +0.045% (560)** at 0.5 orders/day; shallow-pullback-with-fresh-high negative (−0.38% /
   −0.80%). Behavioural overrides (dead-claim release, runner keep) add nothing.
6. **Conclusion so far**: the tail is real and findable, but at a 100 bps round trip the
   fresh-deployment EV of every personality tested is ≤ 0. The binding constraint is the
   ENTRY COST. Next probe: earn the spread instead of paying it — a resting bid into a flush
   (the H025 shape), measured on this corrected substrate.

## Discovery log — second pass (Stage E + day level) and the session state

7. **Resting bid into a flush (Stage E, 540, 533 days)**: fill rates 30–81% with median fill
   delays 11–46 min, but EVERY cell is negative net: −0.57% to −4.84% at +60/+120 min and to
   the window end (best: −30% flush depth, −8% discount, +60 m −0.57%). The flush keeps
   flushing; passive entry does not fix it on this roster.
8. **Day-level conditioning (540, first 200 days)**: the entry-gain level correlates
   NEGATIVELY with the day's outcome (corr −0.18; high-gain days −5.9% mean captured vs
   low-gain −3.3%). No "trade only the biggest" edge. Note: PM clocks carry NO market-wide
   breadth/rank features — the board (`race.minute_full`) begins at 09:30, so a PM entry's
   personality is limited to its own path, its PM history and its siblings.
9. **Structural rules on the full discovery half**: deep-drawdown-repairing ≈ breakeven
   (+0.007%/+0.045% at 540/560, 0.5 orders/day); shallow-pullback negative.
10. **Session state**: the tail is real and measurable; every executable mechanism tested on
    the discovery half (model ranking, structural personalities, flush bids, behavioural
    overrides, day-level filters) is ≤ 0 net at a 100 bps round trip. The binding constraint
    is the cost of a fresh round trip vs edges of the same order. Second half UNTOUCHED.
    Next candidates, in order: (a) the near-open clock 571 with the full-market race features
    (rank/velocity/breadth/leadership) and leak-free folds; (b) outsiders/rotation — the
    emerging leader outside the entry roster; (c) multi-day/tail-riding formulations that
    only pay a single round trip for a monster that must be held through the window.

## Discovery log — third pass (Stage F): the release works, the entry is the problem

11. **Own early, release the dead, ride the runners (533 days, 100 bps, window end 13:00)**:
    the "fade" release (below entry AND >=15 min from the high AND losing over 5 m) beats
    unconditional hold by **+1.7 to +2.5 pp at every clock** — 540 −1.61% vs −4.14% (hold k3),
    560 −1.74% vs −3.54%, 569 −1.51% vs −3.56%, 571 −1.33% vs −3.26%. That is the dud tax
    being cut, reliably and consistently. Trail-only and hold are worse.
12. **But every absolute is still −1.3% to −1.7% per day.** The arithmetic: the equal-weight
    top-5 held to 13:00 is −3.5% to −4.1%; even the best release cannot repair a losing entry.
    The duds (57% of members, captured −11%) dominate; the monsters (2.4%, +118%) contribute
    only ~+2 pp. The ENTRY is the binding problem, not the management.
13. **Consequence for the program**: the entry must move EARLIER than the pop's completion
    (the PM path from 04:00 exists in `pm_snapshots` at 7 clocks for the full PIT universe),
    or the destination set must widen to outsiders/rotation, or the basket must be entered
    only on days whose premarket signature justifies it. No freeze; second half untouched.

## Discovery log — fourth pass: the entry-timing ladder (08:30 → 09:31)

14. **The entry clock barely matters, and earlier is worse.** Top-5 rosters at eight PM clocks,
    533 days, filled members, executable captured to the close:
    | clock | monster share | monster mean captured | equal-weight captured | median |
    |---|---|---|---|---|
    | 08:30 | 2.8% | +79% | −4.87% | −7.9% |
    | 09:00 | 3.1% | +78% | −4.21% | −7.4% |
    | 09:15 | 2.8% | +80% | −4.17% | −7.0% |
    | 09:29 | 2.4% | +92% | −3.64% | −5.8% |
    | 09:31 | 2.6% | +85% | −3.50% | −5.9% |
    The tail exists at every clock (2.4–3.1% monsters) but so does the tax, and the average
    member loses ~4% from ANY PM entry to the close. **Conclusion: the pop accumulates before
    08:30 (04:00–08:30 / overnight), so the entire 08:30–09:31 ladder is too late to own the
    move — the entry problem is not timing within the ladder but the ladder itself.**
    Next: select at an early premarket time (04:00–06:00) using the PM aggregates that already
    exist in `pm_snapshots` (pm_first_et/px, pm_hi/lo, pm_vol, 7 clocks), which requires bars
    for the premarket-selected names (a bounded Alpaca fetch).

## Discovery log — fifth pass (Stage G): the ramp is dead; the selection variable is the problem

15. **The premarket ramp probe (discovery half, causal top-5 at 04:30→08:30, executable
    entries, full-market PM bars)**:
    | clock | mean captured to close | mean to 09:30 | mean MFE to 13:00 | monster share |
    |---|---|---|---|---|
    | 04:30 | −8.60% | −5.76% | +22.6% | 3.2% |
    | 06:00 | −8.68% | −5.82% | +24.0% | 2.8% |
    | 07:30 | −5.65% | −2.45% | +27.1% | 3.4% |
    | 08:30 | −4.66% | −1.20% | +28.2% | 4.1% |
16. **Earlier is monotonically WORSE.** Names that are top gainers at 04:30 lose ~5.8% into
    the open and ~8.6% to the close; the same is true at every clock to 09:31. The monsters
    are in the roster at every clock and the excursions are large (+22–28% mean MFE to 13:00),
    but the roster as a BUY loses at every causally observable moment from 04:30 to 09:31.
17. **Root cause, stated plainly**: the selection variable — the gain versus the prior close —
    is itself the evidence that the move has already happened. Selecting "the biggest gainers
    at T" selects names that are PAST their pop, and the roster's bleed (57% duds) is the
    mean-reversion of a completed move. This does not contradict the phenomenon; it contradicts
    the entry rule we have been using to rent it.
18. **Consequence**: the frontier is no longer "which clock" or "how to manage". It is
    **selection on the EARLY RAMP rather than the gain level** — acceleration, first new highs,
    the first minutes of a move, before the gain is large — or a different mechanism entirely
    (the excursion structure, the flush-recovery bid, the leadership rotation). No freeze; the
    second half remains untouched.

## Takeover audit addendum (2026-10-03; historical claims qualified)

- `lifecycle_preramp.py:109-113` computes MFE/MAE from bars before the actual
  entry too: it imposes the upper horizon but not `et >= entry_et`. Its reported
  +22–28% numbers and monster shares are **not forward-only excursion evidence**
  and are quarantined. Terminal-return negatives do not repair that defect.
  The new first-push diagnostic uses post-fill completed states and next-open sales.
- The 41–47 descriptor “monsters” require both MFE >=100% and terminal captured
  return >=50% (`lifecycle_study.descriptor_table`), not MFE alone. The personality
  taxonomy uses a different definition and close marks. Do not mix their counts,
  returns or inferred day-level tail contributions.
- PM ranking excerpts labelled −0.63%/−0.77% “net” in the historical log are
  gross; the corresponding +120m net values are −1.63%/−1.77%. Conversely,
  near-open `nextdollar_569.md`/`571.md` report +0.60%/+0.53% net at +120m on
  233 days. These are averages of overlapping per-minute candidate returns, not
  portfolio P&L or a promoted edge; blanket “every metric is negative” is wrong.
- Existing `manage_attribution` never emits because replay member rows omit the
  `route` column tested by its guard. Tail/dud attribution is supplied by the new
  diagnostic, explicitly window-bounded and separate from deployed policy state.
- Lifecycle evaluation is unrun and this takeover read no protected outcomes.
  Earlier HARVEST01 work already reported economics across all 1,066 days; the
  lifecycle scoring half must not be described as globally pristine market evidence.

