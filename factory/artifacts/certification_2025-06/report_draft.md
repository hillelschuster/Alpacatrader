# Certification draft — month 2025-06

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 21,996,803  (PASS, from clean files)
- sessions (ET days with data): 20  (PASS)
- tickers: 14,213  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,370  (PASS)
- split suspects: 86  examples: [{'ticker': 'QVCGB', 'et_date': '2025-06-02'}, {'ticker': 'SYHMY', 'et_date': '2025-06-02'}, {'ticker': 'LYRA', 'et_date': '2025-06-02'}, {'ticker': 'ZVZZT', 'et_date': '2025-06-02'}, {'ticker': 'ZWZZT', 'et_date': '2025-06-02'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1448  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 217  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 120,054  (PASS)
- events per day min/median/max: {'min': 5157, 'median': 5918.5, 'max': 6640}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 11.288711288711296, 'p90': 22.446555819477442, 'p99': 50.441176470588246, 'max': 148.51063829787233}  (PASS, internal)

## Worked example ticker-days (for manual review)

- OUST 2025-06-11: prev_close=16.03, 16:00 close=20.36, pct_gain=27.01%, rank@16:00=2
- AG 2025-06-05: prev_close=7.27, 16:00 close=8.405, pct_gain=15.61%, rank@16:00=5
- TIGR 2025-06-25: prev_close=8.115, 16:00 close=9.94, pct_gain=22.49%, rank@16:00=3
