# SWARM SYNTHESIS — 2026-09-24 (interpretation reset)

Status: in progress. This file records the multi-agent investigation launched after the
C1-corrected baselines and the first capital-sequencing diagnostics, plus the parent
measurements taken while the swarm ran. It is the input to the next research decision; no large
branch is launched before this is reviewed.

Read `researches/INTENT.md` "Research philosophy" first: evidence, interpretations and rulers are
separate objects. Everything below is one of those three, labelled.

---

## 1. Parent measurements taken during the swarm

### 1.1 The post-touch population is a mixture, not a fade [OBS]

`factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/touch_mixture.json` — A_pm top-3 anatomy
fills, 1,066 dev days, first +30% touch on a completed bar, 582 touchers over 470 days:

| group | share | post-touch max (mean) | touch-bar close → session close |
|---|---|---|---|
| continuers (post-touch max ≥ +10%) | 60.3% | +52.3% | mean **+9.2%**, median −3.3% |
| faders (post-touch max < +10%) | 39.7% | +4.0% | mean **−19.7%**, median −19.4% |
| all touchers | 100% | — | mean −2.2%, median −10.8% |

Reading [INFERENCE]: the earlier "median toucher gives back 11%" is real but it is the median of a
bimodal mixture. The unconditional post-touch mean is ≈0 because +9.2% × 0.60 and −19.7% × 0.40
roughly cancel. Selling every toucher is therefore close to **mean-neutral variance reduction**,
not the capture of a systematic fade.

### 1.2 Continuation is not identifiable from coarse touch-moment state [OBS]

`factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/continuation_identifiability.json` —
single-feature separation between continuers and faders at the touch bar:

| feature | continuers (mean) | faders (mean) | AUC |
|---|---|---|---|
| rank | 2.06 | 1.92 | 0.574 |
| 5-bar velocity | +16.3% | +13.5% | 0.573 |
| drawdown from running high | −2.7% | −3.1% | 0.551 |
| return entry→touch | +31.0% | +30.2% | 0.538 |
| halts so far | 2.9 | 5.0 | 0.460 |
| minutes since fill | 81 | 99 | 0.434 |
| volume ratio (touch bar / session mean) | 3.10 | 3.67 | 0.449 |

Reading [INFERENCE]: no coarse minute-level path feature separates the two outcomes; the
strongest are weak (AUC ≈ 0.57). The only dimensions that even lean in a sensible direction are
*timing and halt count* (earlier touch, fewer halts → more likely to continue), not the
price-path shape. If continuation is predictable at all, it must come from finer data
(trade/seconds microstructure, quote behaviour, halt sequencing) or from cross-sectional state —
not from the minute-bar state we have been conditioning on.

### 1.3 The HARVEST gain is exposure removal, not a better exit price [OBS]

From the arms' own artifacts (`HARVEST_DIAG/SUMMARY.json`): hold N=2/100 bps mean −3.938% on
time-weighted deployed capital 1.154 (−3.41% per unit of exposure-time); the 100%-exit-at-+30%
arm −3.044% on 0.857. Pricing the removed exposure at the hold arm's per-exposure rate predicts
−2.92%; the realized arm is 12 bps *worse* than that.

Reading [INFERENCE]: the +89 bps/day is ≈ +101 bps of exposure removal and ≈ −12 bps of
price/selection effect. The touch is not a superior exit price; the gain is simply less time
owned. This is consistent with §1.1: the mean post-touch drift is ≈0, so exiting at the touch
cannot be a large price edge.

---

## 2. Agent findings

(to be merged as the swarm reports land; each idea carries mechanism, plausibility, evidence,
what prior code actually tested, bugs found, smallest decisive study, and what a meaningful
result would look like)

---

## 3. Ranked next moves

(to be filled after §2)

---

## 4. Preserved disagreements

(to be filled after §2)

### 1.4 The fade is conditional on WHEN the touch happens (and mildly on halt count) [OBS]

`factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/touch_halt_time_splits.json` — same 582
first +30% touches, split by causal observables at the touch bar:

| touch time | share | touch-bar close → session close | continuation share |
|---|---|---|---|
| before 10:00 | 41.6% | mean **−9.3%**, median −18.3% | 0.649 |
| 10:00–11:00 | 28.7% | mean +1.1%, median −12.9% | 0.581 |
| 11:00–13:00 | 14.3% | mean **+6.4%**, median −4.7% | 0.590 |
| 13:00+ | 15.5% | mean +2.6%, median −0.7% | 0.533 |

