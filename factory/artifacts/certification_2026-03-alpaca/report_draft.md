# Certification draft — month 2026-03

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 32,288,525  (PASS, from clean files)
- sessions (ET days with data): 22  (PASS)
- tickers: 9,627  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 2,339  (PASS)
- split suspects: 48  examples: [{'ticker': 'BATL', 'et_date': '2026-03-02'}, {'ticker': 'BNY', 'et_date': '2026-03-02'}, {'ticker': 'AARD', 'et_date': '2026-03-02'}, {'ticker': 'BATL', 'et_date': '2026-03-03'}, {'ticker': 'POAS', 'et_date': '2026-03-04'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1312  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 28  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 126,537  (PASS)
- events per day min/median/max: {'min': 4602, 'median': 5853.0, 'max': 6523}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 11.790585129784423, 'p90': 22.269767441860473, 'p99': 55.83720099964297, 'max': 135.306312843371}  (PASS, internal)

## Worked example ticker-days (for manual review)

- SEDG 2026-03-20: prev_close=45.77, 16:00 close=51.71, pct_gain=12.98%, rank@16:00=5
- BRZE 2026-03-25: prev_close=18.05, 16:00 close=21.57, pct_gain=19.50%, rank@16:00=6
- ALKS 2026-03-31: prev_close=30.15, 16:00 close=35.37, pct_gain=17.31%, rank@16:00=4
