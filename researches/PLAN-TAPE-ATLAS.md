# PLAN (DRAFT) — Tape Pattern Atlas / Empirical Grammar

**Status: DRAFT. Freeze R is NOT frozen and nothing here depends on it.**
The observation contract below is intended to become Freeze O. No future anatomy, economic label or
P&L is permitted in construction of the observation corpus or discovery geometry. §4.3 now states
the observation acquisition semantics explicitly (admission interface, replace/supplement,
alias-only renaming, collision refusal, verified-absent versus missing-trader, measured net
status, evidence-file gate); §8 Freeze R remains a bounded proposal until the outcome-blind corpus
report exists and is deliberately left unfrozen.

## 1. Purpose and evidence order

The Atlas is first an instrument for understanding the top-gainer phenomenon:

> **raw tape → recurring structure / retrieval geometry → empirical lexicon (if modes exist) →
> subsequent path distributions → economic hypotheses → executable rules**

P&L is not an Atlas-construction gate. A later rule can fail without invalidating a truthful
descriptive asset.

Prior negative evidence is narrow:

* flattened/z-scored 30/60-minute and event-anchored k-means, and the tested H018 DTW
  shape-ordering formulation, are closed;
* E1's tested exhaustion-score release policies add no dollars over `giveback:10`;
* E3 closed CS-1..CS-4 as null; CS-5 survived only its weak kill rule, every delta was ≤0.0168
  (CS-5 ≤0.0055) inside the 0.054 null band, and nothing was promoted.

None of that tests outcome-blind sequence representation, continuous retrieval geometry,
multi-scale raw-print/quote representations, duration grammars or joint race geometry.

## 2. Two freezes separated by a blind corpus inspection

### Freeze O — observation contract (before any corpus build)

Freeze what the tape **is** and what may be read:

* day registry and `guard_day`;
* raw input hashes, IDs, coverage/missingness semantics and lineage;
* independent path versus membership identity;
* retrospective versus prospective rosters;
* positive per-column causal allowlists (default deny);
* row-level `selectable_asof_et`;
* decision instant and occurrence clocks;
* physical separation of observation from outcomes/censoring/policy;
* materialized canonical-table schemas and reconciliation guards;
* permitted outcome-blind corpus summaries.

Freeze O contains no distance metric, channel weighting, scale ladder, segmentation penalty,
augmentation or learned-model parameter.

### Blind corpus inspection

After building the corpus, inspection is limited to a frozen whitelist:

* row/support/coverage/null counts;
* path/window length and magnitude distributions;
* per-block/family/era/channel quantiles;
* duplicate/overlap and roster-census facts;
* no-outcome distance, density, neighbour, perturbation and surrogate statistics;
* resource/cost measurements.

Every inspected statistic and resulting choice enters an **Adaptive Choice Ledger**:

`choice_id, alternatives, evidence inspected, columns read, blind flag, decision, affected node,
recheck block, status`.

### Freeze R — bounded representation matrix (after blind inspection, before anatomy)

Freeze how observations are compared:

* a small declared representation family;
* channels and transforms per view;
* metric and channel weights per view;
* scale ladder and segment/crop rules;
* change-point budget;
* one self-supervised architecture/configuration;
* augmentations/invariances;
* neighbour/density/index rules and sparse exact-recall floor;
* no-outcome quality gates.

Freeze R cannot read any outcome, censor or future-anatomy column. `anatomy@vN` may attach only to
occurrences whose representation and geometry have a non-withdrawn Freeze-R record.

## 3. Observation and policy are separate

Cash has no tape and is not part of discovery. Positions, exposure, funding, blocked slots and
actions exist only in the later policy namespace.

The discovered race is a variable-cardinality set of instruments and observable joint market state.
When a frozen phenomenon becomes an economic hypothesis, it is joined to a separate policy state.

This matches the frozen panel contract, which explicitly excludes cash/exposure/actions from market
state.

## 4. Actual data tiers and their limits

### 4.1 Selected-member panel

`panel.parquet`: 1,900,432 rows, 6,160 A_pm/B600 memberships, 5,905 unique `(day,ticker)` paths,
1,066 dev days. It is a post-fill top-3 population, not the race universe.

Its `state_cross` fields use a ≤10-name A_pm candidate list and ≤2 same-family peers. They are
retained under `legacy_candidate_context`, never labelled market rank or full-race context.

### 4.2 Existing full-roster checkpoint board

