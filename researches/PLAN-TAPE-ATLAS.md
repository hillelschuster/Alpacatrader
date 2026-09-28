# PLAN (DRAFT) — Tape Pattern Atlas / Empirical Grammar

**Status: architecture-reviewed against the current code and data; still DRAFT.**
No Stage-0 implementation or discovery run is authorized until the representation/invariance
registry, version graph and race-universe contract below are frozen.

## 1. Purpose and evidence order

The Atlas is first an instrument for understanding the top-gainer phenomenon, not an alpha model.
Its mandatory order is:

> **raw tape → recurring structure / retrieval geometry → empirical lexicon (if modes exist) →
> subsequent path distributions → economic hypotheses → executable rules**

P&L is deliberately absent from Atlas-construction gates. A later action rule can fail without
invalidating a truthful descriptive asset.

Prior negatives are narrow:

* flattened/z-scored 30/60-minute and event-anchored k-means, plus the tested H018 DTW
  shape-ordering formulation, are closed;
* E1's tested exhaustion-score release policies add no dollars over `giveback:10`;
* E3's 24 hand-built print predictors in five bounded case sets add no material out-of-block
  information.

They do **not** test outcome-blind sequence representations, continuous similarity geometry,
multi-scale motif retrieval, duration grammars, raw-print/quote representations or joint race
geometry.

## 2. Architecture decisions from the code/data review

### 2.1 Market observation and policy state are separate

**Cash is not part of discovery.** It has no tape. Cash, positions, exposure, blocked slots,
funding and actions exist only in the later policy/economic namespace.

The discovered race contains a variable-cardinality set of instruments and their market-observable
joint state. It does not contain a synthetic cash member. When a frozen market pattern later becomes
an action hypothesis, the engine combines it with a separate policy state (cash, positions and
constraints).

This matches the frozen panel contract: policy bookkeeping is not market state. It also prevents a
particular simulator policy from becoming part of the empirical grammar.

### 2.2 The race universe is not the 6,160-member panel

The panel is a selected post-fill population: A_pm/B600 top-3 members only. It cannot, by itself,
support claims about later entrants or a full evolving leaderboard.

Available race substrates are separate coverage tiers:

1. **Full-universe checkpoint board:** `data/sip/universe/rth/<day>.parquet`, all 1,066 dev days,
   roughly 5,000 names/day and 13 declared clocks. Its raw rows contain full-day fields
   (`hi/lo/c_last/vol/n_bars/first_et/last_et/delayed_open`) and future row-presence information.
   A prospective board must therefore rebuild each checkpoint roster from the PIT-eligible universe
   plus events actually observed by that clock, using only `px_T` / `px_T_et` and references already
   known by T. The raw compact table is otherwise retrospective.
2. **Candidate-net minute board:** SIP net bars/trades, roughly 38–115 trade symbols/day (median
   about 65). The stored day roster contains full-day winners/margin names, so it is
   selection-conditioned. Retrospective geometry may use it as labelled; prospective geometry at
   time t must use only snapshot membership selected at or before t plus tape observed by t.
3. **Selected-member minute board:** panel paths for 6,160 A_pm/B600 members. Its live cross fields
   use a ≤10-name A_pm snapshot and ≤2 same-family peers; they are not a full-market leaderboard.
4. **Quote board:** 7–25 symbols/day (median 15), the union of top-3 names across snapshots. Whole-day
   quote availability reveals later membership. Retrospective quote geometry is explicitly
   rank-selected; prospective geometry uses only names selected by snapshots at or before t, and
   future-selected coverage/absence remains metadata, never an input.

Atlas v0 publishes these as distinct retrospective and prospective views. It never silently treats
checkpoint, candidate-net and selected-member coverage as the same race. A true full-universe
minute race requires new data; the current store cannot manufacture it.

The discovery layer uses neutral IDs (`path_id`, `instrument_id`, `rank_slot`) rather than A/B/C/D
trader labels. Human concepts such as leader, replacement, resurrection and handoff are overlaid
only after race geometry freezes.

### 2.3 Magnitude and shape remain parallel

Normalization is a hypothesis, never preprocessing hidden from the evidence.

Every episode keeps parallel views:

