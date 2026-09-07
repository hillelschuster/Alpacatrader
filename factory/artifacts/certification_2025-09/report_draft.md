# Certification draft — month 2025-09

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 24,637,283  (PASS, from clean files)
- sessions (ET days with data): 21  (PASS)
- tickers: 15,346  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,591  (PASS)
- split suspects: 91  examples: [{'ticker': 'ZONE', 'et_date': '2025-09-02'}, {'ticker': 'CTEC', 'et_date': '2025-09-02'}, {'ticker': 'CSTAF', 'et_date': '2025-09-02'}, {'ticker': 'HEAL', 'et_date': '2025-09-02'}, {'ticker': 'DLMAY', 'et_date': '2025-09-02'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1568  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 291  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 131,522  (PASS)
- events per day min/median/max: {'min': 5805, 'median': 6166.0, 'max': 6915}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 10.929648241206031, 'p90': 24.069478908188593, 'p99': 78.18181818181819, 'max': 191.6}  (PASS, internal)

## Worked example ticker-days (for manual review)

- IONS 2025-09-02: prev_close=42.65, 16:00 close=57.44, pct_gain=34.68%, rank@16:00=6
- TECK 2025-09-09: prev_close=35.11, 16:00 close=39.075, pct_gain=11.29%, rank@16:00=15
- LYFT 2025-09-17: prev_close=20.195, 16:00 close=22.87, pct_gain=13.25%, rank@16:00=7
