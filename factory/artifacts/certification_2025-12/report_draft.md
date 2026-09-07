# Certification draft — month 2025-12

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 25,626,270  (PASS, from clean files)
- sessions (ET days with data): 22  (PASS)
- tickers: 15,629  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,633  (PASS)
- split suspects: 124  examples: [{'ticker': 'ULTY', 'et_date': '2025-12-01'}, {'ticker': 'TSLY', 'et_date': '2025-12-01'}, {'ticker': 'XYZY', 'et_date': '2025-12-01'}, {'ticker': 'ZVZZT', 'et_date': '2025-12-01'}, {'ticker': 'FLYE', 'et_date': '2025-12-01'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1708  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 228  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 126,107  (PASS)
- events per day min/median/max: {'min': 4307, 'median': 5802.5, 'max': 6989}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 9.745762711864407, 'p90': 19.779005524861866, 'p99': 47.788461538461526, 'max': 100.4950495049505}  (PASS, internal)

## Worked example ticker-days (for manual review)

- NVO 2025-12-23: prev_close=48.11, 16:00 close=51.625, pct_gain=7.31%, rank@16:00=17
- DBRG 2025-12-29: prev_close=13.93, 16:00 close=15.275, pct_gain=9.66%, rank@16:00=9
- PL 2025-12-11: prev_close=12.98, 16:00 close=17.47, pct_gain=34.59%, rank@16:00=2
