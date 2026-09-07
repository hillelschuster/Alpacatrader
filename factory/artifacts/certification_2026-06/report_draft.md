# Certification draft — month 2026-06

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 31,245,558  (PASS, from clean files)
- sessions (ET days with data): 21  (PASS)
- tickers: 10,089  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,712  (PASS)
- split suspects: 58  examples: [{'ticker': 'ANY', 'et_date': '2026-06-01'}, {'ticker': 'JDZG', 'et_date': '2026-06-01'}, {'ticker': 'SMX', 'et_date': '2026-06-01'}, {'ticker': 'BJDX', 'et_date': '2026-06-02'}, {'ticker': 'FULC', 'et_date': '2026-06-02'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1493  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 25  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 130,893  (PASS)
- events per day min/median/max: {'min': 5037, 'median': 6382.0, 'max': 7231}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 12.26635514018691, 'p90': 20.863309352517987, 'p99': 47.00582935877052, 'max': 83.88291959985182}  (PASS, internal)

## Worked example ticker-days (for manual review)

- DFTX 2026-06-22: prev_close=24.44, 16:00 close=36.69, pct_gain=50.12%, rank@16:00=1
- IRDM 2026-06-29: prev_close=43.56, 16:00 close=54.61, pct_gain=25.37%, rank@16:00=4
- HPE 2026-06-02: prev_close=47.03, 16:00 close=56.16, pct_gain=19.41%, rank@16:00=7
