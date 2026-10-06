# PRE-REG-ENTRY-EV-01 — Where does the next dollar have positive realistic EV? (measurement; staged; internally split)

**Status: HISTORICAL DESIGN; economic interpretation and Stage-B gate superseded 2026-10-06.**
The **original design was recorded before FE-0** (freeze commit `f7cdc5c`, 2026-10-06
00:11 +0300; FE-0/FE-0b outcomes appended later the same day in `ebcaed5`, 00:55; the
533 day tables built after that, 01:18–01:43 +0300). This correction does **not** imply
the original freeze happened after outcomes. What is not pristine is the *present
combined document*: it carries the original design plus later feasibility/outcome
amendments in one file, and the discovery calendar was globally seen, so no section
here — including the DISCOVER/CONFIRM halves — is a fresh outcome-blind artifact. See
the provenance caution at §8. The completed measurement remains evidence about its
original population and timing. This correction does not retroactively turn it into a
sparse A+ event test or a new pre-registration, and it does not re-freeze or re-arm the
superseded gate. No new outcomes are authorized. Protected blocks and live code/flags
remain untouched.

### Interpretation correction — applies before reading the historical design below
- Stage A sampled repeated, minute-weighted top-ten states, not independent rare setups.
  The committed reader implements pooled horizons and eight specific rank-6–10 proxies,
  not the advertised complete coordinate surfaces.
- Its ≥$1 previous-close filter does not verify the denominator or repair upstream rank.
  The NaN-comparison defect can pass the filter; validate before reranking the full board.
- The builder anchors exits to `decision_time+h`, not necessarily `actual_fill_time+h`.
  Retrospective signed promotion age and its pre-promotion/null states are not predictors.
- Full-spread-per-leg repricing is a conservative scenario, not an exact unavoidable
  92–116bps round-trip floor; quote-side executable prices and size units require audit.
- The Stage-A→Stage-B gate is **retired**: poor immediate continuation does not veto
  pullback/reclaim or discounted entry after candidate qualification. Those are NOT TESTED.
- Positive median and deleting top-five days are not universal requirements for convex
  mechanisms. Tail sensitivity is diagnostic; tail reality/executability is the question.
- The uniform −259bps fill offset below a buy limit is **not a queue-adverse fill model**.
  A lower buy price is price improvement; selection, timing and subsequent path are separate.
  Do not execute Stage B from this specification without a separate corrected registration.
- Observed constraints on record: `exact_zero_return_share` cannot identify same-bar
  collapse because actual exit stamps are absent; the reader emits only pooled horizons,
  retrospective promotion anatomy and eight rank-6–10 proxies; the day tables live under
  the gitignored `data/entry_ev/stage_a/` root (1,599 files: 533 parquet + manifest + done
  markers) and are reproducible from the committed producer, not carried in git.
- Unresolved authoritative-close information: AMV 2022-09-28's official close identity is
  still open. An earlier audit reported 82.12 at a 16:00 bar stamp, which under the
  bar-start contract is outside RTH; that needs an authoritative closing-price/auction
  source and is NOT certified here. Do not substitute the 16:00 stamp, and do not
  substitute any value for the board's unverified 52.01 (prior-session bar 912 stamp).

## 0. Posture (what the project already knows)

- The phenomenon is real and extremely right-tailed; a few monster days legitimately may
  contain much of the EV. Tail dependence is to be **decomposed** (real/executable vs
  artifact), not punished. Cash and zero-trade days are valid.
- One simple, causal formulation already captured part of it OOS: the frozen flush-bid
  rule, **+1.14%/trade net of 100bps, 12/14 untouched months** (H025). Its live execution
  problem is queue-adverse fills — an execution fact about one mechanism, not a verdict
  on the phenomenon.