| halts so far | share | touch-bar close → session close | continuation share |
|---|---|---|---|
| 0–1 | 71.5% | mean −2.1%, median −11.0% | 0.625 |
| 2–3 | 12.5% | mean −10.7%, median −14.5% | 0.548 |
| 4–5 | 5.0% | mean +0.4%, median −13.8% | 0.586 |
| 6+ | 11.0% | mean **+5.6%**, median −5.9% | 0.531 |

Reading [INFERENCE, caveat added in §1.5]: the population that fades is the *early* toucher
(before ~10:00), which is also the majority of touches; later touches show a positive *mean* — but
§1.5 shows that mean is tail-driven and does not survive removing a handful of observations, so
treat the later-touch "drift" as unproven. More halts before the touch
also leans positive on the mean (median stays negative everywhere). This makes the unconditional
"sell every +30 touch" rule a mean-neutral average over two economically different regimes, and
suggests the harvest/de-risk decision should be conditioned on *when* the excursion happens —
a causal, mechanism-motivated split (opening attention flush versus sustained repricing), not a
fitted threshold. It also weakens the earlier "the morning is the worst segment" framing: the
morning is worst for *continuation after an early spike*, not because the afternoon is uniformly
better.

### 1.5 The apparent afternoon continuation is a tail lottery, not drift [OBS]

`factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/afternoon_touch_continuation.json` — pooled
over three entry families (A_pm, A_open, B600 top-3 fills), first +30% touch at or after 11:00 ET:
n = 562 observations over 364 days, mean +1.3%, **median −3.6%**, positive share 38.8%, and the
**top five observations account for 202% of the total sum** (mean excluding the top five:
−1.3%). By family: A_pm +4.4% (n=173), A_open +3.5% (n=160), B600 −2.6% (n=229); half-hour buckets
are unstable (+2.9%, +0.8%, +4.1%, +0.4%, −3.2%, ...).

Reading [INFERENCE]: there is no afternoon continuation drift to harvest. What looks like a
positive mean is a handful of giant survivors, exactly the pattern that has failed collision
testing repeatedly in this project. This also demotes the §1.4 "late touches carry positive
drift" observation to "late touches contain more of the lottery", and reinforces the standing
power doctrine: tail-driven means from a few observations are not evidence.

### 1.6 Peak-relative exits improve EV per deployed dollar; entry-relative stops do not [OBS]

Per-deployed-dollar EV (sleeve mean / time-weighted deployed capital) across the corrected
release families, 100 bps, A_pm/N=2:

| policy | sleeve mean | deployed | EV per deployed dollar |
|---|---|---|---|
| R0 hold (F3/F4) | −3.94% | 1.154 | −3.41% |
| R2(L10,w5) full exit | −2.84% | 0.717 | **−3.96%** |
| R2(L10,w5) 50→rest | −2.93% | 0.733 | −3.99% |
| R3(g40) full exit | −3.43% | 1.086 | **−3.16%** |
| R3(g40) 50→rest | −3.34% | 1.087 | **−3.08%** |

Correction (added after §2.1): `avg_deployed` is inflated for arms holding never-resuming
carried tickets (phantom exposure), so the *magnitudes* above shift (hold's deployed ≈ 1.00 rather
than 1.154 after removing 0.153 of phantom; R2 also carries two such tickets). The *ordering* is
unchanged: R3 improves EV per deployed dollar relative to hold, R2 does not.

Reading [INFERENCE]: R2's apparent +110 bps/day is entirely exposure-time reduction — it cuts how
long the sleeve is owned, and per dollar owned it is *worse* than doing nothing. R3, which exits
only when a ticket closes below its own running peak (an MFE-surrender event), improves EV per
deployed dollar while barely changing exposure time (−3.16% / −3.08% vs −3.41%). So the
information that pays is **peak-relative** (how much of its own best excursion a ticket has given
back), not entry-relative depth, and not blanket de-risking. This is the same variable family as
the F6 state rule (retained ≥ 2/3 of MFE), which was ≈0 as a *deployment* rule — so the open
question is why peak-relative state pays as an *exit* but not as an *add*.

---

## 3. Ranked next moves (parent draft; to be revised with the agent findings)

Ranked by expected economic information and potential EV unlocked, not by family number or ease.

