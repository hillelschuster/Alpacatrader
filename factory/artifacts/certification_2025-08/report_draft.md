# Certification draft — month 2025-08

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 23,727,538  (PASS, from clean files)
- sessions (ET days with data): 21  (PASS)
- tickers: 15,001  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,908  (PASS)
- split suspects: 52  examples: [{'ticker': 'ZVZZT', 'et_date': '2025-08-01'}, {'ticker': 'PHLT', 'et_date': '2025-08-01'}, {'ticker': 'FBDC', 'et_date': '2025-08-04'}, {'ticker': 'ZVZZT', 'et_date': '2025-08-04'}, {'ticker': 'ZVZZ.T', 'et_date': '2025-08-04'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1595  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 308  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 130,415  (PASS)
- events per day min/median/max: {'min': 4895, 'median': 6334.0, 'max': 7301}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 12.489927477840457, 'p90': 26.328466335561444, 'p99': 48.262548262548265, 'max': 96.41577060931901}  (PASS, internal)

## Worked example ticker-days (for manual review)

- UUUU 2025-08-26: prev_close=10.91, 16:00 close=12.28, pct_gain=12.56%, rank@16:00=8
- SNOW 2025-08-28: prev_close=200.5, 16:00 close=240.97, pct_gain=20.18%, rank@16:00=2
- TCOM 2025-08-28: prev_close=65.33, 16:00 close=75.03, pct_gain=14.85%, rank@16:00=4