| View | Preserved information |
|---|---|
| absolute level | bar/print price, entry/open/previous-close/running-high/episode-low references |
| relative magnitude | returns from each declared reference, range, drawdown and reclaim depth |
| normalized shape | scale-free geometry, explicitly separate from magnitude |
| speed/duration | both bar count and elapsed ET seconds; gap/hole state retained |
| attention/liquidity | shares, dollar-notional proxy, exact `sum(price*size)` when print-backed, print count |
| race context | named population, population size, rank, absolute cohort level/dispersion and peer state |

No dimensionless row may lose its reference level. No relative-volume row may lose its raw
numerator or named denominator.

Current limits are explicit:

* historical point-in-time float/shares outstanding and market cap are absent, so true turnover or
  float rotation is **unavailable** and must remain null;
* panel `dollar_volume` is `close*volume`, not exact notional;
* panel volume is condition-policy filtered and 53/6,160 member-days use provider bars;
* no panel ADV/RVOL baseline exists;
* quote coverage is rank-selected and local artifacts do not independently prove NBBO status.

Level filters are population definitions, not cleaning: entry price spans roughly $0.86–$868 and
20.2% of members fill below $2.

### 2.4 Invariances are versioned rulers

Every transform or augmentation is entered in an **Invariance Registry** before training:

`id, channels, transform, claimed invariance, economic risk, permitted range, affected scales,
no-outcome probe, comparison view, status`.

At minimum the registry covers:

* crop length, endpoint and left/right/event alignment;
* fill/entry anchoring, level translation and per-window demeaning;
* return/price/volume scaling and whitening;
* time shift, resampling and bounded time warping;
* masking, padding and missingness semantics;
* distance metric and channel weights;
* scale ladder and cross-scale aggregation;
* change-point penalty/minimum duration;
* race-set ordering/permutation treatment;
* overlap, sampling and recurrence weights.

Default constraints:

* no blind per-window z-scoring as the primary view;
* no unbounded time warping; duration/speed always remains a channel;
* clock and tenure remain coordinates—neither a dominant clustering feature nor an erased
  invariant;
* halt/hole structure is never random-masked or zero-filled;
* observed zero, true tape silence, provider absence and terminal censoring are distinct states;
* scales are reported separately, not averaged into one distance;
* metric families are explicit invariance claims, with agreement/disagreement published;
* augmentation may not move sparse/rare episodes into dense regions without being detected.
* every fitted parameter—normalization/whitening statistics, channel or metric weights,
  augmentation/model parameters, segmentation penalties and thresholds—is fit on a declared
  day-grouped training block and judged out-of-block; overlapping source intervals never cross the
  fit/evaluation boundary;
* a prospective decision instant is the completed bar's close; no print after close(t) may enter
  its representation (special retrospective event definitions remain separately labelled);
* volume self-normalization records its warm-up: `volume_vs_own_median` is unavailable before five
  prior bars and may not be backfilled.

Masked reconstruction, temporal prediction and contrastive learning are admissible views, not
assumption-free defaults. The primary baseline is exact retrieval on declared raw/dimensional and
relative coordinates.

### 2.5 Continuous geometry is a valid Atlas product

Atlas v0 does not require clusters or 20–50 motif IDs.

Its primary product may be a calibrated retrieval graph/manifold with:

* nearest historical neighbours and decomposed distance;
* density spectrum and uncertainty;
* stable local transition/duration statistics;
* low-density, uncertain and unique mass;
* exact retrieval for the sparsest stratum when approximate indexing is unreliable.

Discrete motif IDs are created only if a predeclared mode-structure gate is met (for example,
density gaps / neighbourhood connectivity / scale-metric agreement). Otherwise the empirical
lexicon is the frozen retrieval geometry plus versioned neighbourhood descriptors. Human concepts
can be mapped to regions/exemplars, not only to motif IDs.

Unique episodes remain first-class catalogue rows. They receive raw references and later anatomy;
they are never merged away to improve recurrence. Rare, severe episodes may later support an
explicit tail-risk hypothesis, with rarity kept visible.

### 2.6 Minute-first is not minute-only

The current panel is a sparse **printed-U-path-minute** sequence, not a contiguous full-minute grid:
1,900,432 panel rows versus about 2,305,138 member grid-minutes; 69% of member-days contain at
least one print-free minute.

The Episode Store therefore preserves:

1. sparse panel bars;
2. an optional reindexed minute grid with explicit `no_print` state (never fabricated prices);
3. immutable pointers to raw trade and quote day files;
4. bounded, versioned print/quote materializations keyed by parent shas and window specification.

