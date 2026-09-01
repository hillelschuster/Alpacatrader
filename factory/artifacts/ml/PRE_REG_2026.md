# PRE-REGISTRATION — frozen stack on 2026-01..03 (written BEFORE any 2026 eval peek)

Date: 2026-09-01. No 2026 eval numbers have been viewed as of this writing.
Stack under test (all frozen from 2025 work, zero re-tuning):
model_v1.pkl + theta_fixed=0.00115 + M3 (vis_rank<=2 & score>=theta) +
composite (rvol>4 & vwap_dist>0.03 & tod<270, extension unbounded) +
S4 sequencing (t1-entry fills) + price<=20 & cum_dv 5-100M pocket (post-peek 2025
selection — tested as secondary read, not a gate).

## Gates (from oracle adversarial review, adopted verbatim)
Primary read = M3xCOMPOSITE, net @20bps RT, t1-entry fills, label-complete population
(same construction as all 2025 research numbers).

PASS (all): composite net20 monthly avg >= +30bps  AND  >= 0 in >= 2/3 months
            AND  composite net40 avg >= 0
FAIL: composite net20 avg < 0  OR  composite flow collapses (< 15 events/day avg)
Otherwise: PARTIAL — edge attenuated but alive; do not retune, continue paper-only.

## Regime-health telemetry (recorded, not gated)
- share of events with rvol>8 per month (2025 OOS reference: ~17% of all events)
- composite events/day per month (2025 reference: 83)
- M3 stream net20 (2025 reference: +29bps) and whole-day D10 (2025 reference: +22.1bps)
- score-distribution drift: 2026 share >= theta vs 2025 OOS 44% (2026-01 measured 7.7%)

## Interpretation rules (pre-committed)
- If composite FAILS while M3/D10 remain >= 0: regime shift in extreme-RVOL continuation
  (oracle mechanism #1) — park composite, keep base ranking, re-examine in later months.
- If everything < 0: model/theta transfer failed across the 2025/2026 boundary — the
  2025 edge does not survive the regime change; no rescue tuning on 2026 data.
- Diagnostics labeled DIAG (month-own relative thresholds) are NOT gates and NOT
  tradeable as-is; they only separate regime-shift from edge-loss.
- One pass, zero re-tuning, regardless of outcome.
