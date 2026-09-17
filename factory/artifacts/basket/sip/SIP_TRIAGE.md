# SIP panel triage — why revisions happened (facts + coverage)

Panel days: **20**; tail diffs classified: **197** -> {'minor': 180, 'coverage_low': 14, 'large_revision': 3}.

## Case studies (hand-verified)
### 2022-03-10 BRP — legacy bad print (confirmed)
- legacy clean tape contained bars at 1092.71 / 785.47 forming a corrupted spike; glitch-free member MFE +3.7%
- SIP trades max 26.35/26.47 all day; the corrupted prints are ABSENT from SIP
- absolute stored high 1092.71 is invalid; ranking and MFE both contaminated in the legacy record on that day

### 2021-10-25 BKKT — SIP archive gap -> comparison void (SIP is incomplete, legacy looks right)
- SIP trades for BKKT that day: 10 rows, auction/cross conditions only (09:30 Q/O at ~13.8; 16:00 M/6 at 30.5-30.6)
- direct live Alpaca probe 09:35-09:40 ET returns 0 trades for BKKT, BE, RDW (AAPL/SPY return 17k/10k on the same date + window; BE next day returns 6.3k)
- legacy RTH bars show 29.6-31.57 late day with ~1.3M volume; SIP closing print 30.6 is consistent with the legacy scale
- therefore the -56% 'revision' is an instrument artifact, not a legacy error

### 2025-10-30 BNY — scale/adjustment offset x~10.4 (percent-safe, absolute-price unsafe)
- SIP trades all day span 106.03-108.78 (42,328 prints, tight spread)
- legacy highs ~10.26-10.34 across 2025-10-29/30/31 -> consistent scale factor ~10.4
- pattern matches a subsequent 10:1 split adjustment in the legacy tape; percent moves are scale-invariant, absolute levels are not comparable

## Largest large-revision rows (healthy SIP coverage)
| day | ticker | stored | SIP | delta | ratio | n_rth |
|---|---|---|---|---|---|---|
| 2025-10-30 | BNY | 10.26 | 108.78 | 9.6023 | 10.6023 | 42325 |
| 2022-03-10 | BRP | 1092.71 | 26.35 | -0.9759 | 0.0241 | 13019 |
| 2025-03-24 | GOLD | 19.065 | 28.8999 | 0.5159 | 1.5159 | 3949 |

## Coverage-low revisions (SIP incomplete — comparison void)
See sip_triage.json `per_day` + certification rows with class=coverage_low; these are excluded from 'SIP is truth' claims.

Thresholds: {"min_rth_trades": 200, "scale_ratio_flag": 1.5}
