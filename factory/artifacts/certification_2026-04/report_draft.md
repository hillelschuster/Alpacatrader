# Certification draft — month 2026-04

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 29,534,647  (PASS, from clean files)
- sessions (ET days with data): 21  (PASS)
- tickers: 9,727  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,498  (PASS)
- split suspects: 36  examples: [{'ticker': 'ELAB', 'et_date': '2026-04-01'}, {'ticker': 'ELAB', 'et_date': '2026-04-02'}, {'ticker': 'SKYQ', 'et_date': '2026-04-02'}, {'ticker': 'LPCN', 'et_date': '2026-04-02'}, {'ticker': 'BKNG', 'et_date': '2026-04-06'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1288  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 24  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 128,785  (PASS)
- events per day min/median/max: {'min': 4633, 'median': 6216.0, 'max': 7649}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 12.103174603174608, 'p90': 23.811797387039373, 'p99': 49.64537941397445, 'max': 123.94410549104506}  (PASS, internal)

## Worked example ticker-days (for manual review)

- IONQ 2026-04-15: prev_close=35.77, 16:00 close=43.2499, pct_gain=20.91%, rank@16:00=8
- BE 2026-04-29: prev_close=226.73, 16:00 close=287.76, pct_gain=26.92%, rank@16:00=5
- MXL 2026-04-24: prev_close=34.34, 16:00 close=60.32, pct_gain=75.66%, rank@16:00=1
