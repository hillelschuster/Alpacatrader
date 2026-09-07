# Certification draft — month 2026-03

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 28,684,434  (PASS, from clean files)
- sessions (ET days with data): 22  (PASS)
- tickers: 15,521  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 3,233  (PASS)
- split suspects: 93  examples: [{'ticker': 'CSLLY', 'et_date': '2026-03-02'}, {'ticker': 'AARD', 'et_date': '2026-03-02'}, {'ticker': 'BATL', 'et_date': '2026-03-02'}, {'ticker': 'ZVZZT', 'et_date': '2026-03-03'}, {'ticker': 'BATL', 'et_date': '2026-03-03'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 2153  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 101  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 131,373  (PASS)
- events per day min/median/max: {'min': 4979, 'median': 6064.0, 'max': 6709}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 11.171171171171174, 'p90': 21.36409227683048, 'p99': 53.56849876948318, 'max': 139.43661971830988}  (PASS, internal)

## Worked example ticker-days (for manual review)

- BATL 2026-03-06: prev_close=18.98, 16:00 close=22.25, pct_gain=17.23%, rank@16:00=8
- U 2026-03-27: prev_close=17.105, 16:00 close=19.45, pct_gain=13.71%, rank@16:00=8
- NAVN 2026-03-26: prev_close=9.16, 16:00 close=13.11, pct_gain=43.12%, rank@16:00=2