Raw pointers use `(raw_store_generation, day_file_sha256, row_ordinal)` as identity. Trade ID alone
is not unique and intra-microsecond order is not recoverable; file order is preserved as evidence.

E3 is reused narrowly: its frozen anchors/census are named reference sets, while its capped
`stage_b_prints` sample is not an exemplar archive. E3 did not test raw sequence encoders,
multi-scale print motifs, quote geometry, race-level lead/lag or retrieval. Its null neither
licenses nor kills those lanes.

Minute geometry is built first for coverage and cost. A declared **resolution-disagreement probe**
materializes one frozen, coverage-qualified raw print/quote sample solely to compare minute
neighbours with raw-sequence neighbours. Its sample rule, statistic and threshold freeze before
reading the disagreement. If minute-near episodes are systematically sub-minute-far (or vice versa),
a full sub-minute representation becomes a new sibling version; the minute version and Episode
Store remain unchanged.

### 2.7 Freezes are immutable versions, not doctrine

Every node is an immutable directory with a deterministic `manifest.json`:

`node_id, version, schema, producer/code sha, parent node ids+shas, input day shas, config sha,
keys/sort order, coverage classes, deterministic payload shas, status, supersedes/withdraws`.

The day registry is derived only from `basket_sim.dev_days()` and every day passes `guard_day`;
builders never glob raw directories or trust `manifest_index.jsonl` as the research calendar.
Sealed 2024/2025-01 and reserved 2026-06..08 tape is refused; 2021-01-29 is allowed only as the
declared previous-close seed. Each registry row records trade/quote/bar/universe shas, coverage-class
counts and raw-store status, including the seven dev days whose store manifest is `partial`.

Version graph:

1. `raw.index@vN` — per-day trade/quote/bar/universe shas and coverage;
2. `episodes@vN` — path, membership, segment, race-board pointers and masks;
3. `representation.<view>@vN` — channels + Invariance Registry + transforms/model;
4. `geometry.<view>@vN` — metric/index/search parameters + exact-recall audit;
5. `lexicon.within@vN` and `lexicon.race@vN` — optional mode definitions/detection rules;
6. `overlay.human@vN` — annotations/concordance;
7. `anatomy@vN` — future-path overlays;
8. `hypothesis@vN` — later economic tests.

Discovery nodes have no parent edge from anatomy, human overlays or P&L. An omitted channel,
different invariance or new sub-minute view creates `v1`; `v0` remains byte-identical and its claims
remain auditable. Versions are compared through a separate concordance artifact, never rewritten.
Withdrawal records the defect and affected descendants; it does not erase lineage.
If a recorded parent sha no longer matches the on-disk parent, the node is stale. It is never
updated in place: affected materialization is rebuilt as a new version and the stale descendants
are recorded.

The freeze binds representation, transforms, augmentations, metrics, scale ladder, segmentation
penalties, sampling/overlap weights, index parameters and detection rule—not merely model weights.
Run-varying cost/timing lives outside deterministic evidence files.

## 3. Canonical Episode Store

The smallest robust store is a pointer/manifest layer, not a copy of the 23.9-GB trades and 4.1-GB
quotes and not an embedding table.

### 3.1 Independent path and membership identities

* `path_id = (day, ticker)` identifies one market tape.
* `member_id = (sleeve_day, family, ticker, entry_et, entry_rank)` identifies one research
  membership/entry context.
* a membership table maps many members to one path.

The 255 A_pm/B600 shared paths are collapsed **before** representation fitting, indexing,
neighbourhood construction and recurrence counting. Family/sleeve occurrences remain contextual
metadata. Overlapping subsequences use source-interval exclusion zones before fitting and support
counts.

### 3.2 Store tables

1. **`paths`** — path ID, session bounds, sparse bar spans, optional minute-grid mask, raw/quote row
   spans, coverage lattice, source shas.
2. **`memberships`** — member ID, path ID, entry context/family/rank and declared population.
3. **`segments`** — segment/occurrence ID, path ID, `start_et`, `end_et`, scale, segmentation
   version, source interval and coverage.
4. **`race_boards`** — separate checkpoint full-universe, minute candidate-net and minute
   selected-member records, each with roster source, population size and coverage tier.
5. **`costs` / `selftest`** — non-evidence timings plus identity/rebuild checks.

Coverage lattice, carried on every read:

