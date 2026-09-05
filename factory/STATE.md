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

### Adversarial lanes — verdicts integrated
- oracle: decay ranking = (1) regime dependence of extreme-RVOL continuation, (2) execution-
  universe mismatch (~20-40bps haircut: 24.5% of composite rows lack a next bar; non-PIT tags),
  (3) layered post-peek selection. vis_rank sensitivity TESTED LOW (plateau <=1..<=4 within
  ~10bps; random 25% list drop: M3 29->24bps). rvol>8 = 17% of tape, capacity OK ($100-300K
  positions at 0.5-1% of day volume). vwap>12% cell = don't-cap-extension signal, not a cell.
  price<=20: trust directionally (stable sign at 4 cost levels). Recommended = pre-registered
  single 2026 pass. ADOPTED (PRE_REG_2026.md, written before any 2026 peek).
- reviewer: 1 MATERIAL — sequencing picks were conditioned on fwd60_t1entry non-null
  (60m-future tradability, unknowable live). FIXED: score-complete pool + decision-time
  tod<=328 cap; PnL on label-complete picks; null share reported (11.5%). S4 composite
  survived the fix (+77.5bps/unit vs +77.4 before). 5 MINORs fixed/noted (dead code,
  pool-definition documented, cut label cosmetic).

### 2026 FROZEN VERDICT (eval_2026.py, gates in PRE_REG_2026.md, written pre-peek)
2026-01/02/03 = 386,178 events / 61 days, model+theta+rules 100% frozen from 2025.
ALL 3 GATES PASS: composite net20 avg +77.3bps (gate >= +30), 3/3 months >= 0 (gate 2/3),
net40 avg +62.4bps (gate >= 0). Monthly @20: Jan +186 / Feb +5 / Mar +41. n=4,177, 70/day.
Score drift real: only 7.7% of 2026 events >= frozen theta (2025 OOS: 44%) -> strict regime
filter; DIAG month-own rel-p90 confirms attenuation is NOT a threshold artifact.
WHAT TRANSFERRED vs NOT:
- EVENT-LEVEL composite stream: 2025 +91 -> 2026 +87bps @20 (+62 @40). ROBUST. This is the product.
- Pocket (close<=20 & cumdv 5-100M, post-peek 2025 selection): first OOS look = +129.5bps
  @20 (n=1,973), monthly +345/-39/-4. Transferred (volatile Feb).
- Base M3 ranking: 2025 +29 -> 2026 +9.5bps; D10 ref -3bps; score edge now only in >t97
  tail (+39bps; t90-97 band -8.5bps). Attenuated, tail-only.
- ENTRY-TIMING refinements: S1 first-crossing 2025 +56 -> 2026 -2.5bps; S4 scale-ins
  +77 -> +18. DO NOT time within episode in v0; enter on stream events with per-name
  concurrency cap (expectancy ~= stream average, no timing skill needed).
Regime telemetry: rvol>8 share and composite flow did NOT collapse -> oracle mechanism #1
did not fire in H1-2026; mechanism #3 (layered selection) largely dead too.
RECOMMENDATION: forward paper bot = composite stream, event-level admission, 1 position
per ticker, 60m hold, 20-40bps budget, vwap extension unbounded. Sizing next.

### Exposure design (2026-09-01, exposure_design.py) — the conversion layer, SOLVED
Question: 17 qualifying minutes/episode -> how many real positions? Answer from evidence
(derivation 2025 Aug-Dec -> frozen replay 2026, PRE_REG_EXPOSURE.md written pre-peek):
- EXIT: fixed 60m hold dominates BOTH years. State-death exit destroys the edge
  (2025 +5.4 vs +54.6bps; 2026 +1.0 vs +34.4) — continuation persists PAST model-state
  lapse; do not exit when the state dies. 30m hold = capital-efficiency alternative
  (1.23 vs 0.91 bps/capital-minute 2025); 15m dead.
- EXPOSURE: persistence harvest = re-enter after each 60m hold at next qualified minute
  (E3/E6), ~1.6 entries/episode. Cycle-2 keeps full edge (+59.2), cycle-3 dead weight
  (+9.2) but harmless. First-entry-only (E1) is BELOW stream average in 2026 (-2.5).