**M1. Peak-relative state as the primary decision variable (exit side proven, add side not).**
Mechanism: what a ticket has given back from its *own* best excursion is the one state variable
that has improved EV per deployed dollar anywhere in the corrected results (R3, §1.6), while
entry-relative depth (R2) and blanket de-risking only remove exposure. Smallest decisive study:
replace the fixed `g` ruler with a state-dependent retention boundary (halt count, elapsed time,
velocity at the breach) and measure per-dollar EV against R3 rulers and hold, both blocks, both
frictions. Meaningful result: per-dollar EV materially better than R3's −3.08% and not confined to
one block. Contradicting evidence to respect: the F6 state rule used the same variable family as a
*deployment* rule and landed at ≈0, so the exit/add asymmetry itself needs explaining.

**M2. Is continuation identifiable at all? (the bottleneck question).**
Mechanism: the post-touch population is bimodal (§1.1) and no coarse minute feature separates the
branches (§1.2); if nothing finer does either, per-ticket timing is not where the money is.
Smallest decisive study: a causal identifiability study on finer data — trade-level/seconds
microstructure and halt sequencing around the excursion, raw SIP available — measured on unseen
months with an explicit EV-separation metric, not accuracy. Meaningful result: either a stable
conditional EV gap (which converts directly into a hold/sell policy) or a clean negative that
redirects the program to portfolio-level policies.

**M3. Excursion-timing regime split (early versus late touches).**
Mechanism: early touches (before ~10:00) fade; later ones contain the lottery (§1.4, §1.5).
Smallest decisive study: one pre-registered two-bucket harvest/de-risk rule with per-dollar EV and
block splits, plus the tail-concentration check that already demoted the late bucket. Meaningful
result: a conditional policy beating both unconditional sell-all and hold in both blocks; or
confirmation that the split is noise.

**M4. Cross-sectional / basket-relative state.**
Mechanism: capital allocation is a portfolio decision; the race between members (rank migration,
relative MFE, dispersion, handoffs) is observable and untested as a decision variable.
Smallest decisive study: a causal switching test on EXT-1 machinery with per-dollar EV as the
metric. Meaningful result: a reallocation rule that beats equal-weight ownership in both blocks.

**M5. Entry-side cost (the fill is the worst price of the day).**
Mechanism: the sleeve's hole is created at entry and in the first 30 minutes; the anatomy fill is
the 09:30 open. Smallest decisive study: resting-limit entry versus market entry on the same
population with explicit fill rules. Meaningful result: a per-dollar EV shift comparable to the
~250 bps hole, or evidence that limit entry simply misses the survivors.

**M6. Microstructure microscope (feeds M2).** Trade-level inspection of a small archetype set
(giant runner, violent-damaged recovery, quiet death, multi-survivor day) before any
seconds-level modelling.

**M7. Truth-critical audits** (engine semantics, diagnostic producers, manual dollar tracing,
ledger-vs-artifact audit) — required before any promotion, but not alpha-generating.

### 1.7 Cheaper entry via a resting limit is refuted as formulated [OBS]

`factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/entry_limit_probe.json` — 3,187 A_pm top-3
fills, limit placed at the 09:30 fill price minus L%, first 30 minutes only, fill assumed at
min(open of the touching bar, level):

| limit | fill rate | filled: entry→EOD mean | unfilled: entry→EOD mean |
|---|---|---|---|
| −2% | 84.7% | −2.6% | **+9.7%** |
| −5% | 70.7% | −2.2% | **+8.7%** |
| −10% | 47.5% | −2.7% | **+6.5%** |

Baseline (market entry at the fill): −2.5%; MFE from the fill: +21.3%.

Reading [INFERENCE]: the limit does not improve the economics of what it buys (−2.2..−2.7%, same
as baseline) and it systematically avoids the names that run: the names that never dip 2% in the
first half hour average +9.7% to the close. A dip-based entry is therefore a selection against the
strongest tickets, not a cost saving. (Not tested: a limit placed later, or entry after a dip and
reclaim — a different mechanism that would need its own causal definition.)

---

## 2. Agent findings

### 2.1 TouchHarvestChallenger — the "sell into strength" reading is fragile and partly mis-attributed

Mechanism claimed: a +30% intraday excursion marks the end of the ramp, so selling there captures
a regime flip. What the agent verified [OBS]:

* **Identity, exactly.** For every ticket, `net_hold − net_all30 = (exit_px_hold − exit_px_all30)·(1−s)/(entry_px·(1+s))`
  (max error 4.4e-16 over 2,125 tickets). Non-touchers are unaffected by the rule by construction
  (sum of delta exactly 0). So the entire arm effect is an *exit-price difference on the 18.2% of
  tickets that touch* — there is no separate "de-leveraging" channel in this engine (no
  reinvestment, no cash return).
