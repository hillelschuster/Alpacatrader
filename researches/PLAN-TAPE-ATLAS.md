# PLAN (DRAFT) — Action-Transition Atlas (ATA)

Decision: build a tape atlas, but ONLY in the version that targets the untested leverage. A free-form
"let the data find patterns" mine is redundant and likely dead: window/event shape clustering is
already exhausted (silhouette 0.006-0.043 < the 0.05 stopping rule, PRE-REG-PATTERN-01/02), DTW died
(H018), event anchoring did not rescue it, and the two strongest learned objects - exhaustion
(AUC 0.82-0.84) and predicted dispersion (0.67-0.74) - add zero dollars over the trivial
peak-relative ruler (E1a/E1b; retracted look-ahead niche) while sub-minute print information is a
null (E3 Stage-B, all deltas inside the 0.054 band).

## What is new and untested (the atlas' reason to exist)

1. **Cross-name structure**: leader replacement / handoff / rotation among concurrent runners. The
   panel has breadth/cross features and 255 shared A_pm x B600 paths, but no explicit handoff event
   and no policy model. This is the only action lane never tested (adds, reallocation, jump-ship).
2. **Transition grammar with durations**: change-point/HSMM-style states over the member-day
   lifecycle (surge, stall, reclaim, failed reclaim, halt/reopen, collapse, resurrection) with
   explicit durations, instead of cluster medians that average overlapping clouds.
3. **Measured execution cost**: quotes exist (top-3-per-snapshot union, 4.1 GB); a print/NBBO
   slippage-latency model is an EV asset on its own, independent of alpha.

## Layers (all causal; reuse first)

* L0 Canonical record (reuse): panel v2 rows keyed (sleeve_day, family, ticker, entry_rank, et)
  + E3 print episodes (decision-time semantics: features read prints <= close of anchor bar) + basket
  aggregates + duplicate-path linkage. No new data, no new features beyond exported state columns
  (registry guard + prefix-invariance test mandatory).
* L1 Discovery with a leash: PELT-style change-point segmentation on causal prefixes + a tiny event
  alphabet (new-high, reclaim, failed reclaim, halt/reopen with hole class, vol-spike, leader-change)
  -> transition counts/durations per state, conditioned on causal predicted dispersion. HSMM only
  for duration/phase features. Shape clustering stays closed.
* L2 Decision conversion: atlas states -> <=3-conjunctive declarative rule (ledger spec form);
  replay through `basket_atlas_ledger` vs hold and giveback:10; cross-fit by block, day-clustered,
  sleeve+dedup, top-5-day removal, giant-tail clause, Holm across the frozen family count, null band
  from shuffle-label refits. Any lift must survive conditioning on the two closed scores.
* L3 Cross-name arm (the test that matters): handoff/reallocation rules expressed over the engine
  action space (entries once; ADD intents; mid-entry/re-entry via EXT-2 hooks if arms require them),
  judged by the same ledger bar on a pre-registered one-shot unseen window.

## Early falsifier (days, not months)

Fit states on block1, freeze, predict the block2 next-event executable dollar delta
(v_forced_flat change) with the panel's causal columns only: if no state family beats the
dispersion+exhaustion baseline by more than the shuffle-label null band in both directions, stop -
the atlas is descriptive only.

## Prohibitions

No free-form clustering of window shapes; no black-box controller (PRE-REG-BASKET-02 §7); no
member-level statistics as conditioning (E1b lesson); no max-order future labels; one pre-registered
look at unseen months; no promotion without all-four-cell sign stability.
