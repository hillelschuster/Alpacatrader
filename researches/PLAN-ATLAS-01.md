# PLAN-ATLAS-01 — the minute-grain atlas: when does continuation value die?

Status: **proposal, not started.** Written 2026-09-24; rewritten twice the same day — first
window-first (EOD is not the value horizon), then **minute-grain with no horizon grid** on owner
directive. Nothing here is pre-registered; pre-registration with kill rules comes only if the
descriptive stage shows a signal.

## 1. Object

The phenomenon is an early-session explosive-attention event: the climb and climax happen in the
morning-to-midday hours, the afternoon is the relaxation phase. So the object is not "what is the
return to the close" and not "what is the return over the next h bars". It is:

> **At every completed minute, is the climb still alive — and what does it cost to wait?**

Estimated as per-minute **hazard and path statistics**, not as a coarse horizon grid:

- `p_climb_over(t)` = P(no new high after t | state at t) — the hazard that the running high is final;
- `remaining_run(t)` = E[max price after t / price(t) − 1 | state] — what waiting can still earn;
- `cost_of_waiting(t)` = E[drawdown below price(t) before the next new high | state] — what waiting risks;
- `bars_to_next_high(t)`, `bars_to_peak(t)`, `peak_time` — the climax clock.

Hold-versus-sell then *emerges* from those four numbers per minute: holding is worth it while the
hazard is low and `remaining_run` exceeds `cost_of_waiting`; selling belongs where they cross. No
threshold is chosen in advance, and no horizon is imposed — the same statistics are defined at
minute 30 and at minute 300, with the remaining session simply being shorter.

## 2. What is baked in, what must be discovered

Baked in (the thesis): selection of N names at one moment; causal only; executable prices; friction;
cash competes; minute grain; the window frame.

**Not** baked in: that selling belongs near a running high or buying near a local low; that any clock
time is special; that depth, duration, recovery, peak retention or attention is the relevant state;
that the close is a value horizon. Those must emerge from the statistics.

## 3. Panel — first deliverable

`factory/scripts/basket_atlas_panel.py` → `factory/artifacts/basket/phase2/ATLAS/panel.parquet`.
One row per (sleeve day, originally selected member, completed minute t ≥ fill), all 1,066 days,
A_pm top-3 and B600 top-3 (extendable), built from existing SIP tapes and anatomy only.

State (≤ t): return from fill / prev close / 09:30; running MFE and MAE; distance from entry;
distance from own running high; MFE surrendered; bars below entry in the current episode; bars since
that episode's low; reclaim count; failed-reclaim count; bars since last new high; new-high count in
the last 5/15/30 minutes; 1/3/5-minute returns and acceleration; bar persistence; range
expansion/contraction; minute volume and dollar-volume versus own prior median and versus the day's
candidate set; volume acceleration; sparse-bar/gap structure and bars since the last gap; live
percentile of the member among the day's candidates at that minute; peer state (peers making new
highs, peers dying); exposure in the slot; cash; actions taken; pending action.

Outcomes (strictly after t, all from the next open, friction applied separately): the four hazard
statistics above; `final_high_flag` (whether the running high at t is the session's final high);
`tail_class` (MFE from t ≥ +50%/+100%/+300%); and the raw future path summary needed for target/stop
feasibility. The session close appears only as the last minute at which a stop can happen — never as
a value definition.

## 4. Analyses, in order (each can kill the next)

1. **Death-of-value profile.** `p_climb_over`, `remaining_run`, `cost_of_waiting` as functions of the
   minute (clock) *and* of state, block by block, with the crossing where holding stops paying marked
   per cohort. This is the thesis made measurable: *when and in what state does continuation value die?*
2. **Climax structure.** Distribution of peak time by cohort (faders, touchers, giants), and whether
   the state near the peak identifies it — i.e. whether "the climax is near" is knowable causally.
3. **Tail accounting.** For every candidate crossing: dollars of ≥+100% MFE destroyed versus failure
   tax avoided, by block. A rule that wins on average by killing giants is rejected here.
4. **Matched pairs.** Minutes matched on coarse state (depth, episode duration, peak retention, clock,
   prior excursions) with divergent futures: what separates them, block-held out; and *when* the
   divergences occur. Clustered at halts/reopens → go to SIP trade prints (below); diffuse → the
   missing input is news/attention, not resolution.
5. **Extraction and the engine run.** The simplest rule that survives 1–4, as a per-minute stopping
   rule, run on the C1 engine (sell side and adds are expressible today) against the best simple
   rulers — hold-to-flat, the 10:00 cut, the +30 harvest, R3 — per committed dollar, both blocks, with
   tail preservation.
6. **Sub-minute, on demand.** `basket_t5_rawpaths.py` already builds paths from raw SIP **trade
   prints**, not bars, so second-level reconstruction is available with no data purchase — used only
   on the divergence windows that stage 4 identifies.

## 5. Engine gap (verified 2026-09-24)

Entry happens once, at `entry_T` (`basket_sim.py:1111`); the batch hook emits **ADD intents only**
(`:844-858`); there is no mid-session entry, no re-entry, no per-name entry veto. The sell side and
adds are expressible today; the buy side needs a small, explicit extension before stage 5 can act on
the reinforcement/re-entry half of the idea. Stages 1–4 need no engine change.

## 6. Falsifiers (what would make me stop)

- `p_climb_over` and the path statistics are flat in clock and state, in both blocks → minute bars do
  not carry the window's information; go to stage 4 and then decide on data.
- Value dies at the same minute for everyone with no state conditioning → the answer is a clock, not a
  law; keep the best clock and stop modelling.
- Signal in one block only → the failure mode that killed the previous four formulations; stop.
- Only giant-destroying rules carry signal → the phenomenon is not harvestable this way.

## 7. Cost

Panel + stage 1: one focused session on existing tapes. Stage 2–4: a second session. Stage 5 after the
buy-side extension. No data purchase and no engine change before stage 4 says which one is needed.
