# PRE-REGISTRATION — exposure structure on 2026 (written BEFORE 2026 peek)

Derived ONLY from 2025-08..12 composite stream (exposure_2025.log):
- Exit: X_60m fixed hold. X_death +5.4bps vs X_60m +54.6 (state-death exit destroys edge —
  continuation persists past model-state lapse). X_30m = 36.8bps/trade but 1.23 bps per
  capital-minute vs 0.91 for 60m (alternative, not chosen). X_15m dead (7.1bps).
- Cycle timing: cycle1 +60.6, cycle2 +59.2, cycle3 +9.2; re-entries 60-89m after first
  qual keep edge (+71.5), >=120m weaker (+28.7).
- Entry gate refinement: at entry minute, rvol>8 = +70.7bps (n=567) vs rvol 4-8 = -49.7bps
  (n=88) -> gate entries on rvol>8.

## FROZEN STRUCTURE under test on 2026-01..03
E6_persist_rvol8: per ticker-day, enter at first composite-qualified minute with rvol>8;
after each 60m hold, re-enter at next composite-qualified minute with rvol>8 (unlimited
cycles while state re-qualifies, entry tod<=328). Exit = 60m hold. 20bps RT on t1 fills.
Reference structures replayed alongside: E3_persist (rvol>4 gate) and E1_one.

## Gates
PASS: E6 net/unit >= +30bps AND >= 0 in >= 2/3 months AND >= 100 label-complete entries.
FAIL: E6 net/unit < 0 OR < 40 entries. Otherwise PARTIAL.
Also recorded (not gated): E3 for gate-sensitivity, X_30m capital-efficiency read,
concurrency economics at cap=10 x $10k units.