- ENTRY GATE: rvol>8 at the entry minute (2025: +70.7 vs -49.7 for rvol 4-8).
- FROZEN E6 (rvol>8 gate + unlimited 60m re-entry cycles): 2025 +66.4bps/unit wr .548
  5/5 months, +113.9/episode; 2026 +34.4bps/unit wr .498 3/3 months (+78/+3/+27),
  +54.1/episode, 5.4 entries/day. ALL 3 pre-registered gates PASS on 2026.
  Survives 40bps RT at +14.4 (2026) / +46.4 (2025).
- ECONOMICS (cap 10 names x $10k units): 2026 mean +$194/day (p10 -1,262/p90 +1,400),
  57/61 trade-days, ~4 positions avg, zero entries skipped -> not concurrency-bound at
  small size. 2025 mean +$415/day.
- Feature parity: PAPER_BOT_SPEC.md v0 was WRONG on rvol (real def: cum $vol / 20-session
  bucket-interpolated expected cum $vol), market_ret_5m (real: cross-sectional MEDIAN over
  entire clean universe), excess_gain (gain minus that median). Spec rewritten with exact
  build_features.py definitions. Advisor suspicion confirmed and fixed.
Bot spec updated (PAPER_BOT_SPEC.md): composite gate + E6 exposure + fixed 60m exit.
Next: implement v0 paper bot per spec; live rvol baseline table needs 20 sessions warmup.

## 2026-09-02 (session 3) — rvol corruption + corrected verdicts + frozen v2 + fresh-window prep
- **BUG (fixed)**: build_features.rvol_attach cum_sum over unsorted rows → all 11 feature parquets had row-order-partial-sum rvol. Fixed (sort before cumsum). tests/test_feature_parity.py caught it (live engine == intended math).
- **Rebuilt**: features 2025-05..2026-03 (v2), model_v2.pkl (best_iter 56), theta re-derived dev-only = 0.00098 (theta_hi 0.00259).
- **Corrected verdicts**: composite FROZEN Nov+Dec +86.1bps@20 (was +102.6); E6 2025 +45.0bps/unit (was +66.4); **E6 2026 −62.8bps/unit (was +34.4) — 2026 GO REVERSED**; eval_2026 all cuts negative. RVOL_CORRUPTION_REPORT.md.
- **Trust audit**: tests/audit_features_independent.py — hand math from raw chronological bars == research parquet == live engine; 1253 event rows, 0 mismatches (train/OOS/2026 samples). Sortedness audit of all window ops: only rvol_attach was unsorted; labels/rank/m5 order-independent (self-join/median/rank).
- **FROZEN_V2.md committed BEFORE fresh window** (model_v2 + theta 0.00098 + M3/composite/E6_rvol8/X_60m/cap10/$10k; eval protocol pre-registered; no tuning between months).
- **Fresh window blocked on data**: HF dataset (mito0o852/OHLCV-1m + all mirrors) ends 2026-03; updated 2026-05-03. 2026-04..08 exist nowhere local. FIX: backfill from Alpaca SIP historical (entitlement VERIFIED via test pull). factory/scripts/backfill_alpaca.py → HF-schema raw parquets; clean/certify/featurize chain unchanged; validate_backfill.py (bar-level vs HF 2026-03) gates the run. eval_frozen.py written+committed BEFORE fresh data lands.
- Decision rule (pre-registered): paper-deploy iff pooled net/unit > 0 @20bps AND all five months ≥ −20bps/unit; else no-go + mechanism investigation, no re-tune.

## 2026-09-03 — fresh window built (Alpaca SIP backfill) + NO-GO verdict
- **Data fix**: HF dataset ends 2026-03 → backfilled 2026-03..08 from Alpaca SIP
  (backfill_alpaca.py, resumable, ~36-39M rows/month). Validation on 2026-03 vs HF:
  100% listed-ticker bar coverage, 99.5% closes <0.5%, 99.1% vols <1% → PASS.
  Clean/certify/featurize chain unchanged; features 2026-04..08 coverage healthy
  (rvol .94-.97). NOTE: first featurize pass wrote all-null features (clean files in
  data/backfill/, build_features reads data/) — caught by coverage check, re-run fixed.
- **FROZEN one-shot eval (2026-04..08)**: **NO-GO.** Pooled −41.6bps/unit @20 (−61.6
  @40), wr 0.480, 215 entries (2.9/day), −$115/day, all 5 months negative (−16..−153),
  top-5 = 13.2% |gross| (broad loss). Both pre-registered deploy conditions failed.