* **Who owns the loss.** Touchers: +48.0 sleeve units (+24.9% mean net return/ticket, 18.3% of
  dollar-minutes, +6.75 bps/min). Non-touchers: −90.0 units (−10.4%/ticket, 81.7% of
  dollar-minutes, −2.83 bps/min) — 214% of the arm's loss. The harvest rule touches none of that.
* **Magnitude fragility.** +89.4 bps/day is a difference of two bookends: excluding the two worst
  touchers (ISPO 2022-02-17, COSM 2022-12-16, 0.5% of tickets) it is +137.9 bps/day; those two
  alone cost −48.5. Month-blocked bootstrap CI95 of the paired daily delta is [−20.3, +183.9]
  bps/day (N=2), block1 t=0.48 vs block2 t=2.31 — a block-2 phenomenon.
* **Execution is favourable, not conservative.** `HarvestAt` returns `level=None`, so the exit is
  the raw next-bar open, which sits **+85 bps mean above** the +30% level (53.4% of fills at or
  above the level) — +15.4 bps/day of the +89.4. A resting limit at the level would *earn less*.
  The parent artifact `exit_convention_comparison.json` states the question with the sign
  inverted relative to its own numbers; that file needs a correcting note.
* **State effect at a fixed clock is real.** At the completed ET600 bar, tickets that have touched
  +30% bleed −1.97 bps/min versus −0.45 bps/min for untouched tickets (4.3×), and a high-but-sub-30%
  untouched ticket does *not* fade at 10:30 (+0.47%). So the touch carries genuine state
  information — but it identifies the *second-worst* exposure class, not the worst.
* **Clock arms are not redundant with it.** exit580/600/630 earn +246/+145/+126 bps/day, mostly on
  the non-touch cohort (+470/+326/+226), while losing on touchers; the harvest earns on touchers.
  Same tickets, same minutes removed, opposite sign (exit630 on the toucher cohort: −100.5 bps/day).
* **Engine finding (truth-critical).** `avg_deployed_capital` is inflated for arms that keep a
  never-resuming carried ticket open: a name certified `no_bars` in every later session can never
  execute its forced-flat pending, so its cost basis keeps counting as deployed until the
  block/data-end terminal mark (hold arm: ASPA 2023-10-25, GATE 2025-04-01 → +0.153 phantom
  exposure; the harvest arm has none). Any cross-arm exposure decomposition built on
  `avg_deployed_capital` is therefore invalid; the parent §1.3 split is corrected to roughly
  +55 bps/day exposure at the basket-average rate vs +35 bps/day state premium (and at a
  time-matched baseline, +25 vs +64). Also: `avg_deployed` counts *cost basis*, not market value,
  so it understates the risk of a ticket that is already up a lot.
* **Reproducibility gap.** The parent producers for `touch_fade_paths.json`,
  `touch_mixture.json`, `touch_halt_time_splits.json`, `entry_limit_probe.json` and
  `exit_convention_comparison.json` were inline scripts, not committed files; the agent could not
  read their conventions (it cross-checked against engine tickets instead). Fix: commit those
  producers (done for the diag family; the fade/mixture/limit scans still need it).

Decisive test proposed [HYP]: a **time-matched identity-shuffled placebo** — reuse the harvest
arm's own realized (ticket → exit et) multiset, shuffle it within each day across the sleeve's
tickets, and run the same cells. Prediction +20..+30 bps/day (pure time-in-market) versus the
harvest's +89.4; falsified if the placebo reaches ≥+70 bps/day. Economically meaningful threshold:
the state component (harvest − placebo) clearing +50 bps/day with the same sign in both blocks —
today it does not (block1 +21, block2 +204).

### 2.2 PathStateThinker — a causal state vector with provenance, and where it is missing

[OBS] Coordinates that exist today and are causal: `ret_from_fill`, `mfe_so_far`, `mae_so_far`,
`pos_in_range_fill`, `dd_from_running_high`, `mfe_surrendered`, `time_since_running_high`,
`breach_duration_bars`, `reclaimed_entry`, `recovery_speed_bars_from_low`, `failed_reclaim`,
`new_high_count`, rank/rank-change/rank-stability, relative strength, basket dispersion and
breadth (F2_F12 panel, 182,094 obs × 51 cols at ET580/585/590/600/615; F3 structural map, 673,952
rows for the A_pm top-2 own paths). Defects found [OBS]: F2_F12's `time_below_recent_high`
normalises by *clock* minutes since fill (re-introduces the clock; F3's bar-count form is the
clock-free version); F3 has no `mae_to_date`; F3's `entry_et` is 570 for 2,112 of 2,125 groups
(coverage defect, not semantics); F2_F12 has no MFE-surrender column.