- H042 (cash-first selective admission) established: selective admission changed the
  economics materially vs forced-upfront ownership (gross per trade rises with the derived
  fee hurdle; a >0 discovery region existed where the forced-upfront forms were uniformly
  negative). Its tested **marketable** implementations did not earn promotion: at
  25/50bps they were weakened by conservative quote-cost sensitivity; at 100/150bps
  the surviving cells remain too weak/tail-dependent to promote as measured.
  **Non-promotion of those formulations ≠ absence of signal or of selectivity economics.**
- Coarse matched-state lookalike studies found no robust directional separation; that
  constrains representations already tested, not the question.

Economic question (the only objective of this study):
> **Where, causally, does the next dollar have positive realistic expected value inside
> the extreme top-gainer phenomenon — and through which entry mechanism?**

## 1. Cost is mechanism-specific (no single friction for all entries)

- **Marketable entry** costs must be reconstructed side-by-side: ask minus modeled buy
  price, and modeled sell price minus bid. The old full-spread-on-each-leg numbers are
  sensitivity scenarios, not an exact round-trip floor.
- **Resting-limit entry** does not pay crossing cost; it pays fill probability and
 selection. The historical phrasing "live fills realized a mean −259bps vs the intended
 price; that offset is a measured ruler, not a law" should not be read as an adverse-
 selection or queue-priority ruler: **−259bps is not a queue-adverse fill model**. It is
 price improvement on a separate selection/path. Tape first-touch support and paper
 broker fills do not certify real exchange queue priority.
- Every result in this study is reported under the mechanism that produced it. No
  mechanism's number is transferred to another mechanism.

## 2. Staged design (bounded; explicitly NOT a giant grid)

**Stage A — gross continuation shape (no entry mechanisms, no thresholds).**
On the causal minute board (top-10 by gain; ranks 1–10 reported separately), for decision
minutes t in RTH on the discovery block: gross (pre-cost) next-open → next-open return at
h ∈ {1,3,5,10,15,30,60}, plus a coarse path proxy (same-bar-conservative drawdown-first;
MFE/MAE as rulers). Reported as 1-D / 2-D surfaces over at most these coordinate families:
rank; gain; short-horizon velocity (1/3/5/15); distance from the running session high;
time-of-day; promotion age (first minute rank ≤ 5); pullback depth. Volume ratio where the
join is available. No cell selection, no fitted combination: the deliverable is **shape +
breadth + tail anatomy** (see §5). Budget guard: ≤ 7 horizons × ≤ 4 coordinate families.

**Stage B — HISTORICAL, RETIRED immediate-continuation prerequisite.**
The original gate below was conceptually invalid for qualified-name + delayed entry:
it required chasing to work before allowing non-chasing mechanisms. Failure of Stage A
does not close these alternatives. They remain untested and require a new specification,
not a silent amendment using already-inspected outcomes.
The historical, unexecuted mechanism list was:
  (a) marketable next-open;
  (b) first pullback-reclaim: after a pullback ≥ x% from the running high, enter at the
      next open after the first completed bar that reclaims the running high since that
      pullback began; x ∈ {3,5,10}%;
  (c) resting limit −y% below the current price; y ∈ {5,10,15,20}%; two fill treatments:
      optimistic (fill at the limit when a later print trades at/through it) and
      live-adverse (fill only on a ≥ $0.01 trade-through, at limit − 259bps).
Exit: open of first valid bar with et ≥ (entry minute + h). No intra-hold management.

**Stage C — compression to a candidate.**
If a stable pattern emerges (same sign and CI excluding zero in both DISCOVER and CONFIRM;
≥ 100 distinct days; tail anatomized as real/executable; simple enough to state in a few
causal conditions), compress it to the smallest causal state + simplest decision and file
it as a candidate formulation with its own pre-reg. Otherwise the result is recorded
negative for the tested representation.

## 3. Data, splits, causality

- **Discovery block only: 2021-02-01..2023-03-14 (533 days).** Internal split:
  DISCOVER = 2021-02-01..2022-04-07 (300d); CONFIRM = 2022-04-08..2023-03-14 (233d).
  Both halves reported; CONFIRM gets no design changes (any change = new pre-reg).