- **Post-hoc decomposition** (REPORT_FRESH_WINDOW_2026.md): model IC fresh +0.018 (weak
  but alive, ~1/3 of 2025 val +0.059); M3 flat; the 2025-selected composite gates
  (rvol>4 & vwap>0.03) FLIPPED negative (+0.86% 2025 → −0.13% fresh) — the
  extension/volume conditional is what failed to transfer, not primarily the model.
  E6 within-episode timing −42bps vs pool +9bps (noise-dominated, n=215).
- **Status**: no validated edge in this family as of 2026-08. Paper bot = measurement
  instrument (if built). New hypotheses require new data (2026-09+). No re-tuning on
  04-08.

## 2026-09-03 — forensic investigation (advisor brief + "something is off" intuition)
- **Implementation/data/parity: CLEAN.** 11 checks: hand-math audit, sortedness audit,
  bar-level xvendor (100%/99.5%), FULL-STACK March replication (Alpaca −87.7 vs HF
  −74.6bps, same sign/magnitude), eval_frozen replays 2025 at +45.0 EXACTLY, no April
  vendor cliff (Q1 worse than fresh), checks.json/coverage stable, nulls incident
  fixed pre-eval. Reviewer subagent NOT completed (2x provider 429s) — disclosed.
- **What broke**: 60-min continuation lift fired 2/5 OOS months 2025 (Sep/Dec), ~0/8
  in 2026. L/S quintile spread negative 11/13 months. Only Dec-2025 significant
  (t=+2.27, n=41); 2025 pooled t=+1.40 (ns); 13-month arc −10bps t=−0.53.
- **2025 anatomy**: weak signal + mild selection-adjacency + one lucky month (Dec;
  cycle-3 n=20 +222bps, vwap-extreme n=39 +190bps tails). Repeatable core (cycle-1)
  was +30bps t≈1.2. Market-regime proxies do NOT separate winners/losers.
- **Execution exonerated**: t+0/t+1, holds 15/30/60, cycles, timing — nothing flips
  2026; gross negative so costs aren't it. Negative pool, no rule rescues it.
- **Mechanism dead (long)**: no conditioning works in 2026 (12+ variants); multi-day
  continuation NEVER existed (gainer fade both years); short side ≈ breakeven.
- **Candidate**: NONE. rvol 8-12 same-sign both periods but t≈1.0 post-hoc — not a
  recommendation. Next: forward paper MEASUREMENT only (Sep-Dec 2026, zero capital);
  new hypotheses need new pre-reg. NO paper deployment of this stack.

## 2026-09-03 — Cameron/discretionary-momentum research lane (design only, no implementation)
- Researcher brief obtained (DIRECT/INFERRED labeled; no web fetch available — cutoffs need primary-source confirmation, but our tests grid our own numbers anyway).
- Data availability VERIFIED: yfinance float+short% (~0.7s/ticker); Alpaca NewsClient historical news (timing rule: catalyst window ends at 9:30 ET; movers-roundups are coincident, excluded); premarket bars exist in raw backfill files; trade_count NOT retained (design change: keep it in future backfills).
- Key mechanism probes (16-month panel): HINDSIGHT leader minutes +30/+51bps (2025/2026, incl. mega-leaders +25/+67bps) vs tail −41/−43bps — regime correlates with sign in BOTH years. CAUSAL minute-rank-1: negative every month both years (chasing rotates into tops). Gap = early leader identification = the lane (Stage A prediction + Stage B within-leader patterns).
- RESEARCH_CAMERON_LANE.md: translations table, 7 pattern event specs, 8 design changes (D1 regime-conditioning first ... D8 catalyst A/B), hypotheses H-C1..H-C7 with dev/validation split (2025+2026 = dev; forward paper = validation). Score gate retired to covariate; 60-min fixed hold retired.

