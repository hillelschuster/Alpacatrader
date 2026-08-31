# H009 — Failed Breakdown Reclaim vs Clean HOD Breakout — 2025-07 OOS

**Data**: certified `certification_2025-07/events_topN.parquet` (139,400 events, 22 sessions); signals rebuilt from staged clean `data/stage_2025-07/` = **clean_ohlcv_2025-06.parquet + clean_ohlcv_2025-07.parquet** (44M rows) so that **2025-07-01 sessions get 2025-06-30 prior-session close** (deviation from June's month-only staging, applied deliberately per the certification principle: a month's first session needs the prior month's last close). 46,353,625 rows loaded, 59,765 candidate ticker-days.
**Dates**: signals span 2025-06-03 .. 2025-07-31 (41 days). July-only slice = et_date ≥ 2025-07-01 (22 sessions).
**Cost convention** (sibling H006): `roundtrip = cost_bps × 2`. Primary `--cost-bps 10` = **20bps RT**; sensitivity `--cost-bps 20` = **40bps RT**.

## Faithful-subset definition — reconciled across months

Prior work used two different filters (May ledger: strict; June report: loose). Both reported for all months:

- **loose** = `signal_type=="trap" & close > hod_before` (reclaim closes above prior running RTH HOD; dip may be HOD or VWAP) — June-report convention (June n=34,916; reproduces June report exactly)
- **strict** = `signal_type=="trap" & dip_type=="hod" & close > hod_before` (HOD dip AND reclaim above prior HOD) — May-ledger convention (May n=34,923; reproduces May ledger exactly)

## Results (exp_net = mean fwd_ret − roundtrip; edge = trap − clean in bps)

Full table: `factory/artifacts/replication_2025-05_06_07.md`. Key rows, 20bps RT:

| month | slice | variant | n | 30m e | 30m edge | 60m e | 60m edge | wr60 |
|---|---|---|---|---|---|---|---|---|
| 2025-05 | full | strict | 34,923 | −0.050% | +13.4 | +0.054% | +21.5 | 0.494 |
| 2025-06 | full | loose | 34,916 | +0.033% | +20.0 | +0.194% | +28.6 | 0.515 |
| 2025-07 | July-only | loose | 41,316 | −0.009% | +8.5 | +0.028% | +13.7 | 0.494 |
| 2025-07 | July-only | strict | 37,117 | −0.005% | +8.8 | +0.031% | +14.0 | 0.494 |

At 40bps RT July-only is negative everywhere (60m: −0.17%).

## Time-of-day check (July-only, faithful subset)

June report pre-registered "good subset concentrates in 10:00–11:00". July does not confirm:

| bucket | n | 60m e @20bps | 60m edge |
|---|---|---|---|
| 09:30–10:00 | 4,734 | +0.063% | **−36.4bps** |
| 10:00–11:00 | 4,437 | +0.069% | −23.8bps |
| 11:00–12:00 | 2,708 | +0.102% | −4.8bps |
| 12:00–13:00 | 1,602 | −0.042% | +67.0bps |
| 13:00–14:00 | 1,275 | −0.252% | −11.4bps |
| 14:00–15:00 | 1,503 | −0.023% | +18.3bps |
| 15:00–16:00 | (60m n=0 — end of session) | | |

No July TOD cell is net-positive with a positive edge. The June concentration did not replicate.

## Verdict

- **The relative edge is real and persistent**: trap-reclaim beats clean HOD breakout in **3/3 months at every horizon** (July-only edges +8.5/+13.7bps @30/60m; May +13.4/+21.5; June +20.0/+28.6 — direction consistent, magnitude halving each month).
- **The absolute edge is not tradable standalone**: July-only net expectancy @20bps is ≈0 (+0.028% @60m) and negative @40bps. May was also ≈0/negative. Only June (the discovery month) was net-positive.
- **June's strength was the outlier, not the norm.** With three independent months, the honest read: the failed-breakdown-reclaim mechanism carries ~+9–29bps of *relative* information vs clean breakouts, but it does not clear round-trip costs as a standalone long trade at any tested horizon or time-of-day.
- **Not killed as information**: it can serve as a *filter/tiebreaker* inside a broader momentum system (prefer reclaim-structured entries over clean breakouts when both trigger), where it adds ~+10–20bps of selection value on the margin. It cannot carry its own position.

## Files written

- `factory/artifacts/h009_results_2025-07/h009_results.parquet` (primary 20bps RT; June+July signals, 871,606 rows)
- `factory/artifacts/h009_results_2025-07/h009_results_c20.parquet` (40bps RT)
- `factory/artifacts/h009_results_2025-07/h009_summary_c20.json` (c20 run summary; the c10 summary was overwritten by the c20 run — known footgun, same as June — c10 segment metrics are recomputable from the parquet at analysis time)
- `factory/artifacts/replication_2025-05_06_07.md` (3-month comparison table, both variants)
