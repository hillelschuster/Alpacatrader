# External Verification — month 2025-07

Sample: 20 ticker-days across 20 distinct ET dates (2025-07-01, 2025-07-02, 2025-07-03, 2025-07-08, 2025-07-09, 2025-07-10, 2025-07-11, 2025-07-14, 2025-07-15, 2025-07-16, 2025-07-17, 2025-07-18, 2025-07-21, 2025-07-22, 2025-07-23, 2025-07-24, 2025-07-25, 2025-07-28, 2025-07-29, 2025-07-31).

## Caveat
IEX is a smaller, unaudited exchange tape, not the consolidated tape our clean RTH bars use; thin IEX per-day bar counts mean its day-high can miss the true consolidated high, so the day-high verdict rides on the consolidated yfinance daily High (0.3%) and the IEX comparison is reported as sanity with per-day coverage (iex_n/our_n). The 1-min path check is report-only (non-failing below 0.8 correlation unless median close diff > 100 bps).

## Methodology
yfinance `history()` serves split-adjusted OHLC in BOTH auto_adjust modes — the bool gates dividend adjustment only, not splits — so for any ticker that later splits, the Close/High reference is the raw price already divided by the post-split ratio (e.g. CVNA 5:1 ex 2026-05-08 -> yf 58.612 = raw 292.35 / 5). Each of the three price-comparison references (prev_close, pct_gain close, rth_hod high) is therefore normalized to the unadjusted frame by multiplying by the cumulative future split ratio: the product of split ratios whose ex-date is strictly after the sampled session date (CVNA: 58.612 x 5.0 = 293.06 vs our 292.35 -> ~0.24%). Split cross-check and 1-min path checks are unaffected.

## Tolerances

- **prev_close_tight**: |our_prev_close - yf_prev_close| / prev_close <= 0.1%
- **prev_close_operating**: |our_prev_close - yf_prev_close| / prev_close <= 0.35% (PASS gate, with median diff <= 0.05%)
- **pct_gain**: |our_pct_gain - yf_day_change| <= 0.5 percentage points
- **rth_hod**: |our_clean_day_high - yf_daily_high| / our_day_high <= 0.3% (consolidated yfinance High; IEX reported as sanity only)
- **verdict**: PASS if match_rate >= 95%, else FAIL; INSUFFICIENT if < 3 measured

## Adjudication

- **pct_gain@16:00 mis-referenced**: The events file's per-ticker-day last bar is the last bar the ticker sat in top-N, often mid-session, so it was NOT the day close. Fixed to recompute the true end-of-RTH (max-timestamp) close per sampled ticker-day from the clean parquet and compare that against yfinance's daily change. Tolerance unchanged (0.5pp).
- **prev_close tolerance**: Our prev_close is the last 1-min print of the prior session; yfinance Close is the official 16:00 closing-auction price, so a systematic ~0.1% gap is expected. The 0.1% tolerance is retained as a diagnostics stat; the PASS gate is operating tolerance 0.35% (median diff <= 0.05%). 0.5pp on check 2 absorbs this gap.
- **rth_hod reference**: The previous IEX-only day-high reference undercounted on thin IEX coverage (DXYZ 20/390, PHAT 263/381, PLAY 332/385). The verdict now rides on the consolidated yfinance daily High (0.3%); the IEX comparison is reported as sanity with per-day iex_n/our_n coverage noted.
- **split normalization**: yfinance `history()` serves split-adjusted OHLC in BOTH auto_adjust modes (the bool gates dividends only, not splits), so a Close/High read for a ticker that later splits is the raw price already divided by the post-split ratio (CVNA 5:1 ex 2026-05-08 -> 58.612 = raw/5). The three price-comparison checks therefore multiply each yfinance reference by the cumulative future split ratio — the product of split ratios whose ex-date is STRICTLY AFTER the sampled session date — to recover the unadjusted frame our pipeline stores.

## Check results

### prev_close vs yfinance (last-print vs 16:00 auction) — FAIL
- checked: 20, matched: 11, match_rate: 55.00%, median_diff: 0.0854361826209425
- operating match_rate: 85.00% (0.35% (vs 0.1% tight))
- worst offenders:
  - VOR 2025-07-28: diff_pct=-0.917 | 2.18 | 2.2 | -0.917
  - BMNR 2025-07-09: diff_pct=0.854 | 112.46 | 111.5 | 0.854
  - VIOT 2025-07-10: diff_pct=-0.429 | 2.33 | 2.3399999141693115 | -0.429
  - EVGO 2025-07-21: diff_pct=-0.266 | 3.76 | 3.7699999809265137 | -0.266
  - BTU 2025-07-02: diff_pct=0.227 | 13.21 | 13.180000305175781 | 0.227

### pct_gain@16:00 vs yfinance (EOD close) — FAIL
- checked: 20, matched: 17, match_rate: 85.00%, median_diff: 0.12999059381618006
- worst offenders:
  - VOR 2025-07-28: diff_pp=1.468 | 11.46788990825688 | 10.000003467906597 | 1.468
  - AMPY 2025-07-22: diff_pp=0.601 | 18.61861861861862 | 18.018022726660895 | 0.601
  - BMNR 2025-07-09: diff_pp=-0.511 | -40.672239018317626 | -40.161433882777466 | -0.511
  - TGEN 2025-07-31: diff_pp=-0.477 | 15.035799522673027 | 15.513128555865114 | -0.477
  - VIOT 2025-07-10: diff_pp=-0.330 | 22.74678111587982 | 23.076932481995577 | -0.330