## 2026-09-04 — Leader program pass 2 (6 lanes + local; research only, no implementation)
- Web (High): rechecks — gap-fade CONFIRMED (FILTRIX n=66,906); RVOL 2x/5x + 100k PM-vol UNCERTAIN as alpha cutoffs (convention/execution only); float tiers directional, rotation-multiple the real variable; $2-20 tradability compromise (incl. $5-10 worst-fade tier); morning-only + EOD-reversal STRONGLY CONFIRMED. Tradeoff: gross edge first-15-min (unchaseable), net window 09:45-11:00, midday/EOD headwinds.
- Patterns publicly ~0 unconditional; leader-vs-tail test exists nowhere — only our panel can run it. ML verdict: profit-weighted learning-to-rank + calibrated meta-gate, 3-stage funnel; binary leader classification rejected.
- Empirical (21-22 sessions, causal): 10-20% true-gap pocket both years; tod gradient 81/37/18/14 (2025), 46/24/18/5 (2026); top-3-by-15:00 leaders +~48% to close (9/10 beat costs); snapshot #1 holds <=41%, top-5 50-64%; separation@15:00 85.7% vs 12.5% (n=22); share non-monotonic; PM-high distance (~0) + scarcity (~0) DEMOTED. 2025 clean files lack premarket; 2026 backfill has it.
- Doc: researches/07-dominant-leader-program.md (framework v1). No production bot. Next: Stage-A tests 1-7 in doc.

## 2026-09-04 — Leader program pass 3 (advisor consolidation; research only)
- REGIME CAVEAT: causal top-3 fwd flips sign across day-samples (Mar sessions +3.4% both yrs vs spread-year negative vs second 20d +5.7%). Opportunity is regime-clustered, not uniform. No threshold ships without month-blocked proof.
- Representations: diff_pp separation wins (rho .54/.47, 85.7% high-tercile); HHI/share dead; $-vol-only dead (-0.39%, 0% recall). Top-4 gain recall 60% eventual / 95% any-big-mover.
- Stateful: flash-tops below VWAP/off-high vs leaders holding structure; drawdown-from-high corr +.76 with forward; archetypes 7 gap / 0 pure-emergent / 9 other + ELAB late-launch outlier.
- Premarket-backfill GO: 2025 ext-hours ~538MB total, month phases, entitlement OK. Awaiting run approval.
- Target verdict: excursion-gated MFE + recall-first funnel. Doc researches/07 (pass 3). No implementation.

## 2026-09-04 — 2025 premarket repair RUNNING + forward observer BUILT
- Backfill: factory/scripts/backfill_premarket_2025.py (premarket-only <09:30 ET, own
  outputs data/backfill/premarket_ohlcv_2025-MM + premarket_parts/, resume per part).
  API proven, Jan pilot ok (~3-5s/batch, ~40min/mo, ~5GB/yr total, 143GB free).
  Running all 12 months in background (logs/premarket_backfill.log). Existing data untouched.
- Observer: factory/scripts/forward_observe.py (ORDER-FREE by construction + assert +
  test). Session loop 13:25-20:10 UTC, 2-min polls: scans.jsonl (top-30 raw),
  promotions.jsonl (union of top4-gain / gain-x-vol / sep-shortlist + SIP bars/ADV/float/news),
  state.jsonl (rank/sep/$-vol per promoted). Scoring offline — nothing frozen.
  tests: factory/scripts/test_forward_observe.py (5 pass). Dry run ok.
  Live loop started 2026-09-03 22:06 UTC, idles to Fri 13:25 UTC (logs/forward_observe.log).
- Judgment (advisor Q): observer-only first, NO live shadow entries — forward 1-min bars
  make any entry/exit replayable offline with identical SIP data; shadow engine = strategy
  creep. Watch recall-first (2-8 names), precision downstream offline.

## 2026-09-04 — backfill bug postmortem + replay harness + regime wave
- BUG: polars dt.hour()/minute() are Int8 -> `h*60+m` overflowed (10:00 -> 88), so the
  premarket filter kept the COMPLEMENT (evening bars). Caught by hour-dist validation.
  Fix: cast to Int32 + assert zero rows >=14:30 UTC per merged month. Lesson: validate
  distributions, not just row counts. Deleted polluted files, rerunning all 12 months.
- Ops lesson: git-bash `ps` misses Windows python; duplicate background runs raced on
  part files. Use tasklist/wmic + check log coherence (no dup batch lines) for singletons.
- Harness: factory/scripts/replay_watchlist.py — snapshot t -> rules -> E0 (buy-hold,
  selection alpha) vs E1 (first-pullback, timing alpha). Pilot 2d: E0 neg/flat, E1
  positive 70-79% hit (tiny n). One code path for historical + (later) forward-backfilled days.
