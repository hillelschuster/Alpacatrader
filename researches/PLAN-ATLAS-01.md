# PLAN-ATLAS-01 — the minute-grain atlas: the morning window and the fall

Status: **proposal, not started.** Written 2026-09-24; rewritten three times the same day — window
frame, then minute grain, then (this version) a direct executable continuation value, a literal
minute-by-minute clock profile, and a dollar-weighted tail ledger instead of automatic rejection.
Nothing here is pre-registered; pre-registration with stop conditions comes only if the descriptive
stage shows a signal.

## 1. Object

The phenomenon is an early-session explosive-attention event: the climb and climax happen in the
morning-to-midday hours, the afternoon is the relaxation phase. The object is therefore two things
measured on the same tape:

> **(a) The window** — how the value of staying exposed evolves minute by minute through the morning,
> and **(b) the fall** — when, statistically, these names stop climbing and give it back.

**The direct number, at every minute t:** `V(t) = E[executable exit value of staying exposed from the
next open onward | state at t]`, reported in dollars per committed dollar, net of friction.

`V` is only well-defined *relative to a declared continuation* — staying exposed until when? — so it
is computed under several declared continuations and reported side by side:

- `V_hold_flat(t)` — hold to the engine's forced flat (the labeled EOD-anchored baseline, kept only
  for comparability with existing results);
- `V_giveback_g(t)` — hold until the price gives back g% from its running high (g declared, not fitted);
- `V_rule(t)` — hold until the fitted stopping rule fires (once it exists);
- `V_sell(t)` — the executable price now (next open), the cash alternative.

Four descriptive statistics explain *why* `V` moves, and are reported alongside it at every minute:

- `p_climb_over(t)` = P(no new high after t | state at t) — the hazard that the running high is final;
- `remaining_run(t)` = E[max price after t / price(t) − 1] — what waiting can still earn;
- `cost_of_waiting(t)` = E[drawdown below price(t) before the next new high] — what waiting risks;
- climax clock: `bars_to_next_high(t)`, `bars_to_peak(t)`, and the session peak time.

Hold-versus-sell then emerges from `V` and its decomposition. No horizon is imposed, no threshold is
chosen in advance.

## 2. What is baked in, what must be discovered

Baked in (the thesis): selection of N names at one moment; causal only; executable prices; friction;
cash competes; minute grain; the window frame; **the tape is tracked to the session close** so the
fall is observable — but no outcome is *defined as* the close; the close is only where the tape ends.

**Not** baked in: that selling belongs near a running high or buying near a local low; that any clock
time is special; that depth, duration, recovery, peak retention or attention is the relevant state.

## 3. Panel — first deliverable

`factory/scripts/basket_atlas_panel.py` → `factory/artifacts/basket/phase2/ATLAS/panel.parquet`.
One row per (sleeve day, originally selected member, completed minute t ≥ fill), all 1,066 days,
A_pm top-3 and B600 top-3 (extendable), from existing SIP tapes and anatomy only, tracked through the
close.

State (≤ t): return from fill / prev close / 09:30; running MFE and MAE; distance from entry;
distance from own running high; MFE surrendered; bars below entry in the current episode; bars since
that episode's low; reclaim count; failed-reclaim count; bars since last new high; new-high count in
the last 5/15/30 minutes; 1/3/5-minute returns and acceleration; bar persistence; range
expansion/contraction; minute volume and dollar-volume versus own prior median and versus the day's
candidate set; volume acceleration; sparse-bar/gap structure and bars since the last gap; live
percentile of the member among the day's candidates at that minute; peer state (peers making new
highs, peers dying); exposure in the slot; cash; actions taken; pending action.

Outcomes (strictly after t, executable next-open convention, friction applied separately):
`V_hold_flat`, `V_giveback_g` for declared g, `V_sell`, the four statistics above, `final_high_flag`,
`tail_class` (MFE from t ≥ +50%/+100%/+300%), and the raw future-path summary needed for
target/stop/re-entry feasibility.

## 4. Analyses, in order

1. **The morning window, minute by minute.** `V` (all declared continuations) and the four statistics
   as functions of *literal clock minutes* — 09:31, 09:32, 09:33, … through 12:00 initially, reported
   further if the shape warrants — per cohort, **block 1 vs block 2**. Output: the exact shape of the
   window, where the value of staying exposed peaks, and where it dies.
2. **The fall.** When do these names actually stop climbing: the distribution of the session peak
   time per cohort (faders, touchers, giants), the give-back after the peak, and whether the state
   near the peak identifies it causally.
3. **The dollar ledger for any candidate rule.** Not an automatic rejection: for each rule report
   *dollars of giant tail destroyed* (MFE from the decision point ≥ +100%, and ≥ +300% separately)
   versus *dollars of failure tax avoided*, per block, and which specific giants are cut — plus
   whether a cut giant is re-admissible at a later reclaim. A rule may rationally sacrifice monsters
   when the net dollar tradeoff is strongly favourable; what must be prevented is "improving averages"
   by obliterating the phenomenon, which the ledger makes visible.
4. **Matched pairs.** Minutes matched on coarse state (depth, episode duration, peak retention, clock,
   prior excursions) with divergent futures: what separates them, block-held out; and *when* the
   divergences occur. Clustered at halts/reopens → go to SIP trade prints (below); diffuse → the
   missing input is news/attention, not resolution.
5. **Extraction and the engine run.** The simplest rule that survives 1–4, as a per-minute stopping
   rule, run on the C1 engine (sell side and adds are expressible today) against the best simple
   rulers — hold-to-flat, the 10:00 cut, the +30 harvest, R3 — per committed dollar, both blocks, with
   the dollar ledger of stage 3.
6. **Sub-minute, on demand.** `basket_t5_rawpaths.py` already builds paths from raw SIP **trade
   prints**, not bars, so second-level reconstruction needs no data purchase — used only on the
   divergence windows that stage 4 identifies.

## 5. Engine gap (verified 2026-09-24)

Entry happens once, at `entry_T` (`basket_sim.py:1111`); the batch hook emits **ADD intents only**
(`:844-858`); no mid-session entry, no re-entry, no per-name entry veto. Sell side and adds are
expressible today; the buy side needs a small explicit extension before stage 5 can act on
reinforcement/re-entry. Stages 1–4 need no engine change.

## 6. Stop conditions (not verdicts)

Pause and reconsider only where the evidence is clear:

- `V` and the four statistics flat in clock *and* state, in both blocks → minute bars do not carry the
  window's information; go to stage 4 and then decide on data.
- Value dies at the same minute for everyone with no state conditioning → the answer is a clock, not a
  law; keep the best clock.
- Signal in one block only → the failure mode that killed the previous four formulations; park it, do
  not delete it.
- A rule whose entire gain is average-improvement-by-obliteration (giant dollars destroyed far exceed
  failure tax avoided) → parked, and recorded as such; not deleted, not promoted.

## 7. Cost

Panel + stage 1: one focused session on existing tapes. Stage 2–4: a second session. Stage 5 after the
buy-side extension. No data purchase and no engine change before stage 4 says which one is needed.