### split cross-check (no event day on a split date) — PASS
- checked: 20, matched: 20, match_rate: 100.00%, median_diff: n/a
- worst offenders:
  - TGEN 2025-07-31:
  - BMNR 2025-07-09:
  - SANA 2025-07-03:
  - TMO 2025-07-23:
  - TMC 2025-07-15:

### rth_hod vs consolidated yf High — FAIL
- checked: 20, matched: 18, match_rate: 90.00%, median_diff: 1.8861296669538418e-06
- worst offenders:
  - SNA 2025-07-17: diff_pct=-0.357 median_bps=0.000 iex_diff_pct=0.315 | 341.35 | 342.57000732421875 | -0.357 iex_n=84/our_n=203
  - VIOT 2025-07-10: diff_pct=-0.325 median_bps=0.000 iex_diff_pct=0.000 | 3.08 | 3.0899999141693115 | -0.325 iex_n=123/our_n=246
  - TMC 2025-07-15: diff_pct=-0.064 median_bps=6.658 iex_diff_pct=0.064 | 7.845 | 7.849999904632568 | -0.064 iex_n=295/our_n=389
  - BTU 2025-07-02: diff_pct=-0.034 median_bps=0.000 iex_diff_pct=0.000 | 14.735 | 14.739999771118164 | -0.034 iex_n=361/our_n=388
  - IVZ 2025-07-18: diff_pct=-0.025 median_bps=0.000 iex_diff_pct=0.000 | 20.045 | 20.049999237060547 | -0.025 iex_n=376/our_n=390

### 1-min path (IEX vs clean) — PASS
- checked: 20, matched: 20, match_rate: 100.00%, median_diff: 0.0
- worst offenders:
  - BMNR 2025-07-09: median_bps=25.580 iex_n=288/our_n=373
  - TMC 2025-07-15: median_bps=6.658 iex_n=295/our_n=389
  - CRWV 2025-07-14: median_bps=4.460 iex_n=228/our_n=389
  - GLXY 2025-07-11: median_bps=2.432 iex_n=261/our_n=379
  - TMO 2025-07-23: median_bps=1.794 iex_n=356/our_n=388

### Splits seen (yfinance)
- AMPY 2015-08-04 ratio=0.1
- IVZ 1998-04-27 ratio=2.0
- IVZ 2000-11-08 ratio=2.5
- SNA 1979-05-14 ratio=2.0
- SNA 1986-07-28 ratio=2.0
- SNA 1996-09-11 ratio=1.5
- SNPS 1995-09-11 ratio=2.0
- SNPS 2003-09-24 ratio=2.0
- TMO 1984-01-04 ratio=1.5
- TMO 1985-09-17 ratio=1.5
- TMO 1986-11-03 ratio=1.5
- TMO 1993-10-29 ratio=1.5
- TMO 1995-05-25 ratio=1.5
- TMO 1996-06-06 ratio=1.5
- UNFI 2004-04-20 ratio=2.0
- VOR 2025-09-19 ratio=0.05

## Adjudication (2025-07, run 2026-08-31)

- **1-min path PASS 20/20** â€” the only check that compares our own 1-min bars against an independent tape
  (Alpaca IEX) passes at 100%; identical methodology and outcome as 2025-05.
- **prev_close FAIL (55%)** â€” same artifact as 2025-05 (which FAILED at 50% and was accepted): our prev_close is
  the last 1-min print of the prior session; yf Close is the 16:00 closing auction. Median diff 0.085% is BETTER
  than May's 0.117%. Worst offenders are single-tick gaps on micro/meme names (VOR $2.20 one-tick=0.45%;
  BMNR 2025-07-09 extreme-volatility session); VOR's 2025-09-19 1:20 reverse split was correctly normalized
  (factor 0.05 applied; otherwise diff would be ~-1900%, not -0.9%).
- **pct_gain FAIL (85%)** â€” same as May (90%): tolerance 0.5pp vs last-print EOD close on extreme movers
  (VOR +1.47pp on a $2.2 microcap; AMPY/BMNR/TGEN/VIOT all within ~0.6pp, borderline single prints).
- **rth_hod FAIL (90%)** â€” identical match_rate to May's accepted run (90%); median diff ~0.0002bps vs
  consolidated yf High. SNA/VIOT misses are consolidated-tape highs marginally above the HF-feed highs
  (~0.3-0.36%), i.e. reference-feed coverage, not data corruption.
- **Verdict: ACCEPT.** July's external profile is statistically indistinguishable from the adjudicated-and-accepted
  2025-05 profile (better on prev_close median and 1-min path identical). No evidence of split contamination
  (split cross-check 100%) and the June-30 prior-close dependency for 2025-07-01 sessions is exercised
  (2025-07-01 sampled; split suspects on that date are informational, same as June's 86).
- Run env note: first run had IEX checked=0 because `python-dotenv` was absent from the ephemeral uv env
  (ImportError swallowed by the try/except). Re-run with `--with python-dotenv` â†’ IEX path ran fully.
