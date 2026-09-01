# STATE.md — Top-Gainer Research Factory

**Last updated**: 2026-08-28
**Phase**: 2025-06 AND 2025-05 CERTIFIED (both with microstructure adjudications),
on Drive. H006 tail + H009 faithful replicated on May: NOT ROBUST (H006 edge →~0,
H009 edge halved). H007/H010 killed. Next: download+certify 2025-07 as the true
OOS month for H006/H009; run H008.

## ML Phase — Overnight Mission (2026-09-01, in progress)

Pivot approved by user: systematic ML pattern discovery on the certified event
stream, replacing hypothesis-by-hypothesis testing (all ten H001–H010 now tested;
H008/H009 live on as features).

- **Spec**: `factory/artifacts/ml/FEATURE_SPEC.md` v1 + oracle review (design
  critic subagent, ADOPT/MODIFY verdicts) + reviewer leakage audit (10 findings,
  3 must-fix code items — all implemented).
- **Target**: raw `fwd_ret_60m` winsorized ±10% (constant cost shift moved to
  evaluation). Cost scenarios 20/40bps RT at eval only.
- **Sampling**: `sample_weight = 1/n_events(episode)`, cap 120 (episode-equal
  influence). Evaluation always on FULL population.
- **Splits**: train 2025-05+06, dev 2025-07 (burned: early stop + threshold only),
  final OOS 2025-08..12 certifying via `cert_chain.sh`. Nov–Dec frozen as re-OOS
  if August is ever peeked. Rolling-origin added later if first result is alive.
- **Feature build** `factory/scripts/build_features.py`: minute grid per event
  (ticker,et_date), close/high ffill, low/volume NULL on missing bars (real bars
  only for dips + vol stats); H008 RVOL baselines (null at 09:30 — expdv=0 trap);
  H009-faithful trap/reclaim on grid (`hod_before = hod.shift(1)`); range_pos via
  running cum extremes (reviewer HIGH fix); gain_vel time-based; market_ret_5m
  within-session; t+1m-entry label variants (executable-entry proxy; edge must
  survive both entry timings). Outputs `data/ml_features/features_YYYY-MM.parquet`
  (gitignored) + `coverage_YYYY-MM.json`.
- **Coverage 2025-07** (139,400 events): rvol .896, ret_30m .942, vwap_dist 1.0,
  trap_reclaim 1.0, market_ret_5m .993, fwd_ret_60m .738, fwd60_t1entry .683.
- **Training** `factory/scripts/train_ml.py`: LightGBM (single config, depth 6,
  min_data 200) + ElasticNet baseline; decile tables day-ranked; episode-best
  (one trade per ticker-day); day-clustered bootstrap CIs; daily-PnL drawdown;
  Spearman IC; t+1-entry variant; long+short; @20/40bps. Aug–Dec evaluated once,
  frozen.
