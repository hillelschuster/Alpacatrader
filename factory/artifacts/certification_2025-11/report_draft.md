# Certification draft — month 2025-11

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 23,357,289  (PASS, from clean files)
- sessions (ET days with data): 19  (PASS)
- tickers: 15,508  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 3,027  (PASS)
- split suspects: 77  examples: [{'ticker': 'DD', 'et_date': '2025-11-03'}, {'ticker': 'DRMKY', 'et_date': '2025-11-03'}, {'ticker': 'SRPU', 'et_date': '2025-11-04'}, {'ticker': 'EVOK', 'et_date': '2025-11-04'}, {'ticker': 'IGTA', 'et_date': '2025-11-05'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1763  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 251  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 116,852  (PASS)
- events per day min/median/max: {'min': 5271, 'median': 6094.0, 'max': 6929}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 12.786885245901637, 'p90': 27.13567839195979, 'p99': 50.52034058656575, 'max': 99.82262245872558}  (PASS, internal)

## Worked example ticker-days (for manual review)

- AMKR 2025-11-03: prev_close=32.27, 16:00 close=37.86, pct_gain=17.32%, rank@16:00=5
- STGW 2025-11-06: prev_close=4.8, 16:00 close=5.62, pct_gain=17.08%, rank@16:00=14
- OSCR 2025-11-24: prev_close=13.485, 16:00 close=16.49, pct_gain=22.28%, rank@16:00=8