- **Protected data untouched**: validation (2023-03-15..2023-12-29 + 2025-02-03..2026-05-29),
  sealed (2024 + 2025-01), reserved (2026-06..08). Enforced by path allowlist, never by
  month assumption alone. `basket_harvest_anatomy.is_dev_day`-class guards re-checked.
- **Board**: `data/atlas/observation/v0/race.minute_full` (px = close of last completed bar
  et ≤ t−1; causal ranks). Forward opens from the lane pinned by FE-0; event UNKNOWN when a
  forward open is unavailable (never 0), coverage reported per bucket.
- **Volume join**: `data/ohlcv_<month>.parquet` / `clean_ohlcv_<month>.parquet` where the bar
  exists. **Quotes**: `data/sip/net/quotes/<day>.parquet` where covered (ATLAS top-3 union;
  coverage reported per bucket).
- Decision at t uses bars et ≤ t−1 only; entries at the open of the first valid bar et ≥ t;
  no same-bar execution.
- **Correction to this clause (2026-10-06):** the design *required* `prev_close` to be
  verified non-NaN and stated that any event with an unverifiable prev_close would be
  excluded and counted. That requirement was **NOT implemented and NOT met**. The
  executed guard only tested the board's stored value for non-null and ≥$1, which does
  not verify the true prior-session close; AMV 2022-09-28's incorrect value is above $1
  and passed with a false discrepancy flag. Treat the design clause as unmet, not as a
  satisfied guard, and see §8 and the selection-integrity audit for what was actually
  filtered and what remains to be rebuilt.

## 4. FE-0 feasibility spike (go/no-go, before the full run)

Document: (1) the bar lane that reproduces the board's `px` on overlap days and its forward
coverage per day/symbol (including the known raw-file provenance limitation); (2) volume
and quote join coverage; (3) a 3-day smoke of the Stage-A surface with event counts and a
runtime estimate for 533 days. If coverage is structurally poor, the largest usable subset
is reported, and the study proceeds on it only if the subset is not outcome-selected.

## 5. Tail anatomy protocol (for every positive region)

For the top contributing days/trades of any positive region, record: (i) data validity —
verified prev_close, split sanity, halt/auction context; (ii) executability — measured
spread and displayed depth at the entry/exit minutes, resting-fill realism; (iii) mechanism
membership — does it belong to the state/mechanism being measured, or is it one-off?
Monster days stay in the mean; they are classified, not deleted.
**Correction note (2026-10-06):** the depth component must apply the local lot-to-share
×100 conversion. The historical raw-lot depth percentages were computed without it and
are WITHDRAWN, not rescaled; $500/$1000 in the old audit meant account capital, not
$500/$1000 order sizes. No corrected depth percentage is asserted anywhere in this
document.

## 6. Historical escalation gates — RETIRED, not current requirements

The original design required ≥200 observations, ≥100 days, positive net and CI bounds,
and survival after deleting the top-five days. This was not a general economic standard:
minute counts are not sparse event counts and convex EV can legitimately depend on real
monster days. The corrected question separates name qualification from entry price and
anatomizes the tail rather than requiring its removal. No protected-half read is authorized.

## 7. Non-goals and discipline

- No full cross-product of coordinates; no "largest cell"; the purpose is shape discovery
  and compression (many measurements → stable pattern → small causal state → simple
  decision).
- No classification targets; no max/min-over-future labels; outcomes are realized
  executable dollar returns at fixed horizons.
- No threshold, clock, N, list size, stop or horizon is promoted; all are rulers. Rulers
  must never be quoted as conclusions.
- No interference with H025 / the paper bot; no live code or flag changes.