[INFERENCE] The one coordinate that is monotone in forward value in both blocks *and* survives
matching on remaining exposure is **`retained = close_t / running_peak − 1`** (equivalently MFE
surrender) — the same variable family the parent §1.6 found pays as an exit (R3) and does not pay
as a deployment rule (F6 state67). The agent's proposed shape-revealing studies: continuation
hazard versus `retained` with path progress (bars since fill / bars since running high) as the
time coordinate instead of wall clock, and the same for the basket-relative coordinates.

### 2.3 Truth-critical queue opened by the swarm (not alpha, but blocking precision)

* **Frozen carries inflate deployed capital.** A carried ticket whose ticker is certified `no_bars`
  in every later session can never execute its forced-flat pending, so its cost basis keeps
  counting in `deployed_avg`/`deployed_end` until the block/data-end terminal mark (observed:
  ASPA 2023-10-25, GATE 2025-04-01). Smallest fix: after a declared number of consecutive
  certified `no_bars` sessions (or at the block boundary), terminalize the ticket with an explicit
  `NO_RESUMPTION_MARK` kind, exclude it from `deployed_*`, and report the count separately.
* **`avg_deployed` is cost basis, not market value** — a ticket already up 100% carries twice the
  risk that is counted; any exposure-removal arithmetic on rules that act after large moves is
  biased. Fix: also report market-value-weighted deployment.
* **Parent artifact conventions must be readable.** The fade/mixture/halt/limit scans were inline
  scripts; their producers must be committed (the diag family already is) or the numbers are not
  reproducible from the repo.
* **`exit_convention_comparison.json` states its question with the sign inverted** relative to its
  own result (next-open is *above* a resting limit at the level, i.e. favourable). Needs a
  correcting note in the artifact or a re-run with the honest framing.

### 2.4 HoldConvexityChallenger — the conditional-hold case is suggestive but unproven

[OBS] Confirms the mixture (A_pm: 582 touchers, 60.3% continue, 39.7% fade) and the time split
(<10:00 −9.29%, 10:00–11:00 +1.11%, 11:00–13:00 +6.35%, 13:00+ +2.60%). Halt strata are
non-monotone in continuation probability and mostly negative in median. [INFERENCE] The late
positive mean is a right-tail lottery (independently confirmed by §1.5: pooled afternoon mean
+1.29%, median −3.57%, top-5 = 202% of the sum). [OBS] The only *executable* policy actually run
is the unconditional all-+30 exit at the next bar open, and it beats hold-to-close in both blocks
(+0.894%/day pooled, +0.344% block1, +2.110% block2). Verdict: **a conditional hold has not been
shown to beat unconditional selling**; it needs one event-level, next-open, day-clustered policy
comparison (the same machinery as the harvest arm, with the condition as the only change).

### 2.5 RankMigrationAnalyst — rank/relative-strength state exists but has never been tested as a decision

[OBS] Defects in the current state panel worth fixing before any switching study:
`rank_change` is not a true adjacent-checkpoint migration; `rel_strength` is a current
open-to-checkpoint return residual, not a peer-relative MFE; rank stability exists in the panel
but is absent from the capital map; and `basket_score_disp` is empty because of a wrapper bug in
the producer. Rank *level* shows the clearest gradient (B585/B600). What has been tested: F6's
`StateReservePolicy` redeploys reserve on **own-state** (ret_from_fill, retained own MFE) — no
peer-relative or rank-migration condition has ever been run through the engine; F9 tested initial
fixed-gross reweighting, not dynamic switching. Minimum useful study proposed: a bounded
A_pm/N=2 ET600 paired replay with a control, one fixed rank-migration switch and one fixed
peer-MFE-residual switch, using the EXT-1 batch hook and per-dollar EV as the metric.

### 2.6 FailureCollapseAnatomist — no verified early death marker; the decisive study is specified

