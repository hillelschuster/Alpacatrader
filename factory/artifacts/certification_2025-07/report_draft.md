# Certification draft — month 2025-07

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 24,356,822  (PASS, from clean files)
- sessions (ET days with data): 22  (PASS)
- tickers: 14,611  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,657  (PASS)
- split suspects: 67  examples: [{'ticker': 'HSDT', 'et_date': '2025-07-01'}, {'ticker': 'SCAG', 'et_date': '2025-07-01'}, {'ticker': 'RMGUF', 'et_date': '2025-07-01'}, {'ticker': 'YAAS', 'et_date': '2025-07-01'}, {'ticker': 'ANYYY', 'et_date': '2025-07-01'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1521  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 230  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 139,400  (PASS)
- events per day min/median/max: {'min': 4958, 'median': 6492.5, 'max': 7360}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 12.124406958355301, 'p90': 23.642493229038436, 'p99': 66.66666666666667, 'max': 263.265306122449}  (PASS, internal)

## Worked example ticker-days (for manual review)

- BE 2025-07-09: prev_close=24.33, 16:00 close=28.7, pct_gain=17.96%, rank@16:00=5
- MP 2025-07-10: prev_close=30.02, 16:00 close=45.29, pct_gain=50.87%, rank@16:00=3
- MP 2025-07-15: prev_close=48.51, 16:00 close=58.23, pct_gain=20.04%, rank@16:00=3
