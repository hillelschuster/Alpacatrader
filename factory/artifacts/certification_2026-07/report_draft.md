# Certification draft — month 2026-07

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 31,839,599  (PASS, from clean files)
- sessions (ET days with data): 22  (PASS)
- tickers: 10,153  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,417  (PASS)
- split suspects: 65  examples: [{'ticker': 'MQ', 'et_date': '2026-07-01'}, {'ticker': 'MLI', 'et_date': '2026-07-01'}, {'ticker': 'TC', 'et_date': '2026-07-01'}, {'ticker': 'BFOR', 'et_date': '2026-07-01'}, {'ticker': 'SDOT', 'et_date': '2026-07-01'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1298  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 17  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 133,626  (PASS)
- events per day min/median/max: {'min': 4740, 'median': 5994.5, 'max': 7250}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 10.886639676113356, 'p90': 22.282352941176473, 'p99': 44.319056785370535, 'max': 208.252427184466}  (PASS, internal)

## Worked example ticker-days (for manual review)

- AMZN 2026-07-31: prev_close=236.031, 16:00 close=271.57, pct_gain=15.06%, rank@16:00=9
- IREN 2026-07-20: prev_close=33.615, 16:00 close=40.26, pct_gain=19.77%, rank@16:00=2
- PENG 2026-07-08: prev_close=62.71, 16:00 close=78.46, pct_gain=25.12%, rank@16:00=1