`data/sip/universe/rth/<day>.parquet` covers all dev days and roughly 5,300–6,100 PIT names/day at
twelve decision clocks:

`575,580,585,590,595,600,615,630,645,660,690,720`.

It has no afternoon coverage after 12:00. `px_T` is the last completed bar known at T and `px_T_et`
states its age. Whole-day `hi/lo/c_last/vol/n_bars/first_et/last_et/delayed_open` and row presence
are not prospective features.

A prospective checkpoint roster is rebuilt from `data/pit/pit_symbols.parquet` plus data actually
observed by T. Symbols that have not traded by T remain an explicit `not_observed_by_t` stratum;
they are not deleted.

### 4.3 Broad minute race board — build now, source-qualified

Unfiltered provider bars exist for 50 of 51 dev months, and the admitted acquisition below
supplies the 51st. `data/atlas/acquisition/v0` carries the admitted B1/B2 SIP acquisition outside
the repo, and the builder CONSUMES it. The admission interface is explicit:

* `bars/<day>.parquet` — `timestamp` (ns, UTC), 5×`Float64` OHLCV, `ticker` = **canonical PIT
  spelling**, `provider_symbol` (audit only, never a join key);
* `bars/<day>.manifest.json` — `day, scope, status, file, sha256, schema, source{feed=sip,
  adjustment=raw}, requested_symbols, symbols_with_data, symbols_zero_bars, symbols_invalid,
  aliases, errors` plus roster/input/code/config shas;
* `rosters/<day>.json` — PIT vintage, canonical→provider mapping, `expected_universe_present`,
  `raw_existing`, `alias_existing`, and an `unavailable` group (absent from all sources / no RTH in
  baseline / no verified provider spelling);
* `evidence/acquisition_admission.json` — per-blocker B1/B2 entries with the admitted day list,
  each day's file+manifest shas, requested-outcome accounting, and the residual confirmed-trading
  gaps.

Consumption rules, all measured rather than assumed:

* an admitted day **must** declare `status=complete`, `feed=sip`, `adjustment=raw` and no errors,
  and its parquet must **re-hash locally** to the manifest's `sha256`; the builder re-verifies all
  of this and never trusts the admission file's own hashes;
* a **B1** admission *replaces* the floor-qualified 2025-02 clean fallback — the day stops being
  `qualified_floor_source`; a **B2** admission *supplements* the 2026-04/05 baseline month file;
* an alias is honoured **only** through the explicit frozen map (applied as its inverse, injective,
  chain-free); nothing is ever fuzzy-matched, and a name the map declares not-aliased is never
  renamed;
* a canonical name arriving from **two** raw lane files refuses the day: the sources disagree and
  no merge rule can settle it;
* the previous-session denominator **prefers the admitted previous day**; 2025-02-03 still has no
  admitted prior session (2025-01-31 is sealed) and keeps its declared block-gap/stale provenance
  with no sealed look-back;
* a name the acquisition **verified absent** (zero bars / no RTH / invalid symbol) is a market
  fact, not a data hole: it is counted as `raw_roster_verified_absent_n` and never inflates
  `raw_roster_missing_unresolved_n`, which is the number B2 is decided by;
* an absent or incomplete acquisition day is simply **not admitted** — the baseline lane is used
  and the blocker stays open. No bar, zero price or roster row is ever fabricated to close a hole;
* per-row provenance carries the **combined** lane sha256 (every contributing raw file plus the
  alias map), so one column proves the whole lane set a row was built from;
* the acquisition root is explicit and `v1` is the first admitted root. `v0` is a deliberately
  failed canary kept as evidence, and there is no fallback to it: with no admissible root the
  blocker is simply reported open;
* the declared scope vocabulary is exactly `feb2025` (replaces the floor-qualified fallback) and
  `aprmay2026` (supplements the baseline month file); any other scope refuses rather than defaulting;
* `evidence/acquisition_admission.json` is a **union** rebuilt from per-scope attestations, so both
  blocker ids are always present as keys and key presence proves nothing. A blocker resolves only on
  `all_scopes_ready` with no missing or stale scope AND that scope's own `state == "ready"`; a
  `not_ready`/`stale`/`unverified` scope admits no day even if its day list is populated;
* `all_scopes_ready` and a non-empty list are **not** coverage — a one-day-per-scope canary union
  carries both. The consumer recomputes the required day set from the guarded dev calendar (all 19
  dev days of 2025-02 for B1, all 41 of 2026-04/05 for B2), requires the admitted set to cover it,
  and re-hashes every admitted day's parquet and manifest. The required set never comes from the
  evidence;
