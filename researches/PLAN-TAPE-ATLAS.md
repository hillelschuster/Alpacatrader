# PLAN (DRAFT) — Tape Pattern Atlas / Empirical Lexicon

## Decision and objective hierarchy

Build the Atlas. Its first product is **an empirical grammar of the top-gainer race**, not a
classifier, policy or alpha test.

The required hierarchy is:

> **raw tape → recurring structure → empirical lexicon → subsequent path distributions →
> economic hypotheses → executable rules**

The prior draft reversed part of this hierarchy by making ruler-beating P&L an early falsifier and
by starting from a small trader-defined alphabet. That would answer a narrower question than the
one intended. Profitability remains the project's final objective, but it is not the criterion by
which the Atlas itself is first judged.

The previous negative evidence is narrower:

* Flattened/z-scored 30/60-minute k-means, event-anchored k-means and the tested DTW shape-ordering
  formulations are closed (silhouettes 0.006–0.043 below the 0.05 stop rule; H018 failed its
  collision). Do not rerun them.
* E1 shows that the tested exhaustion-score release policies add no dollars over `giveback:10`.
* E3 shows that its six hand-built print-feature families add no material out-of-block information
  in its five frozen event sets.

Those results do **not** establish that data-discovered sequence structure, multi-scale motifs,
learned representations, duration grammars, nearest-neighbour analogues or race-level transitions
are exhausted.

## Two complementary vocabularies

### A. Data-discovered vocabulary (primary discovery layer)

The data is allowed to define recurring structures before outcomes or trader names are attached.
Each discovered object receives an opaque, versioned ID (`M0001`, `M0002`, …), not an economic name.
Discovery may split one apparent human concept into several objects or join concepts that look
different to us.

Admissible discovery families:

1. **Multi-scale subsequence retrieval.** Fixed-length views at several scales plus variable-length
   segments; nearest historical analogues with exclusion zones so overlapping copies of the same
   path do not masquerade as recurrence.
2. **Change-point segmentation.** Online/prefix-safe and retrospective segmentations kept separate;
   the latter may describe a completed movement but is never silently used prospectively.
3. **Self-supervised sequence representations.** Outcome-blind encoders trained by masked
   reconstruction, temporal-context prediction or carefully chosen augmentations. The encoder is
   an index into the source tape, never the canonical data and never an end-to-end controller.
4. **Motif and transition discovery.** Recurrent local neighbourhoods, variable durations,
   transition probabilities and common subsequences over frozen motif IDs.
5. **Similarity retrieval.** Given a historical episode or a live causal prefix: return the nearest
   historical episodes, the distance components, exemplars and later (only after the lexicon
   freezes) what followed.

Use at least two structurally different representations in the pilot:

* a continuous multi-scale self-supervised embedding/retrieval view; and
* a change-point/duration view built from causal raw-path coordinates.
The primary discovery encoder uses minimally transformed observations—normalized price/bar path,
trade/quote/volume event streams, relative rank/breadth, masks and time—not the 14 fall-model
features or the human vocabulary. Existing engineered state columns are retained as a separate
comparison view so the project can measure how much structure they impose or omit.


Agreement is evidence of structure; disagreement is preserved, not averaged away.

### B. Human/trader vocabulary (overlay, not seed labels)

Separately annotate the same source episodes with surge, flush, reclaim, failed reclaim, repeated
high, exhaustion, resurrection, halt/reopen, volume burst, leader handoff, bull-flag-like structure,
collapse and any new owner-defined concept.

The relationship is many-to-many:

* which machine motifs map to a human concept;
* whether one human concept splits into several stable machine motifs;
* which machine motifs have no human name;
* which human concepts are not coherent in the discovered geometry.

Human labels must never be used as pseudo-ground-truth for the unsupervised lexicon.

## Canonical observation substrate (lossless source of truth)

Never store only indicators or embeddings. Every lexicon item must resolve back to the richest
available source observation.

### Within-name record

Key: `(sleeve_day, family, ticker, entry_rank, et/bar_index)`.