- **Accepted biases (documented, not fixed)**: non-PIT universe tags (yfinance
  2026 fetch; PIT archive = future option); certify `n_bars>=30` full-day gate
  conditions on the future (survivor-flavored population; uniform across months);
  H009 dip window 5 grid-min (tighter than H009's 5 bars post-halt).
- **2025-03 downloaded late** (May 1 baselines reach into March); May features
  rebuilt after March clean.

## ML v1 OOS — VERDICT: GO (2026-09-01)

Single frozen pass (protocol pre-registered in `factory/artifacts/ml/OOS_PROTOCOL.md`):
LightGBM depth-6 (57 iters), train May+Jun, dev Jul, OOS Aug..Dec (293,687 events).
**5/5 gates PASS.** Details: `factory/artifacts/ml/REPORT_OOS_v1.md`.

- D10 (top-10% score/day) all-events net @20bps: +10/+49/+23/+20/+6 bps monthly,
  pooled +22.1bps, n=49,200. t+1m-entry pooled +22.4bps (entry-timing robust).
- wr gradient D1→D10 monotone in all 5 OOS months; D1 = −39..−92bps (avoid zone).
- @40bps D10 dead (−25..−37bps) → edge requires ≤30bps RT execution.
- ElasticNet reproduces ordering 5/5 (structure not a tree artifact).
- Phase 6 composite rv>4 & vwap_dist>3% & tod<270, frozen on Nov–Dec: **+89.4bps
  @20bps, wr .605 (n=3,847 full set)**; survives 40bps in label-complete subset.
  Adversarial review: D10 GO claim audited CLEAN; composite PARTIALLY validated —
  the design grid was informed by an earlier slice peek at Nov–Dec (disclosed in
  REPORT_OOS_v1.md). Definitive test = exact frozen rule on next fresh month.
  Script: factory/scripts/composite_ml.py. Sizing research next.
- Dead at: D10@40bps; episode-best picking adds nothing (breadth portfolio).
- Next: pre-register composite rule (rv>3 & vwap+2% & tod<240 built from Aug–Oct,
  validate Nov–Dec untouched); bot integration is a recommendation only (bot off-limits).

## 2025-06 Certification — DONE (2026-08-28)

Internal gates (certify_month.py, `factory/artifacts/certification_2025-06/`):
20 sessions, 21.99M rows, 14,213 tickers → 2,370 candidates (86 split suspects,
1,448 universe-excluded, 217 universe-unknown), **120,054 events**; per-day
5157/5918/6640; pct_gain p50 11.29% / p90 22.45% / p99 50.44% / max 148.51%.
All internal gates PASS (checks.json). Last clean bar ~15:59 (no exact 16:00 bar).

External verification (verify_external.py, 20 ticker-days, seed 42):
pct_gain@EOD PASS (95%, 0.042pp) · splits PASS (100%) · rth_hod vs yf High PASS
(100%, 0.0083%) · 1-min path PASS (4.14bps) · prev_close PASS at operating
tolerance (95% within 0.35%). The only median-gate breaker (UAMY 2025-06-06
+0.662%) was adjudicated as a genuine 15:59 print (56,586 shares) vs yf's 16:00
auction close 3.00 — microstructure, not error; the 0.05% median gate is
miscalibrated for microcaps. Details: external_verification_report.md.
Survivorship caveat stands (current-state exchange tags, not PIT snapshots).

## 2025-05 Certification — DONE (2026-08-28, adjudicated)

Internal gates (`factory/artifacts/certification_2025-05/`): 21 sessions, 22.5M
rows, 14,058 tickers → 3,056 candidates, **123,783 events**; pct_gain p50 13.65%
/ p90 28.86% / p99 63.70%. First session 2025-05-01 dropped (no April locally).

External verify (seed 42): after **split-normalization of the yf reference**
(yf serves split-adjusted OHLC in both auto_adjust modes; ref = yf_price ×
product of split ratios with ex-date AFTER the sampled session — e.g. CVNA
5:1 split 2026-05-08 turned a false 79.95% diff into −0.243%), residual
prev_close/pct_gain/rth_hod offenders (MNTN 05-23, GCL 05-21, HNGE 05-27)
were adjudicated from bars as genuine print-vs-16:00-auction microstructure
(hyper-volatile IPO / dead-tape-then-hot / healthy-tape print). Splits + 1-min
path PASS. Verdict: **ADJUDICATED PASS — 2025-05 CERTIFIED**. Same 0.05%
median-gate miscalibration note as June. Full evidence in
certification_2025-05/external_verification_report.md.

## May Replication of Provisional Positives — NOT ROBUST (2026-08-28)

- **H006 (>8% VWAP fade)**: May n=2,470, exp_net ~0 at 20bps RT (June +0.49%/15m);
  40bps RT negative (−0.19%). 12%+ tail −0.33% vs June −3.29%. **DOWNGRADED
  survivor-provisional → not robust.**
- **H009 faithful subset**: direction preserved, edge halved (+21.5bps @60m vs
  +28.6; 30m +13.4bps not net-positive); 40bps negative. Dilution 91.5% stable.
  **Weak replication only.**
- Comparison: `factory/artifacts/replication_2025-05_vs_2025-06.md`.
- Implication: no hypothesis currently justifies building. The decisive test is
  a forward OOS month: download + certify 2025-07, run H006/H009 unchanged.

## 2025-07 OOS — COMPLETE (2026-08-31)

- **Certification 2025-07 DONE** (`certification_2025-07/`): 22 sessions, 24.36M rows, 14,611 tickers,
  2,657 candidates, 67 split suspects (< June's 86), 139,400 events (4,958–7,360/day).
  Certified with **BOTH** clean_ohlcv_2025-06 + 2025-07 files so 2025-07-01 sessions get the
  2025-06-30 prior-session close (user-corrected design; certified clean_2025-07 predates this fix).
- **External verification**: 1-min path PASS 20/20 (independent Alpaca IEX tape), split cross-check PASS 20/20;
  prev_close/pct_gain/rth_hod FAIL raw gates at 55%/85%/90% — statistically identical to May's
  adjudicated-and-accepted profile (50%/90%/90%; medians slightly better than May). Adjudicated ACCEPT
  (same reference artifacts: last-print vs 16:00 auction, consolidated-vs-feed highs). Run-note: first
  attempt had IEX checked=0 (missing python-dotenv in ephemeral uv env); re-run with --with python-dotenv.
- **H006 KILLED by July OOS** (EXP-H006-2025-07): tail 12%+ n=1,070 (4.3× June) exp −0.065% @20bps
  (June: +3.08% on n=247); monotone gradient absent; 8–12% bucket flipped positive.
- **H009 not executable standalone** (EXP-H009-2025-07): relative edge vs clean replicates 3/3 months
  (+13.7bps @60m July-only, halving each month: 28.6→21.5→13.7) but absolute net ≈0 @20bps, negative @40bps;
  June's 10–11am concentration absent in July. Faithful-subset filters reconciled (May used strict,
  June used loose — both now reported for all months).
- Comparison: `factory/artifacts/replication_2025-05_06_07.md`.

## Where We Are (summary)

H001–H005 (continuation-family hypotheses) were tested on 2025-07 and all killed
(net negative after 20bps roundtrip, universal OOS decay). Results are recorded but
carry a **data-quality caveat**: they ran on a pipeline whose `prior_close`,
split-handling, and universe filtering are not yet trustworthy (details below).
The verdicts are probably still correct (edges were negative, not falsely positive),
but no further conclusions may be built on this foundation until one month is
certified end-to-end.

**Current objective**: certify 2025-06 (chosen because July 2025 is burned by
H001–H005 experiments; May 2025 remains available as trailing baseline history).
Then set up the bounded Google Drive artifact workflow, then resume hypotheses
(H006–H010) on certified data.

## Certification Gates (2025-06 must pass all)

1. **Previous-session close**: `prior_close` must be the previous regular-session
   close per ticker (not "last bar before target date"). Spot-check vs yfinance
   raw daily closes: ≥99% of sampled ticker-days match within 0.1%.
2. **Splits**: detect overnight ratios far from 1 (e.g. <0.5 or >2) per ticker;
   exclude or adjust; cross-check sampled events against yfinance splits.
   No top-N event may span an unhandled split boundary.
3. **Universe**: NYSE/NASDAQ/AMEX common-stock filtering (exclude OTC/pinks,
   warrants, units, preferreds). PIT snapshots not yet downloaded — current-state
   exchange tags (yfinance/Alpaca assets) accepted for 2025-06 with noted
   survivorship caveat.
4. **Liquidity/participation**: events require price ≥ $2, cumulative dollar
   volume ≥ threshold (default $5M), and recent active volume.
5. **Intraday rank + true HOD**: recompute per-minute rank and RTH HOD; verify
   sampled minutes against independent recomputation.
6. **Independent path verification**: sample top-gainer ticker-days; compare 1-min
   bars and HOD against Alpaca IEX historical bars (free tier). IEX ≠ consolidated
   tape — use as sanity check with tolerance, not absolute truth. yfinance 1m is
   unavailable for 2025-06 (30-day window).
7. **Labels**: forward returns (1/3/5/15/30/60m), MFE/MAE spot-checked against bars.

Deliverable: `factory/artifacts/certification_2025-06/{report.md, checks.json,
events_topN.parquet}` — report states PASS/FAIL per gate with numbers.

## Known Data-Integrity Issues (verified in code, 2026-08-27)

- `clean_month.py`: PIT universe join and split exclusion are unimplemented stubs (`pass`).
- `rank_day.py` prior_close = last close before target date **within the monthly
  file** → (a) wrong when a ticker skipped sessions, (b) undefined on month's first
  trading day (ticker dropped), (c) raw unadjusted prices across split dates create
  fake huge % gains that win the rank.
- mito0o852/OHLCV-1m is **raw/unadjusted** (Finnhub). Splits MUST be handled
  explicitly. Audit found split signals (HMBL 890 jumps >20%, HCTI, OPEN, GIBO).
- ~25% of tickers sub-$1 OTC noise; price floor $2 only partially filters;
  no exchange/asset-type filter exists yet.
- Known duplicates (~(timestamp,ticker) dedup exists in both clean and rank steps).

## Datasets

| Source | Role | Status |
|---|---|---|
| `mito0o852/OHLCV-1m` | 1-min OHLCV backbone (raw, unadjusted) | 2025-05/06/07 downloaded+cleaned |
| `yolo22/stock-pit-archives` | PIT universe snapshots 2021+ | NOT downloaded |
| `speb/financial-data` stock_split_events | split dates | NOT downloaded |
| yfinance (installed 1.4.1) | external verification: daily raw closes, splits | available |
| Alpaca data API (IEX feed) | external verification: 1-min bars | keys present |

Data philosophy: sources are not sacred. Replace/supplement if materially better
reconstruction exists. Requirement = best practical data + correct methodology +
point-in-time integrity. Do not overengineer beyond what affects validity.

## Hypothesis Ledger (summary — canonical: HYPOTHESES.jsonl)

- H001 pullback-reclaim continuation — KILLED (2025-07)
- H002 HOD breakout — KILLED (2025-07)
- H003 gain×volume×TOD — KILLED (2025-07; inverted volume finding: high DV underperforms)
- H004 micro-pullback above VWAP — KILLED (2025-07; VWAP filter adds nothing net)
- H005 rank persistence — KILLED (2025-07; persistence less bad, still negative)
- Common failure: mean reversion dominates; 20%+ extremes worst; nothing cleared 20bps cost.
- CAVEAT: all on uncertified pipeline (prior_close/splits/universe gaps above).
  Treat as strong prior, not final truth.
- H006 VWAP-distance fade — **TESTED 2025-06 + replicated 2025-05: NOT ROBUST.**
  June (n=2,470): exp_net +0.493%/15m +0.581%/30m after 20bps RT; 12%+ tail
  (n=247, +3.09%/15m). May replication: exp_net ~0 at 20bps, −0.19% at 40bps;
  tail −0.33%. Edges concentrated in rare June tail; no cross-month stability.
  Artifacts: h006_results_2025-06/, h006_results_2025-05/.
- H007 consolidation-then-break — **TESTED 2025-06 certified: KILLED.** Contraction
  (15m range <1%) WORSE than grind control (≥1.5%): exp_net 60m −0.179% vs
  +0.241% (spread −0.420pp) net 20bps RT; all sub-segments negative; rolling-ATR
  arm untested. Artifacts: h007_results_2025-06/.
- H008 RVOL vs TOD baseline — queued (needs May 20-day baseline → May certification first)
- H009 failed-breakdown reclaim — **TESTED 2025-06 + replicated 2025-05: WEAK.**
  June faithful subset (dip below prior HOD → reclaim above, n=34,916): +20bps
  @30m, +29bps @60m net 20bps RT; broad trap bucket negative (91% VWAP-only
  dilution). May replication: direction preserved, edge halved (+21.5bps @60m),
  30m not net-positive, 40bps negative. Dilution 91.5% stable. Artifacts:
  h009_results_2025-06/, h009_results_2025-05/.
- H010 overnight gap / opening-range hold — **TESTED 2025-06 certified: KILLED.**
  Gap-up-hold continuation positive only at n=8/n=6 (collapses −3.75% @60m);
  stable positive is gap-up-FAIL (+0.51% @60m net 20bps — inverse of claim);
  gap-down segments n≤2 unusable. Required added $1M open-DV gate (62% of
  |gap|>5% universe thin/OTC). Degenerate OOS (19 signal days). Note: bars
  stamped at bar START. Artifacts: h010_results_2025-06/.
- RULE: single-month June results have degenerate OOS — provisional positives
  (H006 tail, H009 faithful) must replicate on a second month before further building.
- COST CONVENTION: experiment scripts compute roundtrip = cost_bps × 2 →
  `--cost-bps 10` = 20bps RT (H001–H005 standard).

## Storage / Google Drive Workflow — LIVE (2026-08-28)

rclone v1.75.0 at `~/.local/bin/rclone`, remote `gdrive:` configured (user's own
GCP OAuth client; shared client_id retiring 2026). Remote root:
`gdrive:algo-research/alpaca-top-gainers/`. Uploaded + MD5-verified
(`rclone check --download`, 0 diffs): `certification/2025-06/` (5 files incl
events_topN.parquet 8.3MB), `ledgers/`, `docs/`, `data/universe_tags.parquet`.

Gotcha: `rclone copy FILE DIR/FILE` creates a DIR named FILE — use `rclone copyto`
for single files. Upload valuable certified/derived artifacts only (no raw public
datasets; Drive is not a database). Don't let storage work delay research.

## Next Actions (ordered)

1. ~~Download + clean + certify 2025-07~~ DONE (2026-08-31, see above).
2. ~~Run H006 + H009-faithful on certified 2025-07~~ DONE: H006 killed; H009 direction-persistent
   but not executable standalone (ledgers updated).
3. ~~H008 RVOL vs 20-day TOD baseline~~ DONE (2026-09-01, EXP-H008-2025-06_07): relative edge real
   (+7..+28bps @60m, 6/6 volume-matched cells, 2/2 months) but absolute ≈0 to negative; stack with
   H009 dead in July. All ten hypotheses now tested on certified data; no standalone executable edge
   survives 20bps RT. Next: synthesis decision (see STATE.md Where We Are).
4. Upload new artifacts (July cert, May/June re-verified outputs, May
   replication artifacts, ledgers, STATE) to Drive + MD5 verify; prune local if
   disk >90%.

## Important Commands

```bash
# Download/audit/clean a month (HF source)
python factory/scripts/download_month.py --year 2025 --month 06
python factory/scripts/audit_month.py --file data/ohlcv_2025-06.parquet
python factory/scripts/clean_month.py --file data/ohlcv_2025-06.parquet

# Certification (new)
python factory/scripts/certify_month.py --file data/clean_ohlcv_2025-06.parquet --month 2025-06
python factory/scripts/verify_external.py --month 2025-06 --sample 20

# Legacy (used for H001-H005, superseded by certify path)
python factory/scripts/rank_day.py --file data/clean_ohlcv_2025-07.parquet --date 2025-07-02
```

## Key Paths

- Research root: `factory/`
- Canonical: `factory/RESEARCH_GOAL.md`, `factory/STATE.md`, `factory/AGENTS.md`
- Ledgers: `factory/HYPOTHESES.jsonl`, `factory/EXPERIMENTS.jsonl`
- Scripts: `factory/scripts/` (13 scripts: download/audit/clean/rank/extract +
  experiment_h001…h009)
- Data: `data/` (raw+clean 2025-05/06/07 parquet; ~6.5GB incl. ~3.8GB ranked
  intermediates — ranked files are reproducible, delete after artifact upload)
- Artifacts: `factory/artifacts/` (h001–h005 summaries/results,
  h001_h005_synthesis.md; ranked_*.parquet)
- Note: `factory/artifacts/decisions.jsonl*` and `data/journal/` are live-trading
  leftovers, not research products — exclude from research uploads.

## ML Phase 2 — live translation (2026-09-01 session)

Mission: convert D10 ranking into executable admission, understand archetypes, sequence
real trades, exploit composite, execution costs, fresh 2026 evidence, paper-bot spec.

### Live-causal admission (live_admission.py, frozen theta from May-Jul only)
| rule | n | net @20bps | @40 | t1 | months |
|---|---|---|---|---|---|
| REF whole-day D10 (non-executable) | 49,210 | +22.1bps | +0.2 | +26.3 | 5/5 |
| M1 fixed theta | 44,221 | +22.6 | +0.3 | +26.2 | 5/5 |
| M2 rolling-threshold | 51,137 | +19.2 | -0.8 | +22.4 | 4/5 |
| M3 vis-rank<=2 + theta | 35,186 | **+29.0** | **+9.0** | +32.8 | 5/5 |
| M4 first-crossing | 1,100 | +26.5 | +6.5 | +33.7 | 4/5 |
| M5 persist 2of3 | 42,822 | +24.9 | +4.9 | +28.2 | 5/5 |
| **M3 x composite** | 8,170 | **+90.5** | **+70.5** | wr .570 | 5/5 |
theta_fixed=0.00115 (May-Jul p90), theta_hi=0.00340 (p97). M3 = score-rank<=2 within
current minute's visible candidate set (et_date,tod_min) — fully causal, beats D10.
Flow: 332 events/day (M3), 83/day (composite).

### Sequencing (sequencing.py, t1-entry fills, OOS Aug-Dec)
First-signal entries are below stream average. S4 scale-in (unit1 first qualifying minute,
unit2/3 at later score>=theta_hi crossings, +5m/+20m spacing) best: composite stream
+77.4bps/unit, +130bps/episode, 5/5 months, 1.7 units/ep. Re-entry-after-cooldown WORST
(late-stage contamination). Chasing first-crossing per episode (M4) degrades vs M3.

### Archetypes (archetypes.py, OOS, post-peek descriptive)
- Money map (M3 stream): rvol>8 & vwap 3-8% +84bps; rvol>8 & vwap>8% +201bps;
  rvol 2-8 rows NEGATIVE on the M3 stream -> extreme volume is the separator, not the model score alone.
- Composite-internal gradients monotone: vwap_dist 3-5% +73 / 5-8% +89 / 8-12% +147 /
  >12% +419 (n=159, small); tod later = better within composite.
- KMeans k=5 inside composite: all 4 real clusters profitable +55..+183bps (broad region,
  not knife-edge). Best: parabolic monster-RVOL (rvol~364, vwap +8.7%, ret15m +5%).
- Blowoff trap: vwap_dist>10% on ALL events loses (-61bps) but WINS inside high RVOL
  -> extension is safe only with volume behind it.
- Execution: price>$20 loses at all costs; pocket = close<=20 & cum_dv 5-100M
  (5-20M best +220bps @20). <$5 survives 100bps. >$100M dv diluted.

### Fresh 2026 evidence (download->clean->certify->features, frozen eval)
2026-01/02/03 certified: 386,178 events / 61 days. eval_2026.py = frozen stack
(model_v1 + theta 0.00115 + M3 + composite + S4 + pocket) first OOS look. Results: see
factory/artifacts/ml/eval_2026.log. PAPER_BOT_SPEC.md = v0 live reproduction spec.

### Adversarial lanes (in flight)
oracle (decay mechanisms, vwap>12% cell, capacity) + reviewer (causality audit of
live_admission/sequencing/archetypes) — record verdicts here when they land.
