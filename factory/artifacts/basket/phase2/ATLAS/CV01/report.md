# CV01 — current-dollar vs ownership-continuity (A/B evidence readout)
**DEV ANATOMY under the next-open + 50 bp/side model. Not a validated policy: no alpha claim, no
optimal horizon/size/stop.** Contract `CV01/contract.json` (sha `3c599dd36569a56e`); code pins A
`2089a7c3` (`basket_current_dollar_curve.py`), B `1caf2986` (`basket_ownership_continuity.py`); base
`64c477c8` = states `60c65d43…`, executions `211cfd0d…`, members `7d9fd196…`; panel `2a021eda…`.

## Primitive and branches

`CV = W_hold / W_exit − 1` on the **current next-open liquidation-dollar** baseline. Entry cost is
common/sunk; the 50 bp sell fee is common to both A continuation alternatives and cancels.
In the ownership experiment, sell/re-enter adds **one** round trip versus retaining
(`fric_k = (1−s)/(1+s) = 0.990049…`). Cash return is 0, so cash = 1 after the sale.
**A (retain)** holds the already-owned unit to the common endpoint; `W_exit` is its numeraire only.
**B (sell → re-enter)** actually sells, waits in cash, and buys once at the next open after the
first completed post-sale bar whose close is ≥ the GROSS actual exit price.

## Support, censoring, uncertainty (published)

- 6,160 members (273 censored); **1,888,885 clock-eligible decisions**, of which **185 lack an
  immediate liquidation print** and stay UNKNOWN (never 0); 1,888,700 have one. `h0` CV is exactly 0.
- B support lattice `data/atlas/continuation/cv01/B/support_by_horizon.parquet`: event-member offsets
  checked 5,165/5,165, mismatch 0. h30: 5,123 at risk / 4,442 priced / 681 internal gaps / 0 unknown
  beyond observed tail / 42 outside session. A's separate `A/support_by_horizon.csv` counts state
  opportunities. Missing in-session endpoints are UNKNOWN; past `session_end` is not at risk.
- B census (`B/manifest.json`): 5,165 sales executed (4,946 complete + 219 censored tape), **4,793
  re-entries** (incl. 214 censored sales with observed reclaim), **1 unresolved** (no next open —
  unknown, not 0), 991 never triggered, 3 preempted. Ledger/legacy reconcile 6,160/6,160; legacy
  `giveback:10` exact (4,946 / A_pm 2,596 / B600 2,350 / block1 3,315 / block2 1,631 / judged 5,887 /
  censored 273); smoke 12/12; peak 1,292 MiB; 86,384 curve rows.
- B estimand = mean over **day × family cells**, with matching day/month-clustered paired SEs.
  A first averages states within member, then members within day/family, then supported units;
  its uncertainty estimands are labelled separately. Partial-tape and complete-only views remain separate.

## A — current-dollar curve (family `ALL`, day × family balanced)

| elapsed minutes after next-open liquidation | balanced CV | occupancy |
|---|---|---|
| 0 | 0.000% (exact) | 0.000% |
| 60 | **−0.438%** | −0.449% |
| 120 | **−0.560%** | −0.708% |
| 240 | **−0.805%** | −1.138% |

Mean continuation is negative at the marked horizons above. The earliest decision-clock bucket
(09:30–10:00) is −1.27% at h60, versus −0.25% at 11:00–11:30. There is no end-of-day primary
horizon: the published object is the pointwise wealth curve, each member's own last observed
executable minute open being the terminal endpoint.

## B — retain vs this re-entry rule vs cash (published `headline_curves`, clock from exit)

| h | A retain | B sell→re-enter | A − cash | B − cash | B − A |
|---|---|---|---|---|---|
| 1 | 1.00172 | 0.99429 | +0.17pp | −0.57pp | −0.74pp |
| 5 | 1.00053 | 0.99014 | +0.05pp | −0.99pp | −1.04pp |
| 15 | 0.99668 | 0.98667 | −0.33pp | −1.33pp | −1.00pp |
| 30 | 0.99056 | 0.98249 | −0.94pp | −1.75pp | −0.81pp |
| 60 | 0.98649 | 0.97922 | −1.35pp | −2.08pp | −0.73pp |
| 120 | 0.98473 | 0.97817 | −1.53pp | −2.18pp | −0.66pp |
| 240 | 0.98573 | 0.97904 | −1.43pp | −2.10pp | −0.67pp |

Decision-clock means agree in sign and magnitude (h30 A 0.99106 / B 0.98286; h60 A 0.98614 / B 0.97913;
h120 A 0.98390 / B 0.97711). Retaining beats this re-entry rule at the reported 1–240-minute endpoints;
paired day-clustered statistics are descriptive, not unseen confirmation. Both branches are
below cash at the reported 15-minute-and-later endpoints; retention is near cash in the first few
minutes. Negative B−A does **not** mean continuation pays: the GB10 sale is protective in these means,
and this re-entry gives part of that protection back. **Holding better than this rule ≠ hold-always
has positive EV**, and these means do not establish a stop policy. Complete-tape sensitivity has
the same ordering (h30 A −1.06pp / B −1.80pp / B−A −0.74pp).

## Tail attribution (audit-only; never a state, filter or denominator)

Entry-to-session MFE tags: retention upside lives in the rare monsters — `entry_mfe_ge_300pct`
(n≈8–13) A +32pp at h30 / +189pp at h240 (B +28 / +178, B−A −3.9 / −11.7) and `entry_mfe_100_300pct`
(n≈133) A +16.6pp / B +12.3pp at h30 — while the bulk `entry_mfe_lt_50pct` (n≈3,917) sits below cash in
both branches. Censored tags stay unknown; these are future labels about the member, never states, and
the CV denominator is **current next-open liquidation proceeds**, never the original entry price.
## Guardrails

- The A marginal split (return ≤ 0: +3.02% per day-member vs negative: −0.449% occupancy) is a
  **weight reversal, not a buy-the-dip edge**; the old "a few member-days dominate the pool"
  explanation is void because the corrected readout is day-balanced.
- Time-in-bin/path-duration and clock composition are an **inference**, not a measured separator, and
  **no causal separator** between "tail worth holding" and "bounce into temporary failure" is
  identified. A candidate next question — does *observable* renewed activity or peer strength separate
  them under a fixed-clock/tenure/damage-matched first-GB→reclaim design — is posed as a question only;
  no fit/held-out/reserved run follows. Alternative causal race admission/replacement stays open: a
  failing management family does not abandon Atlas or race admission, and no new framework, sizing,
  sub-minute work or ML is implied.
- Archives: `B/_superseded_first_pass/` (null supports, 6,159 risk set, global pooling, day-mean SE)
  and `B/_foreign_A_pass/` (A-producer files mis-scoped into B, superseded by the B manifest); all
  hashes are in `B/manifest.json`, and both roots hold byte-identical manifests.