* file present / absent;
* healthy raw / provider-only / unresolved;
* raw-derived bar / provider bar;
* quote present / absent and quote-selection rule;
* printed / print-free minute;
* complete / terminal-censored (future-derived; never a causal input);
* selected-member / candidate-net / checkpoint-universe race tier.

No aggregate silently mixes coverage or substrate classes.

### 3.3 Information already dropped in current derivatives

The store documents, rather than hides:

* U-path condition policy excludes about 45.3% of observed prints from the bar path (keeps 54.7%);
* net-bar merge drops exact `sum(price*size)`, VWAP, print count and bar-source provenance;
* provider-only member-days substitute provider bars;
* quote coverage is the top-3-per-snapshot union;
* full-universe race state exists only at declared checkpoints.

Raw pointers allow later versions to revisit these choices without rebuilding prior versions.

## 4. Discovery products

### 4.1 Retrospective geometry

May use a completed segment/path to describe its full structure, but it is labelled
`retrospective_only`. It cannot be inserted into a live race state or used as a causal detector.

### 4.2 Prospective/prefix geometry

Uses an explicit causal allowlist from `column_registry.json`. It rejects every
`causal_excluded_families` / `future_only_columns` field, including terminal censor flags and future
sequence length. Padding/masks reveal only what is known at `detected_asof_et`.

Each occurrence stores:

`occurrence_id, path_id, start_et, end_et, detected_asof_et, assignment_mode, scale,
representation_version, geometry_version, coverage, density/uncertainty`.

Future anatomy begins strictly after `end_et` for retrospective occurrences and after
`detected_asof_et` for prospective occurrences. The two anatomy tables are never conflated.

### 4.3 Parallel representation views

Pilot views, all outcome-blind:

1. **Dimensional/raw-coordinate retrieval** — level, magnitude, duration, volume/count and race
   context preserved.
2. **Fill/reference-relative retrieval** — declared return/depth coordinates with reference levels.
3. **Normalized-shape retrieval** — an explicit comparison view, never the only geometry.
4. **Change-point/duration geometry** — online and retrospective versions with declared penalties.
5. **Optional self-supervised geometry** — admitted only after its augmentations and probes freeze.
6. **Optional raw print/quote geometry** — added as a new representation version after the
   resolution-disagreement probe.

Race geometry is discovered independently from raw joint boards; it is not merely a sequence of
within-name motif tokens.

### 4.4 Human vocabulary

After within-name and race geometries freeze, annotate stratified exemplars/regions with surge,
flush, reclaim, failed reclaim, repeated high, exhaustion, resurrection, halt/reopen, volume burst,
leader handoff, bull-flag-like structure and new concepts.

Publish many-to-many human↔neighbourhood concordance, concept splits/merges, unnamed regions,
unstable concepts and disagreement examples. Human labels never train the primary discovery
geometry.

## 5. How discovery is judged

No-outcome geometry gates:

1. recurrence/support across independent `(day,ticker,source_interval)` units;
2. density spectrum and evidence for/against discrete modes;
3. block/family/era stability without requiring identical mixture weights;
4. metric/scale/view agreement and explicitly preserved disagreement;
5. nearest-neighbour quality with distance decomposition;
6. exact-vs-approximate recall by density decile; exact search for sparse regions if required;
7. transition/duration/hazard stability for continuous neighbourhoods and any supported motifs;
8. prefix determinism, detection delay and retrospective/prospective concordance;
9. magnitude-rank preservation and invariance-probe results;
10. coverage without coercion: recurring, uncertain, sparse and unique mass all published.

Block-respecting surrogate tapes and shuffled segment order calibrate recurrence/density claims.
Neighbour distances and neighbour identity are separately calibrated against
block/clock/level/volatility/coverage-matched surrogates; that null is published before any
historical episode is called an analogue. There is no forced `k`, support floor disguised as truth,
or requirement that every episode receive a motif ID.

## 6. Freezing and later anatomy

First freeze:

* Episode Store version;
* Invariance Registry and scale/metric/segmentation ruler matrix;
* representation versions;
* retrieval geometries/indexes and recall audits;
* optional within-name and race lexicons only where mode structure is evidenced;
* residual/unique episode catalogue.

Only then attach subsequent anatomy **to every episode/occurrence**, not only motifs:

* next high/re-break probability and time;
* MFE/MAE and multi-horizon path distributions;
* reclaim probability/time/depth;
* remaining tail and terminal-peak probability;
* later rank/leader status, peer replacement and new entrants where the race tier supports them;
* censoring-aware counts and uncertainty.

