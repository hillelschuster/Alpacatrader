# PRE-REG-SIZE-01 — Fill-size response and adverse selection at size

Frozen 2026-09-13. Measurement only; H025 and the live bot untouched; produces a
sizing input, not a rule.

## Motivation
Dollars = fills per month × size × edge. Fills cannot be expanded (PRE-REG-LEADER-01:
all variants fail, V4 retired), capital already recycles in minutes, so **size per
fill is the remaining dollar lever** for a small-capital operator. The sizing
decision needs the size-response curve — including whether larger orders fill
disproportionately on the toxic (gap-through) fills.

## Data (existing artifact; no new fetch)
`factory/artifacts/lb18_fills_micro.parquet` — one row per frozen OOS fill
(n=541). Field semantics verified in `lb18_fills_micro.py`:
- `flush_vol_at` = SIP shares traded at price <= B during the fill minute
  [tf-1, tf) -> the volume that could have filled a resting order at B
  (queue ignored).
- `flush_vol` = total SIP shares in the fill minute; `next_vol` = SIP shares in
  [tf, tf+1).
- `ret` = fill net return under the frozen exit (100 bps friction), `B` = bid
  level, `prior_flush`, `rank`, `fc`.
Limitations stated up front: fill-minute window (not until the exit), SIP only,
queue position ignored, OOS 2024-01..2025-02 only (no 2021-2023 arm in this
pass).

## Frozen definitions
- Order size in shares: `S = N / B`, for
  `N in {2000, 5000, 10000, 25000, 50000, 100000, 200000}` USD.
- Fillability optimistic (queue ignored): `flush_vol_at >= S`.
  Fillability pessimistic (queue ahead assumed equal to our size):
  `flush_vol_at >= 2*S`.
- Exit-capacity proxy: `next_vol >= S` (a bound on the exit minute only;
  reported as a proxy, never as evidence of exit feasibility).
- Adverse selection: fills split into `flush_vol_at` quartiles; report
  n / mean / median / win / stop-breach (ret <= -0.105) per quartile for the
  all and pf2 subsets; Spearman correlation of `flush_vol_at` vs `ret`.
- Primary subset for the gate: `prior_flush >= 2` (the live judge population).

## Frozen gate — "size-safe to N" iff, on the pf2 subset
(a) optimistic fillability share >= 0.90;
(b) mean ret of passing fills >= pf2 baseline (+1.14%) - 0.10pp;
(c) months+ of passing fills >= 10/14;
(d) no adverse-selection cliff: Q4 mean >= Q1 mean - 0.50pp.
Report the largest passing N; if none passes, report the binding constraint and
stop. No adoption, no bot change; any sizing change is a separate user decision.

## Artifacts
`factory/artifacts/lb18_size.json` + `lb18_size.parquet` from
`factory/scripts/size_curve.py` (new; reads the micro parquet, no API calls).
