# PRE-REG-LEADER-01 — Rank / time conditioning of the flush mechanics (frequency vs edge)

Frozen 2026-09-13 by the agent under the standing goal directive (realistic
profitability, top gainers, no subagents). This is a NEW formulation family,
tested separately. **H025 stays frozen and untouched**; its forward paper
continues independently and is never modified by this study.

## Motivation (PRE-REG-CENSUS-01 / EXP-72)

Census over 667 days: 24,655 vacuum events (36.9/day). Recovery-to-session-max
within 30m is monotone in rank — #1 41.4% (7.47 events/day), #2 29.3% (5.74),
#3 24.0% (4.37), off-top-3 13.0% (19.33). AM 29.9% vs PM 13.7%. H025 executes
~1.9 fills/day while top-3 vacuum events run ~17.6/day.

Economic frame: a resting bid at B = 0.9 × reference with target = reference
(+10.1% net) and stop at 0.9B (−11% net) needs a win rate ≳49% (H025 realized
55.0% win, mean win +9.24% / mean loss −8.77%, breakeven 48.7%). The
**unconditional** rank-1 recovery rate (41.4%) is below breakeven — so raw event
headroom is NOT free dollars; only the qualified fraction pays. Question: does
rank/time conditioning deliver MORE fills at an edge that still clears breakeven?

## Declared variants (exactly four; no tuning)

- **V1 — rank-1 anchors**: frozen mechanics, anchors restricted to `rank == 1`.
- **V2 — AM anchors**: frozen mechanics, anchors restricted to `t < 720` ET.
- **V3 — rank-1 AND AM**: intersection of V1/V2.
- **V4 — census-native continuous bid** (frequency-max): while a name is rank 1
  and `t < 720`, maintain a resting bid at B = 0.9 × running session-max close,
  re-armed continuously (not only at strict-state minutes); fill on the first new
  bar with `low <= B`; exit exactly as H025 (stop 0.9B, target c0 = running max
  at the anchor, tl30, 100bps friction).

Baseline = frozen H025 (unfiltered `lb`). **Parity gate:** the baseline run must
reproduce the frozen numbers exactly (OOS all n=541, +0.91%; pf2 n=381, +1.14%;
dev all n=937, +0.39%; pf2 n=658, +1.20%) before any variant is read.

V1–V3 are implemented by filtering `lb` (anchors are its rows) and calling the
frozen engine — no engine changes. V4 uses a new continuous-bid engine in
`lb18_leader.py` only.

## Metrics (OOS 2024-01..2025-02 primary; dev 2025-03..2026-08 secondary)

fills/day, n, mean net %, median, win rate, months+, worst month, worst-5, and
the `pf>=2` partition. All net of 100bps.

## Frozen gates (OOS; ALL required)

1. fills/day ≥ 1.5 × baseline;
2. mean net ≥ +0.50%;
3. win rate ≥ 50%;
4. months+ ≥ 10/14;
5. worst month no worse than the baseline's (−2.51%);
6. dev direction agrees (mean net > 0 and months+ ≥ 11/18).

**Explicit kills:** V4 retires immediately if win < 49% or mean net ≤ 0 — no
tweaking of B, stop, target, or time cut. Any failing variant is recorded and
closed; no parameter search, no post-hoc slicing.

**Adoption: none from this study.** A candidate requires forward paper under its
own pre-reg. H025 is never modified.

## Honesty / limits

Seen-data: the OOS months were used for H025's OOS, so they are only
semi-independent for these variants, and dev is fully seen; **forward paper is
the only clean validation**. Census recovery rates are rulers, not P&L; variants
are priced with the engine's friction. Held-out structure beyond `off` (true
ranks 4+) needs a full-universe rebuild and is out of scope here.

Artifacts: `factory/scripts/lb18_leader.py`; `factory/artifacts/lb18_leader.json`
+ `.parquet`. Ledgers appended after the cycle.