- Regime cover (web lane lost tooling; ran in main): VIX-gate precedents (Concretum,
  AlgoKing VIX<16/16-24/>24, AIBROKER ERM trend/breadth/dispersion/vol), Daniel-Moskowitz
  panic states, internals (TICK/ADD/VOLD), SmallCapLab next-day, Vortex gap map,
  TradeTheMatrix cycles, FOMO-momentum GH (10yr/23k-ticker negative-results program).
- Observer widened 30->50 live snapshots for future-rule replay; full-market depth via
  next-day historical backfill (DO NOT run backfill_alpaca for 2026-09 until month ends —
  partial final would poison resume-skip).

## 2026-09-04 — pass 4: opportunity-state (n=36d) + leader-state (n=150)
- Day base: top-4 fwd +1.4% mean/-0.1% med; max-fwd +39.8% mean (daily continuation exists).
- HOT TAPE replaces scarcity: n10>=4 -> 61% vs 39% (+2.6 vs +0.2); top1-share NEGATIVE
  (<0.5 -> 67% vs 33%); HHI negative; churn negative. Price-separation good, vol-diffusion good.
- Morning window only: 16:00 emergence not still-tradable. Structure persists, behavior re-dices.
- Names: mid-gain@15 0.58 hit (inverted-U); high-accel fades (0.29); rising>stable;
  2+ pullbacks = left-tail (-7.7% mean, hit holds). Health = mid-gain+decel+rising+near-high+VWAP.
- Regime-web lane failed on tooling; NO-WEB priors filed + main-thread web cover (VIX-gate,
  ERM, panic states, internals). Doc 07 pass 4. No implementation.

## 2026-09-04 — Stage-A harness + block regime map (research only)
- Harness: factory/scripts/stagea_eval.py (baselines cur1/top3-20, sep3, opp gates,
  health lite/full, emerging lane; selected-vs-rejected; multi-block). Fast (~25s/day).
- BLOCKS (causal top-N fwd-to-close, first-sessions/mo + mid-Jun check):
  Mar25 +983 | Apr-May -431 | Jun-Jul cur1 -341 | Aug-Sep top3 -194 | Oct-Nov +274 |
  Dec+Mar26 top3 +1086/cur1 +1903 | mid-Jun -1267 (not month-start artifact).
  Summer = cold regime, fall/winter+Mar = hot. Same rules, opposite signs.
- Early: health-lite/full >> none on hot blocks; emerging lane ~noise so far
  (Aug-Sep -403, Oct-Nov -19, Dec-Mar -5); gates untested (hot days pass all).
- Pending: parity-audit + emerging lanes; backfill Dec; Friday forward session live.

## 2026-09-04 — milestone: Stage-A NOT mature (48d fixed harness)
- Pooled: top3 +17 / top5 +46 / sep3 -39 bps, meds neg, hit ~.45; gates/health move
  nothing pooled; selected-vs-rejected coin flip (20-24/48). Raw watchlist != edge.
- Old block map void (clock artifact); new blocks -507..+831, no seasonal form.
- Survives: ET gradient recheck (4d), hindsight effect (recheck queued), heterogeneity,
  emerging-(a) capped, halt-tolerant gating.
- Assets: 2025 PM repaired+validated (325MB); Friday forward captured (1525 rows/28 syms).
- Next: PM retests, forward scoring, month-blocked state/health proof. No Stage B.

## 2026-09-04 — advisories reconciled; per-name logging; canonical state; orphans moved
- Adopted: target-econ (MFE-gated primary + FP-per-opportunity; mean-fwd retired as ranker);
  inference-audit 15-claim table (5 supported directional, 1 disproven, rest pending);
  repo-debt triage (leave-alone + 3 orphan moves only).
- Built: per-name {fwd,mfe,mae,obp} in stagea --out (was the binding constraint).
- Moved: data/stage_*{md5-verified identical} -> data/_superseded/ + receipt;
  zz_leader_health.csv -> factory/artifacts/; deprecation headers rank_day/extract_events.
- Wrote researches/CANONICAL_STATE.md (fresh-context inheritance contract).
- CodeGraph tested live: 98 files/2.9k nodes, blast-radius + dup-map instant. Adopted as
  structural navigator; no reorg. Note: indexes src/, not factory/scripts/.
- /tmp reaped aggressively — write harness outputs to data/_scratch/, not /tmp.
- Observer redeploy (gain_open_anchored) after Friday close ~20:10 UTC.