[OBS] No pre-standard-stop marker currently shows a block-stable error-cost advantage at 100 bps.
The F3 structural map does show large raw rebound opportunity even in deep/long-damage states, but
it is (a) not executable, (b) weighted by path rows rather than ticket events, and (c) produced
under the pre-C1 contract while the F3 surface is now C1 (provenance mismatch to note, not a
semantic conflict for same-session raw paths). [OBS] The corrected R2(L10,w5) cell carries pooled
false-release rates 13.3% / 16.0% / 13.5% at +30/+50/+100 — i.e. roughly one in seven of the
tickets it releases would later touch +30%. [OBS] Named counterexamples: VS (2023-12-29, damaged
then recovered), AVTX (quiet strength that later died), EYEN (2025-02-03, raw intraday loss
breached −10% while checkpoint closes stayed above the R2 close threshold and the day ended
negative), HCAI (2026-04-13, −10.5% peak drawdown at ET595 → +16.1% EOD). [HYP] The study that
would settle it: one row per ticket with (state stratum at the decision bar, subsequent
continuation, the error cost of cutting versus holding at 100 bps), which does not exist today —
the structural map is path-row weighted, so its "rebound opportunity" cannot be converted into an
error-cost comparison without rebuilding it ticket-level.

### 2.7 Own measurement — the time-matched identity-shuffled placebo (100 bps)

Producer: `factory/scripts/basket_diag_placebo.py` (committed); artifacts:
`factory/artifacts/basket/phase2/PLACEBO_DIAG/placebo_N{2,3}_bps100{,_s11,_s12}.json`.

Design: take the harvest arm's own realized exit-time multiset (A_pm, all-+30, 100 bps), shuffle it
*within each day* across that day's exiting tickets, and run the engine with a rule that fires EXIT
one completed bar before its assigned exit et (same next-bar-open convention). Ticket count is
identical across arms (2,125), so entry decisions are arm-independent and only the exit identity
changes; three shuffle seeds per N.

| arm | mean/day | Δ vs hold | block1 Δ | block2 Δ |
|---|---|---|---|---|
| hold (N=2) | −3.938% | — | — | — |
| harvest all-+30 (N=2) | −3.044% | **+0.894%** | +0.344% | +2.110% |
| placebo N=2, seed 20260924 | −3.704% | +0.233% | −0.047% | +0.854% |
| placebo N=2, seed 11 | −3.779% | +0.159% | −0.133% | +0.805% |
| placebo N=2, seed 12 | −3.568% | +0.370% | +0.247% | +0.642% |
| harvest all-+30 (N=3) | −2.836% | **+0.661%** | +0.290% | +1.400% |
| placebo N=3, seed 20260924 | −3.641% | −0.144% | −0.115% | −0.208% |
| placebo N=3, seed 11 | −3.339% | +0.158% | +0.154% | +0.165% |
| placebo N=3, seed 12 | −3.253% | +0.243% | +0.147% | +0.456% |

Exposure is matched within 2% on held minutes (placebo 683,882 vs harvest 670,266 for seed
20260924) and the placebo fires 2,122–2,123 of 2,124 scheduled exits, i.e. it does almost exactly
the same *time-in-market* removal. Result: **the time channel earns +0.16…+0.37%/day at N=2 (18–41%
of the harvest gain) and −0.14…+0.24%/day at N=3 (−22…+37%), with sign instability across seeds.**
The bulk of the harvest edge (roughly 60–80%) is the **identity** of the tickets that exit —
selling at a locally high print — which is the state information the harvest claim adds. This is
the decisive test §2.1 proposed; it decomposes the harvest reading rather than overturning it, and
it kills "exposure removal" as the primary mechanism (consistent with §2.1's note that the engine
has no reinvestment and no cash return).

Caveats found while checking the placebo:
1. `avg_deployed_capital` is **not** a pure same-day exposure measure — it differs 18% between
   placebo and harvest (1.015 vs 0.857) while held minutes differ only 2%, because an arm that
   closes tickets also removes their basis from *later* days' carry accounting. Per-deployed-dollar
   ratios (§1.6, §2.1) are therefore sensitive to carry composition, not only to time-in-market;
   the held-minutes integral is the honest exposure measure.
2. **Engine defect found and fixed** (`factory/scripts/basket_sim.py`): the run fingerprint bound
   only `rule.name`, so two runs sharing a run id but differing in rule *parameters* (e.g. a shuffle
   seed) shared a fingerprint and the second silently returned the first's cached summary — my
   first three seeds produced byte-identical numbers in ~1 s each before the fix. `ReleaseRule` and
   `ScaleInRule` now expose `signature()` (default `name`) and the fingerprint uses it; a
   parameterized rule MUST override it. Verified: same run id + different seed now recomputes
   instead of reusing the cached summary.

### 2.8 Additional truth-critical items (verified this cycle)