## 8. FE-0 / FE-0b outcomes (2026-10-06)
**Provenance caution on this block (added 2026-10-06):** the original design was recorded
before FE-0 (freeze `f7cdc5c`, 00:11 +0300), and this section was appended afterwards
(`ebcaed5`, 00:55) — so the original freeze was **not** post-outcome. What is not pristine
is the *present combined document*: it now carries the original design plus
feasibility/outcome amendments in one file, and §8 itself already contains Stage-A-shaped
numbers (the 3-day smoke baseline, below). The design header's "before any number of this
study is computed" wording therefore holds for the original design text but **not** for
this document as a whole, and the discovery calendar was globally seen — the
DISCOVER/CONFIRM halves are not a fresh outcome-blind confirmation set. Treat §8 as
contemporaneous FE-0 record, not as a clean pre-outcome document.

- **Lane pinned (px parity verified; this does NOT certify ranking or denominators)**:
  the board's price lane is `data/ohlcv_<month>.parquet`. Recorded verification:
  board `source_sha256` reproduced exactly from that lane's file sha + alias-map sha, and
  `px` = close of last completed bar `et ≤ t−1` reproduced exactly (FE-0: 2.0M+ rows × 3
  days; committed `lane_verification.json`: 26/26 months lane-sha + 52/52 day digests,
  390,000/390,000 sampled px exact). Byte parity with the frozen board establishes
  consistency of reproduction only — it does **not** validate previous-close semantics,
  corporate actions, or economic ranking.
  `clean_ohlcv_*` is a near-miss (99.5–99.8%); `sip/net/bars` is roster-only. Lower
  coverage months must pass the same lane-sha gate before use.
- **Coverage (rank ≤ 10 rows)**: forward open at h=0 100%, h=5 ≈ 98.5–98.7%, h=30 ≈ 92%
  (dominated by end-of-day censoring); coverage is rank-bucket invariant; missing =
  UNKNOWN, never 0. Volume joins in the same lane with identical coverage.
- **Runtime — the original estimate was wrong; observed values below supersede it.** The
  pre-run estimate "≈ 59 s/day single-threaded → ≈ 8–9 h for 533 days" was roughly an
  order of magnitude too high. Observed from the 533 committed per-day manifests:
  median **8.8 s/day** (min 3.4, max 13.7), **total 4,677.9 s ≈ 1.30 h** of summed
  per-day runtime, built 2026-10-05 22:18:17Z → 22:43:28Z across parallel shards.
  Sharding + per-day incremental outputs + resume did work as designed; use the
  observed figures for any future capacity estimate.
- **Population filter, NOT an AMV repair**: `prev_close` non-null and ≥$1 plus old board
  flags selects a narrower cohort. It does not independently verify the true prior-session
  close; confirmed AMV's incorrect value is above $1 and can pass. Reranking must follow
  verified denominators and any intended universe exclusions.
- **Execution-data limits**: SIP quotes cover ≈ 34% of top-10 symbol-days; SIP trades
  ≈ 81%. Stage A does not depend on either; measured-spread re-cost applies to the covered
  subset only.
- **Stage B(c) resting-limit deferred**: mechanically computable from SIP trades, but that
  lane is a gain-correlated gated download (72.4% of top-10 symbol-days; in-lane share
  rises monotonically with gain decile; plus fill-rate compounding). Per §4 it is not run
  on this subset; it requires its own registration (re-ingest the missing days, or a
  differently framed measurement). Stage B(a)/(b) run on the full ohlcv lane.
- **FE-0 smoke baseline (3 days, gross, acceptance target for the Stage-A producer)**:
  pooled h=5 n=11,510 mean −0.078% median +0.000%; h=30 n=10,757 mean −0.310% median
  −0.293%; rank 1-3 worse than 6-10; T2 (12:00–14:49) positive at both horizons.

## 9. Deliverables

Producer scripts under `factory/scripts/entry_ev/`; artifacts + a read packet under
`factory/artifacts/entry_ev/`; per-day event tables under `data/entry_ev/stage_a/`;
`factory/EXPERIMENTS.jsonl` entry; STATE/HYPOTHESES record. Commit + push to
`basket-phase2-f1` (clean cutover; no unrelated files).

— parent, 2026-10-06