* the projection window `[565, 965]` ET is a **closed** interval — a bar stamped exactly 16:05:00 ET
  is in window. An exclusive upper bound would make a good day disagree with the baseline lane it
  supplements, which already carries 16:05 bars.

The net lane follows the same discipline. `net_manifest_status` comes from the validated per-day
manifests and the derived index, refined by
`factory/artifacts/.../OBSERVATION/v0/acquisition/net_reconciliation.json`; a day counts as
reconciled only when its files and per-day manifests re-hash locally, its index row is present
after the rebuild, and the derived index on disk matches `derived_index.sha256_after`. The set of
days carrying a `net_manifest_missing` state is therefore **measured**, not the hardcoded
2026-05-21/29 pair: a verified day drops out on its own and a regressing day re-enters.

Every core blocker is resolved only by an evidence **file** that exists, re-hashes to its declared
`sha256`, and whose contents attest the repair: B1/B2 require a verifiable acquisition admission
with no residual confirmed-trading gap, B5 requires physical-disk evidence distinct from the
acquisition file with a positive measured `free_bytes`, B6 requires a reconciliation that verifies
at least one day end to end. A nonempty sha string alone resolves nothing.

The raw lane recovers the sub-$2 slice the clean files removed. It is still mixed-source evidence:
HF feed conditions/as-of revision are unknown, 2026-03..05 is Alpaca SIP, and an admitted day is
fresh SIP. Feed/source/era, staleness and quality remain first-class strata.

The board is built over the full available cross-section, not top-K:

`day, t, ticker, px, prev_close, prev_close_et, prev_close_age_sessions,
prev_close_floor_qualified, gain, age_min, known_by_t, fresh_2m, rank_eligible, rank_known,
rank_fresh_2m, order_slot, n_known, n_fresh_2m, source, quality ratios/flags, roster provenance`.

Rules:

* decision t uses only bars with `et <= t-1`;
* previous close is the sorted last bar of the immediately prior stored session;
* PIT eligibility is the latest vintage available on/before day;
* `known_by_t=false` implies null price/gain/rank—never forward-filled from the future;
* competition rank is `1 + count(gain_j > gain_i)`; `order_slot` is the deterministic ordinal from
  `(gain desc, ticker asc)` and is not a second rank definition;
* both all-known and `age_min <= 2` ranks are stored; `rank_fresh_2m` is the default published race
  coordinate and every rank row carries `population_def` and `n_eligible`;
* top-30/50/100/300 are query views, not builds;
* session end comes from `phase2_session_calendar.json`; no later row exists;
* store `day_high_vs_sip_high_ratio` and `prevclose_vs_sip_clast_ratio`; the outer day-envelope
  warning is outside `[0.5,2.0]`, while previous-close hard warning is `abs(ratio-1) > 0.10`;
* `rank_eligible=false` nulls the quality-filtered rank family while preserving raw rows/ranks;
* previous-close session/date/age/source travel with every gain; floor-qualified or stale
  denominators cannot support rank-trajectory or era-stability claims;
