# ENTRY-EV-01 Stage-A read packet — corrected scope and provenance

## Status

**Historical measurement retained; broader A+ conclusions withdrawn.** The original
numbers describe the original board, not a population reranked after verified previous
closes. ENTRY-EV did not test rare sparse setup events, small causal conjunctions, or
qualified-name followed by delayed pullback/reclaim/discounted entry. Those objects remain
open. No new experiment or protected outcome read is authorized by this correction.

## 1. What was actually measured

- 533 seen discovery dates, 2021-02-01..2023-03-14.
- 1,601,807 repeated ticker-minute rows selected from the already-ranked top ten,
  then filtered to stored previous close ≥$1 plus existing board flags.
- 468,146 rows removed, all by the previous-close-below-$1 filter. This is a
  **population restriction, not previous-close validation**.
- Each available minute row receives equal weight. Persistent ticker-days contribute
  many overlapping horizons. These counts are not independent setup/trade counts or
  an executable daily portfolio return.
- Entry: first observed open at/after decision time t. Exit: first observed open
  at/after **t+h**, for h in {1,3,5,10,15,30,60}. This is decision-anchored, not
  necessarily h minutes after a delayed fill. Long gaps can shorten actual ownership;
  entry and exit can even use the same later open. Missing prices stay UNKNOWN.
- No EOD value target: horizons crossing the session boundary are censored.

## 2. Original pooled results (gross; unchanged)

| Decision horizon | Minute-weighted mean | Median | Earlier seen block mean | Later seen block mean |
|---|---:|---:|---:|---:|
| 1m | −0.011% | 0.000% | −0.012% | −0.010% |
| 3m | −0.032% | 0.000% | −0.033% | −0.030% |
| 5m | −0.053% | 0.000% | −0.056% | −0.047% |
| 10m | −0.100% | 0.000% | −0.107% | −0.088% |
| 15m | −0.144% | −0.070% | −0.153% | −0.125% |
| 30m | −0.252% | −0.211% | −0.272% | −0.211% |
| 60m | −0.411% | −0.410% | −0.447% | −0.341% |

Forward availability was 99.35% at h1, 98.33% at h5, 91.91% at h30, and 84.16% at h60.
These support a descriptive negative for generic immediate continuation on this
original sample. They do not certify an independently validated top-gainer population.
The two chronological blocks were already inspected: they are not a pristine new
confirmation set for a rule derived after this readout.

## 3. Producer → artifact → claims

Historical builder: `factory/scripts/entry_ev/stage_a_build.py` (behavior/data retained).
Original reader/artifact remain in git history and `stage_a_readout.json` (annotated).
Corrected reader: `factory/scripts/entry_ev/stage_a_read.py` → `stage_a_readout_v2.json`.
The parent exercised the corrected CLI over all 533 saved days: original pooled/proxy
numbers and observation-level sensitivities matched unchanged. Behavioral verification:
23 build/read regressions passed; v2 explicitly separates observation and day sensitivities.

The original reader emitted:
1. Pooled horizon means/medians and seen chronological splits.
2. Retrospective signed-promotion anatomy.
3. Eight hand-written rank-6–10 subsets: all; ret5>0; ret5>1%; ret1>2%; within one
   percentage point of rank-five gain; vol30 ratio>1.5; vol30 ratio<0.5; near running high.
4. A subsequently retired immediate-continuation gate.

It did **not** emit the claimed complete rank/gain/velocity/time-of-day/pullback/volume
surfaces. Those broad marginal-surface claims are withdrawn from the evidence packet;
ad-hoc session tables are not a committed producer/artifact chain.
“Best +3.7bps” was best only among these listed proxy/horizon subsets, not an optimum
or exhaustive upper bound on causal EV. The legacy `top5_share` and `mean_excl_top5`
removed five individual minute observations—not five days. Corrected output explicitly
separates event sensitivity from day-balanced sensitivity; neither is portfolio dollars.

## 4. Promotion anatomy: interesting, but retrospective

Rows whose first top-five entry occurs 1–5 minutes later had approximately +0.994%
gross at h1, +2.379% at h3, and +3.086% at h5. The 18,904 count is selected minute rows;
the horizon-specific available counts differ. There were 527 distinct days.
This conditions on a future promotion, including future membership and crossing time;
it is not an observable entry rule. The post-first-promotion 0–5-minute subset had
approximately −0.206% at h5 and −1.032% at h30 on the original board.

The few reported observable proxies did not recover the retrospective run-up value.
**This does not prove that impending promotion is unpredictable.** Relative states,
small conjunctions, sparse episode definitions, tape, and other representations were
not exhausted. A reasonable interpretation is that visible promotion often arrives
near the climax of a burst, rather than automatically initiating an easy chase.

## 5. Population integrity is unresolved

The AMV-class defect can pass the Stage-A filter: an incorrect previous close can be
non-null and above $1, while a missing independent comparison leaves the discrepancy
flag false. Hash-perfect reproduction verifies consistency with the old board, not
correct economic denominator/rank semantics. Excluding a bad ticker after ranking does
not restore the legitimate replacement omitted from the old top ten. Previous-session
close and corporate-action conventions must be validated before full-universe reranking;
any intended universe/price filters must also precede selection.
Reproducible evidence: `audit_selection_integrity.py` → `selection_integrity_audit.json`.
AMV's unverified above-$1 close (52.01, prior-session bar 912) survived the old filter.
GHSI's local 1-for-6 split changes a 09:45 raw +433% gain to −11%; full-cross-section
reranking brings ITI (old rank 11) into the top ten, absent from the post-hoc filtered set.
The latter is a split-only counterfactual, NOT a repaired full population. The exact AMV
official close still needs an authoritative closing-price/auction source; a 16:00
bar-start price must not automatically replace the prior RTH close.

## 6. Invalid gate retired; delayed entry remains NOT TESTED

Failure of immediate next-open continuation is not a valid prerequisite for refusing
to study pullback/reclaim or discounted entry. Strong names can be poor immediate buys;
H025 is empirical prior evidence that qualification and entry price are separate.
The original Stage-B gate, positive-median requirement and automatic tail-removal gate
are retired. No delayed-entry outcome was generated here. Historical specification is
retained in `researches/PRE-REG-ENTRY-EV-01.md`, labeled superseded rather than retrofitted.

## 7. Execution qualification and open economic objects

Charging full quoted width on both legs is a stress scenario (~twice midpoint-to-touch
cost at identical quotes), not exact 92–116bps execution. The audit's ±1-minute summary
included post-fill quotes, so it is not as-of and has no guaranteed temporal bound.
Its depth percentages are withdrawn: the existing historical lot-to-share ×100 policy
was omitted. Its $500/$1000 ruler meant ACCOUNT capital, not $500/$1000 ORDERS.
As-of side-aware prices, size units, actual order notional, latency/impact and coverage
need separate measurement. A fixed lower buy-price offset does not model queue selection.

What stays: generic minute-weighted immediate continuation and several obvious chase
proxies were poor on the original ≥$1 previous-close sample. What remains open: sparse
A+ qualification events; 2–4-condition causal states; qualification then favorable entry;
passive fills; other populations/representations; and how much real tail EV is executable
at actual $500/$1000 order sizes. H042 stays closed as extraction candidate; selective
admission remains a structural lesson. H025 fill realism is high priority, not sole doctrine.