Attach:

* the frozen panel-v2 causal state and raw minute path (`panel.parquet`: 1,900,432 rows, 6,160
  members, 1,066 days, registry-guarded columns);
* OHLCV/bar identity and exact entry/session boundaries;
* pointers into raw SIP prints (price, size, exchange, conditions, trade ID, tape);
* available NBBO quote updates and explicit quote-coverage status;
* causal rank, breadth, peers, leader identity and time/tenure context;
* data-quality, censoring and provider-coverage flags;
* retrospective fields in a physically separate namespace.

### Race record

For each day and decision instant, represent the evolving race:

* active A/B/C/D… members plus cash;
* ordered cross-name price/rank state and membership changes;
* appearances, disappearances and trading interruptions as raw events;
* relative path state, attention/volume and distance between peers;
* duplicate sleeve membership and the one underlying `(day,ticker)` path;
* capital ownership only in the later economic layer.

Existing cross features are inputs, not the race grammar. Retain the causal joint A/B/C/D/cash
state for data-discovered race motifs; assign opaque race-pattern IDs before mapping those frozen
motifs to handoff, co-movement, collapse, resurrection or other human concepts.

### Resolution policy

The source store remains multi-resolution:

1. full-session minute sequences for every member;
2. raw-print and quote pointers for every member/day where data exists;
3. materialized print/quote tensors only for selected segments or query-time retrieval;
4. E3's audited event windows reused as named reference sets, not treated as the whole universe.

This preserves the phenomenon without requiring every 2.8B trade and 0.85B quote update to live in
one training tensor.

## Atlas construction

### Stage 0 — Episode Store and query contract

Build a deterministic, versioned episode store over all 6,160 members and the race timeline.
Store raw references, standardized continuous paths, masks, segment boundaries and provenance.
No future outcomes enter the discovery arrays.

Required queries:

* source tape for any episode;
* multi-scale subsequences ending at or spanning a given time;
* exact and approximate nearest neighbours;
* all occurrences of a frozen motif ID;
* all race contexts in which a motif occurs;
* later, a prefix-safe live query: “show the 100 historically most similar causal prefixes.”

### Stage 1 — Representation and motif pilot

Use the full minute corpus first; raw prints/quotes are attached in a second channel rather than
made a prerequisite.

1. Create multi-scale windows and change-point segments without outcomes.
2. Train/freeze outcome-blind representations separately by development block.
3. Build an approximate-neighbour index while preserving exact distances for the returned set.
4. Discover motif candidates from recurrent neighbourhoods with minimum support across distinct
   days/months, not from a forced `k`.
5. Deduplicate overlapping occurrences and family duplicates before reporting recurrence.
6. Preserve low-density/unique episodes as such; the Atlas must not force every path into a type.

### Stage 2 — Freeze the empirical lexicon

Each motif record must contain:

* versioned pattern ID and representation/model hashes;
* scale, duration distribution and detection delay;
* medoid plus diverse real exemplars and nearest counterexamples;
* frequency by day, month, block, family and clock/tenure coordinate;
* neighbourhood radius/density and assignment uncertainty;
* transition/duration distribution and common predecessor/successor motifs;
* stability under resampling, time blocks and permitted perturbations;
* retrospective vs prefix-detectable status;
* raw episode references sufficient to reproduce every summary.

The lexicon freezes **before** any future-path anatomy is attached.

### Stage 3 — Race grammar discovery and freeze

Compose frozen within-name motifs and the raw joint state into day-level A/B/C/D/cash sequences.
Discover recurring joint configurations and transitions under opaque IDs without seeding
leadership, handoff, co-survival, collapse or resurrection labels. Preserve both the individual
tapes and joint race state. Freeze this race lexicon, with the same provenance, recurrence,
stability, uncertainty and raw-example requirements as the within-name lexicon, before reading any
future anatomy.

### Stage 4 — Human overlay and concordance