Outcomes describe frozen geometry; they never redefine it.

## 7. Economic layer (later)

Stable, economically distinct regions or rare severe episodes may generate hypotheses for exits,
adds, re-entry, jump-ship/reallocation, basket management or execution. One explicit causal rule at
a time is then tested through the ledger/engine.

Current engine limits are part of the action contract, not the discovery architecture:

* EXT-1 can ADD only at five checkpoints and its raw `BatchCheckpointContext.rec` contains future
  fields; an Atlas consumer must receive a new guarded causal context, never `ctx.rec`;
* EXT-2 mid-entry is confined to original snapshot `top_n` and does not re-rank;
* every Atlas policy must implement `signature()` and bind the exact Atlas node IDs+shas through
  `fingerprint_extra`, or two empirical versions could share a run identity;
* declaring a buy-side hook extends the engine event grid, so `deployed_avg` is not directly
  comparable with a no-hook run unless the accounting grid is held identical;
* new-entrant handoff/reallocation is not currently executable and requires a declared extension
  with a causal dynamic candidate universe, funded transfer/ENTER/EXIT intents and deterministic
  accounting.

Failure of an action rule retires the rule, not the Atlas geometry.

## 8. Smallest robust implementation

### Stage 0A — freeze architecture contracts (no data build)

Freeze the manifest schema, ID grammar, coverage lattice, causal allowlist, Invariance Registry
schema, scale/metric ruler matrix and race-tier definitions.

### Stage 0B — Episode Store v0

Build pointer-only `paths`, `memberships`, `segments` and `race_boards` plus day registry,
provenance, costs and selftests. The registry comes from `basket_sim.dev_days()` with `guard_day`,
never a directory listing or store index. Reuse E3 census; add row-span pointers rather than copying
prints. Require byte-identical rebuilds, parent-sha verification and explicit reporting of partial
raw-store days.

### Stage 1 — retrieval geometry pilot

Scope: representation and geometry only—no outcomes, human labels, P&L or forced clustering.

Build dimensional, reference-relative, normalized-shape and change-point views on the full minute
corpus under one frozen ruler matrix. All fitted transforms obey day-grouped block splits and
source-interval embargoes. Publish distance-null calibration, density spectra,
agreement/disagreement, sparse exact recall, unique mass, prefix checks and a nearest-100 query.
Run the frozen bounded resolution-disagreement sample; self-supervised and full sub-minute views are
optional sibling versions, not mandatory foundations.

### Stage 2 — race geometry pilot

Build independent geometry for:

* retrospective and causal-roster full-universe checkpoint boards;
* retrospective full-day and prospective snapshot-as-of-t candidate-net minute boards;
* selected-member minute boards;
* rank-selected retrospective and snapshot-as-of-t quote boards where coverage permits.

Do not merge their evidence. Do not add cash. Report roster provenance and whether any stable joint
geometry exists.

### Stage 3 — freeze empirical products

Freeze the within-name and race retrieval geometries. Create motif IDs only if the mode-structure
gate passes; otherwise freeze a continuous lexicon of neighbourhoods/density/transition
descriptors. Freeze the residual catalogue before reading outcomes.

### Stage 4 — overlays

Add human concordance and subsequent anatomy under separate versions. If minute geometry aliases
materially different raw sequences, add a declared sub-minute representation version and preserve
the minute result unchanged.

### Stage 5 — hypotheses

Only now generate economic/action hypotheses. P&L is not a Stage-0–4 gate.

## 9. Pre-freeze blockers

Before this DRAFT can become a frozen build contract:

1. choose and declare the minute scale ladder, metric families, channel weights and segmentation
   penalty search budget;
2. write the initial Invariance Registry entries, fitted-parameter split/embargo rules and
   no-outcome probe thresholds;
3. define the full-universe checkpoint-board PIT roster and causal column allowlist;
4. define retrospective and prospective candidate-net/quote roster rules and selection provenance;
5. define exact geometry/mode gates, matched-surrogate distance nulls and sparse-region ANN recall
   floors;
6. freeze the resolution-disagreement probe's sample, statistic and threshold;
7. decide whether v0 includes a self-supervised view or reserves it for a sibling version;
8. specify the guarded engine context only when an economic hypothesis exists—not before.

Until these are frozen, implementation does not start.
