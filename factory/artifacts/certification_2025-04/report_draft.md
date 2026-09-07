# Certification draft — month 2025-04

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 24,621,900  (PASS, from clean files)
- sessions (ET days with data): 21  (PASS)
- tickers: 14,234  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 5,786  (PASS)
- split suspects: 53  examples: [{'ticker': 'DLICY', 'et_date': '2025-04-02'}, {'ticker': 'NMAX', 'et_date': '2025-04-02'}, {'ticker': 'AREB', 'et_date': '2025-04-02'}, {'ticker': 'FLX', 'et_date': '2025-04-02'}, {'ticker': 'ICCT', 'et_date': '2025-04-02'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 3205  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 429  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 118,161  (PASS)
- events per day min/median/max: {'min': 4633, 'median': 5942.0, 'max': 6686}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 9.476309226932667, 'p90': 20.669291338582674, 'p99': 44.912280701754376, 'max': 89.24162257495591}  (PASS, internal)

## Worked example ticker-days (for manual review)

- RKLB 2025-04-15: prev_close=19.09, 16:00 close=21.07, pct_gain=10.37%, rank@16:00=10
- HTZ 2025-04-17: prev_close=5.7, 16:00 close=8.2, pct_gain=43.86%, rank@16:00=2
- HIMS 2025-04-29: prev_close=28.51, 16:00 close=35.05, pct_gain=22.94%, rank@16:00=3
