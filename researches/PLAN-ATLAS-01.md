# PLAN-ATLAS-01 — the window atlas: when does marginal continuation EV die?

Status: **proposal, not started.** Written 2026-09-24; rewritten the same day after the owner's
correction that EOD is not the value horizon (see `researches/INTENT.md` §"THE WINDOW"). Nothing
here is pre-registered yet; pre-registration with kill rules comes only if the descriptive stage
shows a signal.

## 1. The question

We own N top-gainer candidates selected at one moment. The phenomenon is an early-session
explosive-attention event: the climb and the climax are concentrated in the morning-to-midday hours,
and the afternoon is the relaxation phase. So the object is **not** "what is the return to the close"
— it is:

> **At each causal moment, what is the marginal value of another unit of exposure over the next
> short horizon, and when through the session does that value die?**

Everything else — when to sell, when to hold, when to reinforce, when to re-enter, when to sit in
cash — is a consequence of that time profile and its state conditioning.

## 2. What is baked in and what must be discovered

Baked in (the thesis, from the owner): selection of N names at one moment; the window frame; causal
only; executable prices; friction; cash competes.

**Not** baked in — these must emerge from the statistics, not be assumed by design:

- that selling belongs near a running high, or buying near a local low;
- that exits belong at a particular clock time (including "before 13:00");
- that depth, duration, recovery, peak retention or attention is the relevant state;
- that the value horizon is the session close — it is one horizon among many, and a priori the wrong
  default.

## 3. Panel (first deliverable)

One row per (sleeve day, originally selected member, completed bar t ≥ fill bar), from the existing
SIP tapes only.

Causal state (≤ t): own path (return from fill, distance from entry, running MFE/MAE, distance from
own running high, MFE surrendered, below-entry episode duration, bars since episode low, reclaim
count, failed-reclaim count, bars since last new high); dynamics (1/3/5/15-bar velocity and
acceleration, higher-high/higher-low counts, bar persistence); attention proxies (volume and
dollar-volume intensity vs own prior bars and vs the day's candidates, volume acceleration,
sparse-bar/halt structure and time since last gap); cross-section (live percentile/rank among the
day's candidates at that minute, rank change and residency, peer state); bookkeeping (exposure in
slot, cash, actions taken).

Outcomes — **horizon profile, not a terminal mark**:

- `forward_return(t → t+h)` from the next open to the close of bar t+h, for h = 5/15/30/60/120 bars;
- `time_to_peak_from_t` and the realized peak price after t (for climax timing);
- `forward_max/min` after t (tail and stop feasibility);
- terminal return, kept as *one* horizon for comparability, never the default;
- `tail_class` (MFE from t ≥ +50%/+100%/+300%) for the dollar-weighted tail test.

## 4. Analyses, in order (each can kill the next)

1. **The window map (the first object).** Conditional marginal continuation value as a function of
   time-of-day and state: `E[forward_return(t → t+h) | state, clock]` for the horizon grid, block by
   block. Question: *where through the session does the conditional mean cross zero, and does it
   cross the same way in both blocks?* This replaces every prior "when should we exit" question with
   one estimable surface.
2. **Climax structure.** Distribution of peak timing by cohort (faders, touchers, giants), and
   whether the state near the peak identifies it. This is what "ride the wave, exit before the
   relaxation" has to be built on.
3. **Action surfaces emerge.** For every bar, compare holding h more bars against exiting now, and
   (where cash exists) against adding — and let the *crossings* define whether the structure is
   high-proximity, clock, attention, path-episode, or a combination. No pre-declared "sell near
   high / buy near low".
4. **Tail accounting.** For every candidate crossing: dollars of ≥+100% MFE destroyed versus failure
   tax avoided, by block. A rule that wins on average by killing giants is rejected here.
5. **Matched-pair patterns.** Moments matched on coarse state with divergent futures: what separates
   them, block-held out; and *when* divergences occur (clustered at halts/reopens → microstructure is
   the missing input; diffuse → news/attention is the missing input). This is the evidence-based
   trigger for any finer-data purchase.
6. **Extraction.** The simplest law that survives 1–5, then the C1 engine run: both blocks,
   per-committed-dollar EV, against the best simple rulers (hold-to-flat, the 10:00 cut, the +30
   harvest, R3), with tail preservation.

## 5. Engine feasibility (verified 2026-09-24)

The C1 engine today: entries happen **once**, at `entry_T` (`basket_sim.py:1111`), budget
`C0*reserve_frac/N`; actions available are ENTER/ADD/REDUCE/EXIT; the batch hook
(`BatchAllocationPolicy`) can request **ADD intents only** (`basket_sim.py:844-858`); there is no
re-entry after exit and no mid-session entry.

Consequence: **the buy side of this plan is structurally untestable today** — the engine can only act
on the sell side and on adds. Acting on the atlas's buy surface (enter later, re-enter after an exit,
or skip a name at the entry moment) requires a small, well-specified engine extension. The
descriptive stages 1–5 need no engine change and can run first.

## 6. Falsifiers (what would make me stop)

- The conditional continuation value is flat in clock and state, in both blocks → minute-bar state
  does not contain the window's information; go to 5's divergence-timing test and then decide on data.
- The value dies at the same time for everyone with no state conditioning → the answer is a clock
  ruler, not a learned law; keep the best clock and stop modelling.
- Signal exists in one block only → the failure mode that killed the previous four formulations; stop.
- Only giant-destroying rules carry signal → the phenomenon is not harvestable this way.

## 7. Cost

Panel + window map + climax structure: one focused session, existing tapes only. Matched pairs and
extraction: a second session. No new data purchase until stage 5 says which data would resolve the
divergence; no engine change until the policy stage.
