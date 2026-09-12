# PRE-REG-EXIT-01 — Exit-timing experiment (flat time-stop vs the frozen exit)

Frozen 2026-09-12 by the agent. **Measurement only: no rule may be adopted from
this study.** Adoption requires a new pre-reg naming the chosen rule plus a
FORWARD paper test (the 14 OOS months are now seen for this exit question).

## Origin (what forced this)
Study B / EXP-65: acting on every fill ~60s after first touch improved EV
(pf2 +0.74..+0.82pp, 10/14 months, std 7.2 vs ~10; worst month −0.55% vs −2.03%),
while instant action was strongly negative (−1.2..−1.8pp) and the 25k rule search
added little over the plain time knob. Two defects in that measurement:
1. the exit was priced at the **last SIP trade** with the same flat 1% friction as
   the baseline — real market-exit slippage is unmodelled;
2. the 14 OOS months are now seen for this exit question.

This pre-reg freezes a clean, small, execution-aware test.

## Question
Does a flat, unconditional time-stop at horizon H after first touch beat the
frozen exit design (stop 0.9·B / target c0 / tl30) **net of a conservative
market-exit model**?

## Design (frozen)
- **Population:** the 541 frozen OOS fills; pf2 subset primary; A3b fills secondary.
- **Anchor:** `t_anchor` = first SIP trade with price <= B (same as Study B).
- **Horizons:** H ∈ {instant, 60s, 2m, 5m, 10m, 15m, 30m} — this exact list, no
  additions after seeing the curve.
- **Exit execution model (the point of this study):**
  - MARKET-EXIT ARM: sell at the NBBO **bid** at `t_anchor + H` (not last trade).
  - SLIPPAGE SENSITIVITY: additionally charge `SLIP` bps on the exit for
    `SLIP ∈ {0, 25, 50, 100}`; report EV vs SLIP. No best-case cherry-pick.
  - If no bid exists at H, fall back to the last SIP trade at/before H and flag it.
- **Candidate rules — exactly two, frozen:**
  - **R1 (flat):** exit every fill at H; the frozen stop/target/tl30 are replaced.
  - **R2 (conditional):** exit at H iff `price_H < B*(1-0.01)`, else keep the
    frozen exit.
- **Baselines:** the frozen engine exit on these fills (net +0.914% all /
  +1.136% pf2) and the tighter-stop diagnostics already computed
  (`lb18_exec.json` stop08/12/15) — same comparators as Study B.
- **Metrics:** pooled EV, median, months+, worst month, std, tail (>−11%) count,
  and the target-hit P&L sacrificed.
- **Splits:** full-period pooled (descriptive) + 7/7 month split with the rule
  FIXED (no per-fold re-selection — the candidates are fixed by construction).

## Gates (frozen)
R1 or R2 is a **candidate** iff, at `SLIP = 50bps`:
1. pf2 pooled EV > frozen (+1.136%) AND > the stop08/12/15 comparators;
2. months improved >= 12/14;
3. worst month no worse than the frozen worst (−2.03%) by more than 0.5pp;
4. target-hit sacrifice reported and < 10% of aggregate target P&L.

A candidate still may NOT be adopted: it needs a new pre-reg + forward paper test.

## Anti-patterns / kills
- No rule search beyond R1/R2. No per-fold re-selection. No tuning H after seeing
  the curve. The full H × SLIP grid is reported, including failures.
- No alpha claim beyond the gate verdict. No lookahead: all exit prices are at or
  after the action time and the anchor uses only trades up to `t_anchor`.

## Artifacts
`factory/scripts/lb18_exit.py` → `factory/artifacts/lb18_exit.json` + `.parquet`.
Reuses the SIP trade/quote cache under `data/subminute/` (extend only the horizon
lookups, paged; no strategy change, no refetch of the frozen fills). Ledgers
appended after the cycle.
