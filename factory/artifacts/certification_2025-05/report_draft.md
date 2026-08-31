# Certification draft — month 2025-05

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 22,546,633  (PASS, from clean files)
- sessions (ET days with data): 21  (PASS)
- tickers: 14,058  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 3,056  (PASS)
- split suspects: 76  examples: [{'ticker': 'PPCB', 'et_date': '2025-05-02'}, {'ticker': 'ZVZZT', 'et_date': '2025-05-02'}, {'ticker': 'BWNB', 'et_date': '2025-05-05'}, {'ticker': 'EPWK', 'et_date': '2025-05-05'}, {'ticker': 'PPCB', 'et_date': '2025-05-05'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1606  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 292  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 123,783  (PASS)
- events per day min/median/max: {'min': 4756, 'median': 6258.0, 'max': 7229}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 13.645352669742906, 'p90': 28.855721393034834, 'p99': 63.69565217391306, 'max': 163.0901287553648}  (PASS, internal)

## Worked example ticker-days (for manual review)

- CRWV 2025-05-27: prev_close=102.77, 16:00 close=123.97, pct_gain=20.63%, rank@16:00=9
- ANF 2025-05-28: prev_close=77.17, 16:00 close=88.47, pct_gain=14.64%, rank@16:00=7
- ASTS 2025-05-02: prev_close=23.01, 16:00 close=26.4, pct_gain=14.73%, rank@16:00=19