## 2026-09-04 — MFE-before-DD confirmed; PM retests; mfe lane timed out (partials kept)
- MFE-before-100bps (33d/165 names, salvaged data/_scratch/mfe_curves.json): pooled
  711/158, P(>200)=0.46; ALL 9 month-blocks positive (298-1678); day-hit 31/33;
  FP/cap 0.8-2.2. Terminal fwd flips sign on same blocks. Target adopted.
- PM retests (20d, n=14,379): echo-70% contradicted blanket (0.56; narrow-PM 0.71 ->
  wide 0.36 monotonic); PM-high distance dead (r=-0.010, permanent demote);
  true gaps weak-negative tilt, tiny-n; PM-$-vol leadership contradicted (0/20).
- mfe-curves lane timed out at 30min despite steer; partial JSON kept. Lesson: cap
  lane scopes to <=24 days or split halves explicitly.

## 2026-09-05 — first forward session scored (n=31 promoted, 26 scored)
- Scorer bugs fixed: str-vs-datetime compare (zeroed all outcomes), month-boundary end,
  empty-stats guard, warrant-symbology variants (5 names genuinely bar-less: BNCWZ,
  EONR.WS, EUDAW, GFAIW, OGGWZ — screener phantoms, 16% of promotions).
- Friday tape: mean fwd -1025 / med -1175 / hit 7/26 / mean MFE +1338. Violent warrant
  tape (BIAFW +9743, TMCWW -7889). MFE>>fwd signature live-confirmed; terminal negative.
- Logging apparatus verified: 178 scans x50, 0 gaps>5min, 2647 state rows, promos
  13:25-19:56, none pre-window. Observer redeployed w/ gain_open_anchored (idle to Mon).

## 2026-09-05 — verification program: rank-1-chase DOWNGRADED, PM retests confirmed
- verify_core.py (new, permanent): A=hindsight (BTAI +17.2/EGG +25.4 fresh, holds);
  B=rank-1 chase h60, 11 monthly points MIXED (means -579..+411) — 'negative every
  month' NOT reproduced; claim downgraded to provisional/horizon-specific. Vectorized
  merge_asof verified exactly vs row-loop (n=328/303, identical means).
- C=PM via harness path (6 fresh days): echo narrow>wide monotonic every day
  (0.54-0.84 vs 0.27-0.39); pmcorr ~0 (one +0.14 day); PM-lead 1/6 (combined 1/26).
- Trust map written to CANONICAL_STATE.md. MFE-magnitude provenance gap noted
  (lane-only code — needs harness-path rerun next).

## 2026-09-05 — interaction scale-up started (per-name E1 + MB detail in harness)
- stagea --out now stores per-name {fwd,mfe,mae,obp,mb100,mb200,e1}. outcome_MB uses
  conservative same-bar rule (DD-first). Convention note: entry-bar included, low-based;
  salvaged lane used looser convention (MSTZ mb100 106 vs 0.0 here) — mine is stricter
  and matches market-order economics. Discrepancy logged, not hidden.
- 3 lanes: rerun-A (03-08), rerun-B (09-12+2026-03, capped scopes), reviewer (audit MB/E1
  wiring + independent mb100 repro spec). Outputs to data/_scratch/sae1_*.json.

## 2026-09-05 — interaction verdict: E1 shows no conditionality; P1s fixed+verified
- E1-first-pullback selected vs rejected: no separation either slice (A +18/0.46 vs
  +9/0.44; B -45/0.32 vs -2/0.42). Pattern #1 fails the attention-conditionality test.
- Fixed: E1 next-bar-open fill, dead-bar ref (et>=t), vectorized MB == loop exactly,
  snapshot cdv vectorized, E1 cached. Post-fix E1 lower (close-fill was optimistic).
- Next: HOD-break + VWAP-reclaim probes before any mechanism rethink.

## 2026-09-05 — E2 (HOD-break) + E3 (VWAP-reclaim) built, scaling
- entries_E2/E3 share _run_trade exit engine with E1 conventions (next-bar fill,
  stop-first, 2R, 15-bar stop, 15:30 ET flatten). Wired e2/e3 into stagea names.
- 3 lanes: rerun-C (03-08), rerun-D (09-12+2026-03), reviewer2 (E2/E3 audit).
