# Certification draft — month 2025-10

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 27,805,119  (PASS, from clean files)
- sessions (ET days with data): 23  (PASS)
- tickers: 15,706  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 3,157  (PASS)
- split suspects: 90  examples: [{'ticker': 'ZVZZT', 'et_date': '2025-10-01'}, {'ticker': 'ZVZZ.T', 'et_date': '2025-10-01'}, {'ticker': 'TANAF', 'et_date': '2025-10-01'}, {'ticker': 'RITR', 'et_date': '2025-10-01'}, {'ticker': 'HOJI', 'et_date': '2025-10-01'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1910  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 293  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 148,877  (PASS)
- events per day min/median/max: {'min': 5769, 'median': 6484.0, 'max': 7389}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 13.47447891022485, 'p90': 27.397260273972613, 'p99': 56.2076749435666, 'max': 495.3714981729598}  (PASS, internal)

## Worked example ticker-days (for manual review)

- AES 2025-10-01: prev_close=13.155, 16:00 close=15.365, pct_gain=16.80%, rank@16:00=12
- W 2025-10-28: prev_close=86.48, 16:00 close=106.46, pct_gain=23.10%, rank@16:00=4
- APLD 2025-10-10: prev_close=29.23, 16:00 close=33.965, pct_gain=16.20%, rank@16:00=5
