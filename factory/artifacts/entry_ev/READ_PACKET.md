# ENTRY-EV-01 Stage-A read packet (discovery block; measurement only)

**Scope.** Discovery days 2021-02-01..2023-03-14 (533/533 built), top-10 causal minute board
(`race.minute_full`), next-open → next-open gross returns at h ∈ {1,3,5,10,15,30,60}.
Guard (pre-reg §8): prev_close non-null, ≥ $1.00, not stale/floor-qualified, no
prevclose-discrepancy flag. Kept 1,601,807 events; dropped 468,146 — **entirely** the
prev_close < $1.00 rule (null/stale/floor/discrepancy = 0). No protected data. Producers:
`factory/scripts/entry_ev/stage_a_build.py` (+ tests), `stage_a_read.py`;
lane verified for all 26 months (`lane_verification.json`: 52/52 board digests match,
px exact 1.000000). Artifact: `stage_a_readout.json`.

## 1. Coverage
Forward availability: h1 99.35%, h3 98.84%, h5 98.33%, h15 95.76%, h30 91.91%, h60 84.16%
(EOD censoring dominates at long h; missing = UNKNOWN, never 0).

## 2. Pooled (gross)
| h | mean | median | DISCOVER | CONFIRM |
|---|---|---|---|---|
| 1 | −0.011% | +0.000% | −0.012% | −0.010% |
| 3 | −0.032% | +0.000% | −0.033% | −0.030% |
| 5 | −0.053% | +0.000% | −0.056% | −0.047% |
| 10 | −0.100% | +0.000% | −0.107% | −0.088% |
| 15 | −0.144% | −0.070% | −0.153% | −0.125% |
| 30 | −0.252% | −0.211% | −0.272% | −0.211% |
| 60 | −0.411% | −0.410% | −0.447% | −0.341% |

Unconditional top-10 minute-board continuation is negative gross at every horizon, in both
halves.

## 3. Conditional surfaces (8 coordinates × h; full tables in the artifact)
Nothing material. Directionally: rank 1-3 worse than 6-10; higher gain worse; strong recent
momentum worse (ret5 top decile −0.22% at h5; ret15 top decile −0.25%); volume spikes worse
(vol30_ratio>1.5: −0.10% at h5); volume dry-up slightly positive (vol30_ratio<0.5: +0.015% /
+0.037% at h5/h15 — 1.5–3.7bps, two orders of magnitude below the 92–116bps measured floor);
at-day-high ≈ 0; time-of-day early worst, midday ≈ 0, late negative; dd-from-high middle
bucket ≈ 0. The best eligible region anywhere: **+3.7bps gross (median 0.0)**.

## 4. Promotion anatomy (the one large positive region)
- **Hindsight bucket** `promo_age ∈ [−5,0)` (name crosses into top-5 for the first time
  1–5 minutes AFTER t): n=18,904, 527 days, h1 **+0.994%**, h3 +2.379%, h5 **+3.086%**,
  h15 +2.620%, h30 +2.195% (medians +0.40%/+2.00% at h1/h5; DISCOVER +2.83% / CONFIRM +3.65%
  at h5). This is a **future-conditioned** state — you cannot know at t that the crossing
  happens in the next 5 minutes; the crossing is (mostly) the price move itself.
- **Post-promotion** (0–5 min after first top-5): h1 −0.041%, h5 −0.206%, h30 −1.032%.
- **Causal proxies at t** (rank 6–10 + observable state, all computable at t):
  momentum (ret5>0) ≈ 0/−0.05%; ret5>1% −0.10% (h5); ret1>2% −0.21% (h5), −0.86% (h30);
  near rank-5 boundary (gap<1%) −0.05% (h5); volume spike −0.10%; at day high −0.03%;
  volume dry-up +0.015% (h5). **Every causal version is ≈ 0 or negative.**
- **Reading.** The promotion climax is where the money is, and it is un-enterable by
  prediction with price/volume state at the minute level: nothing sits in front of it. The
  tradable-looking "A+ moment" (name becomes top-5) *fades*. The run-up belongs to whoever
  is already long.

## 5. Stage-B gate (§2, §6): NOT triggered
Criterion: Stage-A region gross mean AND median above a stated margin (>120bps) over the
measured mechanism cost floor (92–116bps round trip). Best eligible region: +3.7bps.
The hindsight region is excluded by construction (future-conditioned). Per pre-reg §2,
Stage B(a)/(b) does not run on these coordinates; (c) stays deferred (§8).

## 6. What this falsifies — and what it does not
- **Falsifies** (tested representation): marketable next-open minute-level entries on the
  top-10 extreme-gainer board of the discovery block, in this coordinate set, at h ≤ 60 —
  gross EV is insufficient by ~2 orders of magnitude; this is not a cost-margin question.
- **Does not falsify**: passive/limit entry mechanisms; other populations (rank >10,
  premarket, sub-$1 cohort — guard-excluded and counted); other state representations
  (e.g. order-flow); longer holds; funded/owned mechanisms; H025 or the paper bot.
- Scope notes: guard excludes the sub-$1-prev_close cohort (468,146 rows; ~7% of 2021 rows
  rising to ~47% by 2023) — deferred as a labeled sensitivity, not silently dropped;
  DISCOVER/CONFIRM split reported everywhere but the discovery block is not globally
  pristine (used by prior research); EOD censoring at h≥30 is real and treated as UNKNOWN.

— parent, 2026-10-06
