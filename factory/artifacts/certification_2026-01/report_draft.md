# Certification draft — month 2026-01

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 25,080,944  (PASS, from clean files)
- sessions (ET days with data): 20  (PASS)
- tickers: 15,745  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 3,039  (PASS)
- split suspects: 84  examples: [{'ticker': 'BCOMF', 'et_date': '2026-01-02'}, {'ticker': 'SINC', 'et_date': '2026-01-02'}, {'ticker': 'ZBIO', 'et_date': '2026-01-05'}, {'ticker': 'ITOCY', 'et_date': '2026-01-06'}, {'ticker': 'IBIDY', 'et_date': '2026-01-06'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1893  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 222  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 131,800  (PASS)
- events per day min/median/max: {'min': 5916, 'median': 6607.5, 'max': 7195}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 11.676082862523543, 'p90': 21.195184866723988, 'p99': 54.04007756948932, 'max': 157.9867389993972}  (PASS, internal)

## Worked example ticker-days (for manual review)

- INTC 2026-01-28: prev_close=43.95, 16:00 close=48.755, pct_gain=10.93%, rank@16:00=12
- STX 2026-01-28: prev_close=372.0, 16:00 close=442.8, pct_gain=19.03%, rank@16:00=3
- ALMS 2026-01-06: prev_close=8.295, 16:00 close=16.25, pct_gain=95.90%, rank@16:00=1
