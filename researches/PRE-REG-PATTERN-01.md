# PRE-REG-PATTERN-01 — Attention-Pattern Digest (top-3 minute paths)

Draft written 2026-09-12 by the agent from the user's directive; **freezes on user
sign-off**. No computation runs before that sign-off.

## User directive (verbatim anchors)
- Universe: "1-3 MAX MAX" — top-3 gainers only. Top-5 rejected: "first top
  gainers vs fifth is huge difference."
- Cadence: "every minute" — no 5-minute sampling; "the 5 minutes is sort of a
  bias." Sub-minute only "in some windows, if I'll have the data."
- Objective: "no target. only digesting the data and illustrating sort of the
  patterns."
- Context: top gainers carry the attention; patterns are "not random"; the
  point is the explosion/attention window, not blue chips.

## Scope boundary
This is a **digest/illustration** study. Learning is unsupervised; no outcome is
used to fit the representation. A separate, pre-registered **scoring pass**
measures forward outcomes of discovered patterns after the fact (the "ruler",
not a target). Without that pass a pattern cannot be called tradeable, and
nobody may treat cluster membership as a signal before a new pre-reg + OOS.

## 1. Corpus (exists; no new fetch for phase 1)
- Source: `data/leaderboard/path_YYYY-MM-DD.parquet` — the causal top-3 gainer
  minute grid (PIT universe, prev-close gain, last completed bar; rank from
  `lb_*.parquet`). Range 2024-01-02..2026-08-31; 667 days, 7015 symbol-days
  (~10.5/day).
- Every minute on the 570..959 ET grid (09:30–16:00), forward-filled research
  semantics (`lb18.py:81-129`). No 5-min sampling.
- Attention focus for illustrations: primary 09:45–12:30, secondary 14:30–16:00
  (where the flush fills actually concentrate: 50% before noon, AM EV +2.08% vs
  PM +0.18%; a 15:00–16:00 spike). The full session stays in the corpus with
  time-of-day as a channel so structure can separate windows by itself.

## 2. Channels (per minute, scale-free, frozen)
- bar return; range as % of close; close-position-in-range;
- volume vs trailing 20-minute median (ratio), trade-count proxy if available;
- cumulative gain from session open; time-of-day (minute index).
No engineered stacks, no labels.

## 3. Representation (frozen; phase 1)
- Windows of length L ∈ {30, 60} minutes, right-aligned, per-window normalized
  (price divided by first close, volume by window median).
- Unsupervised: k-means/k-medoids on flattened normalized windows plus a
  summary-statistics view; DTW medoids on a subsample for illustration only
  (DTW failed as a predictor in H018 — it is not a predictor here).
- k chosen by a frozen rule (occupancy balance + silhouette) within k ∈ {8, 16,
  24}; exactly one configuration is frozen before illustrations are produced.
- Phase-2 option (separate pre-reg): learned sequence embeddings.

## 4. Primary deliverable — the digest
Per pattern/cluster: median + quartile path envelope; occupancy by time-of-day
and rank; volume profile; example (date,ticker) instances; month stability of
occupancy. Charts + JSON. This is description, not evidence of edge.

## 5. Scoring pass (pre-registered here, runs after the digest is frozen)
For each cluster, measure forward behavior from the window end: MFE/MAE;
EV of a fixed bracket (+4% / −2%, 30 min) and of the flush-style parameters;
month-blocked, 100bps friction. Eligibility for "candidate": forward EV differs
from the population by a material margin AND replicates in >=2 of 3
chronological blocks. Candidates then require a NEW pre-reg + OOS before any
adoption. Multiple-comparison risk over clusters is reported explicitly.

## 6. Sub-minute phase (only if phase 1 shows structure)
Historical SIP trades/quotes are available on the current key (>=15 min old).
Phase 2 stages 5/15/30-second bars for rank-1 names in the primary window
first, expanding only if phase 1 justifies it. IEX 1-min tape
(`data/iex_tape/`) is the live-compatible arm.

## 7. Anti-patterns / kills
- No resurrection of H12 scalars, targets, or 5-min snapshots.
- No supervised target inside the representation; no direction-prediction
  reframing without a new pre-reg.
- Cluster instability across months is the primary risk and must be reported as
  a first-class result, not buried.
- 2026-04..08 carries known contamination from the flush work; any historical
  "OOS" is contaminated — honest validation is forward or from newly staged
  earlier data.

## 8. Artifacts
`factory/artifacts/pattern_digest_*.json` (+ cluster cards, png/csv) from
producer `factory/scripts/pattern_digest.py`. Ledgers appended after the cycle.