* suspect split/bad-print rows are flagged, never silently deleted;
* `day_high_vs_sip_high_ratio` compares the raw lane's **day high** against the provider lane's
  **day max** bar high (a per-ticker maximum, never the last minute's high) — it is a retrospective
  diagnostic and never a rank filter;
* a previous close confirmed off the prior session's provider close by more than the declared 10%
  hard warning is a bad denominator: it nulls the quality-filtered rank family and raises a
  `prevclose_denominator_discrepancy` flag, while raw px/gain and the unfiltered `rank_unfiltered`
  are preserved so the ordering is never destroyed;
* `n_eligible` is the size of the quality-filtered (`rank_known`) population, not the `fresh_2m`
  count.

The source is expected to contain roughly 1.35B observed RTH name-minutes. The dense race board also
emits explicit not-known/stale population rows; the 20-day canary projects about 1.95B rows /
37.7 GiB for the full board. Build time remains roughly 3.5–7 hours at safe concurrency.

This board can observe trajectories such as #30→#12→#5→#2 throughout the session. Existing SIP
checkpoints provide an independent, full-roster morning ruler at twelve clocks. Legacy
`data/leaderboard/lb_*`/`path_*` is top-3/union-of-top-3 lineage only and has a wrong month-first
previous-close fallback on 51 dev days; the rebuilt board supersedes it.

### 4.4 Candidate-net minute board

Raw SIP net covers roughly 38–115 names/day. Its stored `net` includes full-day winners and is
future-selected.

Retrospective geometry may use the full roster with that label. Prospective roster membership comes
only from `data/sip/candidates/<day>.json` snapshot `top`/`margin` lists whose snapshot time is
≤ t; `winners_open` and `winners_prev` are excluded by construction and counted.

### 4.5 Quotes

Current quote files: 853.3M rows / 3.85 GiB / 1,068 days, 7–25 symbols/day (median 15). The roster is
the full-day union of top-3 names across snapshots and leaks future membership: approximately 56%
of stored symbols are not selectable at 09:35 and 25% at 10:00.

Existing quotes remain a retrospective, rank-selected raw lane. Prospective use joins quotes to a
causal roster table `R(day,t)`; future-selected quote presence/absence is metadata, never input.
Until causal acquisition lands, the stored lane serves only a median 57% of `R(day,t)` at 09:35
and 38% at 12:00. Missing quotes for newly added causal-roster names mean **not acquired**, never
“no quote”; no prospective quote channel is materialized from that absence.

The quote channel is a versioned **sibling**, not a prerequisite: quote perfection does not block
core observation construction. Freeze O therefore splits its blockers into gate classes — core
(`B1` raw 2025-02, `B2` 2026-04/05 missing names, `B5` storage, `B6` missing net manifests) and the
quote-sibling `B3_quote_lane` — and defines two independent readiness states: `core_full_ready`
(core bar/print/race corpus built) and `quote_channel_ready` (causal acquisition landed). The core
corpus always materializes the causal quote **roster** (`quote_rosters`); no prospective quote
feature may be materialized before `quote_channel_ready`, so the split cannot leak a null as a quote
state. The deny is registry-derived (the `prospective_quote_channel` value/absence vocabulary) with
the `quote_` prefix only as a frame-level fast path: a `quote_` field the frozen schema itself
declares as roster provenance (membership, source path/sha, row counts, sibling-gate state) is
bookkeeping about the roster artifact and stays legal — no prefix guard may reject existing roster
provenance. A future-selected quote absence or value is never a feature.

The recommended expansion is one bounded later acquisition:

* union of causal top-10 at the twelve B checkpoints plus causal A_open/A_pm top-3;
* about 39 names/day (max measured 52);
* projected 4.8–10.2 GiB, about 99 pages/day, roughly three hours at eight workers after a 20-day
  canary;
* stored under a new root, never overwriting existing quotes.

Top-20 is deferred until the canary measures out-of-net density. Candidate-net quotes are rejected
because that roster contains ex-post winners. Full-market quotes are unnecessary.

Before acquisition, fix `sip_ingest.py`: apply `guard_day`, measure free space on the output
filesystem instead of hard-coded `/mnt/c`, and write the full roster/rule/checkpoint provenance to
the manifest. No provider pricing is assumed.

### 4.6 Future PIT context

Float, shares outstanding, market cap and catalyst/news history are absent today. True turnover or
float rotation remains unavailable and is never approximated.

Future context attaches as versioned sidecars keyed by:

`instrument_id, effective_from, effective_to, source_vintage, source_sha`.

Adding `context.fundamentals@vN` or `context.catalyst@vN` never changes path, membership, segment or
occurrence identity and never rewrites earlier geometry.

## 5. Canonical observation corpus

Storage is not the constraint. The raw archive remains immutable truth, but repeatedly rebuilding
lossy minute/print views is unnecessary.

Four storage classes:

| Class | Meaning |
|---|---|
| raw archive | provider/raw bytes, immutable and authoritative |
| canonical observation | deterministic, reversible projection with parent-row identity and hashes |
| canonical-of-proxy observation | deterministic projection of a qualified third-party substrate; completeness claimed only inside its named population/source |
| rebuildable cache/representation | quote-minute aggregates, tensors, embeddings, ANN indexes |

### 5.1 Identity

* `path_id = (day, ticker)` — one market tape.
* `member_id = (day, family, ticker, entry_et, entry_rank)` — one fill/reference context.
* every segment/episode carries both `path_id` and the applicable `member_id`/anchor; raw tape
  deduplication never erases separate fill-anchored views.

The 255 shared A_pm/B600 paths collapse before raw-tape fitting/index/support counting. Membership
views remain separate, and support reports both independent-path and membership counts.

### 5.2 Canonical materializations

1. **`raw_index/day_registry`** — dev calendar, all raw/bar/universe/coverage shas, source/status and
   selection provenance.
2. **`paths` / `memberships`** — identities, anchors, source spans, coverage and raw row pointers.
3. **`prints.selected_paths`** — full available 09:25–16:05 print tape for each unique selected
   `(day,ticker)`, preserving every raw field plus source row ordinal and parent day sha. Estimated
   about 4–6 GB, month-partitioned and sorted. It is a reversible projection, not new raw truth.
4. **`grid.selected_paths`** — complete session grid with separate axes:
   * `bar_state = raw | provider | none`;
   * `print_state = path_print | excluded_prints_only | no_print`;
   * `within_observed_span`, `session_end`, full-day coverage class and terminal-censor metadata.
   Prospective readers cannot use full-day/censor fields. Tape silence, condition-excluded prints,
   tape end and provider absence never collapse.
5. **`race.minute_full`** — full raw/provider broad minute board, canonical-of-proxy; the canary
   projects about 1.95B dense population-minute rows / 37–40 GiB.
6. **`race.checkpoint_full`** — causal and retrospective full-roster checkpoint views
   (~73M clock rows / ~0.55 GB).
7. **`race.candidate_net`** — retrospective full-day and prospective snapshot-as-of-t views
   (~27.3M grid rows / ~0.55 GB).
8. **`quote_rosters`** — day/checkpoint/rank/symbol/score/rule table; existing quote rows remain raw.
9. **`coverage`** — per-layer presence/status and `selectable_asof_et`.

Selected-path prints are materialized because they preserve raw evidence, make multi-resolution
retrieval cheap and avoid re-implementing the condition/window join. Raw day files and row pointers
remain authoritative. Raw quotes are not copied; minute aggregates are caches with raw spans.

Expected initial canonical corpus: roughly 45–50 GiB including the dense board, plus same-directory
atomic-write transients. Planning headroom is conservatively **70–80 GiB**; the frozen gate reserves
**75 GiB** on the backing volume of the actual output mount (or direct cloud/object-store output).
The gate is measured conservatively, never from one guest number: `data/atlas` resolves onto Windows
C: through 9p, so its reading is the host volume itself, while the ext4 root on a WSL VHDX reports a
virtual capacity (≈895 GiB guest against ≈112 GiB host free) and must stack to a declared
`storage_host_volume` reading with evidence in the tracked resolved-state file — an unverifiable
virtual/unknown local backing refuses the full build. Measured capacity readings are per-run costs and
live in `costs.json` only, never inside the deterministic contracts. Representations/indexes are
additional rebuildable nodes.

### 5.3 Provenance and calendar

Day registry derives only from `basket_sim.dev_days()` and every access calls `guard_day`. Builders
never glob directories or trust a store index as the research calendar. Sealed 2024/2025-01 and
reserved 2026-06..08 tape is refused; 2021-01-29 is previous-close seed only.

Every immutable node manifest contains:

`node_id/version, schema, code sha, parent ids+shas, per-day input shas, config sha, keys/sort order,
coverage/selection classes, payload shas, status, supersedes/withdraws`.

Parent-sha drift makes a node stale; it creates a new version, never an in-place repair.

### 5.4 Default-deny causal registry

Observation tables physically exclude `outcome_*`, `ticket_constant`, future metadata and censor
columns. Every remaining column has explicit Freeze-O flags:

`observable_asof_rule, selectable_asof_et, coordinate_only, prospective_allowed,
retrospective_only, supportable_by_tier`.

Freeze O does **not** decide distance eligibility. Tape/state columns default to available for
Freeze-R consideration; Freeze R declares each view's exact channel tuple. `month`, `block`, family,
clock, tenure and coverage classes are split/audit coordinates, not distance channels. Row-level
roster selectability is checked before column access.

Full-day coverage class and terminal censoring may support retrospective audits/exclusions but never
prospective input.

## 6. Occurrence, detection and completion anatomy

Discovery segments store:

`segment_id, path_id, member_id/anchor_id, start_et, end_et, source_interval, scale,
representation_version, retrospective_only`.

Geometry/detection rows store:

`detection_id, segment_id, detected_asof_et, cutoff_ts_us, prefix_set, geometry_version,
detector_rule_sha, density, uncertainty, neighbour_path_id, neighbour_segment_id,
neighbour_own_bar_index, neighbour_own_et`.

Rules:

* a retrospective object becomes complete at `end_et`;
* a prospective object is usable only at `detected_asof_et`;
* the normal decision cut is the close of the completed bar, `(et_cut+1)*60` seconds after ET
  midnight; no later print/quote enters a prospective representation or its anatomy;
* a declared print-anchored event (for example CS-5 reopen) stores its exact print timestamp as the
  exception;
* anatomy begins after `end_et` for completed-morphology questions and after `detected_asof_et` for
  prospective-action questions;
* first legal anatomy row is the next member bar by bar identity, not `et+1`;
* retrospective and prospective anatomy are separate tables and never averaged;
* every anatomy field group carries `first_available_bar_index` and `supportable_by_tier`.

This supports both “what follows the completed morphology?” and “when did this structure first
become distinguishable, and what remained then?”

## 7. Rare/tail exemplar product

Rare/unique episodes remain catalogue rows whether or not they have neighbours.

After Freeze R, `tail.exemplars@vN` may select frozen geometry using outcome labels. It has no
dependency edge back into observations, representations, geometry or lexicons. Censored members'
tail classes are **undefined**, never false, and never enter matched-ordinary controls.

The retrospective predicate is frozen:

`tail_L(t) := max(high[t+1..tape_end]) / open(t+1) - 1 >= L/100`, for `L in {50,100,300}`.

The occurrence unit is each contiguous true run under `member_id`, with contiguity defined by
adjacent `bar_index` values—not adjacent ET minutes and not family-deduplicated `path_id`. Measured
totals:

| Level | members | contiguous runs | runs live on first observed bar |
|---|---:|---:|---:|
| L50 | 731 | 2,219 | 493 |
| L100 | 219 | 595 | 167 |
| L300 | 20 | 31 | 13 |

All 273 terminal-censored members are unavailable. The L300 scope is 18 day files / about 7.2M
U-path prints; L50 is 528 days / about 142.6M.

Every run stores raw pointers, density/uncertainty and matched ordinary contrasts. A run already
live at the first observed bar records an open-left start rather than fabricating `start_et`.
At frozen causal prefix stages the product asks for nearest ordinary analogues, preserving each
neighbour's own path/segment/clock/bar and excluding neighbours not observable by the query instant.

Rare-tail coverage is reported within liquidity/attention strata. No tail label changes training
weights, sampling, transforms or distances.

## 8. Freeze R — bounded representation matrix

Freeze R happens only after the outcome-blind corpus report. Geometry choices may use only the
permitted blind summaries in §2.

Exactly five principled hand views plus one learned view:

1. **magnitude-dominant** — level, return/depth, raw attention intensity and duration;
2. **normalized-shape-dominant** — declared scale-free path geometry, reference levels retained as
   audit coordinates;
3. **duration/event-dominant** — wall time, printed-bar duration, gaps/holes and change points;
4. **activity/microstructure-dominant** — print/bar arrival, size/count/notional, condition and
   available quote geometry;
5. **balanced multichannel reference** — fixed equal-weight combination of standardized view
   distances, never tuned into the best-looking geometry;
6. **one outcome-blind learned sequence family** — mandatory in the serious pilot.

No view is declared primary after seeing outcomes. Neighbour overlap and pairwise distance agreement
are published. Strong agreement is evidence of structure; disagreement is an empirical result.

### 8.1 Constrained learned family

One family only: a small masked-sequence temporal convolutional encoder, with causal and
retrospective variants of the same architecture.

* objective: masked-span reconstruction only; invalid/padded positions contribute zero loss;
* no future-return prediction, contrastive outcome proxy, cluster loss or policy loss;
* inputs: selected raw/reference-relative minute channels plus explicit validity/coverage masks;
* no clock, block, family, entry rank or future/censor input;
* causal variant uses left-only receptive fields and garbage-suffix invariance;
* retrospective variant is labelled, never used live and must be insensitive to out-of-window junk;
* every augmentation freezes with measured per-view neighbour displacement and a permitted bound
  **before** training; a crop/warp benign for shape is not automatically allowed for
  magnitude/activity; no masking across silence, halts, provider gaps or censor boundaries;
* train by day/block; all normalization/model parameters fit on one block and judged on the other;
  no overlapping source interval crosses the split;
* compare against the same architecture with frozen random weights;
* one configuration and one run per fold; no architecture or hyperparameter retry.

Before training, Freeze R records numeric one-shot gates for recurrence, mutual-neighbour support,
block stability, coordinate/family leakage, magnitude preservation, sparse exact recall and
SSL-over-random improvement, plus measured runtime. Failure retires this learned family for v0, not
the Atlas. These retrieval gates replace silhouette; they do not require clusters.

### 8.2 Geometry gates

Outcome-blind only:

* neighbour distances/identity versus tier-specific block/clock/level/volatility/coverage-matched
  surrogates;
* recurrence and mutual-neighbour support on independent `(day,ticker,source_interval)` units;
* block/era stability within each declared population and feed-source stratum;
* exact-vs-ANN recall by density decile, with exact search in sparse strata where needed;
* magnitude-rank preservation;
* prefix determinism and retrospective/prospective detection delay;
* view agreement/disagreement and augmentation displacement;
* recurring, uncertain, low-density and unique mass all published.

Stability is primarily within the population/era where a structure exists. Cross-family transfer is
valuable evidence but **not a universal kill condition**; A_pm and B600 observe different stages.
Any population-specific claim must reproduce across independent days/eras within that population.

Discrete motif IDs are created only if mode structure is evidenced. Otherwise Lexicon v0 is the
frozen continuous geometry plus neighbourhood/density/transition descriptors.

### 8.3 View × data-tier availability

Every result publishes which geometry the tier actually supports:

| Tier | Magnitude/shape/duration | activity/prints | quotes | learned minute sequence |
|---|---|---|---|---|
| selected paths | yes | yes | rank-selected where acquired | yes |
| candidate-net minute | yes | raw prints | only roster-covered | bar/print sibling versions |
| broad provider minute | yes, source-qualified | bar volume only | no | bar-only variant |
| full checkpoint | twelve-point morning only | no raw sequence | no | no |

No missing channel is encoded as a market zero, and a null from a non-supporting tier cannot enter a
cross-tier mode gate.


## 9. Versioning and later overlays

Immutable graph:

`raw.index → observations → representation → geometry → optional lexicon → human overlay →
anatomy → hypothesis`.

Changing an observation omission, roster, invariant or resolution creates a sibling version.
Earlier versions remain byte-identical; concordance compares versions. Future
`context.fundamentals` / `context.catalyst` sidecars attach by instrument and effective vintage
without changing episode identity.

Human vocabulary is overlaid only after within-name and race geometry freeze. Subsequent anatomy is
attached to every occurrence, including sparse/unique objects, and cannot redefine geometry.

## 10. Smallest serious implementation

### Stage 0A — Freeze O

Freeze:

* manifests/IDs/keys;
* dev calendar and raw provenance;
* physical observation-only schemas;
* multi-axis minute coverage and coverage lattice;
* prospective roster/rank rules, quality ratios and positive causal allowlists;
* the UTC→`America/New_York` DST-aware timestamp/session convention and per-day `session_end`;
* occurrence/detection clock semantics;
* storage/materialization contracts;
* per-tier supportability matrix;
* permitted blind-summary list.

No metric, distance channel, weight, scale, penalty, augmentation or learned-model parameter freezes
here.

### Stage 0B — Build canonical observation corpus

Build deterministically and resumably:

1. day/raw index;
2. paths/memberships/coverage;
3. full selected-path print projection;
4. multi-axis selected-path minute grid;
5. full unfiltered broad minute race board (canary may flag the qualified 2025-02 fallback);
6. checkpoint and candidate-net race boards;
7. causal quote-roster table;
8. selftests/reconciliation/costs.

Build the broad race board in one pass for all ranks; no arbitrary K defines observation. Before the
full v0 build: reacquire raw 2025-02 and the measured missing-name slice on 2026-04/05; repair or
explicitly block the unreconciled net-lane manifests (2026-05-21/29); fix all canary audit
defects; and verify at least 75 GiB of conservatively measured free space on the output mount's
backing volume (planning band 70–80 GiB; declare `storage_host_volume` when that mount is a
virtual/unknown local disk) or a cloud target. No acknowledgement flag may bypass these evidence
gates.

The causal quote lane is **not** on this list. `B3_quote_lane` is a versioned SIBLING: it gates
only the prospective quote channel (`quote_channel_ready`) and never the core bar/print/race
corpus (`core_full_ready`). The core corpus always materializes the causal quote **roster** and
its provenance; quote perfection is not a core prerequisite, and no prospective quote feature may
be materialized before the quote gate passes.

#### 10.1 Resume and proof chain (single heavy owner)

The order is fixed, and every step is a gate on the next:

1. `factory/scripts/basket_tape_atlas_observation.py --selftest` — pure checks only. **Necessary
   but not sufficient**: a pure check cannot prove runtime, so a green suite has twice hidden a
   defect that only an actual `build_one_day` surfaced. Treat pure green as a precondition, never
   as runtime proof.
2. `--stage contract` after any *contract-file* change (re-freezes `contract_lock.json` over
   contract/schema/causal_registry/canary_days/adaptive_choice_ledger). It does **not** cover the
   producer script: that is recorded separately as `code_sha256` in the manifest at build time, so
   a source fix needs no re-lock, only a rebuild.
3. `--stage canary`, then `--stage verify-canary` on the built corpus.
4. `--stage full --resolved-state .../blockers_resolved.json` is the only accepted full entry; any
   other resolved-state path is refused, and no acknowledgement flag substitutes for a verified
   evidence file.

Supporting lanes: net `atlas_net_index_reconcile.py --stage inspect|verify`; acquisition
`atlas_acquire_sip_bars.py --stage verify --scope feb2025|aprmay2026`.

**One heavy owner.** Only one agent runs a build. A 20-day canary is not complete merely because
the day loop finished: it is complete when `verify.json` exists, the tracked canary evidence is
regenerated (a stale `summary.json` carrying an old blocker id, `full_v0_ready` false, or null
determinism is a pre-repair artifact and must be rejected), and a second run is byte-identical.
Resumes must be validated per day, not assumed. Aborts from here are global-memory events, never
an agent decision to stop, and the operating rule is no signals, no kills, no WSL restart: require
headroom above the parent+child target before starting a run rather than intervening mid-run.

### Stage 0C — Blind corpus report and Freeze R

Inspect only permitted outcome-blind summaries. Freeze the five-view + one-learned matrix,
Invariance Registry, scale/metric/segmentation ruler set, tier-specific nulls, index/recall floors
and Adaptive Choice Ledger.

### Stage 1 — Within-name geometry

Build/query all six views; publish nearest-100 retrieval, density, recurrence, disagreement,
uncertainty and rare/unique mass. No outcomes or human labels.

### Stage 2 — Race geometry

Build independent geometries for each race tier. Do not merge their evidence and do not add cash.

### Stage 3 — Freeze empirical products

Freeze continuous within-name/race geometries and residual catalogue. Add motif IDs only where the
mode gate passes.

### Stage 4 — Human and anatomy overlays

Add human concordance, completed-pattern anatomy, prospective detection anatomy and rare-tail
exemplars as separate versioned nodes.

### Stage 5 — Economic hypotheses

Only now formulate exits, adds, re-entry, jump-ship, basket or execution rules.

## 11. Immediate build order after this DRAFT is approved

1. commit Freeze-O schemas/registries and negative leakage selftests;
2. implement the deterministic Stage-0 builder;
3. run a 20-day stratified canary;
4. independently audit identities, multi-axis minute coverage, broad-race ranks, parent shas and
   sealed/reserved refusal;
5. remove every audit blocker and rerun the canary twice;
6. run the full 1,066-day corpus only after acquisition and storage gates pass;
7. only then inspect blind corpus geometry and freeze R.

Resource policy: memory is not a 6-GiB design constraint, but it is the binding one. Report peak
RSS; warn/autosize at 15 GiB and hard-abort at 30 GiB. Worker count is sized as
`floor((soft limit - the producer's own measured resident set) / per-day worker cost)`, because the
producer is resident concurrently with every worker — a request that does not fit is refused, not
clamped into an OOM. Costs record the workers that actually ran alongside the request. The
per-vintage PIT roster is cached in a hard-bounded map and the day registry uses a count-only path,
so walking all 1,066 dev days cannot grow the resident set.

Resuming a crashed full build credits disk space only for payload bytes that RE-hash against a
recorded digest, taken from the completed manifest or, when no manifest exists yet, from the
incremental per-day progress file. Absent or mismatched payloads earn nothing.

Raw-lane provenance is the combined lane-set digest over every contributing file plus the alias map,
never a bare single-file hash, so a two-lane day verifies and drift on any one lane is detected. Disk gating follows the output filesystem/cloud target above.

Architecture work stops here. The next deliverable is the observation substrate.