1. **Two dead columns in the F2_F12 state panel.** `basket_score_disp` is NaN in all 182,094 rows
   (verified) because `basket_f2_f12.py:119` reads `x["sel"]` from the `members` wrappers
   (`{"name": …, "bars": …}`) instead of `x["name"]["sel"]`; `spread_state` is hard-coded `np.nan`
   (`:164`). The capital map lists both as features, so any "no relationship" reading for them is
   vacuous. Smallest fix: `x["name"]["sel"]`, and either implement or drop `spread_state`; then
   re-run F2_F12 (code and artifacts must move together — do not patch the code without the
   re-run).
2. **`rank_change` is not an adjacent migration** and `rel_strength` is a current
   open-to-checkpoint return residual, not a peer-relative MFE (§2.5). Any rank-migration study
   must rebuild both from raw snapshots, not reuse the panel columns.
3. **Frozen-carry deployment inflation** (§2.1): a carried ticket certified `no_bars` can never
   execute its forced-flat pending, so its cost basis keeps counting in `avg_deployed_capital`.
   Smallest fix: terminalize after N certified `no_bars` sessions (report as
   `NO_RESUMPTION_MARK`), or report `avg_deployed` excluding frozen carries.
4. **F3 structural map provenance**: built under the pre-C1 contract while the F3 surface is C1;
   path-row weighted rather than ticket-event weighted, so its rebound opportunity cannot be read
   as an error-cost advantage (§2.6).
5. **Execution-convention framing** on `exit_convention_comparison.json` corrected in place
   (§2.1): the next-open exit is above the level, i.e. favourable, not conservative.

### 2.9 ThesisReconstructor — lineage, drift list, and what has never been tested in its stated form

