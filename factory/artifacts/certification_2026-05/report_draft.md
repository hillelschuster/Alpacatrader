# Certification draft — month 2026-05

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 29,136,867  (PASS, from clean files)
- sessions (ET days with data): 20  (PASS)
- tickers: 9,905  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,643  (PASS)
- split suspects: 63  examples: [{'ticker': 'MSTP', 'et_date': '2026-05-01'}, {'ticker': 'MRAL', 'et_date': '2026-05-01'}, {'ticker': 'AIOS', 'et_date': '2026-05-01'}, {'ticker': 'CUE', 'et_date': '2026-05-01'}, {'ticker': 'SMCL', 'et_date': '2026-05-01'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1341  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 25  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 131,633  (PASS)
- events per day min/median/max: {'min': 5521, 'median': 6619.0, 'max': 7178}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 16.146572104018926, 'p90': 29.867256637168133, 'p99': 66.71982987772462, 'max': 133.88189738625363}  (PASS, internal)

## Worked example ticker-days (for manual review)

- STRL 2026-05-05: prev_close=529.5, 16:00 close=806.47, pct_gain=52.31%, rank@16:00=1
- FLEX 2026-05-06: prev_close=96.39, 16:00 close=134.71, pct_gain=39.76%, rank@16:00=3
- RXT 2026-05-07: prev_close=2.265, 16:00 close=3.53, pct_gain=55.85%, rank@16:00=3
