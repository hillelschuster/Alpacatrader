# Certification draft — month 2026-08

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 30,162,366  (PASS, from clean files)
- sessions (ET days with data): 21  (PASS)
- tickers: 10,251  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,621  (PASS)
- split suspects: 51  examples: [{'ticker': 'TOP', 'et_date': '2026-08-03'}, {'ticker': 'PN', 'et_date': '2026-08-03'}, {'ticker': 'DFNS', 'et_date': '2026-08-03'}, {'ticker': 'UPC', 'et_date': '2026-08-03'}, {'ticker': 'AMIX', 'et_date': '2026-08-04'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1408  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 20  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 129,537  (PASS)
- events per day min/median/max: {'min': 4732, 'median': 6313.0, 'max': 7177}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 12.710242034732671, 'p90': 28.771929824561397, 'p99': 52.71631205673758, 'max': 107.30114836578714}  (PASS, internal)

## Worked example ticker-days (for manual review)

- INSM 2026-08-06: prev_close=98.98, 16:00 close=132.6, pct_gain=33.97%, rank@16:00=3
- PLTR 2026-08-04: prev_close=125.89, 16:00 close=162.55, pct_gain=29.12%, rank@16:00=9
- SE 2026-08-11: prev_close=114.695, 16:00 close=131.43, pct_gain=14.59%, rank@16:00=9
