# PRE-REG-ENTRY-EV-01 — Where does the next dollar have positive realistic EV? (measurement; staged; internally split)

**Status: FROZEN 2026-10-06, before any number of this study is computed.**
Measurement / description only. No rule promotion, no strategy selection, no threshold
tuning, no protected-half read, no bot or live-flag changes. If a design element is found
unimplementable, the study stops and is re-registered; it is never silently adjusted after
seeing outcomes.

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
  25/50bps they under-price measured crossing cost (92–116bps round trip for covered
  names); at 100/150bps the surviving cells are too weak/tail-dependent as measured.
  **Non-promotion of those formulations ≠ absence of signal or of selectivity economics.**
- Coarse matched-state lookalike studies found no robust directional separation; that
  constrains representations already tested, not the question.

Economic question (the only objective of this study):
> **Where, causally, does the next dollar have positive realistic expected value inside
> the extreme top-gainer phenomenon — and through which entry mechanism?**

## 1. Cost is mechanism-specific (no single friction for all entries)

- **Marketable entry** pays crossing cost: measured full quoted spread 46–58bps per leg
  for covered names (round trip 92–116bps); the 25/50bps rungs are below this floor.
- **Resting-limit entry** does not pay crossing cost; it pays fill probability and adverse
  selection (live fills realized a mean −259bps vs the intended price; that offset is a
  measured ruler, not a law).
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

**Stage B — entry mechanism only where Stage A warrants it.**
Stage B runs only for Stage-A regions meeting a pre-stated economic criterion (gross mean
AND median in the region exceed the measured mechanism-equivalent cost floor by a stated
margin — the criterion is fixed in the Stage-A readout BEFORE Stage B runs, and Stage A
itself is reported in full regardless). For those regions only, compare, on the same
events and horizons, in net dollars per $100 deployed:
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
  no same-bar execution; prev_close used by the board must be verified non-NaN (the
  AMV-2022-09-28 defect class — any event with an unverifiable prev_close is excluded and
  counted).

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
membership — does it belong to the state/mechanism being measured, or is it a one-off?
Monster days stay in the mean; they are classified, not deleted.

## 6. Escalation gates (for a later, separate internal-forward study)

A (state × mechanism × h) family may be carried into a separate confirm-only study iff, in
DISCOVER alone: n ≥ 200 events on ≥ 100 distinct days; net > 0 at the mechanism-specific
measured cost; day-clustered 95% CI lower bound > 0; survives excluding its top-5
event-days; and CONFIRM shows the same sign with a 95% CI excluding 0. Failing families are
recorded negative for the tested representation. **No gate authorizes reading the protected
half** — that remains a separate owner-gated decision after a frozen formulation.

## 7. Non-goals and discipline

- No full cross-product of coordinates; no "largest cell"; the purpose is shape discovery
  and compression (many measurements → stable pattern → small causal state → simple
  decision).
- No classification targets; no max/min-over-future labels; outcomes are realized
  executable dollar returns at fixed horizons.
- No threshold, clock, N, list size, stop or horizon is promoted; all are rulers. Rulers
  must never be quoted as conclusions.
- No interference with H025 / the paper bot; no live code or flag changes.

## 8. FE-0 / FE-0b outcomes (2026-10-06, appended before any Stage-A number; economic design unchanged)

- **Lane pinned (verified)**: the board's price lane is `data/ohlcv_<month>.parquet`
  (board `source_sha256` reproduced exactly from that lane's file sha + alias-map sha;
  `px` = close of last completed bar `et ≤ t−1` reproduced exactly on 2.0M+ rows × 3 days).
  `clean_ohlcv_*` is a near-miss (99.5–99.8%); `sip/net/bars` is roster-only. Lower
  coverage months must pass the same lane-sha gate before use.
- **Coverage (rank ≤ 10 rows)**: forward open at h=0 100%, h=5 ≈ 98.5–98.7%, h=30 ≈ 92%
  (dominated by end-of-day censoring); coverage is rank-bucket invariant; missing =
  UNKNOWN, never 0. Volume joins in the same lane with identical coverage.
- **Runtime**: ≈ 59 s/day single-threaded → ≈ 8–9 h for 533 days; shardable; per-day
  incremental outputs + resume required (repo doctrine).
- **Guard (AMV class)**: require `prev_close` non-null AND ≥ $1.00 AND not stale /
  floor-qualified, and exclude `flag_prevclose_discrepancy`. Verified trap: GHSI
  2021-03-01 (prev_close $0.508, gain 4.5–4.8×) passes the board's own flags.
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
