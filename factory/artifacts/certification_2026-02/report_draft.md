# Certification draft — month 2026-02

Gates marked PASS are computed entirely from the clean minute data. Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file price floors) and are confirmed by a separate external-verification script.

- rows: 24,031,034  (PASS, from clean files)
- sessions (ET days with data): 19  (PASS)
- tickers: 15,766  (PASS)
- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): 3,308  (PASS)
- split suspects: 66  examples: [{'ticker': 'ZWZZ.T', 'et_date': '2026-02-02'}, {'ticker': 'ELPW', 'et_date': '2026-02-02'}, {'ticker': 'AZN', 'et_date': '2026-02-02'}, {'ticker': 'BOIL', 'et_date': '2026-02-02'}, {'ticker': 'PHOE', 'et_date': '2026-02-02'}]  (PASS — excluded from events)
- universe excluded (not NYSE/NASDAQ/AMEX equity): 1967  (UNVERIFIED — yfinance tags)
- universe unknown (fetch failure): 172  (UNVERIFIED — network dependent)
- events (is_topN + all gates): 123,005  (PASS)
- events per day min/median/max: {'min': 5461, 'median': 6630.0, 'max': 7279}  (PASS)
- event pct_gain p50/p90/p99/max: {'p50': 13.111888111888112, 'p90': 25.841584158415852, 'p99': 54.166666666666686, 'max': 114.6300914380715}  (PASS, internal)

## Worked example ticker-days (for manual review)

- SPOT 2026-02-10: prev_close=414.74, 16:00 close=476.35, pct_gain=14.86%, rank@16:00=11
- DDOG 2026-02-10: prev_close=114.27, 16:00 close=129.63, pct_gain=13.44%, rank@16:00=14
- FSLY 2026-02-12: prev_close=9.32, 16:00 close=16.03, pct_gain=72.00%, rank@16:00=1