**Lineage [OBS]:** project attention/lifecycle thesis (`factory/RESEARCH_GOAL.md`) → H1–H12
(selection/schedule/gate formulations; H11 collision failure → power doctrine; H12 OOS failure) →
H025 (separate flush mechanism, genuine OOS pass, different population/rule) → BASKET Phase 0
thesis → Phase-1 causal SIP anatomy (1,066 days, policy-free pay-for-team/joint-tail) → Phase-2
family contract (PRE-REG-02) → corrected F1/F3/F4 + bounded F5/F6 diagnostics → this cycle.
**Strongest current fact is negative [OBS]:** all 120 corrected F1 cells, all 20 corrected F3 cells
and all 54 corrected F4 partial cells are net-negative on 1,066 days. Phase-1 establishes a right
tail and post-fill excursions — not an executable policy (`exec` in the Phase-1 lens means "touch
plus a later bar", not sellability; F8 says MFE is not P&L).

**Rulers that drifted into apparent constants** (each with the smallest discriminating readout):
1. **+30% as "the exit"** — a Phase-1 ladder ruler; the tested arm is next-open after a completed
   high touch, *not* a resting sell at the level (`level=None`). Test: predeclared comparison of
   fixed levels vs state-based exits, per-ticket realized EV, fresh data.
2. **"The right tail is a touch phenomenon, not a hold phenomenon"** — the population is bimodal
   (60.0% continue / 40.0% fade pooled; §1.1). Test: conditional hold/sell whose only changed
   variable is continuation state.
3. **"10:00 is the checkpoint that matters"** — the allowed grid is 580/585/590/600/615 and no gate
   at 600 has beaten cash/hold. Test: predeclared checkpoint-action comparison, identical state.
4. **"N=3 is the basket"** — a measurement convention and a legacy owner decision, explicitly not an
   economic choice in PRE-REG-02; F1 tests N=2/3/4 (all negative). Test: joint policy with declared
   breadth alternatives and marginal tail capture net of failed-ticket cost.
5. **"T=600, L=10, g=50 are the constants"** — legacy draft choices derived from descriptive
   anatomy, then demoted; R2(L10,w5) being the best F3 cell does not make it an optimum (all F3
   cells negative). Test: predeclared neighbourhood with both-block incremental EV.
6. **"Staged capital failed"** — F6 evidence is a p=.50 A_pm N=2/3 R0 diagnostic; F7 is unrun.
   Narrow to "the tested reserve arms showed no stable positive per-dollar EV".
7. **"Holding runners is wrong"** — narrow to "unconditional full-basket hold-to-close is negative
   in these primitives".
8. **"Improvements are only de-leveraging"** — supported for blanket R2/ET600 cuts, not universal
   (peak-relative R3 has a different per-dollar ordering; §1.6).
9. **"The morning is universally worst"** — conditional on the tested A_pm full-deployment sleeve
   and on touch timing (late means are tail lotteries; §1.5).
10. **"Phase-1 proves an executable tail"** — it proves path opportunity and access bookkeeping.
11. **"Phase-1 LS materials enable the last-survivor test"** — direct contract/implementation drift:
    `basket_aggregate.py` emits only aggregate `dn5_members/*` / `dn10_members/*` counters; the
    promised per-member state/future/peer-death fields do not exist. This blocks the LS claim.
12. **"C1 settles the economics"** — C1 fixes several defects, but the carry census prerequisite
    remains and the certified ticker-day count disagrees between documents (`HANDOFF.md` §19: 33.7k
    vs `factory/STATE.md`: 44,000+); canaries passing ≠ complete carry coverage.

**Load-bearing claims never tested in their stated form [HYP]:** causal policy pay-for-team
(realized, not stylized); peer-death/last-survivor event study + causal add comparator; full
repaired 840-cell F5 (only a 48-cell diagnostic exists, 0/32 add cells positive in both blocks);
full F6 reserve deployment and any F7 recycling run; basket-level F4 K={2,3} mode (only per-ticket
scale-out ran); a causal 09:40–10:15 state gate that beats cash/hold rather than predicting MFE;
causal identification of continuation beyond coarse minute state; later/conditional entry beyond
the refuted simple resting-limit formulation; quote-aware/queue/partial-fill capacity; rank
migration / peer-relative MFE / breadth as *decisions*; live leaderboard parity (no accepted F13
artifact in the active tree); a frozen combined Phase-3 architecture on an untouched holdout; and
the attention/momentum-flow mechanism itself as a causal driver rather than population selection.

**Other mismatches worth remembering:** F8's EOD-positive/all-profitable counts include marked open
tickets (not realized exits); F10's 10:00 environment labels are post-entry for A-family entries and
therefore cannot explain pre-10:00 entry economics; F4's README reports +11–107 bps and 12/27 pooled
winners while the corrected summary reports +11–101 bps and 24/54 (the two surviving "both blocks"
rows are one treatment at two frictions, not two treatments); `avg_deployed_capital` is cost basis,
not market value.

### 2.10 Own measurement — the non-toucher majority and the false-cut question (A_pm top-3)

Producer: `factory/scripts/basket_diag_touch_scan.py nontouch` (committed); artifact
`factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/nontouch_majority.json`.

One row per A_pm top-3 fill that never reaches +30% within the session (n=2,605 over 1,054 days),
carrying the causal 10:00 state and the realized outcome. This is the cohort the harvest arm never
touches and, per §2.1, where the loss lives.

| 10:00 return | n | share | EOD mean | EOD median | positive | later touch +10% | cut@10:00 minus hold |
|---|---|---|---|---|---|---|---|
| ≥ +10% | 130 | 5.0% | **+1.45%** | +4.06% | 56.2% | 100% | **+12.8%** |
| 0..+10% | 670 | 25.7% | −0.45% | +0.38% | 54.2% | 59.9% | +3.9% |
| −10..0% | 1,105 | 42.4% | −7.72% | −6.57% | 21.4% | 30.7% | +2.9% |
| < −10% | 700 | 26.9% | **−22.0%** | −20.2% | 4.6% | 20.9% | +4.9% |
| **all** | **2,605** | 100% | **−9.23%** | −7.33% | 27.0% | 39.0% | — |

Three facts:
1. **The majority's outcome is largely determined by 10:00.** Early losers (69.3% of the cohort)
   end the day at −7.7% to −22.0% with 4.6–21.4% positive; early flat/up names end roughly flat with
   a *positive median*. Unlike the toucher-continuation question (§1.2, best AUC 0.57), the 10:00
   state separates the majority's level strongly.
2. **Cutting at 10:00 beats holding to the close in every state bucket**, by +2.9% to +12.8% per
   ticket (winning on 59–79% of tickets) — *including* the early winners, where the false-cut cost
   is smallest. So the fixed-clock cut's gain is not a state effect: the sleeve bleeds after 10:00
   whatever the 10:00 state is. This is the missing false-cut/false-hold decomposition for the
   majority cohort (§2.6 asked for it) and it explains why DERISK's unconditional cut beat its
   state-conditioned variant (the state adds nothing to the *decision* even though it predicts the
   level).
3. The aggregate of this cohort is −240.5 return-units against the touchers' positive aggregate
   (§2.1) — the sleeve's sign is decided by the majority, not by the tail.