Only after both lexicons freeze, annotate stratified samples with the human vocabulary. Publish
within-name and race machine↔human concordance matrices, splits, merges, unnamed motifs, unstable
concepts and disagreement examples. Human tags remain editable; machine IDs and their frozen
discovery definitions do not.

### Stage 5 — Subsequent anatomy overlay

Only after both within-name and race lexicons freeze, attach what follows each occurrence:

* next high / re-break probability and time;
* MFE/MAE and full multi-horizon path distributions;
* reclaim probability, time and depth;
* remaining tail, terminal-peak probability and failure depth;
* later leader status, peer replacement and new entrants;
* censoring-aware counts and uncertainty.

These outcomes describe motifs; they do not retroactively redefine either lexicon.

### Stage 6 — Economic hypothesis generation (later)

Only stable, economically distinct phenomena become hypotheses for exits, adds, re-entry,
jump-ship/reallocation, basket management or execution. Translate one hypothesis at a time into an
explicit causal rule and then use the ledger/engine, friction, unseen months and tail accounting.
Failure of an action rule retires that rule—not the descriptive lexicon from which it was proposed.

## How the Atlas itself is judged

Atlas-quality gates are descriptive and representation-specific:

1. **Recurrence:** support across independent days/months, not overlapping copies.
2. **Stability:** neighbourhoods/motifs reproducible across blocks, families and resamples.
3. **Meaningful similarity:** retrieved examples are close under held-out raw-path distances and
   domain-neutral perturbations; nearest-neighbour identity is not clock, price level or volatility
   alone.
4. **Transition and duration stability:** frozen transition/duration distributions reproduce
   out-of-block within uncertainty.
5. **Coverage without coercion:** report recurring, uncertain and unique mass; no forced taxonomy.
6. **Retrieval quality:** query-by-example and prefix queries return stable analogues, with calibrated
   distance and uncertainty.
7. **Outcome separation after freeze:** some motifs may have materially different subsequent path
   distributions after multiplicity correction. This is evidence that the grammar partitions
   consequential structure, not yet evidence of tradable alpha.
8. **Causal detectability:** for prospective use, publish detection delay, prefix-invariance and the
   difference between retrospective and real-time assignments.

P&L is deliberately absent from these construction gates. It returns at Stage 6.

## Failure controls

* Hold all future-path columns out of discovery and representation training.
* Audit embeddings for clock, level, volatility, family and provider-coverage domination.
* Compare motif recurrence against block-respecting surrogate tapes and shuffled segment order.
* Use day-level splits; never let overlapping subsequences cross train/evaluation boundaries.
* Freeze representation families and search budgets before judging stability.
* Report the full search surface and family-wise multiplicity when later attaching outcomes.
* Never use a completed-episode assignment as if it were observable from a live prefix.
* Keep retrospective description and prospective detection as separate products.

## Smallest serious implementation

1. **Episode Store (days):** panel minute sequences, raw SIP/quote pointers, race snapshots, masks and
   deterministic query keys. No model.
2. **Discovery pilot (about one week):** two representations only—multi-scale self-supervised
   retrieval and change-point/duration segments—on the full minute corpus; fixed search budget.
3. **Within-name Lexicon v0 (days):** recurring neighbourhoods, exemplars, transitions,
   stability/coverage report and nearest-100 query; freeze it with no outcomes or human tags.
4. **Race-grammar pilot and freeze (days):** extend frozen within-name motifs into the raw
   A/B/C/D/cash joint state, discover opaque race motifs, and freeze the race lexicon before
   applying handoff/rotation names or outcomes.
5. **Human overlay + outcome anatomy (days):** concordance for both frozen lexicons, then subsequent
   path distributions. Neither may redefine a machine motif.
6. **Go/no-go:** judge both within-name and race layers on recurrence, block/family stability,
   meaningful similarity, transition/duration stability, retrieval quality, coverage without
   coercion and prefix safety. Descriptive-only is acceptable and P&L is not a gate;
   incoherent/unstable retrieval is not.

The first irreversible boundary is not a P&L test. It is freezing both empirical lexicons before
looking at what their motifs predict.
